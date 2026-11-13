"""
Stall Monitor Daemon for Polytoken.

Implements the stall detection rules from swarm_monitor/SKILL.md:
- Detects unaddressed messages (#seq)
- Detects deadlocks where all agents are in 'waiting' or 'stopped'
- Pings responsible agent or coordinator with @<agent> [stall]
"""

import sys
import threading
import time
from typing import Optional
from .board_manager import BoardManager


class SwarmStallMonitor:
    def __init__(self, board_manager: BoardManager, check_interval_seconds: float = 60.0, worker_runner=None):
        self.bm = board_manager
        self.worker_runner = worker_runner
        self.interval = check_interval_seconds
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self.alert_counts = {}
        self._stop_event = threading.Event()

    def start(self):
        self.running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=max(2.0, self.interval + 0.1))

    def _run_loop(self):
        while self.running and not self._stop_event.wait(self.interval):
            self._safe_check()

    def _safe_check(self):
        try:
            self.check_for_stall()
        except Exception as e:
            # stdout carries the JSON-RPC stream; diagnostics must not corrupt it.
            print(f"[StallMonitor Error]: {e}", file=sys.stderr)

    def check_for_stall(self):
        members_data = self.bm.read_members().get("members", {})
        messages = self.bm.read_raw_messages()
        if not messages:
            return

        last_msg = messages[-1]
        if last_msg.get("agent") == "monitor":
            return
        last_text = last_msg.get("text", "")

        # 1. If coordinator posted [done], monitor goes quiet
        if last_msg.get("agent") == "coordinator" and "[done]" in last_text.lower():
            return

        # 2. Check if anyone is actively 'working'
        live_workers = {
            name: m
            for name, m in members_data.items()
            if not m.get("killed", False)
            and name not in ["coordinator", "monitor", "narrator"]
        }
        if not live_workers:
            return
        def reported_active(name, member):
            if member.get("execution_mode") in ("delegated", "bound"):
                return member.get("wake_mode") == "working" or member.get("state") == "running"
            return member.get("wake_mode") == "working" and (
                self.worker_runner is None or self.worker_runner.is_running(name)
            )

        any_working = any(reported_active(name, member) for name, member in live_workers.items())
        if any_working:
            return  # Normal active computation, not a stall

        # 3. Check if all live members are parked
        parked_modes = {"mentions", "anything"}
        all_parked = all(
            (m.get("wake_mode") in parked_modes if m.get("execution_mode") in ("delegated", "bound")
             else m.get("wake_mode") in parked_modes
             or (self.worker_runner is not None and not self.worker_runner.is_running(name)))
            for name, m in live_workers.items()
        )
        if not all_parked:
            return

        # 4. Analyze cause: unaddressed post vs unanswered @mention
        known_names = set(members_data.keys())
        mentioned, is_all, ch_mentions = self.bm.parse_mentions(last_text, known_names)

        if not mentioned and not is_all and not ch_mentions and last_msg.get("agent") != "coordinator":
            alert_key = f"unaddressed_{last_msg['seq']}"
            if self.alert_counts.get(alert_key, 0) < 3:
                self.bm.post_message(
                    agent="monitor",
                    text=f"@coordinator [stall] #{last_msg['seq']} ({last_msg['agent']}'s post) was addressed to nobody; swarm is waiting.",
                )
                self.alert_counts[alert_key] = self.alert_counts.get(alert_key, 0) + 1
            return

        # Unanswered @mention: target is live and parked while last_msg tagged them
        targets = [n for n in mentioned if n in live_workers and n != last_msg.get("agent")]
        if is_all:
            targets = [n for n in live_workers if n != last_msg.get("agent")]
        for target in targets:
            alert_key = f"unanswered_{last_msg['seq']}_{target}"
            if self.alert_counts.get(alert_key, 0) < 3:
                self.bm.post_message(
                    agent="monitor",
                    text=f"@{target} [stall] #{last_msg['seq']} is waiting on your response.",
                )
                self.alert_counts[alert_key] = self.alert_counts.get(alert_key, 0) + 1

"""
TDD regression tests for PolytokenSwarmAdapter, WorkerProcessManager,
WorkspaceManager, and SwarmStallMonitor.
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from pt_swarm.board_manager import BoardManager
from pt_swarm.harness_adapter import PolytokenSwarmAdapter
from pt_swarm.stall_monitor import SwarmStallMonitor
from pt_swarm.worker_runner import WorkerProcessManager
from pt_swarm.workspace_manager import WorkspaceManager


class TestWorkerProcessManager(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.swarm_dir = self.test_dir / ".swarm" / "default"
        self.swarm_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_cross_process_pid_detection_prevents_duplicate_spawn(self):
        # Launch a short-lived sleeping subprocess via manager A
        cmd = [sys.executable, "-c", "import time; time.sleep(5)"]
        mgr_a = WorkerProcessManager(
            swarm_dir=self.swarm_dir,
            workspace_root=self.test_dir,
            worker_cmd_template=cmd,
        )
        res_a = mgr_a.spawn_or_wake(name="worker1", prompt_text="work", brief_seq=1)
        self.assertTrue(res_a["spawned"])

        try:
            # A second WorkerProcessManager in another process/instance must see worker1 running
            mgr_b = WorkerProcessManager(
                swarm_dir=self.swarm_dir,
                workspace_root=self.test_dir,
                worker_cmd_template=cmd,
            )
            self.assertTrue(mgr_b.is_running("worker1"))
            res_b = mgr_b.spawn_or_wake(name="worker1", prompt_text="wake", brief_seq=0)
            self.assertFalse(res_b["spawned"])
            self.assertEqual(res_b["status"], "already_running")
        finally:
            mgr_a.kill_worker("worker1")

    def test_log_file_handle_not_leaked_in_parent(self):
        cmd = [sys.executable, "-c", "import time; time.sleep(0.1)"]
        mgr = WorkerProcessManager(
            swarm_dir=self.swarm_dir,
            workspace_root=self.test_dir,
            worker_cmd_template=cmd,
            spawn_grace_seconds=0.0,
        )
        res = mgr.spawn_or_wake(name="logger", prompt_text="hi", brief_seq=1)
        self.assertTrue(res["spawned"])
        proc = mgr._processes["logger"]
        proc.wait(timeout=3.0)
        # On Windows, if parent still holds an open handle to logger.log, unlink/rename fails
        # or we can verify log_fh was closed; removing the log file must succeed after proc exits.
        log_path = Path(res["log"])
        log_path.unlink()
        self.assertFalse(log_path.exists())

    def test_windows_cmd_quoting_strips_quotes(self):
        with patch.dict(os.environ, {"POLYTOKEN_WORKER_CMD": 'python -m "my module" --flag'}):
            cmd = WorkerProcessManager._resolve_default_cmd()
            self.assertEqual(cmd, ["python", "-m", "my module", "--flag"])

    def test_default_worker_command_is_headless_exec(self):
        # A bare `polytoken` launches the TUI and dies without a TTY, so the
        # default must be the headless `exec` subcommand.
        env = {k: v for k, v in os.environ.items() if k not in ("POLYTOKEN_WORKER_CMD", "POLYTOKEN_BIN")}
        with patch.dict(os.environ, env, clear=True):
            with patch("pt_swarm.worker_runner.shutil.which", return_value="/opt/bin/polytoken"):
                cmd = WorkerProcessManager._resolve_default_cmd()
        self.assertEqual(cmd, ["/opt/bin/polytoken", "exec", "{prompt}"])

    def test_spawn_reports_failure_when_worker_exits_immediately(self):
        mgr = WorkerProcessManager(
            swarm_dir=self.swarm_dir,
            workspace_root=self.test_dir,
            worker_cmd_template=[sys.executable, "-c", "import sys; sys.exit(3)"],
            spawn_grace_seconds=0.2,
        )
        res = mgr.spawn_or_wake(name="doomed", prompt_text="work", brief_seq=1)
        self.assertFalse(res["spawned"])
        self.assertEqual(res["exit_code"], 3)
        self.assertIn("error", res)
        self.assertFalse(mgr._pid_file("doomed").exists())
        self.assertTrue(mgr.is_running("doomed") is False)

    def test_prompt_is_substituted_into_worker_command(self):
        out = self.test_dir / "prompt.txt"
        script = "import sys, pathlib, time; pathlib.Path(sys.argv[2]).write_text(sys.argv[1]); time.sleep(1.0)"
        mgr = WorkerProcessManager(
            swarm_dir=self.swarm_dir,
            workspace_root=self.test_dir,
            worker_cmd_template=[sys.executable, "-c", script, "{prompt}", str(out)],
            spawn_grace_seconds=0.25,
        )
        try:
            res = mgr.spawn_or_wake(name="echoer", prompt_text="hello board", brief_seq=1)
            self.assertTrue(res["spawned"])
            for _ in range(100):
                if out.exists() and out.read_text(encoding="utf-8"):
                    break
                time.sleep(0.02)
            self.assertIn("hello board", out.read_text(encoding="utf-8"))
        finally:
            mgr.kill_worker("echoer")

    def test_initial_prompt_tells_worker_to_pass_caller(self):
        specs = []
        mgr = WorkerProcessManager(
            swarm_dir=self.swarm_dir,
            workspace_root=self.test_dir,
            in_process_spawner=specs.append,
        )
        mgr.spawn_or_wake(name="alice", prompt_text="do work", brief_seq=0)
        self.assertIn('caller="alice"', specs[0]["initial_input"])

    def test_recycled_pid_is_not_treated_as_running(self):
        if not sys.platform.startswith("linux"):
            self.skipTest("start markers are recorded from /proc on Linux only")
        sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
        try:
            mgr = WorkerProcessManager(
                swarm_dir=self.swarm_dir,
                workspace_root=self.test_dir,
                worker_cmd_template=["true"],
            )
            mgr._pid_file("ghost").write_text(
                json.dumps({"pid": sleeper.pid, "start": "0"}), encoding="utf-8"
            )
            self.assertFalse(mgr.is_running("ghost"))
            mgr._write_pid("ghost", sleeper.pid)
            self.assertTrue(mgr.is_running("ghost"))
        finally:
            sleeper.kill()
            sleeper.wait()


class TestPolytokenSwarmAdapter(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_adapter_shares_workspace_manager_and_wires_mention_wake_and_shutdown(self):
        woken = []
        adapter = PolytokenSwarmAdapter(
            workspace_path=str(self.test_dir),
            swarm_id="s1",
            in_process_spawner=lambda spec: woken.append(spec),
        )
        self.assertEqual(
            adapter.workspace_manager.worktrees_dir,
            adapter.worker_runner.workspace_manager.worktrees_dir,
        )

        # Add a worker without running a subprocess, then mention it from coordinator
        adapter.board_manager.add_member("parser", role="worker")
        adapter.board_manager.post_message(agent="coordinator", text="@parser please check")
        self.assertEqual(len(woken), 1)
        self.assertEqual(woken[0]["name"], "parser")

        # Spawn a real subprocess and verify shutdown() terminates it
        cmd = [sys.executable, "-c", "import time; time.sleep(10)"]
        adapter.worker_runner.worker_cmd_template = cmd
        adapter.worker_runner.in_process_spawner = None
        res = adapter.worker_runner.spawn_or_wake("subw", "run", brief_seq=2)
        self.assertTrue(res["spawned"])
        self.assertTrue(adapter.worker_runner.is_running("subw"))
        adapter.shutdown()
        self.assertFalse(adapter.worker_runner.is_running("subw"))


class TestWorkspaceManager(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init"], cwd=str(self.test_dir), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(self.test_dir), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(self.test_dir), capture_output=True, check=True)
        (self.test_dir / "README.md").write_text("hi", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=str(self.test_dir), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(self.test_dir), capture_output=True, check=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_share_worktree_cleanup_removes_branch_and_allows_recreate(self):
        wm = WorkspaceManager(self.test_dir)
        ws1 = wm.provision_workspace("agent1", mode="share")
        self.assertTrue((ws1 / "README.md").exists())
        wm.cleanup_workspace("agent1", mode="share")
        self.assertFalse(ws1.exists())

        # Branch swarm/agent1 should be cleaned up so provisioning again uses a real git worktree
        branches = subprocess.run(
            ["git", "branch", "--list", "swarm/agent1"],
            cwd=str(self.test_dir),
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.assertEqual(branches, "")

        ws2 = wm.provision_workspace("agent1", mode="share")
        self.assertTrue((ws2 / ".git").exists())
        wm.cleanup_workspace("agent1", mode="share")


class TestSwarmStallMonitor(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.bm = BoardManager(self.test_dir)
        self.monitor = SwarmStallMonitor(self.bm)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_does_not_alert_when_all_workers_killed(self):
        self.bm.add_member("w1", role="worker")
        self.bm.post_message("w1", "finished without mention")
        self.bm.kill_member("w1")
        self.bm.kill_member("coordinator")
        self.monitor.check_for_stall()
        msgs = self.bm.read_raw_messages()
        self.assertEqual(len(msgs), 1)

    def test_worker_done_does_not_silence_monitor(self):
        self.bm.add_member("w1", role="worker")
        self.bm.set_member_wake_mode("coordinator", "mentions", wait_since=0)
        self.bm.set_member_wake_mode("w1", "mentions", wait_since=0)
        self.bm.post_message("w1", "[done] finished my part")
        self.monitor.check_for_stall()
        msgs = self.bm.read_raw_messages()
        self.assertEqual(msgs[-1]["agent"], "monitor")
        self.assertIn("[stall]", msgs[-1]["text"])

    def test_detects_unanswered_mention_when_target_parked(self):
        self.bm.add_member("w1", role="worker")
        self.bm.add_member("verifier", role="worker")
        self.bm.post_message("w1", "@verifier please check #1")
        self.bm.set_member_wake_mode("coordinator", "mentions", wait_since=1)
        self.bm.set_member_wake_mode("w1", "mentions", wait_since=1)
        self.bm.set_member_wake_mode("verifier", "mentions", wait_since=1)
        self.monitor.check_for_stall()
        msgs = self.bm.read_raw_messages()
        self.assertEqual(msgs[-1]["agent"], "monitor")
        self.assertIn("@verifier [stall]", msgs[-1]["text"])


    def test_monitor_errors_go_to_stderr_not_stdout(self):
        err, out = io.StringIO(), io.StringIO()
        with patch.object(self.bm, "read_members", side_effect=RuntimeError("boom")):
            with patch("sys.stderr", err), patch("sys.stdout", out):
                self.monitor._safe_check()
        self.assertIn("boom", err.getvalue())
        self.assertEqual(out.getvalue(), "")


if __name__ == "__main__":
    unittest.main()

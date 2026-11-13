"""
Polytoken Harness Adapter for Swarm Execution.

Integrates the Swarm orchestrator with Polytoken's multi-agent runtime.
Handles lifecycle events, agent instantiation, workspace isolation,
stall monitoring, and tool dispatching.
"""

from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .board_manager import BoardManager
from .swarm_tools import SwarmToolHandlers
from .stall_monitor import SwarmStallMonitor
from .worker_runner import WorkerProcessManager
from .workspace_manager import WorkspaceManager


class PolytokenSwarmAdapter:
    def __init__(
        self,
        workspace_path: str,
        swarm_id: str = "default_swarm",
        auto_start_monitor: bool = False,
        in_process_spawner: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ):
        self.workspace_path = Path(workspace_path).resolve()
        self.swarm_dir = self.workspace_path / ".swarm" / swarm_id
        self.swarm_dir.mkdir(parents=True, exist_ok=True)

        self.worker_runner = WorkerProcessManager(
            swarm_dir=self.swarm_dir,
            workspace_root=self.workspace_path,
            in_process_spawner=in_process_spawner,
        )
        self.workspace_manager = self.worker_runner.workspace_manager
        self.board_manager = BoardManager(
            self.swarm_dir, on_member_mentioned=self._on_member_mentioned
        )

        self.stall_monitor: Optional[SwarmStallMonitor] = None
        if auto_start_monitor:
            self.stall_monitor = SwarmStallMonitor(
                self.board_manager, check_interval_seconds=60.0
            )
            self.stall_monitor.start()

    def get_agent_tools(self, agent_name: str) -> Dict[str, Any]:
        """
        Returns callable tool functions formatted for Polytoken's agent loop.
        """
        handlers = SwarmToolHandlers(
            self.board_manager,
            caller_agent_name=agent_name,
            worker_runner=self.worker_runner,
        )

        return {
            "swarm": handlers.handle_swarm,
            "post_message": handlers.handle_post_message,
            "read_board": handlers.handle_read_board,
            "swarm_wait": handlers.handle_swarm_wait,
        }

    def load_system_prompt(self, role: str) -> str:
        """
        Loads the markdown role instructions for an agent.
        """
        role_map = {
            "coordinator": "coordinator.md",
            "worker": "worker.md",
            "verifier": "worker.md",
            "monitor": "monitor.md",
            "narrator": "narrator.md",
        }
        filename = role_map.get(role, "worker.md")
        prompt_file = (
            Path(__file__).resolve().parent / "agent_definitions" / filename
        )
        if prompt_file.exists():
            return prompt_file.read_text(encoding="utf-8")
        return ""

    def on_worker_spawn_requested(
        self,
        name: str,
        brief: str,
        model: Optional[str] = None,
        workspace_mode: str = "inherit",
    ) -> Dict[str, Any]:
        """
        Hook triggered when `swarm(action='add', ...)` is executed.
        Provisions workspace isolation and prepares agent configuration for Polytoken.
        """
        agent_workspace = self.workspace_manager.provision_workspace(
            name, mode=workspace_mode
        )
        system_prompt = self.load_system_prompt("worker")
        initial_input = (
            f"You have just started. Your assignment (recorded on the board as a brief for @{name}):\n\n"
            f"{brief}\n"
        )

        return {
            "name": name,
            "workspace_path": str(agent_workspace),
            "system_prompt": system_prompt,
            "initial_input": initial_input,
            "tools": self.get_agent_tools(name),
        }

    def _on_member_mentioned(self, target_name: str, entry: Dict[str, Any]):
        if target_name == "coordinator" or entry.get("kind") == "brief":
            return
        m_info = self.board_manager.read_members().get("members", {}).get(target_name, {})
        if m_info.get("killed"):
            return
        if not self.worker_runner.is_running(target_name):
            last_read = m_info.get("last_read_seq", 0)
            wake_prompt = (
                f"[swarm] new board message #{entry['seq']} (you have read up to #{last_read}). "
                f"Read anything you have missed, and wait for messages when you have nothing left to do."
            )
            self.worker_runner.spawn_or_wake(
                name=target_name,
                prompt_text=wake_prompt,
                brief_seq=0,
                model=m_info.get("model"),
                workspace_mode=m_info.get("workspace_mode", "inherit"),
                role=m_info.get("role", "worker"),
            )

    def shutdown(self):
        if self.stall_monitor:
            self.stall_monitor.stop()
        self.worker_runner.kill_all()

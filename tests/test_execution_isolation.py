import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from pt_swarm.harness_adapter import PolytokenSwarmAdapter
from pt_swarm.mcp_server import create_runtime


class TestMentionExecutionIsolation(unittest.TestCase):
    def _assert_adapter_does_not_spawn(self, mode):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = PolytokenSwarmAdapter(workspace_path=tmp)
            try:
                bm = adapter.board_manager
                bm.add_member("worker", phase="delegated" if mode == "delegated" else "working")
                if mode == "bound":
                    self.assertTrue(bm.bind_member("worker", "job-1", "running"))
                adapter.worker_runner.spawn_or_wake = Mock()

                bm.post_message("coordinator", "@worker please check this")

                adapter.worker_runner.spawn_or_wake.assert_not_called()
            finally:
                adapter.shutdown()

    def _assert_mcp_does_not_spawn(self, mode):
        with tempfile.TemporaryDirectory() as tmp:
            runner = Mock()
            runner.is_running.return_value = False
            with patch.dict("os.environ", {"POLYTOKEN_SWARM_DIR": str(Path(tmp) / "swarm")}):
                with patch("pt_swarm.mcp_server.WorkerProcessManager", return_value=runner):
                    handlers = create_runtime()
            bm = handlers.bm
            bm.add_member("worker", phase="delegated" if mode == "delegated" else "working")
            if mode == "bound":
                self.assertTrue(bm.bind_member("worker", "job-1", "running"))

            bm.post_message("coordinator", "@worker please check this")

            runner.spawn_or_wake.assert_not_called()

    def test_adapter_skips_delegated_and_bound_members(self):
        for mode in ("delegated", "bound"):
            with self.subTest(mode=mode):
                self._assert_adapter_does_not_spawn(mode)

    def test_mcp_callback_skips_delegated_and_bound_members(self):
        for mode in ("delegated", "bound"):
            with self.subTest(mode=mode):
                self._assert_mcp_does_not_spawn(mode)


if __name__ == "__main__":
    unittest.main()

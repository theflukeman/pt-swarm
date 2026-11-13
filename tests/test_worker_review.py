"""Integration regressions for cross-process worker lifecycle behavior."""

import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path


def _spawn_worker(swarm_dir, workspace_root, cmd, result_queue):
    manager = WorkerProcessManager(swarm_dir, workspace_root, cmd, spawn_grace_seconds=0)
    result_queue.put(manager.spawn_or_wake("same", "work")["spawned"])

from pt_swarm.worker_runner import WorkerProcessManager
from pt_swarm.board_manager import BoardManager
from pt_swarm.swarm_tools import SwarmToolHandlers
from pt_swarm.stall_monitor import SwarmStallMonitor


class CrossProcessSpawnTests(unittest.TestCase):
    def test_two_process_managers_spawn_only_one_worker(self):
        if os.name == "nt":
            self.skipTest("POSIX file-lock integration test")
        with tempfile.TemporaryDirectory() as tmp:
            swarm_dir = Path(tmp) / "swarm"
            marker = Path(tmp) / "spawned"
            script = (
                "import pathlib,time; p=pathlib.Path(r'" + str(marker)
                + "'); p.open('a').write('x\\n'); time.sleep(2)"
            )
            cmd = [sys.executable, "-c", script]
            mgr1 = WorkerProcessManager(swarm_dir, Path(tmp), cmd, spawn_grace_seconds=0)
            mgr2 = WorkerProcessManager(swarm_dir, Path(tmp), cmd, spawn_grace_seconds=0)
            results = []
            import multiprocessing
            queue = multiprocessing.Queue()
            processes = [
                multiprocessing.Process(
                    target=_spawn_worker, args=(swarm_dir, Path(tmp), cmd, queue)
                )
                for _ in range(2)
            ]
            try:
                for process in processes:
                    process.start()
                for process in processes:
                    process.join(10)
                results = [queue.get(timeout=2) for _ in processes]
                self.assertEqual(sorted(results), [False, True])
                deadline = time.monotonic() + 2
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(marker.read_text().splitlines(), ["x"])
            finally:
                mgr1.kill_worker("same")

    def test_workspace_mode_survives_a_new_manager_instance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace_root = root / "source"
            workspace_root.mkdir()
            swarm_dir = root / "swarm"
            cmd = [sys.executable, "-c", "import time; time.sleep(3)"]
            first = WorkerProcessManager(swarm_dir, workspace_root, cmd, spawn_grace_seconds=0)
            result = first.spawn_or_wake("worker", "work", workspace_mode="share")
            self.assertTrue(result["spawned"])
            second = WorkerProcessManager(swarm_dir, workspace_root, cmd, spawn_grace_seconds=0)
            self.assertEqual(second._workspace_mode("worker"), "share")
            self.assertTrue(second.kill_worker("worker"))
            first._processes["worker"].wait(timeout=2)

    def test_status_and_stall_monitor_use_worker_liveness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            swarm_dir = root / "swarm"
            bm = BoardManager(swarm_dir)
            bm.add_member("worker", phase="RUNNING")
            bm.set_member_wake_mode("worker", "working")
            bm.post_message("coordinator", "@worker please do this")
            runner = WorkerProcessManager(swarm_dir, root)
            handlers = SwarmToolHandlers(bm, worker_runner=runner)
            status = handlers.handle_swarm(action="status")
            self.assertFalse(status["roster"]["worker"].get("liveness") == "running")
            monitor = SwarmStallMonitor(bm, worker_runner=runner)
            monitor.check_for_stall()
            self.assertEqual(bm.read_raw_messages()[-1]["agent"], "monitor")

    def test_kill_response_reports_roster_flag_and_confirmed_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            bm = BoardManager(Path(tmp) / "swarm")
            bm.add_member("worker", phase="RUNNING")
            runner = WorkerProcessManager(
                Path(tmp) / "swarm", Path(tmp), [sys.executable, "-c", "import time; time.sleep(3)"],
                spawn_grace_seconds=0,
            )
            runner.spawn_or_wake("worker", "work")
            handlers = SwarmToolHandlers(bm, worker_runner=runner)
            result = handlers.handle_swarm(action="kill", name="worker")
            self.assertTrue(result["killed_flag"])
            self.assertTrue(result["termination_confirmed"])
            self.assertEqual(result["confirmed"], "worker")

    def test_shard_tasks_rejects_empty_list_without_creating_members(self):
        with tempfile.TemporaryDirectory() as tmp:
            bm = BoardManager(Path(tmp) / "swarm")
            handlers = SwarmToolHandlers(bm)
            result = handlers.handle_swarm(action="add", name="empty", brief="work", tasks=[])
            self.assertIn("error", result)
            self.assertNotIn("empty-1", bm.read_members().get("members", {}))

    def test_shard_preflight_rejects_existing_later_name_without_partial_add(self):
        with tempfile.TemporaryDirectory() as tmp:
            bm = BoardManager(Path(tmp) / "swarm")
            bm.add_member("group-2")
            handlers = SwarmToolHandlers(bm)
            result = handlers.handle_swarm(
                action="add", name="group", brief="work", tasks=["first", "second"]
            )
            self.assertIn("error", result)
            members = bm.read_members().get("members", {})
            self.assertNotIn("group-1", members)
            self.assertIn("group-2", members)

    def test_failed_termination_keeps_pid_and_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            swarm_dir = root / "swarm"
            mgr = WorkerProcessManager(swarm_dir, root, [sys.executable, "-c", "pass"])
            path = mgr._pid_file("stuck")
            path.write_text(json.dumps({"pid": os.getpid(), "start": "not-this-process"}))
            with patch.object(mgr.workspace_manager, "cleanup_workspace") as cleanup:
                self.assertFalse(mgr.kill_worker("stuck", workspace_mode="share"))
                self.assertTrue(path.exists())
                cleanup.assert_not_called()

    def test_pid_write_is_atomic_and_liveness_does_not_unlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = WorkerProcessManager(Path(tmp) / "swarm", Path(tmp), [sys.executable, "-c", "pass"])
            path = mgr._pid_file("worker")
            mgr._write_pid("worker", os.getpid())
            self.assertEqual(json.loads(path.read_text())["pid"], os.getpid())
            path.write_text(json.dumps({"pid": 99999999, "start": "stale"}))
            self.assertFalse(mgr.is_running("worker"))
            self.assertTrue(path.exists())

    def test_delegated_member_is_unverified_not_dead_and_not_terminated(self):
        with tempfile.TemporaryDirectory() as tmp:
            bm = BoardManager(Path(tmp) / "swarm")
            bm.add_member("delegate", phase="delegated")
            bm.bind_member("delegate", "job-1", "running")
            runner = WorkerProcessManager(Path(tmp) / "swarm", Path(tmp))
            handlers = SwarmToolHandlers(bm, worker_runner=runner)
            self.assertEqual(handlers.handle_swarm(action="status")["roster"]["delegate"]["liveness"], "unverified")
            result = handlers.handle_swarm(action="kill", name="delegate")
            self.assertFalse(result["termination_confirmed"])

    def test_failed_kill_can_be_retried_after_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            bm = BoardManager(Path(tmp) / "swarm")
            bm.add_member("worker", phase="RUNNING")
            class Runner:
                def __init__(self): self.calls = 0
                def kill_worker(self, *args, **kwargs):
                    self.calls += 1
                    return self.calls == 2
            runner = Runner()
            handlers = SwarmToolHandlers(bm, worker_runner=runner)
            self.assertFalse(handlers.handle_swarm(action="kill", name="worker")["termination_confirmed"])
            self.assertTrue(handlers.handle_swarm(action="kill", name="worker")["termination_confirmed"])

    def test_shards_reject_generated_name_overflow_before_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            bm = BoardManager(Path(tmp) / "swarm")
            handlers = SwarmToolHandlers(bm)
            result = handlers.handle_swarm(action="add", name="a" * 64, brief="work", tasks=["one"])
            self.assertIn("error", result)
            members = bm.read_members().get("members", {})
            self.assertEqual(set(members), {"coordinator"})

    def test_shard_spawn_outcomes_are_per_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            bm = BoardManager(Path(tmp) / "swarm")
            class Runner:
                has_spawner = True
                def __init__(self): self.calls = 0
                def spawn_or_wake(self, **kwargs):
                    self.calls += 1
                    return {"spawned": self.calls == 1, "mode": "test"}
            handlers = SwarmToolHandlers(bm, worker_runner=Runner())
            result = handlers.handle_swarm(action="add", name="batch", brief="work", tasks=["a", "b"])
            self.assertFalse(result["spawned"])
            self.assertEqual([entry["process"]["spawned"] for entry in result["results"]], [True, False])
            self.assertEqual([entry["prompt"].split("assigned slice")[1].split(":")[1].strip() for entry in result["results"]], ["a", "b"])


if __name__ == "__main__":
    unittest.main()

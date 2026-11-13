"""Model resolution for swarm members.

A member's roster ``model`` is a *request*, not a selector: ``polytoken exec --model``
accepts concrete model names, ``name(level)`` reasoning variants, and model-group
references (``@mg:group``), but rejects the ``default`` sentinel. These tests pin the
rules that turn a request into the value handed to the worker process.
"""

import json
import os
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from pt_swarm import model_hook
from pt_swarm.board_manager import BoardManager
from pt_swarm.model_selection import (
    FULL_SELECTOR,
    MINI_SELECTOR,
    NANO_SELECTOR,
    read_session_model,
    resolve_model,
    write_session_model,
)
from pt_swarm.swarm_tools import SwarmToolHandlers
from pt_swarm.worker_runner import WorkerProcessManager

SESSION_MODEL = "neuralwatt/deepseek-v4.1-flash"


class FakeWorkerRunner:
    def __init__(self):
        self.calls = []

    def spawn_or_wake(self, **kwargs):
        self.calls.append(kwargs)
        return {"spawned": True, "mode": "in_process", "name": kwargs["name"]}


class ResolveModelTests(unittest.TestCase):
    def test_role_tiers(self):
        self.assertEqual(resolve_model(None, "worker")[1], FULL_SELECTOR)
        self.assertEqual(resolve_model(None, "verifier")[1], FULL_SELECTOR)
        self.assertEqual(resolve_model(None, "coordinator")[1], FULL_SELECTOR)
        self.assertEqual(resolve_model(None, "monitor")[1], MINI_SELECTOR)
        self.assertEqual(resolve_model(None, "narrator")[1], MINI_SELECTOR)

    def test_unset_request_reports_default(self):
        for request in (None, "", "  ", "default", "Default", "auto"):
            desired, applied = resolve_model(request, "worker")
            self.assertEqual(desired, "default", request)
            self.assertEqual(applied, FULL_SELECTOR, request)

    def test_tier_aliases_map_to_reserved_groups(self):
        self.assertEqual(resolve_model("full", "monitor"), ("full", FULL_SELECTOR))
        self.assertEqual(resolve_model("mini", "worker"), ("mini", MINI_SELECTOR))
        self.assertEqual(resolve_model("nano", "narrator"), ("nano", NANO_SELECTOR))

    def test_concrete_selectors_pass_through(self):
        for request in (
            "codex/gpt-6-luna",
            "codex/gpt-6-astra(high)",
            "@mg:my-group",
            "mg:my-group",
            "modelgroup:my-group",
        ):
            self.assertEqual(resolve_model(request, "worker"), (request, request))

    def test_session_alias_uses_recorded_model(self):
        self.assertEqual(
            resolve_model("session", "worker", session_model=SESSION_MODEL),
            ("session", SESSION_MODEL),
        )

    def test_session_alias_falls_back_to_role_tier(self):
        self.assertEqual(
            resolve_model("session", "narrator", session_model=None),
            ("session", MINI_SELECTOR),
        )

    def test_applied_is_never_the_default_sentinel(self):
        for role in ("worker", "verifier", "monitor", "narrator", "coordinator"):
            for request in (None, "default", "session", "full", "mini"):
                self.assertNotEqual(resolve_model(request, role)[1], "default")


class RosterModelTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.swarm_dir = Path(self._tmp.name) / ".swarm" / "default"
        self.bm = BoardManager(self.swarm_dir)

    def tearDown(self):
        self._tmp.cleanup()

    def test_add_member_records_request_and_selector(self):
        entry = self.bm.add_member(name="w1")
        self.assertEqual(entry["desired_model"], "default")
        self.assertEqual(entry["applied_model"], FULL_SELECTOR)
        self.assertEqual(entry["model"], "default")

    def test_timer_roles_default_to_mini_tier(self):
        narrator = self.bm.add_member(name="narrator")
        monitor = self.bm.add_member(name="monitor-2")
        self.assertEqual(narrator["role"], "narrator")
        self.assertEqual(monitor["role"], "monitor")
        self.assertEqual(narrator["applied_model"], MINI_SELECTOR)
        self.assertEqual(monitor["applied_model"], MINI_SELECTOR)

    def test_explicit_model_is_kept_as_request(self):
        entry = self.bm.add_member(name="w2", model="mini")
        self.assertEqual(entry["desired_model"], "mini")
        self.assertEqual(entry["applied_model"], MINI_SELECTOR)

    def test_set_model_reresolves_against_the_members_role(self):
        self.bm.add_member(name="monitor")
        self.bm.add_member(name="w3")
        self.bm.set_model("monitor", "default")
        self.bm.set_model("w3", "session")
        members = self.bm.read_members()["members"]
        self.assertEqual(members["monitor"]["applied_model"], MINI_SELECTOR)
        self.assertEqual(members["w3"]["applied_model"], FULL_SELECTOR)
        self.assertEqual(members["w3"]["model"], "session")

    def test_swarm_model_reports_the_applied_selector(self):
        handlers = SwarmToolHandlers(self.bm)
        handlers.handle_swarm(action="add", name="w8", brief="Work")
        res = handlers.handle_swarm(action="model", name="w8", model="mini")
        self.assertEqual(res["model"], "mini")
        self.assertEqual(res["applied_model"], MINI_SELECTOR)

    def test_swarm_add_forwards_the_request_to_the_spawner(self):
        runner = FakeWorkerRunner()
        handlers = SwarmToolHandlers(self.bm, worker_runner=runner)
        res = handlers.handle_swarm(action="add", name="w4", brief="Work", model="mini")
        self.assertEqual(res["applied_model"], MINI_SELECTOR)
        self.assertEqual(runner.calls[0]["model"], "mini")
        self.assertEqual(runner.calls[0]["role"], "worker")


class WorkerCommandTests(unittest.TestCase):
    def test_model_flag_is_expanded(self):
        cmd = WorkerProcessManager._expand_cmd(
            ["polytoken", "exec", "--model", "{model}", "{prompt}"],
            {"model": MINI_SELECTOR, "prompt": "hi"},
        )
        self.assertEqual(cmd, ["polytoken", "exec", "--model", MINI_SELECTOR, "hi"])

    def test_model_flag_is_dropped_when_unresolved(self):
        cmd = WorkerProcessManager._expand_cmd(
            ["polytoken", "exec", "--model", "{model}", "{prompt}"],
            {"model": "", "prompt": "hi"},
        )
        self.assertEqual(cmd, ["polytoken", "exec", "hi"])

    def test_default_worker_command_passes_a_model(self):
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("POLYTOKEN_WORKER_CMD", "POLYTOKEN_BIN")
        }
        with patch.dict(os.environ, env, clear=True):
            with patch("pt_swarm.worker_runner.shutil.which", return_value="/opt/bin/polytoken"):
                cmd = WorkerProcessManager._resolve_default_cmd()
        self.assertEqual(cmd, ["/opt/bin/polytoken", "exec", "--model", "{model}", "{prompt}"])

    def test_spawn_resolves_the_request_for_the_spawned_process(self):
        with TemporaryDirectory() as tmp:
            swarm_dir = Path(tmp) / "swarm"
            specs = []
            mgr = WorkerProcessManager(
                swarm_dir=swarm_dir, workspace_root=Path(tmp), in_process_spawner=specs.append
            )
            mgr.spawn_or_wake(name="w5", prompt_text="go", model=None, role="worker")
            mgr.spawn_or_wake(name="narrator", prompt_text="go", model=None, role="narrator")
            self.assertEqual(specs[0]["model"], FULL_SELECTOR)
            self.assertEqual(specs[1]["model"], MINI_SELECTOR)

    def test_spawn_honours_the_recorded_session_model(self):
        with TemporaryDirectory() as tmp:
            swarm_dir = Path(tmp) / ".swarm" / "default"
            write_session_model(swarm_dir, SESSION_MODEL)
            specs = []
            mgr = WorkerProcessManager(
                swarm_dir=swarm_dir, workspace_root=Path(tmp), in_process_spawner=specs.append
            )
            mgr.spawn_or_wake(name="w6", prompt_text="go", model="session", role="worker")
            self.assertEqual(specs[0]["model"], SESSION_MODEL)

    def test_spawned_process_receives_the_model_flag(self):
        with TemporaryDirectory() as tmp:
            swarm_dir = Path(tmp) / "swarm"
            argv_file = Path(tmp) / "argv.json"
            script = (
                "import json, pathlib, sys, time; "
                "pathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:])); "
                "time.sleep(1.0)"
            )
            mgr = WorkerProcessManager(
                swarm_dir=swarm_dir,
                workspace_root=Path(tmp),
                worker_cmd_template=[sys.executable, "-c", script, str(argv_file),
                                     "--model", "{model}", "{prompt}"],
                spawn_grace_seconds=0.25,
            )
            try:
                res = mgr.spawn_or_wake(name="w7", prompt_text="hello", model="mini", role="worker")
                self.assertTrue(res["spawned"])
                for _ in range(100):
                    if argv_file.exists() and argv_file.read_text(encoding="utf-8"):
                        break
                    time.sleep(0.02)
                argv = json.loads(argv_file.read_text(encoding="utf-8"))
                self.assertEqual(argv[0:2], ["--model", MINI_SELECTOR])
                self.assertIn("hello", argv[2])
            finally:
                mgr.kill_worker("w7")


class SessionModelFileTests(unittest.TestCase):
    def test_roundtrip(self):
        with TemporaryDirectory() as tmp:
            swarm_dir = Path(tmp) / "swarm"
            write_session_model(swarm_dir, SESSION_MODEL)
            self.assertEqual(read_session_model(swarm_dir), SESSION_MODEL)
            payload = json.loads((swarm_dir / "session_model.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["model"], SESSION_MODEL)
            self.assertIn("updated_at", payload)

    def test_missing_file_reads_as_none(self):
        with TemporaryDirectory() as tmp:
            self.assertIsNone(read_session_model(Path(tmp) / "nowhere"))

    def test_corrupt_file_reads_as_none(self):
        with TemporaryDirectory() as tmp:
            swarm_dir = Path(tmp)
            (swarm_dir / "session_model.json").write_text("{not json", encoding="utf-8")
            self.assertIsNone(read_session_model(swarm_dir))


class ModelHookTests(unittest.TestCase):
    def test_hook_records_model_from_the_environment(self):
        with TemporaryDirectory() as tmp:
            swarm_dir = Path(tmp) / "swarm"
            env = {"POLYTOKEN_MODEL_NAME": SESSION_MODEL, "POLYTOKEN_PROJECT_DIR": tmp}
            with patch.dict(os.environ, env, clear=True):
                code = model_hook.main(["--swarm-dir", str(swarm_dir)])
            self.assertEqual(code, 0)
            self.assertEqual(read_session_model(swarm_dir), SESSION_MODEL)

    def test_hook_defaults_to_the_project_swarm_dir(self):
        with TemporaryDirectory() as tmp:
            env = {"POLYTOKEN_MODEL_NAME": SESSION_MODEL, "POLYTOKEN_PROJECT_DIR": tmp}
            with patch.dict(os.environ, env, clear=True):
                code = model_hook.main([])
            self.assertEqual(code, 0)
            self.assertEqual(
                read_session_model(Path(tmp) / ".swarm" / "default"), SESSION_MODEL
            )

    def test_hook_is_a_noop_without_a_model(self):
        with TemporaryDirectory() as tmp:
            swarm_dir = Path(tmp) / "swarm"
            with patch.dict(os.environ, {"POLYTOKEN_PROJECT_DIR": tmp}, clear=True):
                code = model_hook.main(["--swarm-dir", str(swarm_dir)])
            self.assertEqual(code, 0)
            self.assertFalse((swarm_dir / "session_model.json").exists())


class CoordinatorModelTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.swarm_dir = Path(self._tmp.name) / ".swarm" / "default"

    def tearDown(self):
        self._tmp.cleanup()

    def test_coordinator_asks_for_the_session_model(self):
        bm = BoardManager(self.swarm_dir)
        coordinator = bm.read_members()["members"]["coordinator"]
        self.assertEqual(coordinator["model"], "session")
        self.assertEqual(coordinator["desired_model"], "session")
        self.assertEqual(coordinator["applied_model"], FULL_SELECTOR)

    def test_coordinator_applies_the_recorded_session_model(self):
        write_session_model(self.swarm_dir, SESSION_MODEL)
        bm = BoardManager(self.swarm_dir)
        coordinator = bm.read_members()["members"]["coordinator"]
        self.assertEqual(coordinator["applied_model"], SESSION_MODEL)

    def test_startup_repairs_a_legacy_coordinator_entry(self):
        self.swarm_dir.mkdir(parents=True)
        legacy = {
            "title": "Default Swarm",
            "members": {
                "coordinator": {
                    "conversation_id": "coordinator",
                    "role": "coordinator",
                    "desired_model": "default",
                    "applied_model": "default",
                    "model": "default",
                }
            },
        }
        (self.swarm_dir / "members.json").write_text(json.dumps(legacy), encoding="utf-8")
        write_session_model(self.swarm_dir, SESSION_MODEL)
        bm = BoardManager(self.swarm_dir)
        coordinator = bm.read_members()["members"]["coordinator"]
        self.assertEqual(coordinator["model"], "session")
        self.assertEqual(coordinator["desired_model"], "session")
        self.assertEqual(coordinator["applied_model"], SESSION_MODEL)

    def test_status_reports_a_model_switch(self):
        bm = BoardManager(self.swarm_dir)
        handlers = SwarmToolHandlers(bm)
        write_session_model(self.swarm_dir, "vendor/late-model")
        coordinator = handlers.handle_swarm(action="status")["roster"]["coordinator"]
        self.assertEqual(coordinator["applied_model"], "vendor/late-model")


if __name__ == "__main__":
    unittest.main()

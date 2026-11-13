"""Provider env vars are derived from the polytoken config, never hardcoded."""

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from pt_swarm import env_vars
from pt_swarm.worker_runner import WorkerProcessManager

CONFIG_TEXT = """
providers:
  neuralwatt:
    api_key: $SOME_PROVIDER_API_KEY
  other:
    token: ${ANOTHER_TOKEN}
web_search:
  exa_key: "${SEARCH_KEY}"
"""


class ReferencedEnvNamesTests(unittest.TestCase):
    def test_reads_dollar_and_braced_forms(self):
        with TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.yaml"
            cfg.write_text(CONFIG_TEXT, encoding="utf-8")
            self.assertEqual(
                env_vars.referenced_env_names([cfg]),
                ["ANOTHER_TOKEN", "SEARCH_KEY", "SOME_PROVIDER_API_KEY"],
            )

    def test_skips_vars_passed_by_default(self):
        with TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.yaml"
            cfg.write_text("paths:\n  a: $HOME\n  b: ${PATH}\n  c: $KEEP_ME\n", encoding="utf-8")
            self.assertEqual(env_vars.referenced_env_names([cfg]), ["KEEP_ME"])

    def test_missing_file_is_not_an_error(self):
        self.assertEqual(env_vars.referenced_env_names(["/nonexistent/config.yaml"]), [])

    def test_default_paths_include_home_and_project_configs(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            project = Path(tmp) / "project"
            (home / ".config" / "polytoken").mkdir(parents=True)
            (project / ".polytoken").mkdir(parents=True)
            (home / ".config" / "polytoken" / "config.yaml").write_text(
                "a: $FROM_HOME\n", encoding="utf-8"
            )
            (project / ".polytoken" / "config.yaml").write_text(
                "b: $FROM_PROJECT\n", encoding="utf-8"
            )
            with patch.dict(os.environ, {"HOME": str(home)}, clear=False):
                os.environ.pop("XDG_CONFIG_HOME", None)
                os.environ.pop("POLYTOKEN_CONFIG", None)
                with patch("pathlib.Path.home", return_value=home), patch(
                    "pathlib.Path.cwd", return_value=project
                ):
                    names = env_vars.referenced_env_names()
            self.assertIn("FROM_HOME", names)
            self.assertIn("FROM_PROJECT", names)


class PassEnvBlockTests(unittest.TestCase):
    def test_block_is_ready_to_paste(self):
        block = env_vars.pass_env_block(["A_KEY", "B_KEY"])
        self.assertEqual(block, "    pass_env:\n      - A_KEY\n      - B_KEY\n")

    def test_empty_block_is_empty(self):
        self.assertEqual(env_vars.pass_env_block([]), "")

    def test_cli_prints_the_block(self):
        with TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.yaml"
            cfg.write_text(CONFIG_TEXT, encoding="utf-8")
            import io
            from contextlib import redirect_stdout

            buf = io.StringIO()
            with redirect_stdout(buf):
                code = env_vars.main(["--config", str(cfg)])
            self.assertEqual(code, 0)
            self.assertIn("pass_env:", buf.getvalue())
            self.assertIn("SOME_PROVIDER_API_KEY", buf.getvalue())


class SpawnHintTests(unittest.TestCase):
    def test_config_load_failure_hint_names_the_derived_vars(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            (home / ".config" / "polytoken").mkdir(parents=True)
            (home / ".config" / "polytoken" / "config.yaml").write_text(
                CONFIG_TEXT, encoding="utf-8"
            )
            script = (
                "print('Error: config load failed: env var $SOME_PROVIDER_API_KEY "
                "referenced in config.yaml is not set'); raise SystemExit(1)"
            )
            mgr = WorkerProcessManager(
                swarm_dir=Path(tmp) / "swarm",
                workspace_root=Path(tmp),
                worker_cmd_template=[sys.executable, "-c", script],
                spawn_grace_seconds=0.25,
            )
            with patch.dict(os.environ, {"HOME": str(home)}, clear=False):
                os.environ.pop("POLYTOKEN_CONFIG", None)
                os.environ.pop("XDG_CONFIG_HOME", None)
                with patch("pathlib.Path.home", return_value=home), patch(
                    "pathlib.Path.cwd", return_value=Path(tmp) / "project"
                ):
                    res = mgr.spawn_or_wake(name="codeless", prompt_text="work")
            self.assertFalse(res["spawned"])
            self.assertIn("pass_env", res["hint"])
            self.assertIn("SOME_PROVIDER_API_KEY", res["hint"])


if __name__ == "__main__":
    unittest.main()

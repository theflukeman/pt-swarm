"""
Additional unit tests for CLI viewer, tool schemas parity, MCP error paths,
SwarmToolHandlers lifecycle actions, and WorkspaceManager modes.
"""

import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pt_swarm import cli, mcp_server
from pt_swarm.board_manager import BoardManager
from pt_swarm.harness_adapter import PolytokenSwarmAdapter
from pt_swarm.swarm_tools import SwarmToolHandlers
from pt_swarm.workspace_manager import WorkspaceManager


class TestCliAndSchemas(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.bm = BoardManager(self.test_dir)
        self.tools = SwarmToolHandlers(self.bm, caller_agent_name="coordinator")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_cli_prints_board_and_status(self):
        self.bm.add_member("parser", role="worker")
        self.bm.post_message("coordinator", "@parser start work")

        out = io.StringIO()
        with patch.object(sys, "argv", ["cli.py", "--swarm-dir", str(self.test_dir), "--status"]):
            with patch("sys.stdout", out):
                cli.main()

        text = out.getvalue()
        self.assertIn("Swarm Roster (2 members)", text)
        self.assertIn("parser", text)
        self.assertIn("coordinator", text)

        out2 = io.StringIO()
        with patch.object(sys, "argv", ["cli.py", "--swarm-dir", str(self.test_dir)]):
            with patch("sys.stdout", out2):
                cli.main()
        self.assertIn("@parser start work", out2.getvalue())

    def test_tool_schemas_match_mcp_server_tools(self):
        schemas_dir = Path(__file__).resolve().parent.parent / "pt_swarm" / "tool_schemas"
        mcp_by_name = {t["name"]: t for t in mcp_server.TOOLS}
        self.assertEqual(
            set(mcp_by_name.keys()),
            {"swarm", "post_message", "read_board", "swarm_wait"},
        )
        for name, mcp_tool in mcp_by_name.items():
            schema_file = schemas_dir / f"{name}.json"
            self.assertTrue(schema_file.exists(), f"Missing schema file {schema_file}")
            schema_data = json.loads(schema_file.read_text(encoding="utf-8"))
            self.assertEqual(schema_data["title"], name)
            self.assertEqual(
                set(schema_data.get("properties", {}).keys()),
                set(mcp_tool["inputSchema"].get("properties", {}).keys()),
            )
            self.assertEqual(
                schema_data.get("required", []),
                mcp_tool["inputSchema"].get("required", []),
            )

    def test_swarm_title_models_model_and_channel_ops(self):
        t_res = self.tools.handle_swarm(action="title", title="New Title")
        self.assertEqual(t_res["status"], "titled")
        self.assertEqual(self.bm.read_members()["title"], "New Title")

        model_handlers = SwarmToolHandlers(
            self.bm,
            caller_agent_name="coordinator",
            model_lister=lambda: [
                {
                    "name": "vendor/alpha",
                    "description": "Alpha",
                    "selectable": ["vendor/alpha", "vendor/alpha(high)"],
                }
            ],
        )
        m_default = model_handlers.handle_swarm(action="models")
        m_all = model_handlers.handle_swarm(action="models", all=True)
        self.assertGreater(len(m_all["models"]), len(m_default["models"]))
        self.assertIn("vendor/alpha", {m["name"] for m in m_default["models"]})
        self.assertNotIn("gemini", json.dumps([m_default, m_all]).lower())

        self.tools.handle_swarm(action="add", name="w1", brief="Work")
        upd = self.tools.handle_swarm(action="model", name="w1", model="fast")
        self.assertEqual(upd["model"], "fast")
        self.assertEqual(self.bm.read_members()["members"]["w1"]["model"], "fast")
        missing = self.tools.handle_swarm(action="model", name="nobody", model="fast")
        self.assertIn("error", missing)

        self.tools.handle_swarm(action="channel", op="create", channel="api", topic="API")
        sub = self.tools.handle_swarm(action="channel", op="subscribe", channel="api", name="w1")
        self.assertIn("api", sub["channels"])
        unsub = self.tools.handle_swarm(action="channel", op="unsubscribe", channel="api", name="w1")
        self.assertNotIn("api", unsub["channels"])
        arch = self.tools.handle_swarm(action="channel", op="archive", channel="api")
        self.assertEqual(arch["status"], "archived")
        lst = self.tools.handle_swarm(action="channel", op="list")
        self.assertTrue(any(c["name"] == "api" and c["archived"] for c in lst["channels"]))

    def test_models_without_discovery_reports_only_default(self):
        handlers = SwarmToolHandlers(self.bm, model_lister=lambda: [])
        res = handlers.handle_swarm(action="models")
        self.assertEqual([m["name"] for m in res["models"]], ["default"])
        self.assertIn("note", res)

    def test_initialize_negotiates_protocol_version(self):
        out = io.StringIO()
        with patch("sys.stdout", out):
            mcp_server.handle_rpc_request(
                self.tools,
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            )
            mcp_server.handle_rpc_request(
                self.tools,
                {"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "2099-01-01"}},
            )
        resps = [json.loads(line) for line in out.getvalue().splitlines() if line]
        self.assertEqual(resps[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(resps[1]["result"]["protocolVersion"], "2024-11-05")

    def test_mcp_server_ping_and_error_responses(self):
        out_buf = io.StringIO()
        with patch("sys.stdout", out_buf):
            mcp_server.handle_rpc_request(self.tools, {"jsonrpc": "2.0", "id": 1, "method": "ping"})
            mcp_server.handle_rpc_request(self.tools, {"jsonrpc": "2.0", "id": 2, "method": "unknown/method"})
            mcp_server.handle_rpc_request(
                self.tools,
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "no_such_tool", "arguments": {}}},
            )
            mcp_server.handle_rpc_request(
                self.tools,
                {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "swarm", "arguments": {"action": "kill"}}},
            )

        resps = [json.loads(line) for line in out_buf.getvalue().splitlines() if line]
        self.assertEqual(resps[0]["result"], {})
        self.assertEqual(resps[1]["error"]["code"], -32601)
        self.assertEqual(resps[2]["error"]["code"], -32601)
        self.assertTrue(resps[3]["result"]["isError"])

    def test_packaging_includes_agent_definitions_and_sidecar_bin(self):
        root = Path(__file__).resolve().parent.parent
        pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('include = ["pt_swarm*"]', pyproject)
        self.assertIn("agent_definitions/*.md", pyproject)
        self.assertIn("tool_schemas/*.json", pyproject)
        # Runtime data must live inside the installed package, not beside it.
        for rel in ("agent_definitions/worker.md", "tool_schemas/swarm.json", "config.example.json"):
            self.assertTrue((root / "pt_swarm" / rel).exists(), f"Missing {rel}")
        self.assertEqual(
            sorted(
                p.name
                for p in (root / "pt_swarm").iterdir()
                if p.is_dir() and not p.name.startswith("__")
            ),
            ["agent_definitions", "tool_schemas"],
        )
        # The distribution must not own generic top-level module names.
        self.assertFalse((root / "tools").exists())
        self.assertFalse((root / "mcp_server.py").exists())

        adapter = PolytokenSwarmAdapter(workspace_path=str(self.test_dir))
        for role in ("coordinator", "worker", "verifier", "monitor", "narrator"):
            self.assertTrue(len(adapter.load_system_prompt(role)) > 0, f"Empty prompt for {role}")

        pkg = json.loads((root / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(pkg.get("bin", {}).get("pt-swarm-ui"), "./sidecar_ui/main.mjs")
        self.assertTrue((root / "sidecar_ui" / "sidecar_sdk" / "index.mjs").exists())


class TestWorkspaceManagerModes(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_inherit_and_non_git_fallback_ignores_heavy_dirs(self):
        (self.test_dir / "src.py").write_text("print(1)", encoding="utf-8")
        (self.test_dir / "node_modules").mkdir()
        (self.test_dir / "node_modules" / "pkg.js").write_text("x", encoding="utf-8")
        (self.test_dir / ".venv").mkdir()
        (self.test_dir / ".venv" / "py").write_text("x", encoding="utf-8")

        wm = WorkspaceManager(self.test_dir)
        self.assertEqual(wm.provision_workspace("a1", mode="inherit"), self.test_dir.resolve())
        wm.cleanup_workspace("a1", mode="inherit")

        ws = wm.provision_workspace("a2", mode="share")
        self.assertTrue((ws / "src.py").exists())
        self.assertFalse((ws / "node_modules").exists())
        self.assertFalse((ws / ".venv").exists())
        wm.cleanup_workspace("a2", mode="share")
        self.assertFalse(ws.exists())

    def test_branch_mode_git_clone_and_cleanup(self):
        subprocess.run(["git", "init"], cwd=str(self.test_dir), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=str(self.test_dir), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=str(self.test_dir), capture_output=True, check=True)
        (self.test_dir / "a.txt").write_text("a", encoding="utf-8")
        subprocess.run(["git", "add", "a.txt"], cwd=str(self.test_dir), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(self.test_dir), capture_output=True, check=True)

        wm = WorkspaceManager(self.test_dir)
        ws = wm.provision_workspace("cloned", mode="branch")
        self.assertTrue((ws / "a.txt").exists())
        wm.cleanup_workspace("cloned", mode="branch")
        self.assertFalse(ws.exists())

        # Also verify git clone command shape when git clone succeeds
        with patch("pt_swarm.workspace_manager.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "true\n"
            ws2 = wm.provision_workspace("cloned2", mode="branch")
            self.assertTrue(any("clone" in call.args[0] for call in mock_run.call_args_list))
            self.assertEqual(ws2, wm.worktrees_dir / "cloned2")


if __name__ == "__main__":
    unittest.main()

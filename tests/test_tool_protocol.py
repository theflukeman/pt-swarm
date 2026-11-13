"""Regression tests for MCP request handling and tool-boundary validation."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from pt_swarm import mcp_server
from pt_swarm.board_manager import BoardManager
from pt_swarm.swarm_tools import SwarmToolHandlers


class ToolBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bm = BoardManager(Path(self.tmp.name))
        self.handlers = SwarmToolHandlers(self.bm)

    def tearDown(self):
        self.tmp.cleanup()

    def test_unknown_arguments_are_rejected_and_do_not_override_caller(self):
        res = self.handlers.handle_read_board(caller="coordinator", agent="w1")
        self.assertIn("error", res)
        self.assertEqual(self.bm.read_members()["members"]["coordinator"]["last_read_seq"], 0)

    def test_member_name_traversal_is_rejected_at_tool_boundary(self):
        res = self.handlers.handle_swarm(action="add", name="../../escape")
        self.assertIn("error", res)
        self.assertFalse((Path(self.tmp.name).parent / "escape").exists())

    def test_tasks_and_n_must_agree_and_count_is_bounded(self):
        handlers = SwarmToolHandlers(self.bm)
        mismatch = handlers.handle_swarm(action="add", name="grp", tasks=["a", "b"], n=5)
        oversized = handlers.handle_swarm(action="add", name="huge", n=10001)
        self.assertIn("error", mismatch)
        self.assertIn("error", oversized)

    def test_mutating_title_and_model_require_values(self):
        title = self.handlers.handle_swarm(action="title")
        model = self.handlers.handle_swarm(action="model", name="coordinator")
        self.assertIn("error", title)
        self.assertIn("error", model)

    def test_invalid_channel_post_is_rejected(self):
        res = self.handlers.handle_post_message(text="hidden", channel="Main")
        self.assertIn("error", res)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.handlers = SwarmToolHandlers(BoardManager(Path(self.tmp.name)))
        self.output = patch("pt_swarm.mcp_server.sys.stdout")
        self.stdout = self.output.start()
        self.stdout.write.reset_mock()

    def tearDown(self):
        self.output.stop()
        self.tmp.cleanup()

    def responses(self):
        return [json.loads(call.args[0]) for call in self.stdout.write.call_args_list]

    def test_non_object_request_gets_invalid_request_and_server_continues(self):
        with patch("pt_swarm.mcp_server.create_runtime", return_value=self.handlers):
            with patch("pt_swarm.mcp_server.sys.stdin", ["[]\n", '{"jsonrpc":"2.0","id":2,"method":"ping"}\n']):
                mcp_server.main()
        responses = self.responses()
        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[0]["error"]["code"], -32600)
        self.assertEqual(responses[1]["id"], 2)

    def test_parse_error_response(self):
        with patch("pt_swarm.mcp_server.sys.stdin", ["{broken\n"]):
            mcp_server.main()
        self.assertEqual(self.responses()[0]["error"]["code"], -32700)

    def test_jsonrpc_10_and_explicit_null_id_are_not_silently_ignored(self):
        mcp_server.handle_rpc_request(self.handlers, {"jsonrpc": "1.0", "id": 4, "method": "ping"})
        mcp_server.handle_rpc_request(self.handlers, {"jsonrpc": "2.0", "id": None, "method": "ping"})
        responses = self.responses()
        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[0]["error"]["code"], -32600)
        self.assertIsNone(responses[1]["id"])

    def test_tool_parameter_errors_are_json_rpc_invalid_params(self):
        mcp_server.handle_rpc_request(self.handlers, {
            "jsonrpc": "2.0", "id": 8, "method": "tools/call",
            "params": {"name": "post_message", "arguments": {"caller": "coordinator"}},
        })
        result = self.responses()[0]
        self.assertEqual(result["error"]["code"], -32602)
        self.assertNotIn("TypeError", result["error"]["message"])

    def test_tool_arguments_must_be_object(self):
        mcp_server.handle_rpc_request(self.handlers, {
            "jsonrpc": "2.0", "id": 9, "method": "tools/call",
            "params": {"name": "post_message", "arguments": []},
        })
        self.assertEqual(self.responses()[0]["error"]["code"], -32602)

    def test_tool_schemas_forbid_unknown_properties(self):
        for tool in mcp_server.TOOLS:
            self.assertFalse(tool["inputSchema"].get("additionalProperties", True))

    def test_wait_does_not_block_ping_or_post_and_responses_are_serialized(self):
        posted = threading.Event()

        class PendingWaitHandlers:
            def handle_swarm_wait(self, **kwargs):
                self.assert_wait_args = kwargs
                if not posted.wait(2):
                    raise AssertionError("post_message was blocked behind swarm_wait")
                return "woken"

            def handle_post_message(self, **kwargs):
                posted.set()
                return {"status": "posted", "seq": 1}

        handlers = PendingWaitHandlers()
        requests = [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "swarm_wait", "arguments": {"since": 0, "timeout_seconds": 2}}}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}),
            json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "post_message", "arguments": {"text": "wake"}}}),
        ]
        with patch("pt_swarm.mcp_server.create_runtime", return_value=handlers):
            with patch("pt_swarm.mcp_server.sys.stdin", [line + "\n" for line in requests]):
                mcp_server.main()
        responses = self.responses()
        self.assertEqual({response["id"] for response in responses}, {1, 2, 3})
        self.assertTrue(posted.is_set())
        self.assertEqual(len({response["id"] for response in responses}), len(responses))

    def test_invalid_tool_boundary_values_return_sanitized_invalid_params(self):
        cases = [
            ("swarm", {"action": "title", "title": None}),
            ("swarm", {"action": "add", "name": "grp", "tasks": "not-a-list"}),
            ("swarm", {"action": "add", "name": "grp", "tasks": [], "n": 2}),
            ("swarm_wait", {"since": -1}),
            ("read_board", {"since": -1}),
            ("swarm", {"action": "models", "name": "ignored"}),
            ("swarm", {"action": "invalid"}),
        ]
        for tool_name, arguments in cases:
            with self.subTest(tool=tool_name, arguments=arguments):
                mcp_server.handle_rpc_request(self.handlers, {
                    "jsonrpc": "2.0", "id": 17, "method": "tools/call",
                    "params": {"name": tool_name, "arguments": arguments},
                })
                response = self.responses()[-1]
                self.assertEqual(response.get("error", {}).get("code"), -32602)
                self.assertEqual(response["error"]["message"], "Invalid params")


if __name__ == "__main__":
    unittest.main()

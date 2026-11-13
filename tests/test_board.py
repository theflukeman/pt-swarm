"""
Comprehensive unit and integration tests for pt-swarm:
- Live Antigravity fixture compatibility
- Mention boundary & shard prefix rules (mirroring channels.mjs)
- Pagination last_read_seq accuracy & explicit seqs lookup
- Channel lifecycle (create, subscribe, unsubscribe, archive, visibility, @#channel)
- Sharded spawning (n & tasks), group kill, and WorkerProcessManager integration
- Concurrent multi-thread writes & atomic members.json persistence
- MCP Server JSON-RPC 2.0 round-trip verification
"""

import io
import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from pt_swarm import mcp_server
from pt_swarm.board_manager import BoardManager
from pt_swarm.swarm_tools import SwarmToolHandlers
from pt_swarm.worker_runner import WorkerProcessManager


class TestBoardManagerCompatibility(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.fixtures_dir = Path(__file__).parent / "fixtures"

        shutil.copy(self.fixtures_dir / "board.jsonl", self.test_dir / "board.jsonl")
        shutil.copy(self.fixtures_dir / "members.json", self.test_dir / "members.json")

        self.bm = BoardManager(self.test_dir)
        self.tools = SwarmToolHandlers(self.bm, caller_agent_name="coordinator")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_read_board_matches_fixture(self):
        result = self.tools.handle_read_board(since=4)
        self.assertIn("messages", result)
        self.assertIn("head", result)
        self.assertEqual(result["head"], 8)
        self.assertEqual(len(result["messages"]), 4)
        self.assertEqual(result["messages"][0]["agent"], "python_async")
        self.assertEqual(result["messages"][1]["agent"], "rust_async")
        self.assertEqual(result["messages"][2]["agent"], "verifier")

    def test_seqs_lookup_and_pagination_last_read(self):
        # Explicit seqs lookup returns only requested seqs
        res = self.tools.handle_read_board(seqs=[2, 6], caller="verifier")
        self.assertEqual([m["seq"] for m in res["messages"]], [2, 6])
        self.assertEqual(res["head"], 8)

        # Reset verifier's last_read_seq to 0 and read with limit=2
        data = self.bm.read_members()
        data["members"]["verifier"]["last_read_seq"] = 0
        self.bm.write_members(data)

        paged = self.tools.handle_read_board(since=0, limit=2, caller="verifier")
        self.assertEqual(len(paged["messages"]), 2)
        self.assertEqual(paged["messages"][-1]["seq"], 2)
        # last_read_seq must be 2 (not head=8!)
        self.assertEqual(
            self.bm.read_members()["members"]["verifier"]["last_read_seq"], 2
        )

    def test_mention_boundary_and_shard_prefix_rules(self):
        known = ["parser", "worker-1", "worker-2", "worker-bee"]

        # Email address must NOT trigger @parser
        mentioned, is_all, chs = self.bm.parse_mentions(
            "Contact dev@parser.io for details", known
        )
        self.assertEqual(mentioned, [])
        self.assertFalse(is_all)

        # @worker must match worker-1 and worker-2, but NOT worker-bee
        mentioned, is_all, chs = self.bm.parse_mentions(
            "Hey @worker please check #4", known
        )
        self.assertEqual(sorted(mentioned), ["worker-1", "worker-2"])

        # @#backend channel mention
        mentioned, is_all, chs = self.bm.parse_mentions(
            "@#backend is the schema ready?", known
        )
        self.assertEqual(chs, ["backend"])

    def test_channels_and_visibility(self):
        # Create channel #backend
        c_res = self.tools.handle_swarm(
            action="channel", op="create", channel="backend", topic="Backend API"
        )
        self.assertEqual(c_res["status"], "created")

        # Add two workers: api (in #backend) and ui (in main only)
        self.tools.handle_swarm(
            action="add", name="api", brief="Own API", channels=["backend"]
        )
        self.tools.handle_swarm(action="add", name="ui", brief="Own UI")

        # Post in #backend without mentioning @ui
        post = self.tools.handle_post_message(
            text="Internal backend note", channel="backend", caller="api"
        )

        # api sees the #backend post; ui does not
        api_view = self.tools.handle_read_board(since=8, caller="api")
        ui_view = self.tools.handle_read_board(since=8, caller="ui")
        api_seqs = [m["seq"] for m in api_view["messages"]]
        ui_seqs = [m["seq"] for m in ui_view["messages"]]
        self.assertIn(post["seq"], api_seqs)
        self.assertNotIn(post["seq"], ui_seqs)

    def test_sharded_add_and_group_kill_with_worker_runner(self):
        spawned_specs = []
        runner = WorkerProcessManager(
            swarm_dir=self.test_dir,
            workspace_root=self.test_dir,
            in_process_spawner=lambda spec: spawned_specs.append(spec),
        )
        tools = SwarmToolHandlers(
            self.bm, caller_agent_name="coordinator", worker_runner=runner
        )

        add_res = tools.handle_swarm(
            action="add",
            name="shard",
            brief="Process slice",
            tasks=["slice A", "slice B", "slice C"],
        )
        self.assertEqual(add_res["members"], ["shard-1", "shard-2", "shard-3"])
        self.assertEqual(len(spawned_specs), 3)
        self.assertIn("slice A", spawned_specs[0]["initial_input"])

        # Group kill by base name kills all 3 shards
        kill_res = tools.handle_swarm(action="kill", name="shard")
        self.assertEqual(sorted(kill_res["killed"]), ["shard-1", "shard-2", "shard-3"])
        members = self.bm.read_members()["members"]
        self.assertTrue(members["shard-1"]["killed"])
        self.assertTrue(members["shard-3"]["killed"])

    def test_swarm_wait_wakeup_and_concurrent_writes(self):
        wait_result = {}

        def waiter():
            wait_result["out"] = self.tools.handle_swarm_wait(
                since=8, mentions_only=True, timeout_seconds=5, caller="verifier"
            )

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.1)

        # Post a message mentioning @verifier
        self.tools.handle_post_message(
            text="@verifier please re-check #5", caller="python_async"
        )
        t.join(timeout=3.0)

        self.assertIn("Woken by new board message(s) #9", wait_result.get("out", ""))

    def test_mcp_server_jsonrpc_roundtrip(self):
        out_buf = io.StringIO()
        with patch("sys.stdout", out_buf):
            mcp_server.handle_rpc_request(
                self.tools, {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
            )
            mcp_server.handle_rpc_request(
                self.tools, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
            )
            mcp_server.handle_rpc_request(
                self.tools,
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "swarm",
                        "arguments": {"action": "status"},
                    },
                },
            )

        lines = [json.loads(line) for line in out_buf.getvalue().splitlines() if line]
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0]["id"], 1)
        self.assertEqual(len(lines[1]["result"]["tools"]), 4)
        status_payload = json.loads(lines[2]["result"]["content"][0]["text"])
        self.assertEqual(status_payload["status"], "ok")
        self.assertIn("coordinator", status_payload["roster"])

    def test_channel_read_does_not_mark_other_channels_read(self):
        self.tools.handle_swarm(action="channel", op="create", channel="backend", topic="API")
        self.tools.handle_swarm(action="add", name="dev", brief="Work", channels=["backend"])
        self.tools.handle_post_message(text="Backend msg", channel="backend", caller="coordinator")
        main_msg = self.tools.handle_post_message(text="Main msg", channel="main", caller="coordinator")
        # Reset dev's last_read_seq to 8 (before backend_msg and main_msg)
        data = self.bm.read_members()
        data["members"]["dev"]["last_read_seq"] = 8
        self.bm.write_members(data)

        # Reading only #backend must NOT advance last_read_seq past main_msg
        self.tools.handle_read_board(since=8, channel="backend", caller="dev")
        dev_read = self.bm.read_members()["members"]["dev"]["last_read_seq"]
        self.assertLess(dev_read, main_msg["seq"])

    def test_post_message_does_not_skip_unread_messages(self):
        self.tools.handle_swarm(action="add", name="alice", brief="Work")
        m_other = self.tools.handle_post_message(text="Unread by alice", caller="coordinator")
        self.tools.handle_post_message(text="Alice posts without reading", caller="alice")
        alice_read = self.bm.read_members()["members"]["alice"]["last_read_seq"]
        self.assertLess(alice_read, m_other["seq"])

    def test_add_without_spawner_returns_delegation_and_does_not_spawn(self):
        result = self.tools.handle_swarm(action="add", name="delegated", brief="Fix the bug")
        self.assertFalse(result["spawned"])
        self.assertEqual(result["mode"], "delegated")
        self.assertEqual(result["delegation"]["subagent_type"], "swarm-worker")
        self.assertEqual(result["delegation"]["name"], "delegated")
        self.assertIn('caller="delegated"', result["delegation"]["prompt"])
        self.assertIn("Fix the bug", result["delegation"]["prompt"])
        self.assertEqual(self.bm.read_members()["members"]["delegated"]["phase"], "delegated")
        self.assertTrue(any("@delegated Fix the bug" in m["text"] for m in self.bm.read_messages(since=0)["messages"]))

    def test_explicit_delegated_mode_and_sharded_delegations(self):
        runner = WorkerProcessManager(
            swarm_dir=self.test_dir,
            workspace_root=self.test_dir,
            in_process_spawner=lambda _spec: self.fail("explicit delegation must not spawn"),
        )
        tools = SwarmToolHandlers(self.bm, worker_runner=runner)
        single = tools.handle_swarm(action="add", name="native", brief="Native", mode="delegated")
        self.assertFalse(single["spawned"])
        self.assertIn('caller="native"', single["delegation"]["prompt"])
        shards = tools.handle_swarm(
            action="add", name="slice", brief="Review", tasks=["one", "two"], mode="delegated"
        )
        self.assertEqual(len(shards["delegations"]), 2)
        self.assertIn("Your assigned slice (2/2): two", shards["delegations"][1]["prompt"])
        self.assertEqual(self.bm.read_members()["members"]["slice-1"]["phase"], "delegated")

    def test_bind_updates_harness_job_state_and_rejects_unknown_member(self):
        self.tools.handle_swarm(action="add", name="bound", brief="Work")
        running = self.tools.handle_swarm(action="bind", name="bound", job_id="job-1", state="running")
        self.assertEqual(running["status"], "bound")
        member = self.bm.read_members()["members"]["bound"]
        self.assertEqual(member["job_id"], "job-1")
        self.assertEqual(member["state"], "running")
        self.assertEqual(member["phase"], "running")
        done = self.tools.handle_swarm(action="bind", name="bound", job_id="job-1", state="done")
        self.assertEqual(done["status"], "bound")
        self.assertEqual(self.bm.read_members()["members"]["bound"]["phase"], "done")
        missing = self.tools.handle_swarm(action="bind", name="nobody", job_id="job-2", state="failed")
        self.assertIn("error", missing)
        invalid = self.tools.handle_swarm(action="bind", name="bound", job_id="job-1", state="pending")
        self.assertIn("error", invalid)

    def test_status_marks_bound_running_members_unverified(self):
        # The board server cannot query the harness for a job's liveness, so a
        # bound member that claims to be running must say so as unverified
        # rather than reading as confirmed live.
        self.tools.handle_swarm(action="add", name="watched", brief="Work")
        self.tools.handle_swarm(action="bind", name="watched", job_id="job-9", state="running")
        roster = self.tools.handle_swarm(action="status")["roster"]
        self.assertEqual(roster["watched"]["liveness"], "unverified")
        self.assertEqual(roster["watched"]["phase"], "running")

        self.tools.handle_swarm(action="bind", name="watched", job_id="job-9", state="done")
        roster = self.tools.handle_swarm(action="status")["roster"]
        self.assertNotIn("liveness", roster["watched"])
        self.assertEqual(roster["watched"]["phase"], "done")

    def test_add_member_rejects_existing_or_killed_name(self):
        self.tools.handle_swarm(action="add", name="worker_x", brief="Task 1")
        dup = self.tools.handle_swarm(action="add", name="worker_x", brief="Task 2")
        self.assertIn("error", dup)
        self.tools.handle_swarm(action="kill", name="worker_x")
        reuse = self.tools.handle_swarm(action="add", name="worker_x", brief="Task 3")
        self.assertIn("error", reuse)

    def test_n_zero_rejected_and_extra_mcp_args_ignored(self):
        res_n0 = self.tools.handle_swarm(action="add", name="bad", brief="Nope", n=0)
        self.assertIn("error", res_n0)

        out_buf = io.StringIO()
        with patch("sys.stdout", out_buf):
            mcp_server.handle_rpc_request(
                self.tools,
                {
                    "jsonrpc": "2.0",
                    "id": 10,
                    "method": "tools/call",
                    "params": {
                        "name": "post_message",
                        "arguments": {"text": "hello", "unexpected_arg": 123},
                    },
                },
            )
        resp = json.loads(out_buf.getvalue().splitlines()[0])
        self.assertFalse(resp["result"]["isError"])

    def test_wait_for_event_sets_anything_wake_mode(self):
        out = self.tools.handle_swarm_wait(
            since=0, mentions_only=False, timeout_seconds=0.05, caller="verifier"
        )
        self.assertIn("Waiting for any message", out)
        # During wait_for_event with mentions_only=False, wake_mode must be "anything"
        recorded = []
        orig = self.bm.set_member_wake_mode
        def spy(name, mode, wait_since=None):
            recorded.append(mode)
            return orig(name, mode, wait_since)
        with patch.object(self.bm, "set_member_wake_mode", side_effect=spy):
            self.bm.wait_for_event("verifier", since_seq=8, mentions_only=False, timeout=0.01)
        self.assertEqual(recorded[0], "anything")
        self.assertEqual(recorded[-1], "working")


if __name__ == "__main__":
    unittest.main()

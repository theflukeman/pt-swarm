"""
Model Context Protocol (MCP) Server for pt-swarm.
Exposes swarm, post_message, read_board, and swarm_wait over JSON-RPC 2.0 stdio.
Compatible with Polytoken's `mcp_servers` configuration.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

from .board_manager import BoardManager
from .swarm_tools import SwarmToolHandlers
from .worker_runner import WorkerProcessManager

TOOLS = [
    {
        "name": "swarm",
        "description": "Manage the swarm of agents working on the shared message board.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "models", "add", "bind", "model", "kill", "title", "channel"],
                    "description": "Action to perform: status, models, add, bind, model, kill, title, or channel.",
                },
                "name": {
                    "type": "string",
                    "description": "Roster name of the worker (short, lowercase, no spaces).",
                },
                "brief": {
                    "type": "string",
                    "description": "The worker's assignment for add: what it owns and what done looks like. Posted publicly on the board as @<name> <brief>.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["delegated"],
                    "description": "Force coordinator-mediated subagent delegation for add.",
                },
                "job_id": {
                    "type": "string",
                    "description": "Harness background job handle for bind action.",
                },
                "state": {
                    "type": "string",
                    "enum": ["running", "done", "failed"],
                    "description": "Latest harness job state for bind action.",
                },
                "model": {
                    "type": "string",
                    "description": "Model to run this member on. Unset means its role tier: workers and verifiers get the full tier, the monitor and narrator the mini tier. Also accepts a model name, a name(level) variant, a @mg:<group> reference, or the aliases full, mini, nano, and session.",
                },
                "workspace": {
                    "type": "string",
                    "enum": ["inherit", "share", "branch"],
                    "default": "inherit",
                    "description": "Workspace isolation mode: inherit shares parent directory, share gives an isolated worktree, branch creates an isolated clone.",
                },
                "fork": {
                    "type": "string",
                    "description": "Optional roster name of an existing member (or 'coordinator') whose conversation history this worker forks from.",
                },
                "n": {
                    "type": "integer",
                    "description": "Number of homogeneous copies to spawn (<name>-1 through <name>-<n>).",
                },
                "tasks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Per-worker slice targets for sharded groups (<name>-1 through <name>-<len>).",
                },
                "title": {
                    "type": "string",
                    "description": "A short descriptive title for the swarm.",
                },
                "channels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Initial channels to subscribe the new worker to.",
                },
                "channel": {
                    "type": "string",
                    "description": "Target channel for channel operation.",
                },
                "op": {
                    "type": "string",
                    "enum": ["create", "list", "subscribe", "unsubscribe", "archive"],
                    "description": "Operation for channel action.",
                },
                "topic": {
                    "type": "string",
                    "description": "Topic description for channel creation.",
                },
                "all": {
                    "type": "boolean",
                    "description": "For models: list all available models.",
                },
                "caller": {
                    "type": "string",
                    "description": "Optional caller roster name override when sharing a single MCP server.",
                },
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
    {
        "name": "post_message",
        "description": "Post a message to the shared swarm message board.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The message body. Use @<name> to address specific members, @all to broadcast, or @#channel to address a channel.",
                },
                "channel": {
                    "type": "string",
                    "description": "Optional channel to post to. Defaults to main or the sender's primary channel.",
                },
                "caller": {
                    "type": "string",
                    "description": "Optional caller roster name override.",
                },
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_board",
        "description": "Read posts from the shared swarm board or retrieve member states.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "since": {
                    "type": "integer",
                    "default": 0,
                    "description": "Return messages posted after this sequence number. Set to 0 to read from beginning.",
                },
                "limit": {
                    "type": "integer",
                    "default": 100,
                    "description": "Maximum number of messages to return in this read.",
                    "minimum": 1,
                },
                "seqs": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Return exactly these messages by sequence number, ignoring since and limit.",
                },
                "channel": {
                    "type": "string",
                    "description": "Optional channel filter. Reads only channels visible to the caller; '*' is treated as no explicit channel filter.",
                },
                "status": {
                    "type": "boolean",
                    "default": False,
                    "description": "When true, returns the status of all members (wake_mode, wait_since, last_read_seq, state) instead of messages.",
                },
                "caller": {
                    "type": "string",
                    "description": "Optional caller roster name override.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "swarm_wait",
        "description": (
            "Sleep until a new message is posted to the board. "
            "Consumes zero tokens while sleeping."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "since": {
                    "type": "integer",
                    "description": "Highest message sequence number the caller has actually seen/processed.",
                },
                "mentions_only": {
                    "type": "boolean",
                    "default": True,
                    "description": "If true, wake only when mentioned directly (@<name>), via @all, or via member's channel. If false, wake on any post.",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "default": 300,
                    "description": "Maximum seconds to wait before waking automatically.",
                },
                "caller": {
                    "type": "string",
                    "description": "Optional caller roster name override.",
                },
            },
            "required": ["since"],
            "additionalProperties": False,
        },
    },
]


_NO_ID = object()


def send_response(req_id: Any, result: Any = None, error: Any = None):
    if req_id is _NO_ID:
        return
    resp: Dict[str, Any] = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        resp["error"] = error
    else:
        resp["result"] = result
    sys.stdout.write(json.dumps(resp) + "\n")
    sys.stdout.flush()


def create_runtime() -> SwarmToolHandlers:
    swarm_dir = Path(os.environ.get("POLYTOKEN_SWARM_DIR", "./.swarm/default")).resolve()
    caller_agent = os.environ.get("POLYTOKEN_AGENT_NAME", "coordinator")
    worker_runner = WorkerProcessManager(swarm_dir=swarm_dir)

    bm_holder: Dict[str, BoardManager] = {}

    def _on_member_mentioned(target_name: str, entry: Dict[str, Any]):
        if target_name == "coordinator" or entry.get("kind") == "brief":
            return
        bm_inst = bm_holder.get("bm")
        if not bm_inst:
            return
        m_info = bm_inst.read_members().get("members", {}).get(target_name, {})
        if m_info.get("killed"):
            return
        if not worker_runner.is_running(target_name):
            last_read = m_info.get("last_read_seq", 0)
            wake_prompt = (
                f"[swarm] new board message #{entry['seq']} (you have read up to #{last_read}). "
                f"Read anything you have missed, complete any required follow-up, then exit."
            )
            worker_runner.spawn_or_wake(
                name=target_name,
                prompt_text=wake_prompt,
                brief_seq=0,
                model=m_info.get("model"),
                workspace_mode=m_info.get("workspace_mode", "inherit"),
                role=m_info.get("role", "worker"),
            )

    bm = BoardManager(swarm_dir, on_member_mentioned=_on_member_mentioned)
    bm_holder["bm"] = bm
    return SwarmToolHandlers(bm, caller_agent_name=caller_agent, worker_runner=worker_runner)


SUPPORTED_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")


def negotiate_protocol_version(requested: Any) -> str:
    """Echo a protocol version we implement; otherwise advertise our baseline."""
    if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    return SUPPORTED_PROTOCOL_VERSIONS[0]


def handle_rpc_request(handlers: SwarmToolHandlers, req: Any) -> None:
    if not isinstance(req, dict) or req.get("jsonrpc") != "2.0" or not isinstance(req.get("method"), str):
        req_id = req.get("id", _NO_ID) if isinstance(req, dict) else _NO_ID
        send_response(req_id if req_id is not _NO_ID else None, error={"code": -32600, "message": "Invalid Request"})
        return
    method = req.get("method")
    req_id = req.get("id", _NO_ID)
    params = req.get("params") or {}
    if not isinstance(params, dict):
        send_response(req_id, error={"code": -32602, "message": "Invalid params"})
        return

    if method == "initialize":
        send_response(
            req_id,
            {
                "protocolVersion": negotiate_protocol_version(params.get("protocolVersion")),
                "serverInfo": {"name": "pt-swarm", "version": "1.0.0"},
                "capabilities": {"tools": {}},
            },
        )
    elif method in ("notifications/initialized", "notifications/cancelled"):
        return
    elif method == "ping":
        send_response(req_id, {})
    elif method == "tools/list":
        send_response(req_id, {"tools": TOOLS})
    elif method == "tools/call":
        tool_name = params.get("name")
        args = params.get("arguments", {})
        if not isinstance(args, dict):
            send_response(req_id, error={"code": -32602, "message": "Invalid params"})
            return
        known = {"swarm": set(TOOLS[0]["inputSchema"]["properties"]), "post_message": {"text", "channel", "caller"}, "read_board": {"since", "limit", "seqs", "channel", "status", "caller"}, "swarm_wait": {"since", "mentions_only", "timeout_seconds", "caller"}}
        if tool_name in known and set(args) - known[tool_name]:
            send_response(req_id, error={"code": -32602, "message": "Invalid params"})
            return

        try:
            if tool_name == "swarm":
                res = handlers.handle_swarm(**args)
            elif tool_name == "post_message":
                res = handlers.handle_post_message(**args)
            elif tool_name == "read_board":
                res = handlers.handle_read_board(**args)
            elif tool_name == "swarm_wait":
                res = handlers.handle_swarm_wait(**args)
            else:
                send_response(
                    req_id,
                    error={"code": -32601, "message": f"Tool '{tool_name}' not found"},
                )
                return

            content_text = (
                json.dumps(res, indent=2) if isinstance(res, (dict, list)) else str(res)
            )
            is_err = isinstance(res, dict) and "error" in res
            send_response(
                req_id,
                {"content": [{"type": "text", "text": content_text}], "isError": is_err},
            )
        except (TypeError, ValueError, KeyError) as e:
            send_response(req_id, error={"code": -32602, "message": "Invalid params"})
        except Exception:
            send_response(
                req_id,
                {"content": [{"type": "text", "text": "Tool execution failed"}], "isError": True},
            )
    else:
        send_response(
            req_id,
            error={"code": -32601, "message": f"Method '{method}' not implemented"},
        )


def main():
    handlers = create_runtime()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            send_response(None, error={"code": -32700, "message": "Parse error"})
            continue
        handle_rpc_request(handlers, req)


if __name__ == "__main__":
    main()

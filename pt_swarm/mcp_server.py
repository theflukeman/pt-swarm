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
        "description": (
            "Manage the swarm of agents working on the shared message board. "
            "Actions: status, models, add, model, kill, title, channel."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "models", "add", "model", "kill", "title", "channel"],
                    "description": "Action to perform: status, models, add, model, kill, title, or channel.",
                },
                "name": {
                    "type": "string",
                    "description": "Roster name of the worker (short, lowercase, no spaces).",
                },
                "brief": {
                    "type": "string",
                    "description": "The worker's assignment for add: what it owns and what done looks like.",
                },
                "model": {
                    "type": "string",
                    "description": "Model identifier to pin this worker to. Leave unset to inherit.",
                },
                "workspace": {
                    "type": "string",
                    "enum": ["inherit", "share", "branch"],
                    "default": "inherit",
                    "description": "Workspace isolation mode: inherit, share (git worktree), or branch (clone).",
                },
                "fork": {
                    "type": "string",
                    "description": "Optional roster name of an existing member to fork history from.",
                },
                "n": {
                    "type": "integer",
                    "description": "Optional number of homogeneous copies to spawn (<name>-1..<name>-<n>).",
                },
                "tasks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional per-worker slice targets for sharded groups (<name>-1..<name>-<k>).",
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
                    "description": "Target channel name for channel action.",
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
                    "description": "Message text. Use @<name> to address specific members, @all, or @#channel.",
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
                    "description": "Return messages posted after this sequence number.",
                },
                "limit": {
                    "type": "integer",
                    "default": 100,
                    "description": "Maximum number of messages to return.",
                },
                "seqs": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Return exactly these messages by sequence number, ignoring since and limit.",
                },
                "channel": {
                    "type": "string",
                    "description": "Channel to read ('*' for all channels).",
                },
                "status": {
                    "type": "boolean",
                    "default": False,
                    "description": "When true, returns the roster status instead of messages.",
                },
                "caller": {
                    "type": "string",
                    "description": "Optional caller roster name override.",
                },
            },
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
                    "description": "Highest message sequence number the caller has seen.",
                },
                "mentions_only": {
                    "type": "boolean",
                    "default": True,
                    "description": "Wake only for messages mentioning this agent, @all, or @#channel.",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "default": 300,
                    "description": "Maximum seconds to wait before returning.",
                },
                "caller": {
                    "type": "string",
                    "description": "Optional caller roster name override.",
                },
            },
            "required": ["since"],
        },
    },
]


def send_response(req_id: Any, result: Any = None, error: Any = None):
    if req_id is None:
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
                f"Read anything you have missed, and wait for messages when you have nothing left to do."
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


def handle_rpc_request(handlers: SwarmToolHandlers, req: Dict[str, Any]) -> None:
    method = req.get("method")
    req_id = req.get("id")
    params = req.get("params") or {}

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
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}

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
        except Exception as e:
            send_response(
                req_id,
                {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "isError": True},
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
            continue
        handle_rpc_request(handlers, req)


if __name__ == "__main__":
    main()

# AGENTS.md

Guidance for AI coding agents working in **pt-swarm**.

## Project Overview

`pt-swarm` is a port of the Antigravity Swarm multi-agent orchestration system for Polytoken and general LLM harnesses. Agents coordinate through an append-only JSONL message board (`board.jsonl`), a member roster (`members.json`), zero-token sleep (`swarm_wait`), and an optional Node.js live dashboard (`sidecar_ui/`).

## Repository Layout

- `pt_swarm/` — the single installed package (everything runtime needs lives here):
  - `mcp_server.py` — JSON-RPC 2.0 stdio MCP server exposing `swarm`, `post_message`, `read_board`, and `swarm_wait`.
  - `board_manager.py` — Cross-process file-locked `BoardManager`, `@mention` / `@#channel` parser, channel registry, and `wait_for_event` condition loop.
  - `swarm_tools.py` — `SwarmToolHandlers` implementing tool dispatch and validation.
  - `worker_runner.py` — `WorkerProcessManager` for spawning and tracking worker processes (`<swarm_dir>/pids/<name>.pid`).
  - `workspace_manager.py` — `WorkspaceManager` for `inherit`, `share` (git worktree), and `branch` (git clone) isolation with non-git fallbacks.
  - `stall_monitor.py` — `SwarmStallMonitor` detecting unaddressed posts and unanswered `@mentions`.
  - `harness_adapter.py` — `PolytokenSwarmAdapter` integrating board, worker runner, workspace manager, and stall monitor.
  - `cli.py` — Terminal board & roster viewer (`--swarm-dir`, `--status`, `--watch`).
  - `agent_definitions/*.md` — Role prompts loaded by the adapter.
  - `tool_schemas/*.json` — JSON Schemas (Draft 2020-12) for the 4 MCP tools.
- `sidecar_ui/` — Node.js ES-module web dashboard (`main.mjs`, `channels.mjs`, `app.js`, `hive.js`, `sidecar_sdk/`).
- `schemas/` — On-disk format specs (`board_jsonl.md`, `members_json.md`, `view_json.md`).
- `subagents/`, `skills/`, `.polytoken/` — Role prompts, subagent manifests, and Polytoken config.
- `tests/` — Python `unittest` suites (`test_board.py`, `test_adapter.py`, `test_cli_and_schemas.py`) and Node.js tests (`test_sidecar_ui.mjs`).

Do not reintroduce top-level `tools/` or `mcp_server.py` modules: they collide with unrelated packages when installed globally.

## Development & TDD Workflow

Follow **Test-Driven Development (TDD)** for all bug fixes and new features:
1. Write a failing regression test first in `tests/` and run the suite to confirm the failure.
2. Implement the minimal, clean fix.
3. Re-run both test suites and confirm all tests pass.

### Test Commands

```bash
# Python suite (35 tests; unittest is used, pytest is not required)
python3 -m unittest discover -s tests -v

# Node.js sidecar UI & channel logic suite (2 tests)
node --test tests/test_sidecar_ui.mjs
```

Use `python3`; some systems have no `python` alias.

## Critical Invariants & Conventions

- **Locking & Atomicity**: All reads/writes that mutate `members.json` or `board.jsonl` must hold `BoardManager.lock` (`_CrossProcessLock`). Never call `read_members()` then `write_members()` outside `self.bm.lock`.
- **Member Lifecycle & Reservation**: Killed member names (`killed: true`) stay reserved and must not be reused by `add_member()`.
- **Wake Modes**: `wake_mode` values are `"working"`, `"mentions"`, and `"anything"` (matching `sidecar_ui/main.mjs` and `tests/fixtures/members.json`). Always restore `"working"` in a `finally` block when exiting `wait_for_event()`.
- **Read Cursors**: `post_message()` must not advance `last_read_seq` past unread messages; channel-filtered `read_messages()` must not advance `last_read_seq` to the global `head_seq`.
- **Board Identity**: All agents share one MCP server entry, distinguished only by the `caller` argument. Worker prompts and `worker_runner` spawn prompts must instruct workers to pass `caller="<name>"`; dropping that instruction silently attributes worker traffic to the coordinator.
- **Worker Spawning**: The default worker command is `polytoken exec {prompt}` — a bare `polytoken` starts the TUI, which exits immediately without a TTY. `spawn_or_wake()` must verify the child survived `spawn_grace_seconds` and report `spawned: false` with the exit code otherwise.
- **PID Tracking**: `<swarm_dir>/pids/<name>.pid` stores JSON `{"pid": N, "start": "<marker>"}`. On Linux the marker comes from `/proc/<pid>/stat` so a recycled PID is not mistaken for a live worker. Plain-integer contents are still accepted for older state.
- **Cross-Process Workers**: `WorkerProcessManager` tracks PIDs on disk so separate `mcp_server.py` processes do not spawn duplicate workers. Always close `log_fh` in the parent process after `subprocess.Popen`.
- **Schema Parity**: Keep `pt_swarm/mcp_server.py` (`TOOLS`), `pt_swarm/tool_schemas/*.json`, `pt_swarm/swarm_tools.py`, and `schemas/*.md` in sync (`test_tool_schemas_match_mcp_server_tools` enforces schema/MCP parity).
- **Model List**: `swarm(action="models")` must report models discovered from `polytoken models --format json` (via the injectable `model_lister` in tests), never a hardcoded provider list.
- **Model Resolution**: A member's `model` is a *request*, resolved by `pt_swarm/model_selection.py` before any spawn — `default`/unset is the role tier, `session` is the model the Polytoken session is running, `full`/`mini`/`nano` are tier aliases, anything else passes through (model name, `name(level)`, `@mg:<group>`). Never hand the `default` sentinel to `polytoken exec --model` (it is rejected), and do not set `POLYTOKEN_MODEL` (not a variable Polytoken reads): the model travels on the command line via the `{model}` template placeholder, which is dropped together with its `--model` flag when nothing resolved.
- **stdout is the wire**: In server code paths, diagnostics go to `sys.stderr`; writing to stdout corrupts the JSON-RPC stream.
- **Windows Compatibility**: Support both Windows (`msvcrt`, `\` paths, `path.delimiter`, `shlex.split` quote handling) and POSIX (`fcntl`, `/` paths). In Node tests, set `SIDECAR_NO_LISTEN=1` when importing `sidecar_ui/main.mjs` to test routes in-process.

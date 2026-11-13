# Porting Manifest: Antigravity Swarm to Polytoken

This document details the provenance of all components in this package, distinguishing between assets directly adapted/copied from Google Antigravity and components that were reverse-engineered and rebuilt for Polytoken.

All runtime Python code lives in the single `pt_swarm/` package so that a global install owns exactly one top-level import name.

---

## 1. Provenance Matrix

| Component | Status | Source in Antigravity | Rebuild Reason & Polytoken Adaptation |
| :--- | :--- | :--- | :--- |
| **`skills/native_swarm/SKILL.md`** | **Adapted Copy** | `builtin/skills/native_swarm/SKILL.md` | Copied from Antigravity; variance: bare `@mention` tokens wrapped in backticks (`` `@mention` ``) so Polytoken does not treat them as local resource paths. |
| **`skills/swarm_monitor/SKILL.md`** | **Adapted Copy** | `builtin/skills/swarm_monitor/SKILL.md` | Copied from Antigravity; variance: bare `@mention` wrapped in backticks (`` `@mention` ``) so Polytoken does not treat it as a local resource path. |
| **`skills/swarm_narrator/SKILL.md`** | **Adapted Copy** | `builtin/skills/swarm_narrator/SKILL.md` | Copied from Antigravity; variance: bare `@mentions` wrapped in backticks (`` `@mentions` ``) so Polytoken does not treat them as a local resource path. |
| **`sidecar_ui/*`** | **Direct Copy** | `builtin/plugins/swarm/sidecars/swarm_board/` | Preserved (`main.mjs`, `channels.mjs`, `hive.js`, `app.js`, `index.html`, etc.) to provide the live board UI. |
| **`assets/logo.svg`** & `plugin.json` | **Direct Copy** | `builtin/plugins/swarm/` | Preserved visual branding and metadata manifest. |
| **`pt_swarm/agent_definitions/coordinator.md`** | **Rebuilt / Authored** | *Implicit in Antigravity coordinator system prompt* | Extracted into a standalone, prompt-ready Markdown file for Polytoken agent initialization. |
| **`pt_swarm/agent_definitions/worker.md`** | **Rebuilt / Authored** | *Implicit `<swarm_worker>` injected by Antigravity runtime* | Synthesized from `<swarm_worker>` references and skill constraints into an explicit system prompt, including the board-identity (`caller`) rule. |
| **`pt_swarm/agent_definitions/monitor.md`** | **Rebuilt / Authored** | *Derived from `swarm_monitor` skill* | Packaged as a dedicated daemon prompt for cron execution. |
| **`pt_swarm/agent_definitions/narrator.md`** | **Rebuilt / Authored** | *Derived from `swarm_narrator` skill* | Packaged as a dedicated background observer prompt. |
| **`pt_swarm/tool_schemas/*.json`** | **Rebuilt / Authored** | *Internal Protobuf definitions in language server* | Formalized as standard JSON Schemas (Draft 2020-12) for `swarm`, `post_message`, `read_board`, and `swarm_wait`. |
| **`pt_swarm/board_manager.py`** | **Rebuilt / Authored** | *Internal compiled Go package (`board/`)* | Re-implemented thread-safe, file-locked JSONL board manager, `@mention` parser, and event sleep loop in Python. |
| **`pt_swarm/swarm_tools.py`** | **Rebuilt / Authored** | *Internal binary RPC handlers* | Re-implemented tool dispatch functions compatible with LLM function-calling interfaces; `models` now reports the models the local Polytoken install actually offers. |
| **`schemas/*.md`** | **Rebuilt / Authored** | *Internal data formats observed in filesystem* | Formalized specifications for `board.jsonl`, `members.json`, and `view.json`. |
| **`pt_swarm/workspace_manager.py`** | **Rebuilt / Authored** | *Antigravity workspace isolation engine* | Provisions isolated git worktrees (`workspace: "share"`) or clones (`"branch"`). |
| **`pt_swarm/worker_runner.py`** | **Rebuilt / Authored** | *Antigravity worker conversation pool* | Spawns, tracks, wakes, and terminates worker processes on `swarm add` and `@mention`; uses Polytoken's headless `exec` mode and verifies the child survived startup. |
| **`pt_swarm/stall_monitor.py`** | **Rebuilt / Authored** | *Antigravity cron / timer daemon* | Standalone daemon monitoring `members.json` for deadlocks and unaddressed posts. |
| **`pt_swarm/cli.py`** | **Rebuilt / Authored** | *Antigravity terminal UI viewer* | Zero-dependency terminal viewer for watching the board live. |
| **`sidecar_ui/sidecar_sdk/index.mjs`** | **Rebuilt / Authored** | *Antigravity internal `sidecar_sdk`* | Standalone Node.js HTTP server shim allowing `sidecar_ui/main.mjs` to run outside Antigravity. |
| **`pt_swarm/mcp_server.py`** | **Rebuilt / Authored** | *Antigravity internal tool RPC* | Native MCP stdio server exposing `swarm`, `post_message`, `read_board`, `swarm_wait`; negotiates protocol versions and keeps diagnostics off stdout. |
| **`subagents/*.md`, `.polytoken/`** | **Rebuilt / Authored** | *Antigravity internal agent personas* | Polytoken-native subagent definitions (`polytoken:` frontmatter) and `.polytoken/config.yaml`. |
| **`pt_swarm/harness_adapter.py`** | **Rebuilt / Authored** | *New integration layer* | Adapter classes that instantiate harness threads when `swarm(action="add")` is invoked. |

---

## 2. Technical Details on Rebuilt Components

### A. The Board Backend (`board_manager.py`)
In Antigravity, the shared message bus is maintained by a compiled Go backend that performs:
- Atomic append-only writes to `board.jsonl` with incrementing sequence IDs.
- Updates to `members.json` tracking last-read and last-post pointers.
- Token-level regex matching on `@name`, `@all`, and `@#channel` triggers (`board/mentions.go`).
- Signal-based waking of parked agent processes (`swarm_wait`).

**How Polytoken Implements It:**
`pt_swarm/board_manager.py` recreates this behavior using:
- Python `threading.RLock` and file locks for atomic read/write operations.
- Direct JSON Lines streaming.
- `threading.Event` condition variable waiting with timeouts to simulate zero-cost sleeps in multi-threaded or async harnesses.

### B. Dynamic Role Prompts (`<swarm_worker>`)
In Antigravity, worker subagents receive system prompt instructions dynamically enclosed in `<swarm_worker>` tags when spawned via `swarm add`. These instructions are never exposed as a plain file on disk.

**How Polytoken Implements It:**
`pt_swarm/agent_definitions/worker.md` codifies the complete set of worker rules:
- Strict scope ownership (never editing files outside the designated assignment).
- Board identity: pass `caller="<name>"` on every swarm tool call, because all agents share one MCP server entry.
- Peer-to-peer reporting via `@mention` with `#seq` referencing.
- Compulsory sleep parking via `swarm_wait` when waiting on verifiers or tests.
- Standalone execution without assuming access to parent conversation transcripts.

### C. Tool Schemas
Antigravity manages tool definitions through language server RPC calls. For Polytoken, these were exported into clean, portable JSON Schemas in `pt_swarm/tool_schemas/` that plug directly into Polytoken or any model runtime supporting OpenAPI/JSON Schema function declarations.

### D. Worker Process Model
Antigravity keeps workers as conversations inside one runtime. Polytoken runs each worker as a separate headless process (`polytoken exec "<prompt>"`), coordinated only through the on-disk board:
- The worker's initial prompt carries its roster name and the `caller` instruction.
- `spawn_or_wake()` waits `spawn_grace_seconds` and reports `spawned: false` with the exit code and log tail when the process dies during startup, so the coordinator never waits on a ghost.
- PID files record the process start marker (`/proc/<pid>/stat`) alongside the PID, so a recycled PID is not mistaken for a live worker.

---

## 3. Validation Against Live Session Logs

To ensure behavioral fidelity, a live Antigravity swarm session was executed and inspected:
- **Golden Fixtures Saved**: Real-world `board.jsonl` and `members.json` captured from the live session were archived under `tests/fixtures/`.
- **Verified Output Shapes**:
  - `post_message` verified to return exact payload: `{"status": "posted", "seq": N}`.
  - `read_board` verified to return: `{"messages": [...], "head": <int>}` with message objects preserving `kind: "brief"`, `request_id`, and `time: "YYYY-MM-DD HH:MM:SS"`.
  - `swarm_wait` verified to return string response: `"Waiting for a message that calls on you by name, or on @all posted after #N."`.
- **Automated Tests**: Python tests are discovered with `python3 -m unittest discover -s tests -v`; Node.js dashboard tests run with `node --test tests/test_sidecar_ui.mjs`. The suites cover board and roster behavior, worker processes, workspace isolation, stall monitoring, CLI output, schema parity, packaging, environment/model selection, and the sidecar UI.

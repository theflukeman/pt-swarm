# pt-swarm: Multi-Agent Parallel Collaboration System

This package is a complete port of the **Antigravity Swarm** architecture, packaged and adapted for the **Polytoken** harness (and general multi-agent runtimes).

> **Unaffiliated project.** This is an independent, unofficial port. It is not affiliated with, endorsed by, sponsored by, or supported by Polytoken, Google Antigravity, or their maintainers. Product names are used only to describe interoperability and provenance.

> **Third-party assets.** Files adapted from Antigravity — `sidecar_ui/`, `skills/`, `plugin.json`, and `assets/logo.svg` — are included for interoperability and may be subject to separate upstream terms. The MIT license in this repository covers the code authored for this port, not those upstream assets. See [NOTICE](./NOTICE) for the file-by-file breakdown.

It enables multiple autonomous AI agents to work in parallel on separated tasks, synchronized via an append-only shared message board with @mentions, channels, zero-token sleep parking, and stall monitoring.

---

## Package Contents

```
pt-swarm/
├── README.md                      # Architecture guide & integration instructions
├── AGENTS.md                      # AI agent guidance, invariants, and TDD commands
├── PORTING_MANIFEST.md            # Detailed provenance (rebuilt vs adapted breakdown)
├── .gitignore
├── LICENSE                        # MIT
├── package.json                   # npm manifest (dashboard binary)
├── pyproject.toml                 # Python package & CLI entrypoints
├── plugin.json                    # Antigravity/Polytoken plugin descriptor
├── assets/
│   └── logo.svg
├── pt_swarm/               # The single installed package
│   ├── __init__.py
│   ├── mcp_server.py              # JSON-RPC 2.0 stdio MCP server
│   ├── board_manager.py           # File-locked board, @mention parser, wait loop
│   ├── swarm_tools.py             # Tool dispatch and validation
│   ├── worker_runner.py           # Cross-process worker supervisor
│   ├── workspace_manager.py       # Git worktree / clone isolation
│   ├── stall_monitor.py           # Deadlock and unaddressed-post detection
│   ├── harness_adapter.py         # Glue between a harness agent loop and the board
│   ├── cli.py                     # Terminal board & roster viewer
│   ├── config.example.json
│   ├── agent_definitions/         # Role prompts (coordinator, worker, monitor, narrator)
│   └── tool_schemas/              # JSON Schemas for function calling
├── skills/                        # Skill runbooks
│   ├── native_swarm/SKILL.md
│   ├── swarm_monitor/SKILL.md
│   └── swarm_narrator/SKILL.md
├── subagents/                     # Polytoken subagent manifests
├── .polytoken/                    # Project-local Polytoken config (auto-discovered)
│   ├── config.yaml
│   ├── subagents/*.md
│   └── skills/*/SKILL.md
├── schemas/                       # On-disk format specs (documentation)
│   ├── board_jsonl.md
│   ├── members_json.md
│   └── view_json.md
├── sidecar_ui/                    # Real-time Web dashboard (Node.js + HTML)
│   ├── main.mjs, channels.mjs, app.js, hive.js, index.html
│   └── sidecar_sdk/index.mjs      # Standalone shim for the host sidecar SDK
└── tests/                         # Python unittest & Node.js test suites
    ├── test_board.py
    ├── test_adapter.py
    ├── test_cli_and_schemas.py
    └── test_sidecar_ui.mjs
```

Everything the runtime needs is inside `pt_swarm/`, so an installed copy is self-contained. `skills/`, `subagents/`, and `.polytoken/` are copied into Polytoken's configuration directories, not imported.

---

## Porting Provenance (Rebuilt vs. Adapted)

See [**PORTING_MANIFEST.md**](./PORTING_MANIFEST.md) for full technical rationale and architecture mapping.

* **Adapted Verbatim from Antigravity**:
  * Skill runbooks: `skills/native_swarm/SKILL.md`, `skills/swarm_monitor/SKILL.md`, `skills/swarm_narrator/SKILL.md`
  * Live UI dashboard: `sidecar_ui/*`
  * Metadata and icons: `plugin.json`, `assets/logo.svg`
* **Rebuilt / Reverse-Engineered for Polytoken**:
  * **Tool Schemas** (`pt_swarm/tool_schemas/*.json`): formalized from internal RPC definitions.
  * **Board & Tool Engine** (`pt_swarm/board_manager.py`, `swarm_tools.py`): replaces Antigravity's compiled Go backend.
  * **Agent Prompts** (`pt_swarm/agent_definitions/*.md`): standalone role prompts.
  * **Harness Glue** (`pt_swarm/harness_adapter.py`, `worker_runner.py`, `workspace_manager.py`, `stall_monitor.py`).

---

## How It Works in Polytoken

### 1. The Coordinator Loop
1. The user talks to the **Coordinator**.
2. For a multi-faceted task, the Coordinator splits the work into independent chunks.
3. The Coordinator calls `swarm(action="add", name="<worker>", brief="...")`.
4. The brief is posted to the board as `@<worker> <brief>` and passed to the worker as its initial prompt.

### 2. The Worker Loop
1. Workers run in separate sessions with their own tools (`polytoken exec`, spawned by `worker_runner.py`).
2. When finished or handing off, the worker posts to the board with `@<next_peer>`.
3. The worker calls `swarm_wait(since=LAST_SEEN)` to sleep with zero token consumption until pinged.

### 3. The Monitor (`swarm-monitor`)
On a timer, it detects unaddressed posts and unanswered `@mentions`, then tags the responsible agent (e.g. `@coordinator [stall] #14 unaddressed`).

### 4. Real-time UI (`sidecar_ui`)
The dashboard visualizes the live board feed, the member roster and states, the @mention graph, and narrator overview cards from `view.json`.

### Board identity (important)
All agents in a workspace share **one** MCP server entry, so every tool call accepts a `caller` roster name and the MCP server's own `POLYTOKEN_AGENT_NAME` is only a default. The worker prompt and the spawn prompt both instruct workers to pass `caller="<name>"`; without it, a worker's posts and read cursors are attributed to the coordinator. The coordinator's identity comes from its MCP `env`.

---

## Model selection

A member's `model` is a **request**, not a literal selector. `swarm(action="add", model=...)`
records it on the roster, and `WorkerProcessManager` resolves it once per spawn into a value
`polytoken exec --model` accepts:

| Request | Runs on |
|---|---|
| unset, `default`, `auto` | the member's role tier: `@mg:polytoken:default_model_full` for workers and verifiers, `@mg:polytoken:default_model_mini` for the monitor and narrator |
| `full`, `mini`, `nano` | that reserved Polytoken tier group |
| `session` | the model this Polytoken session is running, as recorded by the hook below; the role tier when nothing is recorded |
| anything else | passed through unchanged: a model name, a `name(level)` reasoning variant, or a `@mg:<group>` group reference |

The roster keeps both halves: `model`/`desired_model` is the request and `applied_model`
is the selector the process launches with. `polytoken exec` rejects the `default` sentinel
("model 'default' is not configured"), so a member with no resolved model starts without
`--model` at all. `swarm(action="models")` lists the models this install can run and
repeats these rules under `selection`.

MCP stdio children start with a cleared environment, so the swarm server cannot see which
model the session is running. A hook records it instead: `python3 -m pt_swarm.model_hook`
writes `POLYTOKEN_MODEL_NAME` into `<swarm_dir>/session_model.json`. `hooks.json` is
environment-local (it is gitignored here), so add the block to `.polytoken/hooks.json` in
the project or to `~/.config/polytoken/hooks.json` for a global install:

```json
[
  {
    "name": "swarm-session-model",
    "event": "session_start",
    "handler": { "bash": "python3 -m pt_swarm.model_hook; echo '{\"outcome\":\"allow\"}'" }
  },
  {
    "name": "swarm-session-model-switch",
    "event": "pre_user_prompt",
    "handler": { "bash": "python3 -m pt_swarm.model_hook; echo '{\"outcome\":\"accept\"}'" }
  }
]
```

Without the hook everything still works — `session` just falls back to the role tier.

---

## Quickstart Integration Example (Python)

```python
from pt_swarm.harness_adapter import PolytokenSwarmAdapter

# 1. Initialize the adapter with your workspace path
adapter = PolytokenSwarmAdapter(workspace_path="./my_project", swarm_id="swarm_01")

# 2. Provide the coordinator agent with swarm tools
coordinator_tools = adapter.get_agent_tools("coordinator")
coordinator_prompt = adapter.load_system_prompt("coordinator")

# 3. Mount coordinator_tools into your agent loop.
# When the coordinator calls swarm(action="add", name="parser", brief="..."),
# adapter.on_worker_spawn_requested() provisions the workspace and configuration.
```

---

## Installation & Setup

### Option A: Single Project (Workspace-Local)

1. **In this repository**: Polytoken auto-discovers `.polytoken/config.yaml`, `.polytoken/subagents/`, and `.polytoken/skills/` out of the box. The server runs as `python3 -m pt_swarm.mcp_server`.
2. **In another project**: Install the package (`pip install /path/to/pt-swarm`) and copy this repository's `.polytoken/` into that project's root, updating `command`/`args` in `.polytoken/config.yaml` to `pt-swarm-mcp` (or keep `python3 -m pt_swarm.mcp_server` with the package installed).

### Option B: Global Installation (Available in Any Project)

1. **Install the Python package & CLI entrypoints**:
   ```bash
   pip install --user .
   # Homebrew and most distro Pythons are PEP 668 "externally managed" and
   # reject a bare `pip install .`:
   #   pip install --user --break-system-packages .
   # `--user` keeps the install in ~/.local and leaves the interpreter's own
   # site-packages untouched. Isolated alternative: `pipx install .`.
   ```
   This registers `pt-swarm-mcp` (`pt_swarm.mcp_server:main`) and `pt-swarm-cli` (`pt_swarm.cli:main`) on your `PATH`, and installs the role prompts, tool schemas, and example config inside the package.

2. **Add the MCP server to your global Polytoken config** (`~/.config/polytoken/config.yaml`):
   ```yaml
   mcp_servers:
     swarm:
       transport: stdio
       command: pt-swarm-mcp
       env:
         POLYTOKEN_SWARM_DIR: "./.swarm/default"
         POLYTOKEN_AGENT_NAME: "coordinator"
   ```
   Polytoken launches stdio MCP servers with the **project directory as the working directory**, so `./.swarm/default` isolates each workspace's board to that project.

   Because this entry sits in the *global* config, every project now gets a swarm: a session creates `.swarm/default/` lazily on first use. To enable it in selected projects only, put the same block in that project's `.polytoken/config.yaml` instead.

3. **Environment variables must be declared**: MCP stdio children start with a *cleared* environment. Only `HOME`, `PATH`, `LANG`, and `TMPDIR` are passed by default; anything else (for example `POLYTOKEN_WORKER_CMD` or `POLYTOKEN_BIN`) must appear under `env:` or `pass_env:` for that server. Variables exported in your shell are not visible to the server.

4. **Copy subagents & skills globally** (prefer copying over symlinking so the global setup survives moving or deleting this checkout):
   ```bash
   mkdir -p ~/.config/polytoken/subagents ~/.config/polytoken/skills
   cp subagents/*.md ~/.config/polytoken/subagents/
   cp -r skills/* ~/.config/polytoken/skills/
   ```
   Polytoken discovers global definitions in `$XDG_CONFIG_HOME/polytoken/subagents/` and `$XDG_CONFIG_HOME/polytoken/skills/` (usually `~/.config/polytoken/...`). Keep the MCP server named `swarm` so the `mcp__swarm__*` tool references in the subagent manifests resolve.

5. **Install the Web UI globally** (optional):
   ```bash
   npm pack
   npm install -g --prefix ~/.local ./pt-swarm-1.0.0.tgz
   rm -f pt-swarm-1.0.0.tgz
   # run it with:
   POLYTOKEN_SWARM_DIR=./.swarm/default pt-swarm-ui
   ```
   The dashboard binds `127.0.0.1:5388` by default; override with `HOST` and `PORT`.

   Two packaging details matter: installing from the folder (`npm install -g .`) creates a *symlink* back to this checkout, so the global command breaks if the checkout moves — install the packed tarball for a self-contained copy. And when npm's default prefix is not writable (common with Homebrew or distro Node), pass `--prefix ~/.local`; its `bin/` directory must be on `PATH`.

6. **Worker process spawning**:
   `WorkerProcessManager` launches workers with `polytoken exec "<prompt>"`, which needs `polytoken` on `PATH` (passed through by default). To use a different command, set `POLYTOKEN_WORKER_CMD` in the MCP server's `env:`, for example:
   ```yaml
   env:
     POLYTOKEN_WORKER_CMD: "polytoken exec {prompt}"
   ```
   Supported placeholders: `{prompt}`, `{name}`, `{model}`, `{workspace}`, `{swarm_dir}`.
   `{model}` is the member's resolved selector (see *Model selection*). When a template puts
   `{model}` after a `--model`/`-m` flag and nothing resolved, both tokens are dropped, so
   `polytoken exec --model {model} {prompt}` degrades to `polytoken exec <prompt>` instead of
   failing on an invalid model name.
   A worker that exits during startup is reported as a failed spawn (`spawned: false` with its exit code and log tail) rather than a running agent.

7. **Uninstall**:
   ```bash
   pip uninstall pt-swarm
   npm uninstall -g --prefix ~/.local pt-swarm
   rm -rf ~/.config/polytoken/skills/{native_swarm,swarm_monitor,swarm_narrator}
   rm -f ~/.config/polytoken/subagents/swarm-{coordinator,monitor,narrator,worker}.md
   # finally, remove the `mcp_servers.swarm` block from the global config
   ```

---

## Running the MCP Server, UI & Tests

```bash
# Start the MCP stdio server (configured automatically by .polytoken/config.yaml)
POLYTOKEN_SWARM_DIR=./.swarm/default POLYTOKEN_AGENT_NAME=coordinator \
  python3 -m pt_swarm.mcp_server

# Watch the board in a terminal
python3 -m pt_swarm.cli --swarm-dir ./.swarm/default --watch

# Launch the live web dashboard (http://127.0.0.1:5388/)
POLYTOKEN_SWARM_DIR=./.swarm/default node sidecar_ui/main.mjs

# Run all tests (35 Python unittest + 2 Node.js tests)
python3 -m unittest discover -s tests -v
node --test tests/test_sidecar_ui.mjs
```

Notes:
- Use `python3`; many systems do not provide a `python` alias.
- The dashboard accepts `POLYTOKEN_SWARM_DIR` (direct board directory), `SWARM_CONVERSATION_ID`, `HOST`, and `PORT`. Without `POLYTOKEN_SWARM_DIR` it falls back to `./.swarm/default`.
- `.polytoken/hooks.json` and `.polytoken/context.py`, if present in a checkout, are environment-local session hooks; they are git-ignored and are not part of the distributable package.

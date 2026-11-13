# Spec: running swarm members as Polytoken subagents

Status: phase 1 implemented (delegated `add` + `bind`); phase 2 open.
Origin: findings #18.1, #18.6, #18.7, #18 (minors), #20.1 of the 2026-11-13 review, and a live
10-member swarm in which every worker died within two minutes and the roster reported them all as
`RUNNING`.

## The problem, measured

`WorkerProcessManager.spawn_or_wake()` launches `polytoken exec --model <m> <prompt>`. Verified
against the installed CLI:

- `polytoken exec` runs **one** turn and exits. There is no resume flag.
- `polytoken continue` resumes a session but attaches the TUI, so it cannot be driven from a
  supervisor.
- Therefore a member survives only while it keeps calling tools. The first turn that ends without
  a tool call exits the process — and `swarm_wait` **always** returns within `timeout_seconds`
  (300 s), which manufactures exactly that turn.
- `add_member` mints a `conversation_id` (`board_manager.py:524`) that no code ever reads. The
  roster advertises conversation continuity the launcher cannot deliver.
- `phase` is hard-coded `RUNNING` at add time (`board_manager.py:526`), so a dead member reads as
  live. A zombie passes `is_running()` (`worker_runner.py:155-170`): `kill(pid, 0)` succeeds and
  `/proc/<pid>/stat` field 22 keeps the original start marker.

Net effect: every wake is a **cold** conversation, the coordinator must re-drive each member by
name, and results arrive only if someone polls the board.

## Non-goals

- **Polytoken plugins.** Not available yet; this is why the MCP server cannot reach the harness's
  subagent machinery from its own process, and why spawning moves up one level instead.
- **Replacing the board.** The append-only board stays: peer-to-peer handoff, the audit trail, and
  portability to harnesses without subagents.
- **Windows/POSIX process handling.** The external spawner keeps working unchanged for other
  harnesses; this spec adds a path, it does not remove one.

## Design

### Responsibility split

| Component | Owns | Cannot do |
|---|---|---|
| MCP server (`pt_swarm/mcp_server.py`, `swarm_tools.py`, `board_manager.py`) | board, roster, policy, validation | call `subagent` — separate process |
| Coordinator session | execution: `subagent`, `message_subagent`, `resume_from`, `job_status` | be replaced by the server |

The server records *intent to spawn*; the coordinator performs the spawn. That is the whole change
in one sentence.

### Lifecycle

```
coordinator                MCP server                    board
    |                          |                            |
    |-- swarm add(name,brief)->|                            |
    |                          |-- roster entry ----------->|
    |                          |   phase="delegated"        |
    |                          |-- brief post ------------->|  #N
    |<-- {spawned:false,        |                            |
    |     mode:"delegated",     |                            |
    |     delegation:{...}} ----|                            |
    |                          |                            |
    |-- subagent(swarm-worker, prompt=delegation.prompt) --> job handle
    |-- swarm bind(name, job_id) ->|                        |
    |                          |-- phase="running" -------->|
    |                          |                            |
    |                          |<--- member posts (peer handoff)
    |<-- completion notification (starts a coordinator turn) |
    |-- swarm bind(name, job_id, state="done") ->|           |
    |                          |-- phase="done" ----------->|
```

**Waking a member.** If the job is live: `message_subagent(job_id, text)` — delivered at its next
iteration boundary, context intact. If the job is terminal: `subagent(..., resume_from=job_id)` —
a new job forked from the prior conversation, so the member keeps everything it learned.

### State model

`phase ∈ {delegated, running, done, failed, killed}`

- `add` writes `delegated` — a state that cannot be mistaken for running work.
- `bind` writes `running` / `done` / `failed` under the board lock.
- `kill` writes `killed`; killed names stay reserved (existing invariant, unchanged).
- `job_id` is stored on the roster entry; it is the member's identity for wake and resume, and it
  replaces the unused `conversation_id` as the thing that means "this member".
- Anything that would report a member as running must be reproducible from the roster, not inferred
  from a PID.
- `status` adds `liveness: "unverified"` to a member that has a `job_id` in `running` — the one claim
  the server cannot check for itself. Implemented in `swarm_tools.py` and pinned by
  `test_status_marks_bound_running_members_unverified`.

### Wire contract

`swarm(action="add", ...)` with no process spawner available returns:

```json
{
  "status": "added",
  "name": "verifier",
  "spawned": false,
  "mode": "delegated",
  "delegation": {
    "subagent_type": "swarm-worker",
    "name": "verifier",
    "prompt": "<the initial input the process spawner would have used>"
  }
}
```

`prompt` is the same string the external spawner builds today (identity hint + "You have just
started. Your assignment (recorded on the board as #N for @name):" + brief). One renderer, two
transports — `worker_runner` and the delegation result must not drift.

Sharded adds (`n`, `tasks`) return one delegation entry per shard member.

`swarm(action="bind", name="verifier", job_id="general-purpose:verifier", state="running")`:

```json
{"status": "bound", "name": "verifier", "job_id": "general-purpose:verifier", "phase": "running"}
```

Unknown member, unknown state, or empty `job_id` → a validation error, never a traceback.

### Subagent definition

`subagents/swarm-worker.md` must grant the tools a member needs to *do* work — file read/write/edit,
grep, glob, shell, todo — plus the four `mcp__swarm__*` board tools. A native member that can only
post is the same failure in a different costume: the first attempt at this review produced ten
members that could not open a file. Definition must keep passing
`polytoken validate subagent swarm-worker`.

### Failure modes and what the system does

| Failure | Required behaviour |
|---|---|
| Coordinator session restarts | jobs die. Roster still says `running` for bound members → `status` must not claim liveness the coordinator cannot confirm; treat `job_id` with no live job as unknown, not running. |
| `bind` called twice | idempotent update of `job_id`/`phase`; last write wins under the lock. |
| Member finishes without posting | the completion notification is the report; coordinator folds the job's final output into a board post so peers can see it. |
| A member parks (`swarm_wait`) | harmless in this model — it costs a blocked tool call in a live job, and the job outlives it. The one-shot death disappears. |
| No coordinator (plain MCP client) | `add` falls back to the external spawner exactly as today; if neither exists, `spawned:false, mode:"unconfigured"` with the existing hint. |

### Testing

TDD, per `AGENTS.md`. Existing suites must stay green
(`python3 -m unittest discover -s tests`, `node --test tests/test_sidecar_ui.mjs`).

Cases to pin:

1. `add` with no spawner returns `mode:"delegated"` + a `delegation` whose `prompt` is byte-identical
   to what the external spawner would pass.
2. `add` with a spawner configured is unchanged (regression guard).
3. `bind` sets `phase` and `job_id` under the lock; repeated `bind` updates; unknown member errors.
4. New members read `delegated`, never `RUNNING`, before a `bind`.
5. Sharded `add` returns one delegation per shard.
6. Schema parity: `TOOLS`, `tool_schemas/swarm.json`, `swarm_tools.py` and `schemas/*.md` agree on
   `bind`, `job_id`, and the phase enum.
7. `subagents/swarm-worker.md` validates and lists the work tools.

## Migration

- No on-disk format break: `members.json` gains optional `job_id` and a new `phase` value; readers
  tolerate their absence (legacy rosters keep working, as with plain-integer pid files).
- The board format is untouched.
- The external spawner stays the default when `polytoken` is on PATH; delegated mode is chosen
  explicitly or when nothing can spawn.

## Decisions

1. **Reconciliation — the board reports uncertainty; it never infers liveness.** The server cannot
   query `job_status`, so `swarm(action="status")` marks any member that has a `job_id` and
   `phase: "running"` with `"liveness": "unverified"`. Every other phase is stated plainly, because
   someone reported it: `delegated`, `done`, `failed`, `killed`.
   *Rejected:* relying on the coordinator to restate state each turn — that swaps a hard-coded lie
   for a remembered one, and rots the first time a turn is skipped.
2. **Helpers — a worker asks the coordinator.** A member that wants to subdivide its work posts
   `@coordinator helper request: <brief>` and carries on; the coordinator calls `add` and spawns it.
   *Rejected:* dropping helper spawning in delegated mode — the external-spawn path still has the
   capability, and a `swarm-worker` subagent has no `subagent` tool, so the coordinator is the only
   party that can honour it. No code is required for this; it is a documented convention.
3. **The board post is the report of record.** A member's findings go on the board, addressed
   `@coordinator`, exactly as the brief says. The job's final message is transport, not a second
   report: when the member posted, the coordinator ignores it; when the member finished without
   posting, the coordinator posts the job's final message to the board under that member's name so
   the record stays complete. *Rejected:* treating the job result as the deliverable — it wakes
   exactly one reader, leaves peers nothing to cite by `#seq`, and disappears when the session does.

## Open questions
1. **Cost accounting.** Ten native subagents are ten background jobs in one session with no PID
   bookkeeping; the concurrency ceiling becomes the harness's limit, not the swarm's.

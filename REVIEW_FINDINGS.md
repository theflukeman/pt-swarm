# Code review findings

## Scope, method, and totals

Reviewed `pt_swarm/board_manager.py`; worker spawning, model resolution, workspaces, stall monitoring, and adapter files; the MCP server, tool dispatch, and schemas; the dashboard server and SDK; dashboard front end; and tests, documentation, and packaging. Six reviewers examined disjoint file sets. Two independent verifiers re-ran the reported mechanisms; findings below use verifier-corrected triggers, repros, and severities rather than taking the reports on trust.

Baseline: `46bc6d85bb7ee95d8e1727b8076df7a38d07cb84` (`git rev-parse HEAD`). The in-flight native-subagent change shifts some `pt_swarm/` line numbers; cited lines are those in the review reports and may need adjustment against the current working tree.

**73 listed entries:** 1 critical, 12 major, 35 minor, 25 nit. This includes 72 confirmed unique findings plus one separately marked unproven timing nit. The corrected core verifier tally (#24) plus UI verifier tally (#29) is 73 confirmed before deduplication; it counts the repeated member-name root cause (#18 finding 5 / #19 finding 3) twice. After that duplicate is removed, the verified total is 72; the table’s 73 entries include the unproven nit so the table reconciles to every finding row.

| Area | Critical | Major | Minor | Nit (verified) | Unproven nit | Total entries |
|---|---:|---:|---:|---:|---:|---:|
| Board / roster | 0 | 3 | 5 | 5 | 0 | 13 |
| Worker lifecycle | 1 | 6 | 7 | 5 | 0 | 19 |
| MCP tool layer | 0 | 0 | 7 | 5 | 0 | 12 |
| Dashboard server | 0 | 1 | 2 | 4 | 0 | 7 |
| Dashboard front end | 0 | 0 | 5 | 3 | 0 | 8 |
| Tests / docs / packaging | 0 | 2 | 9 | 2 | 1 | 14 |
| **Total** | **1** | **12** | **35** | **24** | **1** | **73** |

## Findings

### Board / roster

**B-01 — Major — `pt_swarm/board_manager.py:486-489, 337, 367`**
`read_members()` catches any exception and substitutes an empty roster; a later persist can overwrite the existing roster and title. Trigger: truncate `members.json` to an incomplete object (or cause another read error such as permission/descriptor exhaustion), then post or otherwise persist; the file becomes `{"members": {}}`. **Fix:** do not convert read failures into writable empty state; surface the error and preserve the original file until recovery.

**B-02 — Major — `pt_swarm/board_manager.py:462`**
A channel-filtered read can advance the global cursor past messages the caller has not read. Trigger: reader at cursor 0, main messages #1–#5 and `#qa` #6; reading `#qa` returns [6] and moves the cursor to 6, so catch-up since the cursor returns no missed messages. **Fix:** do not advance a global cursor based on a partial channel view; track visibility/read state consistently.

**B-03 — Major — `pt_swarm/board_manager.py:317, 333, 391`**
A torn trailing JSONL line can be merged with the next post, while sequence allocation only considers parsed messages, causing duplicate sequence numbers and loss of the first post. Trigger: tear the last line after #3 and append another message; two authors are told seq 3 and the first seq-3 post disappears. **Fix:** handle incomplete tail records before append and allocate monotonically from a durable sequence source.

**B-04 — Minor (downgraded from major) — `pt_swarm/board_manager.py:45-52`**
If lock acquisition raises, the process-local `RLock` remains held while depth stays zero and no file handle exists. Trigger: replace the lock path with a directory, or encounter a filesystem acquisition failure; later operations fail and auxiliary threads (including stall monitoring) can block. The verifier found same-thread calls raise the filesystem error rather than deadlock, and tool-call exceptions are contained, so the originally claimed server-wide stop is overstated. **Fix:** release the `RLock` in an exception-safe acquisition path.

**B-05 — Minor — `pt_swarm/board_manager.py:122-146, 663`**
The coordinator has no `channels` key, and subscription treats missing/empty channels as an empty list rather than its effective all-channel membership. Trigger: subscribe coordinator to `qa`; stored membership becomes `['qa']`, replacing effective `['*']`. **Fix:** seed the prior effective channel set before applying subscription changes.

**B-06 — Minor — `pt_swarm/board_manager.py:439-442`**
Reading explicit sequence IDs bypasses channel visibility and malformed IDs can raise. Trigger: an outsider uses `seqs=[2]` to retrieve an invisible “classified” message; `seqs=["abc"]` raises `ValueError`. **Fix:** apply the normal visibility predicate and validate/coerce sequence inputs with a controlled error.

**B-07 — Minor — `pt_swarm/board_manager.py:407-409`**
Python visibility uses the member’s current channel set, unlike dashboard replay’s per-message historical membership. Trigger: subscribe at #3, unsubscribe at #5, then read #6 on `#qa`; dashboard sees the earlier message that Python’s current-set reader drops. **Fix:** make both readers use the same historical visibility rule, or document and consistently apply another rule.

**B-08 — Minor — `pt_swarm/board_manager.py:287, 632`**
An archived channel cannot be recreated: creation sees its existing name and reports `{"status":"exists"}`, without an unarchive operation. Trigger: archive a channel, then create it again. **Fix:** allow explicit unarchive/recreation or return a clear archived-state response and operation.

**B-09 — Nit — `pt_swarm/board_manager.py:85`**
POSIX `flock` has no timeout and can block indefinitely. Trigger: another process holds the lock indefinitely. **Fix:** add a bounded lock wait or document the blocking behavior.

**B-10 — Nit — `pt_swarm/board_manager.py:374-377`**
Exceptions from `on_member_mentioned` are swallowed with `except Exception: pass`. Trigger: mention callback fails; the caller gets no indication. **Fix:** report the exception to stderr or an appropriate diagnostic channel.

**B-11 — Nit — `pt_swarm/board_manager.py` (`read_raw_messages`); `pt_swarm/cli.py:44,53`; `pt_swarm/stall_monitor.py:49`**
`read_raw_messages()` is called without the board lock by CLI and stall-monitor paths and only catches JSON decode errors. Trigger: concurrent mutation or a non-decode I/O/Unicode error during raw read. **Fix:** use the shared locking/read path and handle expected I/O errors explicitly.

**B-12 — Nit — `pt_swarm/board_manager.py` board read/write paths**
Board operations reparse the board in O(board size) work. Trigger: each operation on a growing board repeats the parse. **Fix:** use an appropriate index/cache or otherwise avoid repeated full-board parsing.

**B-13 — Nit — `pt_swarm/board_manager.py:29`**
Python’s `re.UNICODE` `\w` mention-name rule differs from the dashboard JavaScript name-rune rule; measured examples `²` and `⅛` match Python but not JavaScript. Trigger: names containing those characters yield different mention parsing. **Fix:** align the accepted character sets.

### Worker lifecycle

**W-01 — Critical — `pt_swarm/worker_runner.py:42-46, 167`**
A zombie process is treated as a live worker, preventing a replacement from starting; a legacy PID or unavailable `/proc` also fails open as alive for any live PID. Trigger: zombie child PID with unchanged `/proc/<pid>/stat` start marker; `is_running()` returns true and `spawn_or_wake()` reports `already_running`. Verifier observed 8 of 10 PID files pointing to Z-state processes whose parent MCP server remained alive. **Fix:** detect zombie state and fail safely when the start marker cannot be validated; reap children where possible.

**W-02 — Major — `pt_swarm/stall_monitor.py` (alert-count/cap logic; line not specified in report)**
The monitor posts the same unaddressed-post alert on every tick because its cap is keyed by a sequence the monitor itself advances. Trigger: five ticks produced six messages, including a fresh alert on each tick. **Fix:** track the original post/alert identity so posting the alert does not reset the cap.

**W-03 — Major — `pt_swarm/worker_runner.py:335-338`**
Kill checks only whether a PID is alive and discards its recorded start marker; it does not escalate to SIGKILL but reports success. Trigger: stale PID file followed by PID reuse; kill can send SIGTERM to an unrelated process. The verifier retained major severity because stale PID files were observed and the consequence is signaling an unrelated process. **Fix:** validate the recorded start marker before signaling and report actual termination outcome.

**W-04 — Major — `pt_swarm/worker_runner.py:181,185,256`; `pt_swarm/mcp_server.py:218-231`**
The worker-manager lock is process-local, and the mention callback’s running-check and spawn are not protected by an inter-process lock. Trigger: two server processes handle concurrent mentions; both can see no worker and spawn. The verifier confirms the race, with a window of milliseconds for `inherit` and potentially seconds for `share`/`branch`; workspace provisioning can also raise `FileExistsError` before the local `try` and be swallowed by the mention callback. **Fix:** make reservation/check-and-spawn atomic across processes and handle provisioning failure explicitly. *Deduplication:* #19’s cross-area note describes this same race; it is not a second finding.

**W-05 — Major — `pt_swarm/swarm_tools.py:166,181-182`; `pt_swarm/worker_runner.py:127,254`; `pt_swarm/workspace_manager.py:42,108`**
Member names are not validated and are used verbatim as filesystem path components. Trigger: add `../../escape`; it is accepted and produces a PID path containing traversal, allowing provisioning outside the intended directory. **Fix:** validate names against a safe, documented identifier grammar before persistence or path construction. *Deduplication:* this is also #19 finding 3; listed once here.

**W-06 — Major — `pt_swarm/mcp_server.py:220-223`; `pt_swarm/harness_adapter.py:120-123`**
The worker prompt tells a one-shot `polytoken exec` worker to wait/park for messages after its task; it exits but the roster remains RUNNING. Trigger: worker completes, follows “wait for messages when you have nothing left to do,” and exits. **Fix:** remove the parking clause for one-shot workers or only use it where the process can be woken again.

**W-07 — Major — `pt_swarm/stall_monitor.py:68-70`; `pt_swarm/board_manager.py:138`**
Stall detection is disabled while any member has `wake_mode == "working"`; the coordinator defaults to that mode and `wait_for_event()` restores it. Trigger: ordinary active coordinator state; monitor concludes there is a live worker and suppresses detection. Verifier observed coordinator, narrator, monitor, and both verifiers marked working, including dead members. **Fix:** exclude coordinator/dead workers and base suppression on actual live worker state.

**W-08 — Minor — `pt_swarm/swarm_tools.py` (status handler; line not specified in report)**
Status returns roster entries verbatim with `phase` set to RUNNING at add time rather than reflecting current process status. Trigger: exited/zombie worker remains reported RUNNING. **Fix:** derive status from process lifecycle or update it on exit.

**W-09 — Minor — `pt_swarm/worker_runner.py:267-268`**
The 0.25-second spawn grace sleep occurs while holding the adapter-wide lock; it is too short to cover a real `polytoken exec` startup/config-load failure, so the `spawned:false` path is mostly dead in practice, while every spawn blocks `is_running` and `kill` during the sleep (about 2.5 seconds for a 10-member sharded add, plus provisioning). **Fix:** use a grace period that covers real startup and observe child exit without holding the bookkeeping lock.

**W-10 — Minor — `pt_swarm/worker_runner.py` (`kill_all`)**
`kill_all` calls `kill_worker(name)` with default `inherit`, making workspace cleanup a no-op for other workspace modes. Trigger: kill-all for share/branch workers. **Fix:** pass each member’s configured workspace mode.

**W-11 — Minor — `pt_swarm/workspace_manager.py:90-108`**
Workspace cleanup discards commits (`git branch -D` for worktrees and directory removal for clones). Trigger: kill/cleanup a worker with unmerged work. **Fix:** preserve or report unmerged work before destructive cleanup.

**W-12 — Minor — `pt_swarm/workspace_manager.py:27-33,49-73`**
Git and repository-detection calls do not catch `OSError`. Trigger: git executable unavailable or process creation fails. **Fix:** handle `OSError` and return a controlled fallback/error.

**W-13 — Minor — `pt_swarm/model_hook.py:29-31`; `.polytoken/hooks.json`; `pt_swarm/worker_runner.py:238`**
The model hook overwrites shared `session_model.json` per worker; the hook is wired for session-start and pre-user-prompt, and workers share `POLYTOKEN_SWARM_DIR`. Trigger: different workers start/use different model selection and overwrite the common file; currently masked when members use one model. **Fix:** store model state per worker/session or avoid shared-file writes.

**W-14 — Minor — `pt_swarm/config.example.json:6-9`; `pyproject.toml:36`**
The example config ships in the wheel but its `default_model`, `monitor_interval_seconds`, and `enable_sidecar_ui` settings have no reader. The hardcoded `gemini-3.8-flash` contradicts role-tier default model resolution; `swarm_dir` (`./.swarm`) differs from adapter `.swarm/<swarm_id>`, interval 180 differs from monitor 60, and role list omits verifier. Trigger: reader configures these values expecting them to control runtime, but they are unused/misleading. **Fix:** remove unused keys or wire them up, and remove the hardcoded model name.

**W-15 — Nit — `pt_swarm/stall_monitor.py`**
`"any"` appears in parked modes. Trigger: parked-mode configuration checks this value. **Fix:** use the supported wake-mode value or document the alias.

**W-16 — Nit — `pt_swarm/stall_monitor.py`**
`stop()` joins for 2 seconds while the monitor can be sleeping for 60 seconds. Trigger: stop during the long sleep; thread may outlive the join. **Fix:** make the wait interruptible or align the join timeout.

**W-17 — Nit — `pt_swarm/worker_runner.py` log handling**
Logs are append-only and read with `read_text()[-800:]`, so the full file is read to obtain a short tail. Trigger: large accumulated worker log. **Fix:** read a bounded tail efficiently.

**W-18 — Nit — `pt_swarm/harness_adapter.py:80-82`**
An empty prompt is substituted when prompt content is missing. Trigger: missing/empty role prompt silently launches without instructions. **Fix:** reject it or emit a diagnostic and use an explicit fallback.

**W-19 — Nit (partly unproven) — `pt_swarm/worker_runner.py:239`**
`POLYTOKEN_INITIAL_PROMPT` is set in the child environment and no reader exists in this repository; whether Polytoken itself reads it is unproven. **Fix:** confirm external consumer or remove the redundant variable.

### MCP tool layer

**T-01 — Minor (downgraded from major) — `pt_swarm/mcp_server.py:316-319`**
A non-object JSON request kills the server loop. Trigger: send scalar/array JSON (the verifier observed child exit 1, `AttributeError` on `.get`, and a trailing ping unanswered). The mechanism is confirmed, but the verifier downgraded impact: this deployment’s Polytoken client does not send those forms, and MCP 2025-06-18 removed JSON-RPC batching; it is a malformed-input robustness issue, not a release-blocking cross-agent outage. **Fix:** validate the decoded request shape and continue serving after invalid input.

**T-02 — Minor (downgraded from major) — `pt_swarm/mcp_server.py:312, 281-282`; `pt_swarm/swarm_tools.py:378-383`; `pt_swarm/board_manager.py:707`**
`swarm_wait` blocks the stdin request loop. Trigger: a 3-second wait followed by ping answered the ping only after 3.05 seconds. Verifier observed separate MCP server processes per `polytoken exec` session; cross-agent freeze requires several callers sharing one process, though parallel calls in one session are delayed. **Fix:** isolate long waits from request dispatch or otherwise permit concurrent request processing.

**T-03 — Minor — `pt_swarm/swarm_tools.py:191-241`**
A sharded add that fails partway returns a bare error after creating some members, leaving the group incomplete and retry impossible. Trigger: an existing `grp-2` causes first attempt to leave `grp-1` and `grp-2`; retry then fails on `grp-1`. **Fix:** make shard creation atomic or return the successfully created members and support recovery.

**T-04 — Minor — `pt_swarm/swarm_tools.py:333-343`; `pt_swarm/board_manager.py:328-329`**
`post_message` accepts mis-cased/invalid channel strings. Trigger: post to `Main`; normal default read and `channel="main"` do not show it, while exact-case `Main` does. **Fix:** validate/normalize channel names.

**T-05 — Minor — `pt_swarm/swarm_tools.py:100-103, 303-307`**
Omitted parameters silently apply destructive defaults: title clears the swarm title and model resets a member to its role tier. Trigger: invoke title/model action without the value. **Fix:** require the parameter for mutating actions or distinguish omitted from explicit empty/default.

**T-06 — Minor — `pt_swarm/swarm_tools.py:81,334,353,375`; `pt_swarm/tool_schemas/*.json`**
Unknown arguments are swallowed and reinterpreted as the server identity; schemas do not set `additionalProperties: false`. Trigger: `handle_read_board(agent='w1')` returns messages and advances coordinator cursor 0→3, leaving w1 at 0; workspace-mode argument to add is ignored and stored as inherit. **Fix:** reject unknown arguments and align handler identity with explicit caller data.

**T-07 — Minor — `pt_swarm/mcp_server.py:268-302`**
Parameter errors are surfaced as raw Python exception text instead of JSON-RPC invalid-params errors. Trigger: omit required text/since or pass invalid sequence/limit; internal method names and conversion errors appear in tool output, and `limit=-1` silently drops the newest result with `isError:false`. **Fix:** validate parameters and return standard, sanitized error responses.

**T-08 — Nit — `pt_swarm/mcp_server.py:316-319, 191, 250`**
Three framing conformance gaps: unparseable lines are dropped without `-32700`; JSON-RPC 1.0 envelopes receive a 2.0 response instead of `-32600`; explicit `id:null` is treated as a notification and receives no response. Trigger: send each of those request forms. **Fix:** implement the relevant parse/invalid-request responses and distinguish absent ID from null ID.

**T-09 — Nit — `pt_swarm/tool_schemas/*.json`; `pt_swarm/mcp_server.py:TOOLS`; `tests/test_cli_and_schemas.py:63-71`**
Schema and advertised tool definitions differ in 18 description fields; current parity test does not detect description drift. Types, enums, defaults, items, properties, and required lists match. **Fix:** compare descriptions as well as structural schema fields.

**T-10 — Nit — `pt_swarm/tool_schemas/read_board.json:24`; `pt_swarm/mcp_server.py:142`**
`channel="*"` is documented as reading all channels, but it behaves like omission and reads only the caller-visible stream while advancing cursor to global head. Trigger: caller uses `*` with an invisible message in the board. **Fix:** implement the documented meaning or correct the schema text.

**T-11 — Nit — `schemas/board_jsonl.md:18-30`; `schemas/members_json.md`**
On-disk format docs have drift: example includes `channel:"main"` and `control:null` though writer omits them; control-op list omits create/archive/unsubscribe; `#<seq>` cross-reference is not parsed; channel-registry format is undocumented; members spec omits `fork`. **Fix:** update examples and cover emitted fields/operations.

**T-12 — Nit — `pt_swarm/swarm_tools.py:174-192`; `pt_swarm/tool_schemas`**
When `tasks` is supplied it overrides `n` without checking disagreement, and neither count is bounded. Trigger: tasks length 2 with n=5 creates only two; n=100000 attempts 100,000 adds/posts/spawns synchronously. **Fix:** reject disagreement, cap count, and report created members.

### Dashboard server

**D-01 — Major — `sidecar_ui/main.mjs:113,333-334,394-410,509-514,689-713,1288-1290`; `sidecar_ui/sidecar.json:4-8`**
The dashboard looks for transcripts and `brain/` under app data, but this port supplies the swarm directory and does not create that layout. Trigger: run without `brain/`; allowed files and transcript-derived steps/activity are absent and members fall back to `wake_mode`, so a dead worker can display as working. Adding the expected layout makes the file/transcript features appear. **Fix:** adapt the data paths to this port’s layout or implement/provide the expected data sources.

**D-02 — Minor — `sidecar_ui/main.mjs:898-931` (`raw` at 928)**
Full board and members text is serialized in every `/api/swarm` response even when Raw tab is unused. Trigger: polling a large board; a 20 MB post yielded a 41.9 MB response, approximately twice board size. **Fix:** omit raw text unless requested and/or bound response payloads.

**D-03 — Minor — `sidecar_ui/sidecar_sdk/index.mjs:39-73`; `sidecar_ui/main.mjs:70,901-902`; README:352**
The server has no Host/Origin or CSRF check, and environment can override the loopback bind; README’s CSRF-token instruction is not implemented. Trigger: request `/api/swarm` with `Host: evil.example:58731`; verifier received 200 and full snapshot. Response also contains absolute `appData` and `dir`. **Fix:** enforce host/origin or CSRF protections and constrain/document bind overrides.

**D-04 — Nit — `sidecar_ui/sidecar_sdk/index.mjs:64-67`**
Static-client error response exposes the underlying filesystem error and absolute path. Trigger: remove `index.html`; request returns 500 with an `ENOENT` path. **Fix:** log details server-side and return generic client text.

**D-05 — Nit — `sidecar_ui/main.mjs:112-128,245,1290`**
`findAppDataDirs()` is repeated per request/member and checks `~/.gemini` plus product directories, though this port takes the standalone branch. Trigger: repeated requests incur repeated filesystem scans. **Fix:** cache product discovery or skip it when `POLYTOKEN_SWARM_DIR` is set.

**D-06 — Nit — `sidecar_ui/main.mjs:1407`; `sidecar_ui/README.md:125-134`**
Frame endpoint requires `view.reel.frames`, absent from the closed documented `view.json` schema and frame documentation. Trigger: narrator follows README and cannot provide a frame. **Fix:** document the reel field in the schema and frame section.

**D-07 — Nit — `sidecar_ui/README.md:347-358`**
Develop instructions name obsolete Antigravity environment variables and SDK loader invocation; this port runs `node sidecar_ui/main.mjs` with `POLYTOKEN_SWARM_DIR`/`HOST`/`PORT`. **Fix:** update the Develop section to match the port.

### Dashboard front end

**F-01 — Minor — `sidecar_ui/app.js:1135,1426,1436`; README:228-250**
The narrator page is opt-in using `?narrator=frame`, while README describes `?narrator=plain` as the opt-in; with built-in Overview the toggle is hidden. Trigger: default or `?narrator=plain` shows plain view and no iframe; only `?narrator=frame` renders it. **Fix:** choose and document one contract. Verifier notes the code’s plain-only-under-built-in-Overview behavior may be deliberate; the stale README claim remains.

**F-02 — Minor — `sidecar_ui/app.js:470`; `sidecar_ui/index.html:45`**
Documented header status line is dead: render clears it and the element remains hidden. Trigger: render the page. **Fix:** either render the status line or remove/update the documentation.

**F-03 — Minor — `sidecar_ui/hive.js:345,757`**
Role workers are absent from Members tab and seven documented `data-testid` attributes are missing. Trigger: use Members view or tests/automation relying on those selectors. **Fix:** include role workers and restore/document the selectors.

**F-04 — Minor — `sidecar_ui/app.js:685-718,672-683`**
The complete feed is rebuilt whenever etag changes, and etag changes on each agent step, making work O(all posts) per activity tick. Trigger: growing feed with active agents. **Fix:** update incrementally or avoid rebuilding unchanged feed content.

**F-05 — Minor — `sidecar_ui/app.js:999-1046,1835-1852`**
Replay continues ticking and re-rendering graph while Graph tab is hidden and is not stopped on tab switch. Trigger: start replay then switch tabs. **Fix:** pause/stop replay when hidden and resume intentionally.

**F-06 — Nit — `sidecar_ui/app.js:383-387`**
`state.etag` advances before `ingest()`; a renderer exception permanently skips that snapshot while header still says Live. Trigger: renderer throws during ingest. **Fix:** commit etag after successful ingest and show an error state.

**F-07 — Nit — `sidecar_ui/hive.js:393-396, 733`; `sidecar_ui/main.mjs:577-596`**
System-attention text appears twice in the member row. The verifier corrected the trigger: the duplicated title/line must coincide; real server text yields “@w1 looks stuck — w1: no progress…” rather than the reported identical phrase. **Fix:** avoid repeating the member/message text.

**F-08 — Nit — `sidecar_ui/index.html:13-15`**
Trusted chrome loads a third-party stylesheet and two preconnects from Google. Trigger: load dashboard. **Fix:** self-host assets or document/remove external requests.

### Tests / docs / packaging

**P-01 — Major — `MANIFEST.in:1-4`**
The sdist ships `tests/` without `tests/fixtures/`; documented tests fail when run from the source distribution. Trigger: build/extract sdist and run documented suite. Verifier built the sdist and confirmed missing fixtures. **Fix:** include fixtures or exclude/document tests from the artifact.

**P-02 — Major — `MANIFEST.in` / packaging**
The sdist omits files required by documented install steps, making the README installation path unavailable from the artifact. **Fix:** include the referenced subagents, skills, schemas, assets, config example, package/plugin metadata and docs, or state that the sdist is Python-only and direct users to the repository/tarball.

**P-03 — Minor — `tests/test_cli_and_schemas.py:153-180`**
Packaging test checks configuration strings and source-tree file existence but never builds an artifact, so it cannot detect P-01/P-02. **Fix:** build and inspect/install an sdist in the packaging test.

**P-04 — Minor — `tests/test_board.py:197-210`**
Channel-cursor test covers only half the invariant; the failing read order can lose a message while suite passes. **Fix:** test filtered read followed by catch-up in the failing order.

**P-05 — Minor — `tests/test_board.py:1-12,147-165`**
Suite advertises concurrency coverage it does not have, and critical locking invariant has no test. **Fix:** add concurrent process/thread mutation and locking tests; correct coverage description.

**P-06 — Minor — `tests/test_adapter.py:59-75`**
`test_log_file_handle_not_leaked_in_parent` cannot fail on POSIX. **Fix:** make the test portable and assert the intended handle state on supported platforms.

**P-07 — Minor — `tests/test_board.py:248-262`**
Wake-mode restoration is tested only on successful `wait_for_event`, not the exception/exit path required by the invariant. **Fix:** test restoration when waiting exits exceptionally or is interrupted.

**P-08 — Minor — `tests/test_cli_and_schemas.py:52-71`**
Schema parity test compares only title, property keys, and required lists, not the full parity claimed by project guidance. **Fix:** compare all schema attributes, including descriptions, types, enum/default/items.

**P-09 — Minor — `AGENTS.md:25,39`; `README.md:56-60,280`; `PORTING_MANIFEST.md:82`**
Test counts and suite listings are stale. **Fix:** update them to the actual suites/counts.

**P-10 — Minor — `AGENTS.md:64`**
Documented default worker command differs from the code. **Fix:** document the actual command.

**P-11 — Minor — `README.md:15-61`**
Package Contents tree does not match the repository/package tree. **Fix:** update the tree.

**P-12 — Nit — `tests/test_cli_and_schemas.py:174-176`; `pt_swarm/agent_definitions/worker.md:7`**
Worker role-prompt test only checks non-empty content and would not catch deletion of required `caller="<your-name>"` instruction; spawn-prompt half is pinned. **Fix:** assert the role prompt contains `caller=`.

**P-13 — Nit — `tests/test_adapter.py:106-121`**
Test hardcodes `$NEURALWATT_API_KEY`, contrary to guidance not to hardcode provider variables. **Fix:** use a neutral placeholder variable name.

**P-14 — Nit (unproven; excluded from verified tally) — `tests/test_board.py:157`; `tests/test_adapter.py:135-138`**
Timing headroom is limited: the board test sleeps 0.1 seconds before posting into a 5-second wait, and the adapter test polls for 2 seconds for a 1-second child. Neither flaked in three runs, and whether these timings fail on a loaded machine is unproven. **Fix:** no verified failure; collect evidence under a loaded/slow environment before treating this as a confirmed defect.

## Severity changes and refuted escalation claims

Three major findings were downgraded to minor:

- **B-04 / #12.2, lock `__enter__`:** acquisition exception leaves the lock held, but same-thread calls raise the acquisition error rather than deadlock; dispatch catches tool exceptions, so impact is subsequent errors and auxiliary-thread blocking, with a filesystem-fault trigger.
- **T-01 / #19.1, non-object JSON:** server termination on scalar/array input was reproduced, but the deployed Polytoken client does not send it and MCP 2025-06-18 removed batching; judged malformed-input robustness, not cross-agent release blocker.
- **T-02 / #19.2, blocking `swarm_wait`:** blocking was measured, but separate `polytoken exec` sessions have separate server processes. It delays parallel calls in one process; the claimed multi-agent shared-process freeze was not established.

Two proposed escalations were refuted by verifier reproductions:

- **Dashboard `file` action containment:** `canonicalFile` rejects relative/non-absolute or encoded/traversal/backslash/NUL paths before realpath and then requires a real path below `root + separator` and a regular file. Symlink to `/etc/hostname`, absolute `/etc/hostname`, traversal, sibling `brain2`, encoded traversal, backslash traversal, and `/api/frame?path=/etc/hostname` were all rejected; a legitimate `brain/ok.png` was accepted.
- **Board-parser crash claims:** parser robustness was checked against torn tail lines, invalid UTF-8, and a 20 MB line. The dashboard drops a torn tail and picks it up after completion, decodes invalid UTF-8 with U+FFFD and still parses, and handles the large line; no parser crash was found. The Python-side torn-line sequence-reuse issue B-03 is a separate confirmed board-writing defect.

## Verified sound

The following surfaces were explicitly inspected or exercised and produced no finding:

- **Board and roster:** verifier reports confirmed the Python/dashboard visibility divergence rather than misattributing it; dashboard channel-name regex, reserved names, effective-channel defaults, and channel-registry create/archive handling otherwise match. Board read robustness was exercised for torn line, invalid UTF-8, and 20 MB line as described above.
- **Worker/tool dispatch:** duplicate and killed member names are rejected; `n < 1` is rejected. Unknown actions return an error rather than mutating a default. Model discovery failure modes degrade to `[]` with an actionable note and injectable discovery path. `swarm(kill)` reads roster and kills under the board lock.
- **MCP protocol:** stdout remains clean apart from JSON-RPC responses across the server import/request path; notifications receive no response and cancellation is accepted silently; unknown methods/tools return `-32601` and the server continues; initialize negotiates supported versions and advertises MCP-shaped capabilities; tool definitions and schema property sets/required/type/enum/default/items agree (the reported drift is descriptions); result/error wrapping follows MCP content plus `isError`, and ordinary handler exceptions are contained.
- **Dashboard server:** `main.mjs` realpath containment for allowed files held under traversal, symlink, absolute-path and sibling-directory probes; allowed files are limited to expected brain assets.
- **Dashboard front end / XSS:** browser canary was inserted into attacker-controlled titles, member/model/activity/channel data, messages, channel metadata, narrator/system text, directory/raw data, and view JSON strings, then every tab in both modes was visited. Result: no injected image or executable inline handler; interpolated text was escaped. Hive displays raw `view.json` rather than calling `sanitizeView`, so its own escaping is the backstop; the verifier found it holds for current interpolation sites.
- **Action allowlists:** `url` requires parsed HTTPS, no credentials, and an allowed host/suffix (so `javascript:` and lookalike `docs.google.com.evil.tld` fail). `file` requires an own-property key in the validated file map and opens only the URI from the snapshot allowlist; `performAction` rechecks the action, preventing frame navigation from smuggling one. Board posts are capped at snapshot head.
- **Iframe and postMessage:** listener checks `event.source` against the actual iframe window, inbound message type allowlist, and an 8 KB serialized-size limit. Sandbox has opaque `null` origin, making source identity plus sandbox the appropriate check. Also verified: restrictive srcdoc CSP, `<` payload escaping, bootstrap `</script` neutralization, theme-token filtering, user activation/focus/recent-input/rate gates, and target ID validation.

## Unproven / not re-verified

- **`POLYTOKEN_INITIAL_PROMPT` external consumer (#18 nit, W-19):** confirmed set by this repository and no in-repository reader found. Whether Polytoken itself consumes it was not verified; external implementation evidence is missing. The duplicate-in-environment observation is confirmed; only the external-consumer question is unproven.
- **Test timing/flakiness headroom (#21 nit, P-14):** sleep/poll timing was observed not to flake in three runs, but behavior under a loaded/slow environment was not established. This entry is listed but excluded from confirmed finding totals.
- **Front-end render-throw reachability (#29):** whether a renderer exception can occur in real supported data (both directions of the claim) remains unproven; no concrete evidence showing a reachable exception was supplied.
- **Chromium frame-navigation blocking and `bind()` listener accumulation (#29):** verifier did not re-verify these pass-2 sound claims; browser/navigation and repeated-bind measurements are absent.
- **External Go visibility rule (#29):** no Go sources are present, so conformity to a documented Go rule cannot be checked here. Verified only that dashboard replay follows its own documented per-message rule and Python uses current membership.

## Deduplication notes

- **#18 finding 5 and #19 finding 3:** both describe unvalidated member names being used as filesystem path components; represented once as W-05.
- **#19 cross-area note and #18 cross-process race:** both describe the same `is_running` then spawn window across MCP processes; represented once as W-04.
- The verified tallies count the name-validation issue under both worker lifecycle and MCP tool layer. This document removes one duplicate entry; all other findings, including nits, remain individually listed.

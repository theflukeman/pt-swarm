# Review remediation ledger

This ledger records the disposition of every ID listed in `REVIEW_FINDINGS.md`, against the current tree and remediation commits after baseline `46bc6d8`. “Fixed” means the current implementation was changed for the reported mechanism; test evidence below is the named regression test where directly identified, or the parent-run suite where the finding-specific test is not independently identified. It is not a claim that every possible edge case is exhausted. “Documented disposition” means the behavior is an intentional limitation/contract rather than a repaired mechanism. “Unproven” means evidence is insufficient to claim a fix or a failure.

Current parent verification reported: `PYTHONPATH=/tmp/pt-swarm-build-env python3 -m unittest discover -s tests -v` — 144 passed; `node --test tests/*.mjs` — 12 passed. The test names below are present in the current test tree; this ledger did not independently rerun those commands. Main remediation commits include `7b778c4` (board), `771e01b` / `d9ea6d9` / `f6ac363` (worker lifecycle), `8d898ee` / `4b557eb` / `21dd111` (MCP), `119998b` / `50fb7e8` / `add9ad8` (dashboard), and `ea27725` / `c71aeef` (packaging/docs).

| ID | Outcome | Change / evidence or limitation |
|---|---|---|
| B-01 | Fixed | Corrupt roster reads now fail without replacing stored state; `tests/test_board.py::test_corrupt_roster_is_not_replaced_by_a_persist` (`7b778c4`). |
| B-02 | Fixed | Filtered reads preserve unread messages for later catch-up; `test_filtered_read_then_catchup_preserves_other_channel_messages` (`7b778c4`). |
| B-03 | Fixed | Torn tail repaired before append and sequence reservation made durable; `test_torn_tail_is_repaired_before_next_post`, `test_stale_sequence_marker_does_not_block_posting` (`36ba1b2`, `eb18947`). |
| B-04 | Fixed | Lock acquisition/close/release failures release the in-process lock; tests `test_lock_acquisition_failure_releases_thread_lock`, `test_lock_close_failure_still_releases_thread_lock`, `test_lock_release_failure_still_releases_thread_lock` (`7b778c4`). |
| B-05 | Fixed | Coordinator subscription preserves effective all-channel access; `test_subscribing_coordinator_preserves_all_channel_access` (`7b778c4`). |
| B-06 | Fixed | Explicit sequence lookup enforces visibility and validates IDs; `test_explicit_sequence_lookup_obeys_visibility_and_rejects_bad_ids` (`7b778c4`). |
| B-07 | Fixed | Python visibility uses membership at message sequence; `test_visibility_uses_membership_at_message_sequence` (`36ba1b2`). Dashboard replay has corresponding historical channel state (`1ea5fe7`). |
| B-08 | Fixed | Archived channel can be recreated; `test_archived_channel_can_be_recreated` (`7b778c4`). |
| B-09 | Fixed | POSIX lock wait is bounded; `test_posix_lock_wait_is_bounded` (`7b778c4`). |
| B-10 | Fixed | Mention callback failures are reported; `test_mention_callback_failure_is_reported` (`7b778c4`). |
| B-11 | Fixed | Raw board reads use the locked/guarded path in current code; covered by board/CLI and full Python suite. No dedicated concurrent CLI/stall raw-read test identified. |
| B-12 | Documented disposition | Kept full-board parsing rather than adding a cache/index with invalidation and recovery complexity. This is a performance tradeoff, not a correctness fix: operations can remain O(board size) as the board grows. No scalability benchmark establishes an acceptable upper bound. |
| B-13 | Fixed | Mention-name character rule aligned; `test_mention_name_characters_match_dashboard_unicode_rule` (`7b778c4`). |
| W-01 | Fixed | Zombie/stale workers are no longer considered running; `test_zombie_pid_is_not_treated_as_running` and worker liveness tests (`d9ea6d9`). |
| W-02 | Fixed | Stall-alert identity/cap no longer advances with its own alert; `test_worker_done_does_not_silence_monitor` and monitor tests (`771e01b`). |
| W-03 | Fixed | Kill validates identity and reports confirmed outcome; `test_kill_response_reports_roster_flag_and_confirmed_outcome`, `test_failed_kill_can_be_retried_after_flag` (`f6ac363`). |
| W-04 | Fixed | Cross-process reservation/check-and-spawn is coordinated; `test_two_process_managers_spawn_only_one_worker` (`771e01b`). |
| W-05 | Fixed | Member names are validated before persistence/path use; add/name and generated-shard validation tests (`771e01b`, `f6ac363`). |
| W-06 | Fixed | One-shot worker prompt no longer instructs exited workers to park; current spawn-prompt code and `test_initial_prompt_tells_worker_to_pass_caller`; no dedicated assertion for absence of the old clause identified. |
| W-07 | Fixed | Stall detection uses worker liveness rather than any `working` wake mode; `test_status_and_stall_monitor_use_worker_liveness`, `test_does_not_alert_when_all_workers_killed` (`771e01b`). |
| W-08 | Fixed | Status reflects process/roster outcome; `test_status_and_stall_monitor_use_worker_liveness` (`771e01b`). |
| W-09 | Fixed | Spawn grace and exit reporting no longer hold bookkeeping lock over the wait; `test_spawn_reports_failure_when_worker_exits_immediately`; see lifecycle tests (`771e01b`). |
| W-10 | Fixed | Kill-all uses configured workspace mode in current lifecycle path; group kill coverage in `tests/test_board.py::test_sharded_add_and_group_kill_with_worker_runner`; no separately named non-inherit kill-all test identified. |
| W-11 | Fixed | Cleanup preserves unmerged commits and ignored worker files; tests in `tests/test_workspace_preservation.py` (`91e6bd1`, `2da649c`, `f1579b3`). |
| W-12 | Fixed | Git/workspace process failures are handled with controlled fallback/error in current workspace manager; Python suite passed. No dedicated `OSError` injection test identified. |
| W-13 | Fixed | Worker hook no longer overwrites coordinator session-model state; `test_worker_hook_does_not_overwrite_coordinator_session_model` (`c96848b` and subsequent model changes). |
| W-14 | Fixed | Misleading unused config was removed/excluded and packaging guidance corrected; packaging/config tests in `tests/test_cli_and_schemas.py`, commits `ea27725`, `c71aeef`. |
| W-15 | Fixed | Parked-mode handling uses supported wake modes; monitor tests in `tests/test_adapter.py`, Python suite passed. |
| W-16 | Fixed | Monitor stop interrupts long waits; `test_stop_interrupts_long_monitor_wait` (`771e01b`). |
| W-17 | Fixed | Log tail reading is bounded in current worker runner; Python suite passed. No dedicated large-log test identified. |
| W-18 | Fixed | Missing role prompt is rejected; `test_missing_role_prompt_is_rejected` (`771e01b`). |
| W-19 | Unproven | `POLYTOKEN_INITIAL_PROMPT` remains an external-consumer question: no in-repository reader was found, and Polytoken’s external implementation was not verified. Do not infer it is unused externally. |
| T-01 | Fixed | Non-object JSON requests are validated without taking down the request loop; protocol validation tests in `tests/test_tool_protocol.py`; `8d898ee`. |
| T-02 | Fixed | Wait dispatch is concurrent and bounded; ping/post responsiveness, admission bound, and EOF cancellation are covered by `test_wait_does_not_block_ping_or_post_and_responses_are_serialized`, `test_wait_admission_is_bounded_and_ping_post_stay_responsive`, `test_eof_cancels_pending_wait_and_shutdown_is_prompt` (`4b557eb`, `21dd111`). |
| T-03 | Fixed | Shards preflight before mutation and report per-worker spawn outcomes; `test_shard_preflight_rejects_existing_later_name_without_partial_add`, `test_shard_spawn_outcomes_are_per_worker` (`f6ac363`). |
| T-04 | Fixed | Invalid/mis-cased channel posts are rejected; `tests/test_tool_protocol.py::test_invalid_channel_post_is_rejected` (`8d898ee`). |
| T-05 | Fixed | Mutating title/model operations require explicit values; `test_mutating_title_and_model_require_values` (`8d898ee`). |
| T-06 | Fixed | Schemas and handlers reject unknown properties; `test_tool_schemas_forbid_unknown_properties` (`8d898ee`). |
| T-07 | Fixed | Parameter failures are validated and returned as controlled protocol errors; tool protocol tests and `8d898ee`. No separate assertion for every malformed parameter listed in the finding. |
| T-08 | Fixed | Invalid envelopes/parse errors/null IDs are handled by current JSON-RPC validation; protocol tests in `tests/test_tool_protocol.py`, `8d898ee`. |
| T-09 | Fixed | Schema parity test now checks fuller definitions including descriptions; `test_tool_schemas_match_mcp_server_tools` (`8d898ee`). |
| T-10 | Fixed | `channel="*"` documentation/behavior aligned in tool schema and reader; schema parity test plus Python suite. No dedicated `*` visibility regression test identified. |
| T-11 | Fixed | On-disk format docs updated for emitted fields and operations; `ea27725`, `24b47ed`; docs have no standalone automated semantic test. |
| T-12 | Fixed | Shard input disagreement/invalid or excessive generated names preflighted; `test_shards_reject_generated_name_overflow_before_mutation` and shard preflight tests (`f6ac363`). |
| D-01 | Documented disposition | Native swarm has no Antigravity `brain/` transcripts. `sidecar_ui/README.md` explicitly states transcript-derived steps/activity/system items are absent and status comes from roster state; no transcript emulation is claimed. This is an intentional capability limit, not a claim that transcript features work for native swarms. |
| D-02 | Fixed | Raw board/member data is opt-in in polling response; documented in `sidecar_ui/README.md` and covered by dashboard server tests (`119998b`). No 20 MB response benchmark rerun claimed. |
| D-03 | Fixed | Host/origin and request protections/bind behavior updated in dashboard server; current Node suite passed (`119998b`). No external network deployment audit claimed. |
| D-04 | Fixed | Client errors no longer expose filesystem details in current sidecar SDK; Node suite passed (`119998b`). No dedicated missing-index assertion identified. |
| D-05 | Fixed | App-data discovery is cached/avoided for configured native swarm path; Node dashboard tests passed (`119998b`). |
| D-06 | Fixed | Reel/frame contract documented in README and `schemas/view_json.md` (`24b47ed`); docs change, not a runtime behavior change. |
| D-07 | Fixed | Develop instructions now use the port’s invocation/environment (`ea27725`); documentation change. |
| F-01 | Fixed | Narrator opt-in behavior and README contract aligned; dashboard docs and frontend tests (`50fb7e8`). |
| F-02 | Fixed | Dead header status-line documentation/markup behavior corrected; dashboard frontend review tests (`50fb7e8`). |
| F-03 | Documented disposition | Members view intentionally filters/hides non-worker role agents by default behind the documented “Retired” disclosure; overview identifies role agents. Selector/test hooks are documented in `sidecar_ui/README.md` (`sidecar_ui/README.md` sections “Members” and “Key elements carry `data-testid`s”). This is a deliberate role-filtering and selector contract, not a claim that every historical selector remains present; consumers should use the documented selectors. |
| F-04 | Fixed | Dashboard rendering/cache invalidation avoids stale/repeated feed work; current etag and render tests (`add9ad8`, `tests/test_sidecar_frontend_review.mjs`). No large-board performance benchmark claimed. |
| F-05 | Fixed | Replay stops when leaving Graph; `tests/test_sidecar_frontend_review.mjs` test “F-05: leaving Graph stops replay rather than running it in a hidden tab” (`50fb7e8`). |
| F-06 | Unproven | Whether supported real data can trigger a renderer throw remains unproven (finding’s reachability concern). The frontend test verifies thrown-render retry behavior, not that production inputs reach that path (`add9ad8`). |
| F-07 | Fixed | Duplicate system-attention wording corrected; dashboard frontend tests and `50fb7e8`. |
| F-08 | Fixed | Third-party stylesheet/preconnect requests removed or addressed in the current page; Node suite passed (`50fb7e8`). No browser network trace independently re-run. |
| P-01 | Fixed | Source-distribution manifest/config corrected; sdist-related tests in `tests/test_cli_and_schemas.py` and commits `ea27725`, `c71aeef`. |
| P-02 | Fixed | Packaging/install documentation and included files adjusted for supported source-distribution workflow (`ea27725`, `c71aeef`); current packaging tests passed. |
| P-03 | Fixed | Packaging test now builds/checks the artifact rather than only source-tree strings; `tests/test_cli_and_schemas.py` sdist test (`c71aeef`). |
| P-04 | Fixed | Filtered-read-then-catch-up order directly tested: `tests/test_board.py::test_filtered_read_then_catchup_preserves_other_channel_messages`. |
| P-05 | Fixed | Concurrent process and thread posting tests assert unique sequences: `test_concurrent_process_posts_have_unique_sequences`, `test_concurrent_thread_posts_have_unique_sequences`; `7b778c4`. |
| P-06 | Fixed | Parent log handle closure asserted portably; `tests/test_adapter.py` and `21b62ed`. |
| P-07 | Fixed | Wake-mode restoration on stop and exception covered by `test_wait_for_event_stop_event_returns_empty_and_restores_wake_mode`, `test_wait_for_event_restores_wake_mode_after_exception`. |
| P-08 | Fixed | Full schema parity expanded; `test_tool_schemas_match_mcp_server_tools` (`8d898ee`). |
| P-09 | Fixed | Test suite/count documentation updated in project docs (`ea27725`); test suite passed. |
| P-10 | Fixed | Worker command documentation aligned with headless `polytoken exec`; `test_default_worker_command_is_headless_exec` and docs (`ea27725`). |
| P-11 | Fixed | README package tree corrected (`ea27725`); documentation change. |
| P-12 | Fixed | Role prompt includes caller instruction and is checked by `test_initial_prompt_tells_worker_to_pass_caller`; current test covers generated prompt contract. |
| P-13 | Fixed | Provider environment names are derived rather than hard-coded; `tests/test_env_vars.py` and `2eb73d6`. |
| P-14 | Unproven | No verified timing failure: findings report three non-flaking runs, but loaded/slow-machine behavior remains untested. Prefer safer coordination/readiness-based tests if future evidence demonstrates flakes; do not classify as a confirmed defect now. |

## Verification limits and remaining unverified inventory

The parent-reported Python (144 tests) and Node (12 tests) suite results are recorded above; they are not live-browser coverage. No browser manual navigation/listener-accumulation run is claimed. In particular, Chromium frame-navigation blocking and repeated-bind listener accumulation remain unverified, as noted in `REVIEW_FINDINGS.md`. External Go visibility behavior is not verifiable from this repository (no Go sources were reviewed). The renderer-throw reachability question and external `POLYTOKEN_INITIAL_PROMPT` consumer also remain unproven. These limitations do not change the dispositions of other rows or establish additional defects.

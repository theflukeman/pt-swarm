# Specification: members.json

Stored at:
`<swarm_dir>/members.json`

Tracks the active roster, member configuration, and execution status.

## Schema

```json
{
  "title": "Parser Rewrite Swarm",
  "members": {
    "coordinator": {
      "conversation_id": "coordinator",
      "role": "coordinator",
      "desired_state": "RUNNING",
      "desired_model": "default",
      "applied_model": "default",
      "workspace_mode": "inherit",
      "phase": "RUNNING",
      "wake_mode": "working",
      "wait_since": 12,
      "last_read_seq": 14,
      "last_nudged_seq": 14,
      "last_park_blocked_seq": 0,
      "killed": false,
      "model": "default"
    },
    "parser": {
      "conversation_id": "conv-002",
      "role": "worker",
      "desired_state": "RUNNING",
      "desired_model": "gemini-3.8-flash",
      "applied_model": "gemini-3.8-flash",
      "workspace_mode": "share",
      "channels": ["main", "backend"],
      "phase": "RUNNING",
      "wake_mode": "mentions",
      "wait_since": 14,
      "last_read_seq": 14,
      "last_nudged_seq": 14,
      "last_park_blocked_seq": 0,
      "last_post_seq": 14,
      "last_post_time": "2026-11-12 17:34:00",
      "killed": false,
      "model": "gemini-3.8-flash"
    },
    "monitor": {
      "conversation_id": "conv-003",
      "role": "monitor",
      "desired_state": "RUNNING",
      "desired_model": "gemini-3.8-flash",
      "applied_model": "gemini-3.8-flash",
      "workspace_mode": "inherit",
      "channels": ["*"],
      "phase": "RUNNING",
      "wake_mode": "anything",
      "wait_since": 14,
      "last_read_seq": 14,
      "last_nudged_seq": 14,
      "last_park_blocked_seq": 0,
      "killed": false,
      "model": "gemini-3.8-flash"
    }
  }
}
```

### Wake Modes & Lifecycle Fields:
- `wake_mode`: `"working"` (active), `"mentions"` (parked in `swarm_wait` for `@name`/`@all`/`@#channel`), or `"anything"` (parked for any visible message).
- `phase` / `desired_state`: `"RUNNING"` or `"KILLED"`.
- `killed`: `true` when terminated permanently; killed names stay reserved and cannot be reused.

### Model Fields:
- `model` / `desired_model`: the model **request** as the coordinator or user made it. `"default"` means "use this member's role tier"; `"session"` means the model the Polytoken session is running; `"full"`/`"mini"`/`"nano"` name the reserved tier groups; anything else is a concrete model name, a `name(level)` reasoning variant, or a `@mg:<group>` reference.
- `applied_model`: the selector the worker process launched with, resolved from the request (see `pt_swarm/model_selection.py`). It is never `"default"`, because `polytoken exec --model` rejects that sentinel.
- Role tiers: workers and verifiers run the full tier, the monitor and narrator the mini tier. `swarm(action="model")` rewrites all three fields and takes effect the next time the member starts.

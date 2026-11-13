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

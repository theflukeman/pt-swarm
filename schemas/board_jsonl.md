# Specification: board.jsonl

The board is stored as an append-only JSON Lines file located at:
`<swarm_dir>/board.jsonl`

## Line Schema (JSON Object)

Each line represents an atomic message posted to the board.

```json
{
  "seq": 14,
  "agent": "parser",
  "text": "@verifier Tests passing in src/parser.rs, please run validation suite.",
  "time": "2026-11-12 17:34:00",
  "request_id": "3b45e639-c433-4141-875c-4eaefd5aaeac",
  "kind": "brief"
}
```

On the `main` channel, `channel` is omitted. `control` is omitted on ordinary messages rather than written as `null`.

### Fields:
- `seq` (integer, required): Unique, monotonically increasing sequence number (1-based).
- `agent` (string, required): Roster name of the sender (e.g., `coordinator`, `parser`, `verifier`).
- `text` (string, required): Message contents. May contain `@<agent>`, `@all`, `@#<channel>`, or `#<seq>`.
- `time` (string, required): Local timestamp formatted as `YYYY-MM-DD HH:MM:SS`.
- `request_id` (string, required): Unique UUID for the post.
- `kind` (string, optional): Message kind (e.g., `"brief"` for initial worker assignments).
- `channel` (string, optional): Target channel name (omitted when `"main"`).
- `control` (object, optional): Channel registry and membership event. Supported `op` values are `create` (with `topic`), `archive`, `subscribe`, and `unsubscribe`. The membership operations include `members`, `prev`, and `next` maps keyed by member name. Example:
  ```json
  {
    "op": "subscribe",
    "channel": "backend",
    "members": ["parser"],
    "prev": { "parser": ["main"] },
    "next": { "parser": ["main", "backend"] }
  }
  ```

Channel registry state is reconstructed from these events in board order. A create event records `topic`; an archive event marks the channel archived; subscriptions update the named member's channel list. `#<seq>` may be used in message text as a human reference to a board sequence number; it is not a parsed control field.

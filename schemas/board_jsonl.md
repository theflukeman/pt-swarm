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
  "kind": "brief",
  "channel": "main",
  "control": null
}
```

### Fields:
- `seq` (integer, required): Unique, monotonically increasing sequence number (1-based).
- `agent` (string, required): Roster name of the sender (e.g., `coordinator`, `parser`, `verifier`).
- `text` (string, required): Message contents. May contain `@<agent>`, `@all`, `@#<channel>`, or `#<seq>`.
- `time` (string, required): Local timestamp formatted as `YYYY-MM-DD HH:MM:SS`.
- `request_id` (string, required): Unique UUID for the post.
- `kind` (string, optional): Message kind (e.g., `"brief"` for initial worker assignments).
- `channel` (string, optional): Target channel name (omitted when `"main"`).
- `control` (object, optional): Used for channel lifecycle operations.
  ```json
  {
    "op": "subscribe",
    "channel": "backend",
    "members": ["parser", "db"],
    "prev": { "parser": ["main"] },
    "next": { "parser": ["main", "backend"] }
  }
  ```

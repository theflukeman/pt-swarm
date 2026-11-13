# Specification: view.json

Stored at:
`<swarm_dir>/view.json`

Written exclusively by the `narrator` agent to power the real-time Swarm UI dashboard. Must stay under 64 KB.

## Schema

```json
{
  "version": 1,
  "as_of_seq": 31,
  "updated_at": "2026-11-12T17:35:00Z",
  "headline": "Drafting parser rewrite; core AST passing, lexer in review.",
  "phase": {
    "label": "Verification",
    "index": 2,
    "total": 3
  },
  "attention": [
    {
      "id": "att-1",
      "kind": "decision",
      "severity": "high",
      "title": "Decide: Tokenizer error recovery strategy",
      "why": "parser and lexer workers propose conflicting recovery rules",
      "refs": [{ "seq": 18 }, { "seq": 22 }, { "member": "parser" }],
      "actions": [
        { "type": "open_conv", "target": "coordinator", "label": "Open coordinator" },
        { "type": "board_post", "target": 22, "label": "Jump to #22" }
      ],
      "short": {
        "title": "Error recovery strategy",
        "line": "Resolve parser vs lexer recovery conflict"
      }
    }
  ],
  "workstreams": [
    {
      "id": "ws-parser",
      "title": "Parser AST construction",
      "owners": ["parser"],
      "state": "active",
      "note": "3 tests failing in parser_test.rs",
      "refs": [{ "seq": 14 }],
      "progress": { "done": 2, "total": 5 },
      "short": { "title": "Parser AST", "line": "2 of 5 tests passing" }
    }
  ],
  "milestones": [
    { "id": "m-1", "text": "Lexer passes standard suite", "refs": [{ "seq": 10 }] }
  ],
  "risks": [
    { "id": "r-1", "text": "Recursive descent grammar exceeds recursion limit", "refs": [{ "seq": 14 }] }
  ],
  "now": {
    "coordinator": "Waiting for verifier test report",
    "parser": "Refactoring token lookahead",
    "verifier": "Executing cargo test"
  },
  "feedback_ack": {
    "through_seq": 31,
    "text": "Switched to Kanban columns as requested."
  }
}
```

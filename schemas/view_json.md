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
  },
  "reel": {
    "frames": [
      { "path": "/work/app/.swarm/default/brain/shot-01.png", "seq": 24, "caption": "Board feed" },
      { "path": "/work/app/.swarm/default/brain/shot-02.png", "seq": 31, "caption": "Members tab" }
    ]
  }
}
```

## Progress reel (`reel`)

`reel.frames` is how the narrator feeds the Overview's progress reel (UI work
only): the pane shows them as a then/now comparison with a slider. It is
optional and absent from a narrator's normal output; a view without it renders
exactly as before. The sidecar frame endpoint (`/api/frame`) serves a frame only
when its path is listed here and passes the containment checks below.

```json
"reel": { "frames": [ { "path": "<absolute image path>", "caption": "<why it changed>", "seq": 31 } ] }
```

-   `path` (required per frame): an absolute path to a `.png`, `.jpg`/`.jpeg`
    or `.webp` file. The pane serves it from `/api/frame` **only** when it is
    listed here, and then only after canonicalization: it must `realpath` to an
    existing regular file under `realpath(<appData>/brain/)` (for a native
    swarm, that is `<swarm_dir>/brain/` — create the directory and write the
    screenshots there), and be at most 8 MB. Anything else is a 404. Listing a
    path grants no other access: `file` actions in `attention` / `workstreams`
    are separate, and both use the same containment.
-   `caption` (optional): one line, clipped to 90 characters in the reel.
-   `seq` (optional, recommended): the board seq the frame corresponds to; the
    reel uses it to show when the frame was taken. Without it the frame falls
    to the current time.
-   At most **24 frames** is recommended. The pane renders the last 40 and
    needs **at least 2** to show the reel at all (a single frame has nothing to
    compare), so a single screenshot should stay in a normal post instead.

When the built-in Overview is on and no narrator lists frames, the pane falls
back to `screenshot: <absolute path> | <caption>` lines in workers' board posts
(not the coordinator's, whose briefs quote the format), keeping the last 24
distinct paths. Those obey the same `<appData>/brain/` containment.

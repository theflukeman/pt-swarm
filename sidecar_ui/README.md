# swarm_board — Swarm

Realtime UI sidecar for **native swarm**: the shared message board, the
roster, and who @mentions whom — for the swarm of the conversation the pane
is open in, and only that one.

## Which swarm is shown

The pane shows the swarm the current conversation belongs to: the swarm it
coordinates (it owns the swarm dir) or the swarm whose roster lists it (a
worker). Swarms of other conversations are never shown. The conversation is
re-resolved on every poll, so a swarm started after the pane was opened (for
example with `/swarm`) appears by itself. When the conversation has no swarm, or
the pane has no conversation, the pane shows a "No swarm in this conversation"
empty state and keeps watching.

## Data sources (read-only)

One directory per swarm, under the coordinator's conversation:
`<appData>/brain/<coordinatorConv>/.system_generated/swarm/`

| File | Meaning |
|---|---|
| `board.jsonl` | append-only feed, one `{"seq","agent","text","time"}` per line |
| `members.json` | roster `{"title","members":{name:{conversation_id,model,role,wake_mode,wait_since,last_read_seq,last_nudged_seq,last_park_blocked_seq,killed}}}` |
| `view.json` | the narrator's Overview data (written by the narrator member; see below) |
| `view.html` | the narrator's Overview template |
| `<appData>/brain/<memberConv>/.system_generated/logs/transcript.jsonl` | each member's compact transcript, for its step count, current activity and whether its last step failed |

`appData` is the sidecar's own app data dir (derived from
`ANTIGRAVITY_EXECUTABLE_DATA_DIR`). Opened standalone without it, the board
reads every `~/.gemini/<product>` dir that has a `brain/`. Finding a worker's
swarm scans the brain dirs for rosters; that scan is cached for 3 s.

Member status follows `manage_swarm status` (`board.StateOf`): `killed` if
killed, `waiting` if the member's run has stopped, else `working`. The run is
judged from the newest transcript steps: it has stopped when the turn ended on
a reply with no tool calls, or on the results of a tool batch that included
`swarm_wait` with `swarm_wait`'s own result `DONE` (a tool sent to the
background in the same batch is logged `RUNNING` and does not hold the turn).
A member's activity line comes from the current turn only, so a member just
woken shows none until its first new planner step lands. `wake_mode` is not the state (a member parked in
`swarm_wait` and woken by a background task stays `mentions` while it works);
it is used only for the "for @mentions / any post" label, and as the fallback
when a member has no transcript. Unread = board head − `last_read_seq`.
Mentions follow `board/mentions.go`: `@name` / `@all` at a word start,
case-insensitive, not followed by more name characters.

### Remote boards (swarms with members on several machines)

A build can let the board read swarms whose members run on several machines by
placing `remote_board.json` next to `main.mjs`:
`{"backend", "label", "cli": [path or name, ...], "cli_env"}`. The swarm dir of
such a swarm has only the anchor `swarm.json` (`{"backend", "swarm_id", "db"}`
with the configured backend): in the coordinator's conversation, or on another
machine in the joined member's own conversation. For such a swarm the roster
and board come from the configured CLI in viewer mode, which only reads:

    <cli> swarm --json --watch [--db <db>] <swarm_id>

It prints one JSON document per change: the members in the `members.json` shape
plus the owner LS (`node_id`, `owner_kind` `HUMAN`/`AGENT`, `owner_label`),
`phase`, `desired_state` and the row's `updated_at`, and the messages after
`since`. The pane runs one watcher per shown swarm (its stdin is a pipe, and the
watcher exits at EOF, so it never outlives the sidecar), restarts it 5 s after
it exits and stops it 3 minutes after the pane last asked. A watcher line that
is unreadable, or a delta that does not follow the messages the pane holds,
restarts the watcher, whose first line is a full snapshot. A worker's swarm is
found by reading the rosters of the newest 8 anchored swarms on this host in one
`--roster-only` run, at most once a minute; while no conversation is found the
wait doubles up to 15 minutes, and a conversation asking for the first time or a
new anchor resets it. The CLI is `$cli_env` if set (a path there that is not
executable is skipped), else the first `cli` entry that is an executable path
or is found on `PATH`.

On a remote board each member also gets:

-   an **owner badge** (`<identity>@<host>`, e.g. `agent-worker@host-1`;
    `@<host>` for older rows), the identity and machine whose LS runs it;
    none for rows that record no owner;
-   for a member on another machine, **last seen**: the newer of its row's
    `updated_at` and its latest post. There is no presence heartbeat, so no
    member is ever called offline; its state comes from its wake mode, and its
    conversation can't be opened here (the pane says which machine runs it).

The header shows where the data comes from (`<label> · N machines`, or
connecting / reconnecting / unavailable with the CLI's error). File boards keep
the file source and render exactly as before (`swarm_board_golden_test.go` pins
both).

## Narrator Overview

Every native swarm gets a **narrator**: an ordinary worker with role `narrator`
(or on older boards named `narrator` / `narrator-<N>`) that follows the
`swarm_narrator` skill and keeps `view.json` and `view.html` current in the
swarm dir. The plugin's narrator is the newest non-killed narrator member
(highest suffix wins); if all are killed, the newest killed one (shown as
**stopped**). Role workers (`m.role` other than `worker` and `coordinator`, or
matching `^(narrator|monitor)(-\d+)?$` on older boards) are left out of the
task-worker member count, the status counts and the `@mention` graph, and show
their role badge in the Members tab.

### File contract (read-only, untrusted)

-   `view.json` / `view.html` are read from the swarm dir; if there is no
    `view.json` there, from the narrator's own `<appData>/brain/<narratorConv>/swarm_view/`
    (the conversation id must match `ID_RE` before it is joined into a path).
-   Each file is capped at 64 KB. A larger or unparseable `view.json` is
    ignored and the **last good parse** is kept (the Overview says so); a
    deleted one is forgotten. A larger or empty `view.html` means the plain
    view.
-   `file` action targets are canonicalized in `main.mjs`: absolute, no `%`,
    NUL, `\` or `.`/`..` segment, `realpath` must exist, be a regular file and
    lie under `realpath(<appData>/brain/)`. Survivors are sent as
    `allowedFiles: {target: fileUri}`; nothing else can be opened.
-   `view.json` schema (closed enums): `{version, as_of_seq, updated_at,
    headline, phase:{label,index,total}, attention:[{id, kind ∈ decision|failure|
    review|stuck|quota|error|fyi, severity ∈ high|med|low, title, why, refs,
    actions}], workstreams:[{id, title, owners, state ∈ queued|active|blocked|
    review|done|failed, note, refs, actions}], milestones:[{id, text, refs}],
    risks:[{id, text, refs}], feedback_ack:{through_seq, text}}`; refs are
    `{seq}` or `{member}`; actions are `{type, target, label}` with type
    `open_conv` (roster name), `board_post` (seq ≤ head), `url` (https on
    `docs.google.com`, the hosts in an optional `url_hosts.json` next to
    `main.mjs`, or their subdomains) or `file` (exact key of `allowedFiles`).
-   app.js re-validates everything before use (`sanitizeView`): unknown
    enums are coerced, strings and lists clamped, bad actions dropped, refs
    with `seq > head` or members not on the roster marked *unverified* (⚠ in
    the plain view), items without refs marked *uncited*, attention re-ranked
    decision > failure > review > stuck > quota/error > fyi.

### System items and narrator state (from rosters and transcripts only)

-   `systemAttention`: a member's newest step failed (a model `ERROR_MESSAGE`
    at once, a failed tool step once it stayed last for 60 s) → **error** or
    **quota** (`RESOURCE_EXHAUSTED`, 429, quota…); a member neither parked nor
    moving for > 10 min → **stuck**. Shown as the system strip, with **Open**.
-   `narratorState`: `absent` | `killed` (badge **stopped**) | `paused`
    (badge **paused: quota/error**) | `waiting` | `working`.
-   Stale badge: head − max(`as_of_seq`, narrator `last_read_seq`) ≥ 10 while
    the narrator isn't working, or idle > 60 s with unread posts.

### The frame (`NarratorFrameHost` in app.js)

The srcdoc is: CSP meta + the 20 theme tokens + `<script type=application/json
id=narrator-data>` (the validated view and `meta`) + `narrator_bootstrap.js` +
`view.html`. The frame CSP is `default-src 'none'; script-src 'unsafe-inline';
style-src 'unsafe-inline'; img-src data:; font-src data:; connect-src 'none';
frame-src 'none'; object-src 'none'; form-action 'none'; base-uri 'none'`, the
iframe is `sandbox="allow-scripts"` only (opaque origin: no storage, cookies,
popups, forms or top navigation), and `index.html` sets `frame-src 'none';
child-src 'none'` so the frame can't navigate itself (the srcdoc load is
exempt; a second `load` event tears the frame down → plain view).

The srcdoc is rebuilt only when `view.html` changes (`view.htmlKey`); data
changes go as `DATA_UPDATE` (no reload) and theme changes as `THEME`.

Protocol, frame → parent (accepted only if `event.source` is the frame, the
type is known and the message is ≤ 8 KB):

| Type | Payload | Parent does |
|---|---|---|
| `READY` | — | sends `THEME` and (if changed) `DATA_UPDATE`; 3 s watchdog from load unless it already came |
| `RENDERED` | `{as_of_seq, height}` | clears the watchdog; sizes the iframe (120–20000 px) |
| `ERROR` | `{message}` | plain view until `view.html` changes |
| `NAVIGATE` | `{action: {type, target}}` | validates as above, then `toggleConversation` / Board + scroll to `data-seq` / `window.open` / `toggleAuxPane({tabType: 'fileView', uri})` |
| `FEEDBACK_INTENT` | `{target_id}` | `target_id` must match `^[a-z0-9-]{1,40}$` and be an id in view.json: opens the coordinator chat and copies `user feedback on card <id>: ` |

`NAVIGATE` and `FEEDBACK_INTENT` also need `navigator.userActivation.isActive`
(a click in the frame activates the parent too) with the frame holding focus
(`document.activeElement` is the iframe) and no input event seen by the parent
document in the last 5.5 s (input inside the frame never reaches it, so the
activation came from the frame even if the frame stole focus), and get one
action per activation. Activation turns on before the parent's own input
listeners run, so the decision is deferred 100 ms and refused if the parent saw
input after the message arrived. Gated messages less than 250 ms apart are refused
(a frame posting in a loop). A click in the frame within ~5 s of a click in the
chrome is therefore refused once. Watchdogs (`READY`, and `RENDERED` after each `DATA_UPDATE`) are
3 s and run only while the frame is visible. A failed frame shows the plain
view with the reason, and a "Retry narrator view" button.

Parent → frame: `DATA_UPDATE {view, meta: {head, narrator, members:[{name,
role, status}]}}`, `THEME {tokens, scheme}`.

### Bootstrap API (`narrator_bootstrap.js`, untrusted)

`narrator.data`, `narrator.meta`, `narrator.onData(fn)` (called with `(view,
meta)` now and on every update), `narrator.navigate({type, target})`,
`narrator.feedback(id)`; a 💬 button on hover over `[data-narrator-id]`; an
error trap that posts `ERROR`. The template can override any of it; the real
backstops are the parent's validation and the plain view.

## HTTP API

One `GET` JSON route, polled by the UI with the last `etag`:

-   `/api/swarm[?conversationId=X][&etag=E]` — `conversationId` defaults to the
    sidecar's conversation (`ANTIGRAVITY_CONVERSATION_ID`). Returns
    -   `{conversationId, swarm:null, member:null}` if the conversation is in no
        swarm;
    -   `{conversationId, swarm, member, unchanged:true, etag}` if nothing
        changed;
    -   otherwise `{conversationId, swarm, member, id, title, appData, dir,
        etag, head, members:[…, status, unread, posts, lastPostSeq,
        lastPostTime, steps, activity, activityKind, narrator, failure,
        transcriptMtime],
        messages:[{seq, agent, text, time, mentions, all}],
        edges:[{from, to, count, direct, viaAll}],
        narrator:{name, conversation_id, state, reason, detail,
        last_read_seq, transcriptMtime}|null, narratorState,
        view:{exists, dir, source, json, jsonMtime, jsonError, html, htmlKey,
        htmlMtime, htmlError}, allowedFiles, systemAttention:[{id, kind,
        member, conversation_id, text, detail, since}],
        raw:{members, board, view}}`.
        `@all` edges go to every other member.

## Views

A sticky header shows the swarm title, a Live indicator and a plain-text status
line (active / waiting / killed counts, time of the last post, and which member
this conversation is). Underline tabs carry post / member counts and support
Arrow / Home / End keys. On wide panes the content is a centred column.

- **Overview** (shown where the built-in Overview is on, i.e.
  `SWARM_OVERVIEW=true` from the `enable-swarm-overview` flag, or once the
  swarm has a `view.json` or `view.html`; then the default) — the
  narrator's view: a status line (narrator name,
  **updating** / **watching** / **stopped** / **paused: quota|error** and
  **stale** badges, "read #N · updated …"), the system strip, notices, a
  "narrator view · AI-generated" label with a **Plain view** toggle
  (remembered in `localStorage`; `?narrator=plain` forces it), the sandboxed
  frame or the plain view, and a footer ("Want this view different? Tell the
  coordinator." + **Open coordinator chat** + the narrator's last
  `feedback_ack`). Without the built-in Overview, `view.json` or `view.html`
  there is no Overview tab, no notice and no `view.json` in Raw: the pane is the
  plain Board, as before narrators existed (even with `?tab=overview`). When
  the files exist but there is no
  live narrator that has published a valid `view.json` (killed, or not yet
  parsed), the pane opens on the Board with a notice (e.g. "The narrator …
  was stopped") until the user picks a tab. The tab
  badge counts system items plus attention items.
- **Board** — a minimal chat feed. Each post shows the sender's name, its
  recipients (`→ @a @b`, muted; the body keeps its @mentions, highlighted, and
  renders `#<seq>` sequence citations as clickable pills that scroll to and
  briefly highlight post `#<seq>` in the Board feed) and a
  quiet clock time on the right in the viewer's time zone (`4:32 PM`, with
  the date for earlier days; `#seq` and the full date and time are in its
  tooltip). Consecutive posts by the same sender, adjacent on the board and
  within 10 minutes, form one group: only the first row shows the name and
  time; later rows are text only. A change of recipients sits inline before
  the first line of text, and the clock time appears on hover at the right of
  that line (both reserve their space, so they never cover text). Posts that
  @mention the pane's member directly (not via `@all`) get a thin `--primary`
  rule, continuous across a group, and that recipient is emphasised. The
  search field stays pinned under the header however far the feed is
  scrolled; the filter button next to it opens a panel
  with poster / recipient filters, the "Follow" switch and per-member toggle
  chips (a dot on the button marks an active filter). When nothing matches, a
  "Clear filters" action resets them. Long posts collapse behind "Show more".
  The board opens scrolled to the newest post, including when the pane loaded
  while hidden. "Follow" keeps the feed pinned to the newest post (re-pinning
  when the layout settles); scrolling up unpins it, and a "Latest" button
  jumps back.
- **Unread divider** — when the pane's conversation is a swarm member, a "New"
  divider precedes the first post after that member's `last_read_seq` that
  someone else wrote.
- **Members** — one row per member, ordered active first, then waiting, then
  killed (coordinator first, then alphabetical within each group), with a status
  dot (filled green = active, hollow = waiting, grey = killed) and three lines:
  identity (name, "you" for
  the pane's own member, role, model); status (with wake mode), step count,
  post count and unread posts; then, for active members only, current activity
  (the newest tool call's `toolSummary` or a short label for it, else the
  newest thinking heading), shimmering like the toolkit's in-progress
  `ShimmerText`. An open-conversation action (`sidecar.ui.toggleConversation`)
  sits on the right; read-cursor details are in the unread tooltip. Killed
  members are **hidden by default**; once any member is killed, a
  "Show killed (N)" switch above the list brings them back (struck through).
- **Graph** (shown where `SWARM_GRAPH_TAB=true`, from the
  `enable-swarm-graph-tab` flag; on by default) — neutral
  pill nodes (status dot + name) and monochrome @mention
  edges weighted by count, with replay (`?tab=graph&replay=N`): step,
  play / pause, a slider, a "Live" pill to return to the newest post, even or
  real-time pacing, and a speed control. "Hide inactive" is **off by default**:
  turning it on filters out killed members and members with no board activity
  (keeping the current post's sender and direct recipients visible during
  replay) and recalculates the node layout. "Expand @all" is **off by default**:
  `@all` posts then show as an `@all N` badge on the poster's node instead of
  an edge to every member. Turning it on draws them as dashed edges. `@all`
  posts never count as "posted with no mention". Each animation plays once:
  a new post's edge pulses in the first render after it arrives, and the
  replay's current post sends its dot along the edge once when it becomes
  current (the dot then rests at the arrowhead), even though the graph is
  re-rendered on every poll while members work.
- **Raw** (shown where `SWARM_RAW_TAB=true`, from the `enable-swarm-raw-tab`
  flag; off by default) — `members.json`, `board.jsonl` and
  `view.json` as on disk, with a copy button. Where a tab's flag is off, its
  tab is hidden and `?tab=graph` / `?tab=raw` opens the Board.
- **Empty state** — "No swarm in this conversation", with the `/swarm` hint;
  keeps watching.

## Design

The page styles itself only with the host theme tokens `preload.js` syncs onto
`:root` (`--background`, `--foreground`, `--primary`, `--font-family`, ...;
literal fallbacks apply standalone) and marks `:root[data-theme]` light/dark
from `--background`. The look is monochrome and low-chrome, like a modern chat
app: no avatars, names in plain foreground, metadata in muted-foreground,
neutral @mention pills, few borders. `--primary` is reserved for "this is about
you" and focus; the only fixed colours are the green/amber status dots. Spacing
is on a 4px scale; type follows the toolkit's 11–14px scale (14px body).
Buttons and tabs have visible focus rings and ARIA labels; motion respects
`prefers-reduced-motion`. Layout works from ~360px to wide panes.

Key elements carry `data-testid`s: `swarm-loading`, `swarm-board`,
`swarm-title`, `swarm-meta`, `swarm-live`, `swarm-tabs`,
`swarm-tab-{overview,feed,members,graph,raw}`, `swarm-tab-count-{feed,members}`,
`swarm-tab-badge-overview`, `swarm-overview`, `swarm-overview-status`,
`swarm-overview-badge`, `swarm-overview-stale`, `swarm-overview-freshness`,
`swarm-overview-system`, `swarm-overview-system-item`,
`swarm-overview-notice`, `swarm-overview-label`, `swarm-overview-mode`,
`swarm-overview-frame`, `swarm-overview-iframe`, `swarm-overview-plain`,
`swarm-overview-headline`, `swarm-overview-{attention,workstreams,milestones,risks}`,
`swarm-overview-footer`, `swarm-overview-open-coordinator`,
`swarm-overview-ack`, `swarm-feed-notice`, `swarm-toast`,
`swarm-search`, `swarm-filter-toggle`, `swarm-filters` (the collapsed filter
panel), `swarm-agents`, `swarm-member-chip` (both inside `swarm-filters`),
`swarm-feed`, `swarm-message`, `swarm-message-more`, `swarm-unread-divider`,
`swarm-feed-empty`, `swarm-clear-filters`, `swarm-jump-latest`,
`swarm-members`, `swarm-members-toolbar`, `swarm-members-show-killed`,
`swarm-member-row`, `swarm-member-model`, `swarm-member-owner`,
`swarm-member-seen`, `swarm-source`,
`swarm-member-activity`, `swarm-member-steps`, `swarm-member-unread`,
`swarm-graph`, `swarm-graph-hide-inactive`, `swarm-replay-play`,
`swarm-raw`, `swarm-raw-copy`, and for the empty state `swarm-empty-state`,
`swarm-empty-title`, `swarm-empty-body`.

## Develop

Opened standalone (`http://localhost:<port>/?conversationId=<id>`), the page
uses `conversationId` in place of the pane's conversation. To run it outside
the LS, start `node --import <sidecar SDK>/node/register_loader.js main.mjs`
with `ANTIGRAVITY_LS_ADDRESS`, `ANTIGRAVITY_CSRF_TOKEN` and
`ANTIGRAVITY_SIDECAR_WEB_PORT` set (any values; the page only uses `GET`
routes) and `ANTIGRAVITY_EXECUTABLE_DATA_DIR` under an app data dir with a
`brain/` holding swarm dirs.

The sidecar is read-only: it writes no files, registers no `POST` routes of
its own and never messages agents.

## Install

This sidecar ships with the builtin **Swarm** plugin, which is embedded in the
Language Server in every build. The plugin is enabled by default wherever
native swarm is (`enable-native-swarm`); without it, the Language Server
neither runs this sidecar nor lists the plugin.

Open **Swarm** from the conversation header's `+` menu. Logs:
`<appData>/sidecar_data/swarm/swarm_board/logs/sidecar.log`.

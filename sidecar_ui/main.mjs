#!/usr/bin/env node
/**
 * Native Swarm Board sidecar backend.
 *
 * Reads native swarm's on-disk state, read-only:
 *   <appData>/brain/<coordinatorConv>/.system_generated/swarm/board.jsonl
 *   <appData>/brain/<coordinatorConv>/.system_generated/swarm/members.json
 * (written by native swarm's board package),
 * plus the narrator's Overview files next to them (view.json, view.html; see
 * the swarm_narrator skill). A remote swarm (members on several machines; its
 * swarm dir holds only the swarm.json anchor) is read through the board's CLI
 * in viewer mode, `<cli> swarm --json <swarm_id>` (see "Remote boards" below).
 * Nothing here writes a file or messages an agent.
 */
import { execFile, spawn } from 'node:child_process';
import { accessSync, constants as fsConstants, existsSync, readFileSync, readdirSync, realpathSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import { basename, delimiter, dirname, isAbsolute, join, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { SidecarApp, Response } from './sidecar_sdk/index.mjs';
import { activeAt, ALL, channelMentions, channelOf, channelRegistry, channelsAt, controlOf, currentChannels, effectiveChannels, MAIN, memberView, mentionsOf, visibleUnread } from './channels.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const STATIC = {
  '/styles.css': ['styles.css', 'text/css'],
  '/app.js': ['app.js', 'application/javascript'],
  '/hive.js': ['hive.js', 'application/javascript'],
  '/hive.css': ['hive.css', 'text/css'],
  // Inlined by app.js into the narrator frame's srcdoc; served as text.
  '/narrator_bootstrap.js': ['narrator_bootstrap.js', 'text/plain'],
};
const ID_RE = /^[0-9a-z_-]{2,}$/i;
// The narrator is any member with role "narrator", or (on older boards where
// role was "worker") named "narrator" or "narrator-<N>" (a killed name stays
// reserved, so a replacement gets the next suffix). Role workers (system
// observers like narrator and monitor) are recognised generically by role,
// with a name regex fallback for older boards.
const NARRATOR_RE = /^narrator(?:-(\d+))?$/;
const ROLE_WORKER_RE = /^(?:narrator|monitor)(?:-\d+)?$/;

function roleOf(name, m) {
  const r = typeof m?.role === 'string' ? m.role : '';
  if (r && r !== 'worker') return r;
  const match = /^(narrator|monitor)(?:-\d+)?$/.exec(String(name ?? ''));
  return match ? match[1] : r;
}

function isRoleWorker(name, m) {
  const r = roleOf(name, m);
  return Boolean((r && r !== 'worker' && r !== 'coordinator') || ROLE_WORKER_RE.test(String(name ?? '')));
}
// view.json / view.html larger than this are ignored (last good JSON kept;
// no HTML, so the UI shows the plain view).
const VIEW_MAX_BYTES = 64 * 1024;
// A member not parked in swarm_wait whose transcript hasn't moved for this
// long is reported as stuck.
const STUCK_MS = 10 * 60 * 1000;
// A tool step that failed is normally followed by another step; only a failed
// step that stayed last this long counts as the member having stopped on it.
const STEP_ERROR_SETTLE_MS = 60 * 1000;
const QUOTA_RE = /RESOURCE_EXHAUSTED|quota|\b429\b|exhausted your capacity|rate.?limit/i;

// Hosts whose https links (subdomains included) the narrator's `url` actions
// may open. A build may add its own in url_hosts.json next to this file.
const HOST_RE = /^[a-z0-9-]+(?:\.[a-z0-9-]+)+$/;
const URL_HOSTS = ['docs.google.com', ...readUrlHosts(join(__dirname, 'url_hosts.json'))];

// The Overview drawn without a narrator (from the coordinator's plan.json and
// screenshots workers flag) is on where the language server sets
// SWARM_OVERVIEW=true, from the enable-swarm-overview flag (see sidecar.json).
// Without it the pane keeps the narrator's Overview.
const BUILTIN_OVERVIEW = process.env.SWARM_OVERVIEW === 'true';

// The Graph and Raw tabs are shown only where the language server sets
// SWARM_GRAPH_TAB=true / SWARM_RAW_TAB=true, from the enable-swarm-graph-tab
// and enable-swarm-raw-tab flags (see sidecar.json).
const GRAPH_TAB = process.env.SWARM_GRAPH_TAB === 'true';
const RAW_TAB = process.env.SWARM_RAW_TAB === 'true';

// Boards that live outside the swarm dir (members on several machines). A
// build that has them describes them in remote_board.json next to this file:
// {backend, label, cli: [path or name, ...], cli_env}. Without it none are read.
const REMOTE = readRemoteBoard(join(__dirname, 'remote_board.json'));

function readRemoteBoard(path) {
  try {
    const r = JSON.parse(readFileSync(path, 'utf-8'));
    const cli = Array.isArray(r?.cli) ? r.cli.filter((c) => typeof c === 'string' && c) : [];
    if (typeof r?.backend !== 'string' || !r.backend || !cli.length) return null;
    const label = typeof r.label === 'string' && r.label ? r.label : 'Remote';
    return { backend: r.backend, label, cli, cliEnv: typeof r.cli_env === 'string' ? r.cli_env : '' };
  } catch {
    return null;
  }
}

function readUrlHosts(path) {
  try {
    const hosts = JSON.parse(readFileSync(path, 'utf-8'));
    return Array.isArray(hosts) ? hosts.filter((h) => typeof h === 'string' && HOST_RE.test(h)) : [];
  } catch {
    return [];
  }
}

// ---------------------------------------------------------------------------
// Locating app data dirs. Under a language server the board reads only the app
// data dir this sidecar runs under, found from ANTIGRAVITY_EXECUTABLE_DATA_DIR.
// Opened standalone, it reads every ~/.gemini/<product> dir that has a brain/.
// ---------------------------------------------------------------------------

let appDataDirsCache = null;
function findAppDataDirs() {
  const directSwarmDir = process.env.POLYTOKEN_SWARM_DIR || join(process.cwd(), '.swarm', 'default');
  const directDirs = existsSync(join(directSwarmDir, 'members.json')) ? [directSwarmDir] : [];
  if (process.env.POLYTOKEN_SWARM_DIR) return directDirs;
  if (appDataDirsCache) return [...directDirs, ...appDataDirsCache];
  const dataDir = process.env.ANTIGRAVITY_EXECUTABLE_DATA_DIR;
  if (dataDir) {
    // <appData>/sidecar_data/<plugin>/<sidecar>/data. Until a fresh app data
    // dir has its brain/, read nothing rather than any other dir.
    for (let d = dataDir; d !== dirname(d); d = dirname(d)) {
      if (basename(d) !== 'sidecar_data') continue;
      const appData = dirname(d);
      appDataDirsCache = existsSync(join(appData, 'brain')) ? [appData] : [];
      return [...directDirs, ...appDataDirsCache];
    }
    appDataDirsCache = [];
    return directDirs;
  }
  const root = join(homedir(), '.gemini');
  const dirs = safeReaddir(root).sort().map((name) => join(root, name));
  appDataDirsCache = dirs.filter((c) => existsSync(join(c, 'brain')));
  return [...directDirs, ...appDataDirsCache];
}

// Re-evaluated on every use (a few stats): a fresh app data dir may get its
// brain/ only after the sidecar has started.
console.log(`[swarm_board] app data dirs: ${findAppDataDirs().join(', ')}`);

const swarmDir = (appData, conv) =>
  existsSync(join(appData, 'members.json'))
    ? appData
    : join(appData, 'brain', conv, '.system_generated', 'swarm');

function safeReaddir(path) {
  try {
    return readdirSync(path);
  } catch {
    return [];
  }
}

function statOf(path) {
  try {
    const s = statSync(path);
    return { mtime: s.mtimeMs, size: s.size };
  } catch {
    return { mtime: 0, size: -1 };
  }
}

// File text cache keyed by path, invalidated on mtime/size change. With
// maxBytes, a larger file is not read and comes back as {text: null,
// tooLarge: true}.
const fileCache = new Map(); // path -> {key, text}
function readCached(path, maxBytes = Infinity) {
  const s = statOf(path);
  if (s.size < 0) return { text: null, ...s };
  if (s.size > maxBytes) return { text: null, tooLarge: true, ...s };
  const key = `${s.mtime}:${s.size}`;
  const hit = fileCache.get(path);
  if (hit && hit.key === key) return { text: hit.text, ...s };
  let text = null;
  try {
    text = readFileSync(path, 'utf-8');
  } catch {
    return { text: null, ...s };
  }
  fileCache.set(path, { key, text });
  return { text, ...s };
}

function parseRoster(text) {
  try {
    const r = JSON.parse(text);
    if (!r || typeof r !== 'object') return null;
    return { title: r.title || '', members: r.members && typeof r.members === 'object' ? r.members : {} };
  } catch {
    return null;
  }
}

// Mirrors board.parse: skip blank and unparseable lines.
function parseBoard(text) {
  const out = [];
  for (const line of (text || '').split('\n')) {
    if (!line.trim()) continue;
    try {
      const m = JSON.parse(line);
      if (m && typeof m === 'object') out.push(m);
    } catch {
      // A torn final line is picked up on the next read.
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Mentions and channel visibility live in channels.mjs (mirrors of the board
// package's Go rules, tested against the same table).
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Conversation titles via agentapi, fetched asynchronously and cached.
// ---------------------------------------------------------------------------

const titleCache = new Map(); // conv -> string | Promise
function fetchTitle(conv) {
  const exe = process.env.ANTIGRAVITY_AGENTAPI_EXE;
  const file = exe || 'agentapi';
  const args = [...(exe ? ['agentapi'] : []), 'get-conversation-metadata', conv];
  return new Promise((resolve) => {
    execFile(file, args, { encoding: 'utf-8', timeout: 5000 }, (err, stdout) => {
      if (err) return resolve('');
      try {
        const md = JSON.parse(stdout || '{}');
        resolve(md.title || md.summary || md.name || md.metadata?.title || md.conversation?.title || '');
      } catch {
        resolve('');
      }
    });
  });
}
function convTitle(conv) {
  const cached = titleCache.get(conv);
  if (typeof cached === 'string') return cached;
  if (!cached) titleCache.set(conv, fetchTitle(conv).then((t) => titleCache.set(conv, t)));
  return '';
}

// ---------------------------------------------------------------------------
// Resolving a conversation to its swarm. A conversation is in a swarm if it
// owns a swarm dir (the coordinator) or is on a roster (a worker, whose board
// lives under its parent conversation).
// ---------------------------------------------------------------------------

const isDirectSwarmDir = (appData) => existsSync(join(appData, 'members.json'));

function locate(id) {
  for (const appData of findAppDataDirs()) {
    if (isDirectSwarmDir(appData)) {
      if (id === 'default' || memberNamed(rosterAt(appData, id), id)) return appData;
      continue;
    }
    if (existsSync(join(swarmDir(appData, id), 'members.json'))) return appData;
  }
  return null;
}

function rosterAt(appData, id) {
  const { text } = readCached(join(swarmDir(appData, id), 'members.json'));
  return text == null ? null : parseRoster(text);
}

const memberNamed = (roster, conv) =>
  Object.entries(roster?.members || {}).find(([, m]) => m?.conversation_id === conv)?.[0] || null;

// Finding a worker's swarm scans every brain dir, so the list of swarm dirs is
// rebuilt at most every ROSTER_SCAN_TTL_MS; a swarm created later shows up
// within that delay. The rosters themselves are re-read (mtime-cached) on use.
const ROSTER_SCAN_TTL_MS = 3000;
let swarmDirs = { builtAt: 0, list: [], remote: [] }; // list: [{appData, id}]; remote: [{appData, id, mtime}]

function scanSwarmDirs() {
  const now = Date.now();
  if (now - swarmDirs.builtAt < ROSTER_SCAN_TTL_MS) return swarmDirs;
  const seen = new Set();
  const list = [];
  const remote = [];
  for (const appData of findAppDataDirs()) {
    if (isDirectSwarmDir(appData)) {
      const id = basename(appData);
      if (!seen.has(id)) {
        seen.add(id);
        list.push({ appData, id });
      }
      continue;
    }
    for (const id of safeReaddir(join(appData, 'brain'))) {
      if (seen.has(id)) continue;
      if (existsSync(join(swarmDir(appData, id), 'members.json'))) {
        seen.add(id);
        list.push({ appData, id });
      } else if (existsSync(join(swarmDir(appData, id), ANCHOR_FILE))) {
        seen.add(id);
        remote.push({ appData, id, mtime: statOf(join(swarmDir(appData, id), ANCHOR_FILE)).mtime });
      }
    }
  }
  remote.sort((a, b) => b.mtime - a.mtime);
  swarmDirs = { builtAt: now, list, remote };
  return swarmDirs;
}

const listSwarmDirs = () => scanSwarmDirs().list;

// Returns {appData, swarm, member, remote}, with swarm null if conv is in no
// swarm. remote is set (the swarm's anchor) for a remote swarm.
function resolve(conv) {
  const own = locate(conv);
  if (own) return { appData: own, swarm: conv, member: memberNamed(rosterAt(own, conv), conv) };
  // A remote swarm's dir holds only the anchor: in the coordinator's
  // conversation, or (on another node) in that node's never-run swarm root.
  const anchored = locateRemote(conv);
  if (anchored) {
    const roster = remoteWatch(anchored.anchor).roster;
    return { appData: anchored.appData, swarm: conv, member: memberNamed(roster, conv), remote: anchored.anchor };
  }
  for (const { appData, id } of listSwarmDirs()) {
    const member = memberNamed(rosterAt(appData, id), conv);
    if (member) return { appData, swarm: id, member };
  }
  // A worker of a remote swarm: its board lives under its parent (the
  // coordinator, or this node's swarm root), found by the rosters of the
  // anchored swarms on this host.
  const hit = remoteMemberOf(conv);
  if (hit) return hit;
  return { appData: null, swarm: null, member: null };
}

// ---------------------------------------------------------------------------
// Member activity from each member's compact conversation transcript:
//   <appData>/brain/<memberConv>/.system_generated/logs/transcript.jsonl
// one JSON step per line; PLANNER_RESPONSE steps carry `thinking` and
// `tool_calls` [{name, args}] whose string args are JSON-quoted.
// ---------------------------------------------------------------------------

const transcriptPath = (appData, conv) =>
  join(appData, 'brain', conv, '.system_generated', 'logs', 'transcript.jsonl');

// Board tools say what a member is doing on the board, not in its own work.
const BOARD_ACTIVITY = {
  swarm_wait: 'Waiting for posts',
  read_board: 'Reading the board',
  post_message: 'Posting to the board',
};

function unquote(v) {
  if (typeof v !== 'string') return v == null ? '' : String(v);
  const s = v.trim();
  if (s.length >= 2 && s.startsWith('"') && s.endsWith('"')) {
    try {
      return String(JSON.parse(s));
    } catch {
      return s.slice(1, -1);
    }
  }
  return s;
}

function oneLine(s, maxLen = 96) {
  const out = String(s || '')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/\s+/g, ' ')
    .trim();
  return out.length > maxLen ? `${out.slice(0, maxLen - 1).trimEnd()}…` : out;
}

// A short label for one tool call, e.g. "Unit test execution" or "Edit app.js".
function describeToolCall(tc) {
  const name = tc?.name || '';
  if (!name) return '';
  if (BOARD_ACTIVITY[name]) return BOARD_ACTIVITY[name];
  const args = tc.args && typeof tc.args === 'object' ? tc.args : {};
  const summary = unquote(args.toolSummary) || unquote(args.toolAction);
  if (summary) return oneLine(summary);
  const path = unquote(args.Path || args.AbsolutePath || args.TargetFile);
  if (path) {
    const verb = { read_file: 'Read', write_file: 'Write', edit_file: 'Edit' }[name];
    if (verb) return `${verb} ${basename(path)}`;
  }
  if (name === 'code_search' && args.Query) return oneLine(`Search ${unquote(args.Query)}`);
  if (name === 'swarm' && args.action) return oneLine(`swarm ${unquote(args.action)} ${unquote(args.name)}`);
  return name;
}

// The last bold "**Heading**" line of a thinking block.
function thinkingHeading(thinking) {
  if (typeof thinking !== 'string') return '';
  const hs = [...thinking.matchAll(/(?:^|\n)\s*\*\*([^*\n]{3,96})\*\*\s*(?=\n|$)/g)];
  return hs.length ? oneLine(hs[hs.length - 1][1]) : '';
}

// {steps, activity, activityKind: 'tool'|'thinking'|'', last, idle, mtime} for
// a member, cached on the transcript's mtime+size (also returned as `key` so
// the snapshot etag changes as members work).
const transcriptCache = new Map(); // path -> {key, summary}
const NO_TRANSCRIPT = { key: '-', summary: null };
function summarizeTranscript(appData, conv) {
  if (!appData || !conv || !ID_RE.test(conv)) return NO_TRANSCRIPT;
  const path = transcriptPath(appData, conv);
  const s = statOf(path);
  if (s.size < 0) return NO_TRANSCRIPT;
  const key = `${s.mtime}:${s.size}`;
  const hit = transcriptCache.get(path);
  if (hit && hit.key === key) return hit;
  let text;
  try {
    text = readFileSync(path, 'utf-8');
  } catch {
    return NO_TRANSCRIPT;
  }
  const lines = text.split('\n').filter((l) => l.trim());
  let steps = lines.length;
  let activity = '';
  let activityKind = '';
  // The newest parseable step: its status and error say whether the member
  // stopped on an error (a model error is an ERROR_MESSAGE step; a failed
  // tool step has status ERROR).
  let last = null;
  // Walk back to the newest planner step of the current turn that says what
  // the member is doing. A turn starts after user input or after a swarm_wait
  // batch; until the new turn's first planner step lands (the planner step is
  // logged only once the model finishes), there is no activity to show, and
  // the previous turn's "Waiting for posts" would be stale. System messages
  // are not a boundary: hooks also insert them mid-turn.
  for (let i = lines.length - 1; i >= 0; i--) {
    let step;
    try {
      step = JSON.parse(lines[i]);
    } catch {
      continue; // A torn final line is picked up on the next read.
    }
    if (!step || typeof step !== 'object') continue;
    if (i === lines.length - 1 && Number.isFinite(step.step_index)) steps = Math.max(steps, step.step_index + 1);
    if (!last) {
      last = {
        type: String(step.type || ''),
        status: String(step.status || ''),
        error: typeof step.error === 'string' ? oneLine(step.error, 240) : '',
      };
    }
    if (step.type === 'USER_INPUT') break;
    if (step.type !== 'PLANNER_RESPONSE') continue;
    const calls = Array.isArray(step.tool_calls) ? step.tool_calls : [];
    if (calls.some((tc) => tc?.name === 'swarm_wait')) break;
    const labeled = calls.filter((tc) => describeToolCall(tc)).pop();
    const label = labeled ? describeToolCall(labeled) : '';
    const heading = thinkingHeading(step.thinking);
    if (label) {
      activity = label;
      activityKind = 'tool';
    } else if (heading) {
      activity = heading;
      activityKind = 'thinking';
    } else {
      continue;
    }
    break;
  }
  const idle = turnEnded(lines);
  const entry = { key, summary: { steps, activity, activityKind, last, idle, mtime: s.mtime } };
  if (transcriptCache.size > 500) transcriptCache.clear();
  transcriptCache.set(path, entry);
  return entry;
}

// ---------------------------------------------------------------------------
// Full snapshot of one swarm.
// ---------------------------------------------------------------------------

// Whether a transcript's newest step ends a turn, i.e. the member's run has
// stopped: a final reply with no tool calls, or the results of a tool batch
// that included swarm_wait, once swarm_wait's own result is DONE (the
// wait-check hook ends the turn only then; a failed or invalid swarm_wait lets
// the turn go on). The other results in the batch do not matter: a tool sent
// to the background is logged RUNNING and does not hold the turn, and a failed
// sibling does not stop the wait. Results are logged in call order, so
// swarm_wait's is the one at its index. Anything newer (a system message from
// a background task, user input, a model error the planner is retrying) means
// the member is mid-turn; a model error it stopped on is reported by
// stepFailure. A torn final line counts as mid-turn; the next read settles it.
function turnEnded(lines) {
  const results = []; // tool results after the newest planner step, newest first
  for (let i = lines.length - 1; i >= 0; i--) {
    let step;
    try {
      step = JSON.parse(lines[i]);
    } catch {
      return false;
    }
    if (!step || typeof step !== 'object') return false;
    if (step.type === 'GENERIC') {
      results.unshift(step);
      continue;
    }
    if (step.type !== 'PLANNER_RESPONSE') return false;
    const calls = Array.isArray(step.tool_calls) ? step.tool_calls : [];
    if (calls.length === 0) return results.length === 0;
    const wait = calls.findIndex((c) => c?.name === 'swarm_wait');
    return wait >= 0 && results.length >= calls.length && results[wait].status === 'DONE';
  }
  return false;
}

// A member's state from the roster alone, since a native swarm has no
// transcript to judge a run by. `killed` is the roster's own flag; a member
// parked in swarm_wait records `wait_since`; the native harness binds a member
// to a job and reports its state (running / done / failed), so a finished or
// failed run is waiting, not working. `wake_mode` is the wake policy and never
// establishes current activity.
function statusOf(m) {
  if (m.killed || m.phase === 'KILLED' || m.desired_state === 'KILLED') return 'killed';
  if (Number(m.wait_since) > 0) return 'waiting';
  const state = String(m.state ?? m.phase ?? '').toLowerCase();
  if (state === 'done' || state === 'failed') return 'waiting';
  return 'working';
}

// Board times are "YYYY-MM-DD HH:MM:SS" in the LS's local time. This server
// runs on the LS host, so parse them here (in that zone) into epoch ms; the
// browser may be in a different zone. Null when unparseable.
function boardTimeMs(t) {
  const ms = t ? Date.parse(String(t).replace(' ', 'T')) : NaN;
  return Number.isFinite(ms) ? ms : null;
}

// ---------------------------------------------------------------------------
// The narrator and its Overview files (view.json, view.html). Both files are
// untrusted whoever wrote them: they are size-capped here, the JSON is
// re-validated by app.js, and the HTML only ever runs in a sandboxed frame.
// ---------------------------------------------------------------------------

// "narrator" ranks 1, "narrator-<N>" ranks N; anything else is not a narrator.
function narratorRank(name, m) {
  if (roleOf(name, m) !== 'narrator') return -1;
  const match = NARRATOR_RE.exec(name) || /-(\d+)$/.exec(name);
  return match?.[1] ? Number(match[1]) : 1;
}

// The swarm's narrator: the newest (highest-ranked) non-killed narrator member,
// else the newest killed one (so the UI can say it stopped), else null.
function pickNarrator(roster) {
  let live = null;
  let dead = null;
  for (const [name, m] of Object.entries(roster.members)) {
    if (!m || typeof m !== 'object') continue;
    const rank = narratorRank(name, m);
    if (rank < 0) continue;
    if (m.killed) {
      if (!dead || rank > dead.rank) dead = { name, rank };
    } else if (!live || rank > live.rank) {
      live = { name, rank };
    }
  }
  return (live || dead)?.name || null;
}

// How a member's newest step failed, or null. A model error (ERROR_MESSAGE)
// counts at once; a failed tool step only once it has stayed the newest step
// for STEP_ERROR_SETTLE_MS (the agent normally carries on after one).
function stepFailure(tr, now) {
  const last = tr?.last;
  if (!last) return null;
  const modelError = last.type === 'ERROR_MESSAGE';
  const settledToolError = last.status === 'ERROR' && now - (tr.mtime || 0) >= STEP_ERROR_SETTLE_MS;
  if (!modelError && !settledToolError) return null;
  const text = last.error || (modelError ? 'model error' : 'step failed');
  return { kind: QUOTA_RE.test(text) ? 'quota' : 'error', text };
}

// System items for the Overview's chrome, computed from rosters and
// transcripts only (never from narrator files): members whose newest step
// failed (error or quota), and members that are neither parked nor moving
// (stuck for more than STUCK_MS). The narrator is reported by narratorState.
function systemAttention(members, now) {
  const out = [];
  for (const m of members) {
    if (m.killed || m.narrator || m.roleWorker) continue;
    if (m.failure) {
      out.push({
        id: `sys-${m.name}-${m.failure.kind}`,
        kind: m.failure.kind,
        member: m.name,
        conversation_id: m.conversation_id,
        text: `${m.name}: last step failed (${m.failure.kind === 'quota' ? 'quota' : 'error'})`,
        detail: m.failure.text,
      });
    } else if (m.status === 'working' && m.transcriptMtime && now - m.transcriptMtime > STUCK_MS) {
      // The UI renders "for N min" from `since`, so the text (and the etag)
      // doesn't change every minute.
      out.push({
        id: `sys-${m.name}-stuck`,
        kind: 'stuck',
        member: m.name,
        conversation_id: m.conversation_id,
        text: `${m.name}: no progress, and not waiting on the board`,
        detail: '',
        since: m.transcriptMtime,
      });
    }
  }
  return out;
}

// absent | killed | paused (newest step failed) | waiting | working.
function narratorStateOf(m) {
  if (!m) return 'absent';
  if (m.killed) return 'killed';
  if (m.failure) return 'paused';
  return m.status === 'waiting' ? 'waiting' : 'working';
}

// Parsed view.json per swarm, keeping the last good parse while the file is
// torn, invalid or too large. A deleted file forgets it.
const viewJsonCache = new Map(); // swarm id -> {key, error, good: {json, mtime} | null}
function parseViewJson(id, f) {
  if (f.size < 0) {
    viewJsonCache.delete(id);
    return { json: null, mtime: 0, error: '' };
  }
  const key = `${f.mtime}:${f.size}`;
  let c = viewJsonCache.get(id);
  if (!c || c.key !== key) {
    let parsed = null;
    let error = '';
    if (f.tooLarge) {
      error = 'too_large';
    } else {
      try {
        const v = JSON.parse(f.text ?? '');
        if (v && typeof v === 'object' && !Array.isArray(v)) parsed = v;
        else error = 'invalid';
      } catch {
        error = 'invalid';
      }
    }
    c = { key, error, good: parsed ? { json: parsed, mtime: f.mtime } : c?.good || null };
    viewJsonCache.set(id, c);
  }
  return { json: c.good?.json || null, mtime: c.good?.mtime || 0, error: c.error };
}

// The narrator's files live next to board.jsonl; if there is no view.json
// there, fall back to the narrator's own brain dir (<narratorConv>/swarm_view).
function readView(appData, id, dir, narrator) {
  let vdir = dir;
  let source = 'swarm';
  let jf = readCached(join(dir, 'view.json'), VIEW_MAX_BYTES);
  const conv = narrator?.conversation_id;
  if (jf.size < 0 && typeof conv === 'string' && ID_RE.test(conv)) {
    const alt = join(appData, 'brain', conv, 'swarm_view');
    const altJson = readCached(join(alt, 'view.json'), VIEW_MAX_BYTES);
    if (altJson.size >= 0) {
      vdir = alt;
      source = 'narrator';
      jf = altJson;
    }
  }
  const hf = readCached(join(vdir, 'view.html'), VIEW_MAX_BYTES);
  const { json, mtime, error } = parseViewJson(id, jf);
  let htmlError = '';
  if (hf.tooLarge) htmlError = 'too_large';
  else if (hf.size >= 0 && !hf.text?.trim()) htmlError = 'empty';
  return {
    etag: `${vdir}|${jf.mtime}:${jf.size}|${hf.mtime}:${hf.size}`,
    view: {
      // Whether the narrator has written anything (view.json or view.html).
      // Without either, the UI shows no Overview at all.
      exists: jf.size >= 0 || hf.size >= 0,
      // Where the files were read from: `swarm` (the swarm dir) or `narrator`
      // (its own swarm_view dir). The directory itself is not sent.
      source,
      json,
      jsonMtime: mtime,
      jsonError: error,
      html: htmlError ? null : hf.text,
      // Changes exactly when view.html (or the dir it is read from) does, so
      // the UI rebuilds the frame only then.
      htmlKey: hf.size < 0 ? '' : `${source}|${hf.mtime}:${hf.size}`,
      htmlMtime: hf.size < 0 ? 0 : hf.mtime,
      htmlError,
    },
    rawJson: jf.text || '',
  };
}

// `file` action targets that are safe to open: absolute, no `%`, NUL,
// backslash or `.`/`..` segment, existing regular files whose real path lies
// under <appData>/brain/. Returns {target: file URI} for the survivors; the UI
// opens a `file` NAVIGATE only for an exact key of this map.
const VIEW_LISTS = ['attention', 'workstreams', 'milestones', 'risks'];
const MAX_FILE_ACTIONS = 64;
function allowedFilesOf(appData, json) {
  const out = {};
  if (!json) return out;
  let brainReal;
  try {
    brainReal = realpathSync(join(appData, 'brain'));
  } catch {
    return out;
  }
  const root = brainReal.endsWith(sep) ? brainReal : brainReal + sep;
  let seen = 0;
  for (const list of VIEW_LISTS) {
    if (!Array.isArray(json[list])) continue;
    for (const item of json[list]) {
      if (!Array.isArray(item?.actions)) continue;
      for (const a of item.actions) {
        if (a?.type !== 'file' || typeof a.target !== 'string' || Object.hasOwn(out, a.target)) continue;
        if (++seen > MAX_FILE_ACTIONS) return out;
        const real = canonicalFile(a.target, root);
        if (real) out[a.target] = pathToFileURL(real).href;
      }
    }
  }
  return out;
}

function canonicalFile(target, root) {
  if (!target || target.length > 4096 || !isAbsolute(target)) return null;
  if (process.platform === 'win32' ? /[%\0]/.test(target) : /[%\0\\]/.test(target)) return null;
  if (target.split(/[\\/]/).some((seg) => seg === '.' || seg === '..')) return null;
  try {
    const real = realpathSync(target);
    return real.startsWith(root) && statSync(real).isFile() ? real : null;
  } catch {
    return null;
  }
}

// A file board: members.json and board.jsonl in the swarm dir.
function snapshot(appData, id) {
  const dir = swarmDir(appData, id);
  const membersFile = readCached(join(dir, 'members.json'));
  const boardFile = readCached(join(dir, 'board.jsonl'));
  return buildSnapshot(appData, id, dir, {
    roster: parseRoster(membersFile.text || '') || { title: '', members: {} },
    msgs: parseBoard(boardFile.text),
    sourceKey: `${membersFile.mtime}:${membersFile.size}|${boardFile.mtime}:${boardFile.size}`,
    raw: { members: membersFile.text || '', board: boardFile.text || '' },
  });
}

// For "@#channel" addressing: a function (message index, text) ->
// {channels, members}, the active channels the text addresses as of that
// message and the members they call on (each member's set as of that
// message); null when the board has no channels or no "@#" at all.
function channelAddressees(roster, names, msgs, channels) {
  if (!channels.length || !msgs.some((m) => String(m.text ?? '').includes('@#'))) return null;
  const active = activeAt(channels, msgs);
  const sets = new Map(names.map((n) => {
    const m = roster.members[n];
    const role = typeof m.role === 'string' ? m.role : '';
    const current = currentChannels(n, Array.isArray(m.channels) ? m.channels : null, msgs);
    return [n, channelsAt(n, current, msgs).map((set) => effectiveChannels(role, set, n))];
  }));
  return (i, text) => {
    const chans = channelMentions(text).filter((c) => c !== MAIN && active[i]?.(c));
    if (!chans.length) return null;
    const members = names.filter((n) => {
      const set = sets.get(n)[i];
      return set.includes(ALL) || chans.some((c) => set.includes(c));
    });
    return { channels: chans, members };
  };
}

// The snapshot of one swarm from its roster ({title, members} in the
// members.json shape) and board messages, whichever backend they came from.
// remote (remote boards only) adds owner badges, phases and last-seen; a file
// board's snapshot has exactly the fields it always had.
function buildSnapshot(appData, id, dir, { roster, msgs, sourceKey, raw, remote = null }) {
  // The title can arrive later from agentapi, so it is part of the etag.
  const title = roster.title || convTitle(id);
  const head = msgs.length ? Number(msgs[msgs.length - 1].seq) || msgs.length : 0;
  const names = Object.keys(roster.members).filter((n) => roster.members[n] && typeof roster.members[n] === 'object');
  // What a `session` model request resolves to on this board (the model hook writes it).
  const sessionModel = remote ? '' : readSessionModel(dir);
  // Member transcripts change as members work, so their stats are in the etag.
  const transcripts = new Map(names.map((n) => [n, summarizeTranscript(appData, roster.members[n].conversation_id)]));
  const trKeys = names.map((n) => `${n}:${transcripts.get(n).key}`).join(',');
  const narratorName = pickNarrator(roster);
  const vf = readView(appData, id, dir, narratorName ? roster.members[narratorName] : null);
  // The coordinator's plan (goal, threads, task names), used when no narrator
  // has written a view.json.
  const posted = BUILTIN_OVERVIEW ? postedFrames(roster, msgs) : [];
  const pf = BUILTIN_OVERVIEW ? readCached(join(dir, 'plan.json'), VIEW_MAX_BYTES) : { size: -1 };
  let plan = null;
  if (pf.size >= 0 && !pf.tooLarge) {
    try {
      const v = JSON.parse(pf.text ?? '');
      if (v && typeof v === 'object' && !Array.isArray(v)) plan = v;
    } catch {}
  }
  const allowedFiles = allowedFilesOf(appData, vf.view.json);
  const now = Date.now();
  // Failures and "stuck" depend on the clock as well as on file stats.
  const failures = new Map(names.map((n) => [n, stepFailure(transcripts.get(n).summary, now)]));
  const stuckKeys = names.map((n) => {
    const f = failures.get(n);
    const mt = transcripts.get(n).summary?.mtime || 0;
    return `${f ? f.kind : ''}${mt && now - mt > STUCK_MS ? 's' : ''}`;
  }).join(',');
  // The model label is in the etag so a model change (a switch, or the hook
  // recording one) reaches the client without another field changing.
  const modelKeys = names.map((n) => memberModelLabel(roster.members[n], sessionModel)).join(',');
  const etag = `${sourceKey}|${title}|${trKeys}|${vf.etag}|${pf.mtime}:${pf.size}|${Object.keys(allowedFiles).join('\0')}|${stuckKeys}|${modelKeys}`;

  const posts = new Map();
  const edges = new Map(); // "from\0to" -> edge
  // Channels exist only once a member created one; until then every new
  // field below is left out, so a one-channel swarm's snapshot is unchanged.
  const channels = remote?.channels || channelRegistry(msgs);
  // "@#channel" calls on the members whose set as of the message holds the
  // channel (or "*"), while it is active then (channels.mjs mentionsIn).
  const byChannel = channelAddressees(roster, names, msgs, channels);
  const messages = msgs.map((m, i) => {
    const seq = Number(m.seq) || 0;
    const agent = String(m.agent ?? '');
    const text = String(m.text ?? '');
    const time = m.time || '';
    const ts = boardTimeMs(time);
    const channel = channelOf(m);
    const control = controlOf(m);
    const chanFields = { ...(channel !== MAIN ? { channel } : {}), ...(control ? { control } : {}) };
    // A channel change is a system line, not a post: no mentions, no counts.
    if (control) return { seq, agent, text, time, ts, mentions: [], all: false, ...chanFields };
    const { mentions, all } = mentionsOf(text, names);
    // channel_mentions: the active channels it addresses; via_channel: the
    // members they call on that it does not name.
    const ch = byChannel ? byChannel(i, text) : null;
    const viaChannel = ch ? ch.members.filter((n) => n !== agent && !mentions.includes(n)) : [];
    const chanMentionFields = ch?.channels.length ? { channel_mentions: ch.channels, via_channel: viaChannel } : {};
    const p = posts.get(agent) || { posts: 0, lastPostSeq: 0, lastPostTime: '', lastPostTs: null };
    posts.set(agent, { posts: p.posts + 1, lastPostSeq: seq, lastPostTime: time, lastPostTs: ts });
    if (!isRoleWorker(agent, roster.members[agent])) {
      const direct = new Set([...mentions, ...viaChannel]);
      const targets = new Set(all ? names : mentions);
      for (const n of direct) targets.add(n);
      targets.delete(agent);
      for (const to of targets) {
        if (isRoleWorker(to, roster.members[to])) continue;
        const key = `${agent}\0${to}`;
        const e = edges.get(key) || { from: agent, to, count: 0, direct: 0, viaAll: 0 };
        e.count++;
        if (direct.has(to)) e.direct++;
        else e.viaAll++;
        edges.set(key, e);
      }
    }
    return { seq, agent, text, time, ts, mentions, all, ...chanFields, ...chanMentionFields };
  });

  const members = names.map((name) => {
    const m = roster.members[name];
    const last = Number(m.last_read_seq) || 0;
    const tr = transcripts.get(name).summary;
    const role = roleOf(name, m);
    // With channels, unread counts only what the member sees (its set as of
    // each message): the CLI computes it in Go for remote boards; file
    // boards use the JS mirror. `channels` is then the member's effective
    // set (main implied; ["*"] = every channel).
    let unread = Math.max(0, head - last);
    let chanFields = {};
    if (channels.length) {
      const storedRole = typeof m.role === 'string' ? m.role : '';
      const current = currentChannels(name, Array.isArray(m.channels) ? m.channels : null, msgs);
      unread = remote && Number.isFinite(m.unread) ? Math.max(0, m.unread) :
        visibleUnread(name, storedRole, current, msgs, last, channels);
      chanFields = { channels: effectiveChannels(storedRole, current, name).filter((c) => typeof c === 'string') };
    }
    return {
      name,
      conversation_id: m.conversation_id || '',
      model: memberModelLabel(m, sessionModel),
      role,
      wake_mode: m.wake_mode || '',
      status: remote ? remoteStatusOf(m) : statusOf(m),
      wait_since: Number(m.wait_since) || 0,
      last_read_seq: last,
      unread,
      last_nudged_seq: Number(m.last_nudged_seq) || 0,
      last_park_blocked_seq: Number(m.last_park_blocked_seq) || 0,
      killed: !!m.killed,
      steps: tr?.steps || 0,
      activity: tr?.activity || '',
      activityKind: tr?.activityKind || '',
      narrator: narratorRank(name, m) >= 0,
      roleWorker: isRoleWorker(name, m),
      failure: failures.get(name),
      transcriptMtime: tr?.mtime || 0,
      ...(posts.get(name) || { posts: 0, lastPostSeq: 0, lastPostTime: '', lastPostTs: null }),
      ...chanFields,
      ...(remote ? remoteMemberFields(m, posts.get(name)?.lastPostTs) : {}),
    };
  });
  members.sort((a, b) =>
    (a.role === 'coordinator' ? 0 : 1) - (b.role === 'coordinator' ? 0 : 1) || a.name.localeCompare(b.name));

  const nm = narratorName ? members.find((m) => m.name === narratorName) : null;
  const narratorState = narratorStateOf(nm);
  return {
    id,
    title,
    etag,
    head,
    members,
    messages,
    ...(channels.length ? { channels } : {}),
    edges: [...edges.values()],
    narrator: nm ? {
      name: nm.name,
      conversation_id: nm.conversation_id,
      state: narratorState,
      reason: nm.failure?.kind || '',
      detail: nm.failure?.text || '',
      last_read_seq: nm.last_read_seq,
      transcriptMtime: nm.transcriptMtime,
    } : null,
    narratorState,
    view: vf.view,
    ...(plan ? { plan } : {}),
    ...(posted.length ? { frames: posted } : {}),
    ...(BUILTIN_OVERVIEW ? { builtinOverview: true } : {}),
    ...(GRAPH_TAB ? { graphTab: true } : {}),
    ...(RAW_TAB ? { rawTab: true } : {}),
    allowedFiles,
    urlHosts: URL_HOSTS,
    systemAttention: systemAttention(members, now),
    // The files themselves: the route sends this only when the client asks
    // (?raw=1), so a poll no longer carries the board twice.
    raw: { ...raw, view: vf.rawJson },
    ...(remote ? { source: remote.source } : {}),
  };
}

// ---------------------------------------------------------------------------
// Remote boards (swarms with members on several machines; see REMOTE). The
// swarm dir of a remote swarm holds only the anchor, swarm.json {backend,
// swarm_id, db}; the roster and board come from the board's CLI in viewer mode:
//
//   <cli> swarm --json --watch [--db <db>] <swarm_id>
//
// one NDJSON document per change:
//   {version, swarm_id, title, head, since, members: {name: member},
//    messages: [message with seq > since]}
// Members carry the members.json keys (conversation_id, role, killed, model,
// wake_mode, cursors...) plus the owner LS (node_id, owner_kind, owner_label),
// phase, desired_state and updated_at. `model` is the display label computed by
// memberModelLabel (the effective selector, not the roster's request), and a
// remote board has no local session_model.json, so `session` shows its selector.
// There is no presence: a member on
// another LS shows when it was last seen. One watcher process runs per shown
// swarm and is stopped once the pane stops asking for it. Everything in a
// document is untrusted (any writer of the board DB, including the user's
// agent identity, can write it): it is only ever rendered escaped.
// ---------------------------------------------------------------------------

const ANCHOR_FILE = 'swarm.json';
// A watcher nobody has asked for in this long is stopped. Long enough to
// outlast a first board warm-up (~40 s) and a hidden pane's timers, which
// browsers throttle to about once a minute.
const WATCH_IDLE_MS = 3 * 60 * 1000;
// A watcher that exited is restarted no sooner than this.
const WATCH_RESTART_MS = 5 * 1000;
// Rosters of the anchored swarms (to find a worker's swarm) are read in one
// CLI run for the newest MAX_PROBES anchors, at most every PROBE_TTL_MS; each
// run that finds nothing doubles the wait, up to PROBE_MAX_MS, until the set
// of anchors changes or a conversation is found.
const PROBE_TTL_MS = 60 * 1000;
const PROBE_MAX_MS = 15 * 60 * 1000;
const MAX_PROBES = 8;

function readAnchor(appData, id) {
  const { text } = readCached(join(swarmDir(appData, id), ANCHOR_FILE), 4096);
  if (text == null) return null;
  try {
    const a = JSON.parse(text);
    if (!REMOTE || a?.backend !== REMOTE.backend || typeof a.swarm_id !== 'string' || !ID_RE.test(a.swarm_id)) return null;
    return { swarmId: a.swarm_id, db: typeof a.db === 'string' ? a.db : '' };
  } catch {
    return null;
  }
}

function locateRemote(id) {
  for (const appData of findAppDataDirs()) {
    const anchor = readAnchor(appData, id);
    if (anchor) return { appData, anchor };
  }
  return null;
}

const executable = (p) => {
  try {
    accessSync(p, fsConstants.X_OK);
    return statSync(p).isFile();
  } catch {
    return false;
  }
};

// The board's CLI: $cli_env if set, else the first REMOTE.cli entry that is an
// executable path or, for a bare name, an executable on PATH. A $cli_env path
// that is not executable is skipped, so a stale path in the environment falls
// back to REMOTE.cli.
let cliPath = { at: 0, path: '' };
function swarmCLI() {
  const override = REMOTE.cliEnv && process.env[REMOTE.cliEnv];
  if (override && (!override.includes('/') || executable(override))) return override;
  if (Date.now() - cliPath.at < PROBE_TTL_MS) return cliPath.path;
  let path = '';
  for (const c of REMOTE.cli) {
    const hasSep = c.includes('/') || c.includes('\\');
    const hits = hasSep ? [c] : (process.env.PATH || '').split(delimiter).filter(Boolean).map((d) => join(d, c));
    path = hits.find(executable) || '';
    if (path) break;
  }
  cliPath = { at: Date.now(), path: path || REMOTE.cli[REMOTE.cli.length - 1] };
  return cliPath.path;
}

// TERM=dumb keeps terminal escapes (e.g. a launcher's `tput cnorm`) out of
// the NDJSON the CLI writes to stdout when the sidecar inherits a real TERM.
const cliEnv = () => ({ ...process.env, TERM: 'dumb' });

const cliArgs = (anchor, extra) =>
  ['swarm', '--json', ...extra, ...(anchor.db ? ['--db', anchor.db] : []), anchor.swarmId];

// A roster's `model` is the request ("default", "session", a tier alias or a
// concrete selector) and `applied_model` is what the process launches with. Show
// the effective model: the reserved tier groups read as "full tier" & co., and a
// `session` request resolves to the model the Polytoken session is running.
const MODEL_TIER_LABELS = {
  '@mg:polytoken:default_model_full': 'full tier',
  '@mg:polytoken:default_model_mini': 'mini tier',
  '@mg:polytoken:default_model_nano': 'nano tier',
};

function memberModelLabel(m, sessionModel) {
  const request = typeof m.model === 'string' ? m.model : '';
  const applied = typeof m.applied_model === 'string' ? m.applied_model : '';
  if (request === 'session') return sessionModel || MODEL_TIER_LABELS[applied] || applied || request;
  const selector = applied || request;
  return MODEL_TIER_LABELS[selector] || selector;
}

// The model this session is running, as recorded by pt_swarm's model hook
// (POLYTOKEN_MODEL_NAME), or '' when that hook is not installed.
function readSessionModel(dir) {
  const { text } = readCached(join(dir, 'session_model.json'), 4096);
  if (!text) return '';
  try {
    const v = JSON.parse(text);
    return typeof v?.model === 'string' ? v.model : '';
  } catch {
    return '';
  }
}

// Keeps the members.json view of a document's member: the legacy `killed`
// and `model` keys stay authoritative for everything the file path computes.
function normalizeMembers(raw, sessionModel = '') {
  const out = {};
  if (!raw || typeof raw !== 'object') return out;
  for (const [name, m] of Object.entries(raw)) {
    if (!m || typeof m !== 'object') continue;
    const phase = typeof m.phase === 'string' && m.phase ? m.phase : 'RUNNING';
    out[name] = {
      ...m,
      phase,
      killed: !!m.killed || m.desired_state === 'KILLED' || phase === 'KILLED',
      model: memberModelLabel(m, sessionModel),
    };
  }
  return out;
}

// The document's channel registry, [{name, topic, created_by, archived}]
// (validated shapes only), or null when it has none (a swarm without
// channels, or a CLI that predates them: the log is used instead).
function normalizeChannels(raw) {
  if (!Array.isArray(raw) || !raw.length) return null;
  const out = [];
  for (const c of raw) {
    if (!c || typeof c !== 'object' || typeof c.name !== 'string' || !c.name || c.name === MAIN) continue;
    out.push({
      name: c.name,
      topic: typeof c.topic === 'string' ? c.topic : '',
      created_by: typeof c.created_by === 'string' ? c.created_by : '',
      archived: !!c.archived,
    });
  }
  return out.length ? out : null;
}

// Applies one watcher line. since 0 is a full snapshot (the first line);
// otherwise the messages follow `since`. A delta that would leave a gap after
// the messages we hold (a line was lost or unreadable) is not applied: the
// watcher is restarted, so its first line is a full snapshot again. Returns
// false when the line was rejected.
function applyDoc(w, doc) {
  if (!doc || typeof doc !== 'object' || doc.swarm_id !== w.anchor.swarmId) return false;
  const msgs = Array.isArray(doc.messages) ? doc.messages.filter((m) => m && typeof m === 'object') : [];
  const since = Number(doc.since) || 0;
  const held = w.messages.length ? Number(w.messages[w.messages.length - 1].seq) || 0 : 0;
  if (since === 0) {
    w.messages = msgs;
  } else if (since <= held) {
    w.messages = w.messages.concat(msgs.filter((m) => (Number(m.seq) || 0) > held));
  } else {
    return false;
  }
  w.roster = { title: typeof doc.title === 'string' ? doc.title : '', members: normalizeMembers(doc.members) };
  w.channels = normalizeChannels(doc.channels);
  w.loaded = true;
  w.error = '';
  w.version++;
  return true;
}

// Restarts a watcher whose stream can no longer be trusted (a lost or bad
// line): a request after WATCH_RESTART_MS respawns it, and its first line is a
// full snapshot. The throttle stays, so a CLI that keeps sending bad lines is
// not re-warmed on every poll.
function resync(w, why) {
  console.warn(`[swarm_board] resyncing the watcher of ${w.anchor.swarmId}: ${why}`);
  const c = w.child;
  w.child = null;
  w.exitedAt = Date.now();
  c?.kill();
}

const watches = new Map(); // swarm id -> watch
function remoteWatch(anchor) {
  let w = watches.get(anchor.swarmId);
  if (!w) {
    w = { anchor, child: null, roster: { title: '', members: {} }, messages: [], loaded: false,
      error: '', version: 0, lastUsed: 0, exitedAt: 0 };
    watches.set(anchor.swarmId, w);
  }
  w.lastUsed = Date.now();
  if (!w.child && Date.now() - w.exitedAt >= WATCH_RESTART_MS) startWatch(w);
  return w;
}

function startWatch(w) {
  let child;
  try {
    // stdin stays open for the child's life: the watcher exits at its EOF,
    // so it dies with this process even if the swarm never changes again.
    child = spawn(swarmCLI(), cliArgs(w.anchor, ['--watch']), { env: cliEnv(), stdio: ['pipe', 'pipe', 'pipe'] });
  } catch (e) {
    w.error = String(e?.message || e);
    w.exitedAt = Date.now();
    w.version++;
    return;
  }
  w.child = child;
  let buf = '';
  let errTail = '';
  child.stdout.setEncoding('utf-8');
  child.stdout.on('data', (chunk) => {
    if (w.child !== child) return; // replaced or resynced: ignore its tail
    buf += chunk;
    let i;
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i);
      buf = buf.slice(i + 1);
      if (!line.trim()) continue;
      let doc;
      try {
        doc = JSON.parse(line);
      } catch (e) {
        resync(w, `bad line: ${e}`);
        return;
      }
      if (!applyDoc(w, doc)) {
        resync(w, `delta since #${doc?.since} does not follow the held messages`);
        return;
      }
    }
  });
  child.stderr.setEncoding('utf-8');
  child.stderr.on('data', (chunk) => {
    errTail = (errTail + chunk).slice(-2048);
  });
  const done = (why) => {
    if (w.child !== child) return;
    w.child = null;
    w.exitedAt = Date.now();
    const tail = oneLine(errTail, 240);
    w.error = why?.code === 'ENOENT' ? `board CLI not found (${swarmCLI()})${REMOTE.cliEnv ? `; set ${REMOTE.cliEnv}` : ''}` :
      tail || (why ? String(why.message || why) : 'swarm watcher exited');
    w.version++;
  };
  child.on('error', done);
  child.on('exit', (code, signal) => done(code === 0 ? null : new Error(`swarm watcher exited (${signal || code})`)));
}

setInterval(() => {
  const now = Date.now();
  for (const [id, w] of watches) {
    if (now - w.lastUsed < WATCH_IDLE_MS) continue;
    if (w.child) {
      const c = w.child;
      w.child = null;
      c.kill();
    }
    watches.delete(id);
  }
}, 10 * 1000).unref();

// The rosters of the anchored swarms on this host, from one `--roster-only`
// run over all of them, to find the swarm of a worker whose board lives under
// another conversation. A conversation's swarm never changes once found.
const probeState = { at: 0, wait: PROBE_TTL_MS, key: '', pending: false, rosters: new Map(), missed: false };
const convSwarm = new Map(); // conv -> {appData, id, anchor}
const askedConvs = new Set(); // conversations that have asked; a new one resets the backoff

function probeRosters(dirs) {
  const key = dirs.map((d) => d.anchor.swarmId).join(',');
  const now = Date.now();
  if (probeState.pending || !dirs.length) return probeState.rosters;
  if (key === probeState.key && now - probeState.at < probeState.wait) return probeState.rosters;
  if (key !== probeState.key) probeState.wait = PROBE_TTL_MS;
  else if (probeState.missed) probeState.wait = Math.min(probeState.wait * 2, PROBE_MAX_MS);
  probeState.key = key;
  probeState.at = now;
  probeState.pending = true;
  probeState.missed = false;
  // One DB per user: the anchors normally all name it; pass it when they agree.
  const dbs = [...new Set(dirs.map((d) => d.anchor.db))];
  const args = ['swarm', '--json', '--roster-only', ...(dbs.length === 1 && dbs[0] ? ['--db', dbs[0]] : []),
    ...dirs.map((d) => d.anchor.swarmId)];
  execFile(swarmCLI(), args, { env: cliEnv(), encoding: 'utf-8', timeout: 120000, maxBuffer: 16 << 20 }, (err, stdout) => {
    probeState.pending = false;
    probeState.at = Date.now();
    const rosters = new Map();
    for (const line of String(stdout || '').split('\n')) {
      if (!line.trim()) continue;
      try {
        const doc = JSON.parse(line);
        if (typeof doc?.swarm_id === 'string' && doc.members) {
          rosters.set(doc.swarm_id, { title: '', members: normalizeMembers(doc.members) });
        }
      } catch {
        // A bad line is skipped; the rest still count.
      }
    }
    if (rosters.size || !err) probeState.rosters = rosters;
  });
  return probeState.rosters;
}

function remoteMemberOf(conv) {
  const cached = convSwarm.get(conv);
  if (cached) {
    const roster = remoteWatch(cached.anchor).roster;
    return { appData: cached.appData, swarm: cached.id, member: memberNamed(roster, conv), remote: cached.anchor };
  }
  const dirs = [];
  for (const { appData, id } of scanSwarmDirs().remote.slice(0, MAX_PROBES)) {
    const anchor = readAnchor(appData, id);
    if (anchor) dirs.push({ appData, id, anchor });
  }
  if (!askedConvs.has(conv)) {
    // A conversation asking for the first time (a worker just placed here)
    // gets the short wait again; the run rate stays at most one per minute.
    if (askedConvs.size > 1000) askedConvs.clear();
    askedConvs.add(conv);
    probeState.wait = PROBE_TTL_MS;
    probeState.missed = false;
  }
  const rosters = probeRosters(dirs);
  for (const { appData, id, anchor } of dirs) {
    const watched = watches.get(anchor.swarmId);
    const member = memberNamed(watched?.loaded ? watched.roster : rosters.get(anchor.swarmId), conv);
    if (member) {
      convSwarm.set(conv, { appData, id, anchor });
      probeState.wait = PROBE_TTL_MS;
      remoteWatch(anchor);
      return { appData, swarm: id, member, remote: anchor };
    }
  }
  if (!probeState.pending) probeState.missed = true;
  return null;
}

// Whether a member's conversation lives on this host (so the pane can open
// it in place): its brain dir exists in one of the local app data dirs.
function localConversation(conv) {
  if (!conv || !ID_RE.test(conv)) return false;
  return findAppDataDirs().some((d) => existsSync(join(d, 'brain', conv)));
}

// The owner badge: the LS that runs the member (its OwnerLabel, kind
// HUMAN/AGENT), or null for rows that don't record one.
function ownerOf(m) {
  const label = typeof m.owner_label === 'string' ? m.owner_label.slice(0, 32) : '';
  if (!label) return null;
  return { label, kind: m.owner_kind === 'AGENT' ? 'AGENT' : m.owner_kind === 'HUMAN' ? 'HUMAN' : '' };
}

// A remote member's state comes from its roster row like a local one's; there
// is no presence, so the pane shows when it was last seen instead.
function remoteStatusOf(m) {
  return statusOf(m);
}

// When a member on another LS was last seen: its row's last write
// (updated_at, refreshed on roster reads) or its latest post, whichever is
// newer; null when neither is known.
function lastSeenOf(m, lastPostTs) {
  const upd = typeof m.updated_at === 'string' ? Date.parse(m.updated_at) : NaN;
  const seen = Math.max(Number.isFinite(upd) ? upd : 0, Number.isFinite(lastPostTs) ? lastPostTs : 0);
  return seen || null;
}

function remoteMemberFields(m, lastPostTs) {
  const local = localConversation(m.conversation_id || '');
  return {
    phase: m.phase,
    owner: ownerOf(m),
    local,
    last_seen: local ? null : lastSeenOf(m, lastPostTs),
  };
}

function remoteSnapshot(appData, id, anchor) {
  const w = remoteWatch(anchor);
  const owners = new Map(); // label -> kind
  for (const m of Object.values(w.roster.members)) {
    const o = ownerOf(m);
    if (o && !owners.has(o.label)) owners.set(o.label, o.kind);
  }
  const source = {
    kind: 'remote',
    label: REMOTE.label,
    state: w.loaded ? (w.child ? 'live' : 'stale') : (w.error ? 'error' : 'connecting'),
    error: w.error,
    swarm_id: anchor.swarmId,
    db: anchor.db,
    owners: [...owners].map(([label, kind]) => ({ label, kind })),
  };
  return buildSnapshot(appData, id, swarmDir(appData, id), {
    roster: w.roster,
    msgs: w.messages,
    sourceKey: `remote:${w.version}:${source.state}`,
    raw: {
      members: JSON.stringify(w.roster, null, 2),
      board: w.messages.map((m) => JSON.stringify(m)).join('\n') + (w.messages.length ? '\n' : ''),
    },
    remote: { source, channels: w.channels || null },
  });
}

// ---------------------------------------------------------------------------
// HTTP routes.
// ---------------------------------------------------------------------------

const app = new SidecarApp();

app.page('/', () => readFileSync(join(__dirname, 'index.html'), 'utf-8'));

for (const [route, [file, type]] of Object.entries(STATIC)) {
  app.api(route, () => {
    try {
      return new Response(readFileSync(join(__dirname, file), 'utf-8'), { contentType: type });
    } catch (err) {
      console.error(`[swarm_board] failed to read ${file}:`, err);
      return new Response('Not found', { status: 404 });
    }
  }, 'GET');
}

// Progress reel frames: images the narrator listed in view.json `reel.frames`
// (UI work only). Served only if listed there and under <appData>/brain/.
// Screenshots members flag on the board, one per line:
//   Screenshot: /abs/path.png | what changed
// The pane builds the progress timeline from these when no narrator lists
// frames. Only workers' posts count, and /api/frame still checks each path is
// an image under <appData>/brain/.
const SHOT_RE = /^\s*(?:[-*]\s*)?screenshot:\s*`?((?:\/|[a-zA-Z]:[\\/])[^\s`|]+\.(?:png|jpe?g|webp))`?\s*(?:\|\s*(.+?))?\s*$/gim;
const MAX_POSTED_FRAMES = 24;
function postedFrames(roster, msgs) {
  const out = new Map(); // path -> frame (a re-posted path moves to its latest post)
  for (const m of msgs) {
    // Workers only: not the coordinator (its briefs quote the format), nor a
    // narrator or monitor, whatever role string it was added with.
    const who = roster.members?.[m.agent];
    if (!who || who.role === 'coordinator' || isRoleWorker(m.agent, who)) continue;
    for (const g of String(m.text ?? '').matchAll(SHOT_RE)) {
      out.delete(g[1]);
      out.set(g[1], { path: g[1], seq: Number(m.seq) || 0, caption: String(g[2] || '').slice(0, 80) });
    }
  }
  return [...out.values()].slice(-MAX_POSTED_FRAMES);
}

const FRAME_EXT = { '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp' };
const MAX_FRAME_BYTES = 8 * 1024 * 1024;
app.api('/api/frame', (data) => {
  const notFound = () => new Response('not found', { status: 404 });
  const conv = data.conversationId || app.config.conversationId || '';
  const target = typeof data.path === 'string' ? data.path : '';
  if (!conv || !ID_RE.test(conv) || !target) return notFound();
  const { appData, swarm, remote } = resolve(conv);
  if (!swarm) return notFound();
  // The same view.json /api/swarm shows: the narrator's fallback dir and the
  // last good parse while the file is being rewritten, for file and remote
  // boards. Only the roster is read, not the whole board.
  const roster = remote ? remoteWatch(remote).roster : rosterAt(appData, swarm);
  const narratorName = roster ? pickNarrator(roster) : null;
  const view = readView(appData, swarm, swarmDir(appData, swarm), narratorName ? roster.members[narratorName] : null).view?.json;
  const frames = Array.isArray(view?.reel?.frames) ? view.reel.frames : [];
  const listed = frames.some((f) => f && f.path === target) || (BUILTIN_OVERVIEW && !frames.length &&
      postedFrames(roster || { members: {} }, remote ? remoteWatch(remote).messages :
        parseBoard(readCached(join(swarmDir(appData, swarm), 'board.jsonl')).text)).some((f) => f.path === target));
  if (!listed) return notFound();
  const type = FRAME_EXT[(target.match(/\.[a-z]+$/i)?.[0] || '').toLowerCase()];
  let brainReal;
  try { brainReal = realpathSync(join(appData, 'brain')); } catch { return notFound(); }
  const real = type && canonicalFile(target, brainReal.endsWith(sep) ? brainReal : brainReal + sep);
  if (!real) return notFound();
  try {
    if (statSync(real).size > MAX_FRAME_BYTES) return notFound();
    return new Response(readFileSync(real), { contentType: type, headers: { 'Cache-Control': 'private, max-age=3600' } });
  } catch {
    return notFound();
  }
}, 'GET');

// The only data route: the swarm of one conversation (the pane's own unless
// ?conversationId= overrides it, for standalone use). Resolving on every poll
// is what makes a swarm started after the pane opened show up by itself.
app.api('/api/swarm', (data) => {
  const conv = data.conversationId || app.config.conversationId || '';
  if (!conv || !ID_RE.test(conv)) return { conversationId: conv || null, swarm: null, member: null };
  const { appData, swarm, member, remote } = resolve(conv);
  if (!swarm) return { conversationId: conv, swarm: null, member: null };
  const snap = remote ? remoteSnapshot(appData, swarm, remote) : snapshot(appData, swarm);
  const { raw, ...snapFields } = snap;
  // Who "you" are is part of the response, so it is part of the etag.
  const etag = `${swarm}|${member || ''}|${snap.etag}`;
  // The Raw tab asks for the files explicitly; nothing else pays for them.
  const wantsRaw = data.raw === '1';
  if (!wantsRaw && data.etag && data.etag === etag) return { conversationId: conv, swarm, member, unchanged: true, etag };
  return {
    conversationId: conv,
    swarm,
    member,
    ...snapFields,
    ...unseenBy(snap, member),
    ...(wantsRaw ? { raw } : {}),
    etag,
  };
}, 'GET');

// With channels, the seqs of the snapshot's messages that member does not
// see (each judged by its subscriptions as of that message), for the feed's
// "New" divider; nothing otherwise.
function unseenBy(snap, member) {
  const me = member && snap.channels ? snap.members.find((m) => m.name === member) : null;
  if (!me) return {};
  const { visible } = memberView(me.name, me.role, me.channels, snap.messages, snap.channels);
  return { unseen: snap.messages.filter((_, i) => !visible[i]).map((m) => m.seq) };
}

export { app, canonicalFile, locate, resolve, snapshot };

if (process.env.SIDECAR_NO_LISTEN !== '1') {
  app.run();
}

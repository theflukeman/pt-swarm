/**
 * Mentions and channel visibility for the Swarm pane, mirroring the
 * native_swarm board package (board/mentions.go, board.ChannelsAt and
 * board.Member.Sees). Pure functions, no I/O: main.mjs uses them for file
 * boards, and the Go test runs them against the same table as the Go code,
 * board/testdata/sees_cases.json, so the two cannot drift.
 *
 * Shapes are the board's JSON: a message has `channel` ("" or absent = main)
 * and, for a channel change, `control` {op, channel, topic, members, prev};
 * a member has `channels` (absent/null = its role's default, ["*"] = all).
 */

export const MAIN = 'main';
export const ALL = '*';

// ---------------------------------------------------------------------------
// Mentions, mirroring board/mentions.go: "@" at a word start, then the name
// (or "<prefix>" when name is "<prefix>-<digits>"), then a non-name rune or
// end of text.
// ---------------------------------------------------------------------------

const NAME_RUNE = /[\p{L}\p{Nd}_-]/u;

function namedToken(lowerText, lowerName) {
  if (!lowerName) return false;
  for (let i = lowerText.indexOf('@'); i >= 0; i = lowerText.indexOf('@', i + 1)) {
    if (i > 0) {
      // Spreading the last two code units yields the whole preceding rune
      // even when it is a surrogate pair.
      const before = [...lowerText.slice(Math.max(0, i - 2), i)].pop();
      if (NAME_RUNE.test(before)) continue;
    }
    if (!lowerText.startsWith(lowerName, i + 1)) continue;
    const j = i + 1 + lowerName.length;
    if (j >= lowerText.length) return true;
    if (!NAME_RUNE.test(String.fromCodePoint(lowerText.codePointAt(j)))) return true;
  }
  return false;
}

function named(lowerText, lowerName) {
  if (namedToken(lowerText, lowerName)) return true;
  const m = lowerName.match(/^(.+)-\d+$/);
  return m ? namedToken(lowerText, m[1]) : false;
}

// {mentions: the names text calls on by name or copy prefix, all: @all}.
export function mentionsOf(text, names) {
  const lower = String(text || '').toLowerCase();
  return {
    mentions: names.filter((n) => named(lower, String(n).toLowerCase())),
    all: named(lower, 'all'),
  };
}

// board.Mentions: whether text calls on name (by name, copy prefix or @all).
export function mentions(text, name) {
  const lower = String(text || '').toLowerCase();
  return named(lower, String(name || '').toLowerCase()) || named(lower, 'all');
}

// ---------------------------------------------------------------------------
// Channels.
// ---------------------------------------------------------------------------

// A message's channel; "" and absent are main.
export const channelOf = (m) => (typeof m?.channel === 'string' && m.channel ? m.channel : MAIN);

// A message's channel change, or null for an ordinary post.
export const controlOf = (m) => (m?.control && typeof m.control === 'object' && !Array.isArray(m.control) ? m.control : null);

// board.DefaultChannels / EffectiveChannels: a stored set, or (null) its
// role's default: coordinator, narrator and monitor see every channel,
// everyone else only main. A "worker" (or roleless) member named narrator,
// monitor or <that>-<N> counts as that role (older boards and role workers
// added with role "worker"), as board.RoleOf.
const ALL_BY_ROLE = new Set(['coordinator', 'narrator', 'monitor']);
const ROLE_WORKER_NAME = /^(?:narrator|monitor)(?:-\d+)?$/;
export function effectiveChannels(role, set, name) {
  if (Array.isArray(set)) return set;
  if (ALL_BY_ROLE.has(role)) return [ALL];
  if ((!role || role === 'worker') && ROLE_WORKER_NAME.test(String(name ?? ''))) return [ALL];
  return [];
}

// board's touches: a (un)subscribe whose members include name.
function touches(m, name) {
  const c = controlOf(m);
  return !!c && (c.op === 'subscribe' || c.op === 'unsubscribe') && Array.isArray(c.members) && c.members.includes(name);
}

// A prev/next entry: an array, or null for the role default.
const storedSet = (map, name) => {
  const v = map && typeof map === 'object' ? map[name] : null;
  return Array.isArray(v) ? v : null;
};

// board.ChannelsAt: member name's stored set (null = role default) in force
// at each of msgs (contiguous, ascending, ending at the head); current is its
// set now. Before msgs it is the prev of the first control touching name,
// else current; each touching control (in effect at its own message) sets
// it to its next. Returns an array aligned with msgs.
export function channelsAt(name, current, msgs) {
  let set = Array.isArray(current) ? current : null;
  const first = msgs.find((m) => touches(m, name));
  if (first) set = storedSet(controlOf(first).prev, name);
  return msgs.map((m) => {
    if (touches(m, name)) set = storedSet(controlOf(m).next, name);
    return set;
  });
}

// ---------------------------------------------------------------------------
// "@#channel" (board/channel_mentions.go): "@#parser" calls on every member
// whose set as of the message holds #parser or "*", if #parser exists and is
// not archived as of the message; "@#main" calls on nobody.
// ---------------------------------------------------------------------------

const CHANNEL_NAME = /^[a-z][a-z0-9-]{0,23}$/;
const RESERVED = new Set(['main', 'all', 'here']);

// board.ChannelMentions: the channels text addresses with "@#name", in order
// of first appearance and lowercased, "main" included.
export function channelMentions(text) {
  const lower = String(text || '').toLowerCase();
  const out = [];
  for (let i = lower.indexOf('@#'); i >= 0; i = lower.indexOf('@#', i + 1)) {
    if (i > 0 && NAME_RUNE.test([...lower.slice(Math.max(0, i - 2), i)].pop())) continue;
    let j = i + 2;
    while (j < lower.length) {
      const r = String.fromCodePoint(lower.codePointAt(j));
      if (!NAME_RUNE.test(r)) break;
      j += r.length;
    }
    const name = lower.slice(i + 2, j);
    if ((name === MAIN || (CHANNEL_NAME.test(name) && !RESERVED.has(name))) && !out.includes(name)) out.push(name);
  }
  return out;
}

// board.MentionsIn: whether text calls on name, whose effective set as of the
// message is set: a mention (@name, prefix, @all), or "@#c" for a channel c
// in set (or set holds "*") that active(c) reports active. A null active
// addresses no channel.
export function mentionsIn(text, name, set, active) {
  if (mentions(text, name)) return true;
  if (!active) return false;
  const all = set.includes(ALL);
  return channelMentions(text).some((c) => c !== MAIN && (all || set.includes(c)) && active(c));
}

// board.ActiveAt: for each of msgs (ascending, ending at the head), the
// active test as of that message, from the registry at the head
// ([{name, archived}]) and the create/archive controls in msgs: a channel
// counts from its create on, an archived one only before its archive. Null
// entries when there is no registry.
export function activeAt(registry, msgs) {
  const reg = new Map((Array.isArray(registry) ? registry : []).map((c) => [c.name, c]));
  if (!reg.size) return msgs.map(() => null);
  const created = new Map();
  const archived = new Map();
  for (const m of msgs) {
    const c = controlOf(m);
    if (c?.op === 'create') created.set(c.channel, Number(m.seq) || 0);
    if (c?.op === 'archive') archived.set(c.channel, Number(m.seq) || 0);
  }
  return msgs.map((m) => {
    const seq = Number(m.seq) || 0;
    return (c) => {
      const ch = reg.get(c);
      if (!ch) return false;
      if (created.has(c) && seq < created.get(c)) return false;
      if (ch.archived) return archived.has(c) && seq < archived.get(c);
      return true;
    };
  });
}

// board.SeesIn: whether member name, with effective set `set` in force at
// msg, sees it: main, a channel the set holds (or "*"), its own post, or a
// post that calls on it (mentionsIn with active, the channels' state then).
export function seesIn(name, set, msg, active) {
  const ch = channelOf(msg);
  if (ch === MAIN || msg?.agent === name) return true;
  if (set.includes(ALL) || set.includes(ch)) return true;
  return mentionsIn(msg?.text, name, set, active);
}

// board.View.Unread: whether a visible message counts as unread. Control
// messages never do (they never concern anyone).
export const countsUnread = (msg) => !controlOf(msg);

// What member name (role, current stored set) sees of msgs: {visible, unread}
// as two arrays of booleans aligned with msgs (board.View{Member}.Visible
// and View.Unread). registry is the channel registry at the head.
export function memberView(name, role, current, msgs, registry) {
  const sets = channelsAt(name, current, msgs);
  const active = activeAt(registry, msgs);
  const visible = msgs.map((m, i) => seesIn(name, effectiveChannels(role, sets[i], name), m, active[i]));
  return { visible, unread: msgs.map((m, i) => visible[i] && countsUnread(m)) };
}

// The channel registry rebuilt from the log's control messages, as
// board.ApplyControl builds Roster.Channels: [{name, topic, created_by,
// archived}] in creation order.
export function channelRegistry(msgs) {
  const byName = new Map();
  for (const m of msgs) {
    const c = controlOf(m);
    if (!c || typeof c.channel !== 'string' || !c.channel) continue;
    if (c.op === 'create' && !byName.has(c.channel)) {
      byName.set(c.channel, { name: c.channel, topic: typeof c.topic === 'string' ? c.topic : '', created_by: String(m.agent ?? ''), archived: false });
    } else if (c.op === 'archive') {
      const e = byName.get(c.channel) || { name: c.channel, topic: '', created_by: '', archived: false };
      e.archived = true;
      byName.set(c.channel, e);
    }
  }
  return [...byName.values()];
}

// A member's current stored set: the next of the last control in msgs that
// touches it, else `stored` (members.json is only a cache of the log, as in
// board.RebuildChannels).
export function currentChannels(name, stored, msgs) {
  for (let i = msgs.length - 1; i >= 0; i--) {
    if (touches(msgs[i], name)) return storedSet(controlOf(msgs[i]).next, name);
  }
  return Array.isArray(stored) ? stored : null;
}

// How many of msgs after lastRead member name sees and counts as unread.
export function visibleUnread(name, role, current, msgs, lastRead, registry) {
  const { unread } = memberView(name, role, current, msgs, registry);
  let n = 0;
  for (let i = 0; i < msgs.length; i++) {
    if (unread[i] && (Number(msgs[i].seq) || 0) > lastRead) n++;
  }
  return n;
}

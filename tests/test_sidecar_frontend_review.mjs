import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const app = readFileSync(new URL('../sidecar_ui/app.js', import.meta.url), 'utf8');
const hive = readFileSync(new URL('../sidecar_ui/hive.js', import.meta.url), 'utf8');
const html = readFileSync(new URL('../sidecar_ui/index.html', import.meta.url), 'utf8');
const readme = readFileSync(new URL('../sidecar_ui/README.md', import.meta.url), 'utf8');

function functionBody(source, name) {
  const start = source.indexOf(`function ${name}(`);
  assert.notEqual(start, -1, `${name} exists`);
  const open = source.indexOf('{', start);
  let depth = 0;
  for (let i = open; i < source.length; i += 1) {
    if (source[i] === '{') depth += 1;
    if (source[i] === '}' && --depth === 0) return source.slice(open + 1, i);
  }
  assert.fail(`unterminated ${name}`);
}

function methodBody(source, signature) {
  const start = source.indexOf(signature);
  assert.notEqual(start, -1, `${signature} exists`);
  const open = source.indexOf('{', start);
  let depth = 0;
  for (let i = open; i < source.length; i += 1) {
    if (source[i] === '{') depth += 1;
    if (source[i] === '}' && --depth === 0) return source.slice(open + 1, i);
  }
  assert.fail(`unterminated ${signature}`);
}

function makeFeedHarness() {
  const state = {
    snap: {head: 8, messages: [], members: [{name: 'w1'}]}, channel: '', me: null,
    hiddenAgents: new Set(), expanded: new Set(), unseen: new Set(), feedKey: null,
    view: 'board', tab: 'feed', firstLoad: false, stick: false,
  };
  const feed = {innerHTML: ''};
  const controls = Object.fromEntries(['f-search', 'f-agent', 'f-mention', 'f-channel', 'f-autoscroll']
    .map((id) => [id, {value: '', checked: false}]));
  const counters = {visible: 0, measure: 0, jump: 0};
  const invoke = new Function('state', '$', 'nearBottom', 'memberOf', 'visibleMessages', 'seesNow', 'messageHtml',
    'updateFilterToggle', 'measureClamps', 'scrollToLatest', 'updateJump', 'ICON', 'newSet', functionBody(app, 'renderFeed'));
  const render = (newSet = new Set()) => invoke(state, (id) => id === 'feed' ? feed : controls[id], () => true,
    () => null, () => { counters.visible += 1; return state.snap.messages; }, () => true,
    (m) => { if (m.fail) throw new Error('render broke'); return 'row'; }, () => {},
    () => { counters.measure += 1; }, () => {}, () => { counters.jump += 1; }, {search: '', inbox: ''}, newSet);
  return {state, feed, counters, render};
}

function hiveUpdateHarness(renderFn) {
  const body = methodBody(hive, 'update(snap, el, opts) {');
  const root = {isConnected: true, parentNode: null};
  const el = {innerHTML: '', appendChild(child) { child.parentNode = this; }, closest() { return null; }};
  const roots = new Map();
  const S = {swarm: null, etag: null, live: null};
  const window = {SwarmHive: {}};
  const document = {createElement: () => ({className: '', isConnected: true, parentNode: null})};
  const state = {};
  const fn = new Function('snap', 'el', 'opts', 'window', 'document', 'ROOTS', 'S', 'state', 'closeReel', 'toModel', 'render', body);
  return {call(snap, opts = {view: 'cards'}) {
    const view = opts.view;
    fn(snap, el, opts, window, document, roots, S, state, () => {}, (s) => ({id: s.id}), renderFn);
  }, el, roots, S};
}

test('F-02: header status displays useful swarm and member information', () => {
  const renderMeta = functionBody(app, 'renderMeta');
  assert.match(renderMeta, /swarm-meta/);
  assert.match(renderMeta, /textContent/);
  assert.doesNotMatch(renderMeta, /innerHTML\s*=\s*''/);
});

test('F-04: feed cache invalidates when roster names or unseen sequences change at the same head', () => {
  const {state, counters, render} = makeFeedHarness();
  render();
  assert.equal(counters.visible, 1);
  state.snap.members.push({name: 'w2'});
  render();
  assert.equal(counters.visible, 2, 'new roster row state causes a feed rebuild');
  state.unseen.add(3);
  render();
  assert.equal(counters.visible, 3, 'unseen state causes a feed rebuild');
});

test('F-04: render failure does not cache the feed key and can retry', () => {
  const {state, counters, render} = makeFeedHarness();
  const message = {seq: 1, fail: true};
  state.snap.messages = [message];
  assert.throws(() => render(), /render broke/);
  assert.equal(state.feedKey, null);
  message.fail = false;
  render();
  assert.equal(counters.visible, 2);
  assert.notEqual(state.feedKey, null);
});

test('F-05: leaving Graph stops replay rather than running it in a hidden tab', () => {
  const selectTab = functionBody(app, 'selectTab');
  assert.match(selectTab, /previousTab === 'graph'[\s\S]*replayStop\(\)/);
});

test('F-06: hive render errors propagate and the same etag retries', () => {
  let attempts = 0;
  const harness = hiveUpdateHarness(() => { attempts += 1; if (attempts === 1) throw new Error('render broke'); });
  const snap = {id: 'swarm', etag: 'same'};
  assert.throws(() => harness.call(snap), /render broke/);
  assert.equal(harness.roots.get(harness.el).etag, '');
  harness.call(snap);
  assert.equal(attempts, 2);
  assert.equal(harness.roots.get(harness.el).etag, 'same');
});

test('F-07: server-shaped attention detail does not repeat the member title', () => {
  const start = hive.indexOf('    (Array.isArray(snap.systemAttention)');
  assert.notEqual(start, -1, 'system attention mapping exists');
  const end = hive.indexOf('\n    // Short copy', start);
  assert.notEqual(end, -1, 'system attention mapping has a boundary');
  const mapAttention = new Function('snap', 'attention', 'isRole', 'key', 'head', 'stripMemberPrefix', hive.slice(start, end));
  const attention = [];
  mapAttention({systemAttention: [{member: 'w1', kind: 'stuck', text: 'w1: no progress'}]}, attention,
    () => false, (name) => name, 12, (text, member) => {
      const stripPrefix = new Function('text', 'member', functionBody(hive, 'stripMemberPrefix'));
      return stripPrefix(text, member);
    });
  assert.equal(attention[0].title, '@w1 looks stuck');
  assert.equal(attention[0].why, 'no progress');
  assert.equal(`${attention[0].title} — ${attention[0].why}`, '@w1 looks stuck — no progress');
});

test('F-08: dashboard chrome loads no third-party Google fonts', () => {
  assert.doesNotMatch(html, /fonts\.googleapis\.com|fonts\.gstatic\.com/);
});

test('F-02 header status is populated while narrator Overview status remains documented', () => {
  assert.match(readme, /narrator's view: a status line/);
  assert.match(functionBody(app, 'renderMeta'), /const summary =/);
});

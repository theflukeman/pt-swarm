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

test('F-02: header status displays useful swarm and member information', () => {
  const renderMeta = functionBody(app, 'renderMeta');
  assert.match(renderMeta, /swarm-meta/);
  assert.match(renderMeta, /textContent/);
  assert.doesNotMatch(renderMeta, /innerHTML\s*=\s*''/);
});

test('F-04: feed renderer skips a rebuild when feed-relevant state is unchanged', () => {
  const renderFeed = functionBody(app, 'renderFeed');
  assert.match(renderFeed, /feedKey/);
  assert.match(renderFeed, /feedKey\s*===\s*state\.feedKey/);
  assert.ok(renderFeed.indexOf('feedKey === state.feedKey') < renderFeed.indexOf('visibleMessages()'));
});

test('F-05: leaving Graph stops replay rather than running it in a hidden tab', () => {
  const selectTab = functionBody(app, 'selectTab');
  assert.match(selectTab, /previousTab === 'graph'[\s\S]*replayStop\(\)/);
});

test('F-06: failed snapshot ingestion cannot advance the etag', () => {
  const poll = functionBody(app, 'poll');
  assert.ok(poll.indexOf('ingest(r)') < poll.indexOf('state.etag = r.etag'));
});

test('F-07: attention detail does not repeat the title already shown as status', () => {
  const row = functionBody(hive, 'membersView');
  assert.match(row, /bodyLine/);
  assert.match(row, /showRoleSub/);
  assert.match(row, /showRoleSub[\s\S]*x\.title[\s\S]*bodyLine/);
});

test('F-08: dashboard chrome loads no third-party Google fonts', () => {
  assert.doesNotMatch(html, /fonts\.googleapis\.com|fonts\.gstatic\.com/);
});

test('F-02 header status is populated while narrator Overview status remains documented', () => {
  assert.match(readme, /narrator's view: a status line/);
  assert.match(functionBody(app, 'renderMeta'), /const summary =/);
});

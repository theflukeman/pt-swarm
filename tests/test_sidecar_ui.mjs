import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { request as httpRequest } from 'node:http';

test('sidecar_ui resolves directSwarmDir only for its members and accepts coordinator id', async () => {
  const root = mkdtempSync(join(tmpdir(), 'swarm-sidecar-'));
  const directDir = join(root, 'direct_swarm');
  mkdirSync(directDir, { recursive: true });
  writeFileSync(
    join(directDir, 'members.json'),
    JSON.stringify({
      title: 'Direct Swarm',
      members: {
        coordinator: {
          conversation_id: 'coordinator',
          role: 'coordinator',
          wake_mode: 'working',
          model: 'session',
          desired_model: 'session',
          applied_model: '@mg:polytoken:default_model_full',
        },
        w1: {
          conversation_id: '11111111-1111-1111-1111-111111111111',
          role: 'worker',
          wake_mode: 'anything',
          wait_since: 1,
          model: 'default',
          applied_model: '@mg:polytoken:default_model_mini',
        },
        w2: {
          conversation_id: '22222222-2222-2222-2222-222222222222',
          role: 'worker',
          wake_mode: 'anything',
          model: 'vendor/pinned(high)',
          applied_model: 'vendor/pinned(high)',
        },
        w3: {
          conversation_id: '33333333-3333-3333-3333-333333333333',
          role: 'worker',
          wake_mode: 'mentions',
          job_id: 'job-3',
          state: 'done',
          model: 'default',
          applied_model: '@mg:polytoken:default_model_full',
        },
      },
    }),
  );
  writeFileSync(
    join(directDir, 'session_model.json'),
    JSON.stringify({ model: 'neuralwatt/deepseek-v4.1-flash' }),
  );
  writeFileSync(
    join(directDir, 'board.jsonl'),
    '{"seq":1,"agent":"coordinator","text":"@w1 hi","time":"2026-11-12 10:00:00"}\n',
  );

  process.env.SIDECAR_NO_LISTEN = '1';
  process.env.POLYTOKEN_SWARM_DIR = directDir;
  process.env.SWARM_CONVERSATION_ID = 'coordinator';

  try {
    const { app, canonicalFile } = await import('../sidecar_ui/main.mjs');
    const swarmHandler = app.routes.get('GET:/api/swarm');
    assert.ok(swarmHandler, 'GET:/api/swarm route should be registered');

    const r1 = await swarmHandler({});
    assert.equal(r1.swarm, 'coordinator');
    assert.equal(r1.member, 'coordinator');
    assert.equal(r1.title, 'Direct Swarm');
    const w1 = r1.members.find((m) => m.name === 'w1');
    assert.equal(w1.status, 'waiting');
    // A finished harness job is not "working": the roster's own state decides.
    assert.equal(r1.members.find((m) => m.name === 'w3').status, 'waiting');
    assert.equal(r1.members.find((m) => m.name === 'coordinator').status, 'working');

    // Model labels show what the member runs, not the roster's request: tier
    // groups read as tiers, `session` reads as the model the session is on, and
    // a pinned selector passes through.
    const byName = Object.fromEntries(r1.members.map((m) => [m.name, m]));
    assert.equal(byName.coordinator.model, 'neuralwatt/deepseek-v4.1-flash');
    assert.equal(byName.w1.model, 'mini tier');
    assert.equal(byName.w2.model, 'vendor/pinned(high)');

    // Unrelated UUID must NOT falsely match directSwarmDir
    const r2 = await swarmHandler({ conversationId: '99999999-9999-9999-9999-999999999999' });
    assert.equal(r2.swarm, null);

    // canonicalFile accepts a real file inside root on both POSIX and Windows
    const sampleImg = join(directDir, 'shot.png');
    writeFileSync(sampleImg, 'png');
    assert.ok(canonicalFile(sampleImg, directDir));
    assert.equal(canonicalFile(join(directDir, '..', 'escape.png'), directDir), null);

    // No absolute paths leave the server: a rebound page learns the board,
    // not where the operator's swarm lives.
    assert.equal(r1.dir, undefined);
    assert.equal(r1.appData, undefined);
    assert.equal(r1.view.dir, undefined);

    // The Raw tab's file contents are opt-in (?raw=1); a normal poll carries
    // the board once, not twice.
    assert.equal(r1.raw, undefined);
    const r4 = await swarmHandler({ raw: '1' });
    assert.match(r4.raw.board, /@w1 hi/);
    assert.match(r4.raw.members, /"coordinator"/);

    // ETag unchanged response
    const r3 = await swarmHandler({ etag: r1.etag });
    assert.equal(r3.unchanged, true);
    // ...unless the files are asked for, which must still be answered in full.
    assert.equal((await swarmHandler({ etag: r1.etag, raw: '1' })).unchanged, undefined);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('standalone HTTP shim rejects hostile Host and cross-origin requests', async () => {
  const { SidecarApp, Response } = await import('../sidecar_ui/sidecar_sdk/index.mjs');
  const app = new SidecarApp();
  app.api('/private', () => ({ secret: 'snapshot' }));
  app.api('/page', () => new Response('page'));
  const server = app.run(0);
  try {
    await new Promise((resolve) => server.once('listening', resolve));
    const port = server.address().port;
    const getStatus = (headers) => new Promise((resolve, reject) => {
      const req = httpRequest({ host: '127.0.0.1', port, path: '/private', headers }, (res) => {
        res.resume();
        res.on('end', () => resolve(res.statusCode));
      });
      req.on('error', reject);
      req.end();
    });
    assert.equal(await getStatus({ Host: 'evil.example' }), 403);
    assert.equal(await getStatus({ Host: `127.0.0.1:${port}`, Origin: 'https://evil.example' }), 403);
    const local = await fetch(`http://127.0.0.1:${port}/private`);
    assert.equal(local.status, 200);
  } finally {
    server.close();
  }
});

test('channels.mjs parses mentions, channel mentions, and member visibility', async () => {
  const { activeAt, channelMentions, mentionsOf, memberView, channelRegistry } = await import(
    '../sidecar_ui/channels.mjs'
  );

  assert.deepEqual(channelMentions('Ping @#backend and @#main'), ['backend', 'main']);
  assert.deepEqual(mentionsOf('Email dev@parser.io or tag @worker', ['parser', 'worker-1', 'worker-2']), {
    mentions: ['worker-1', 'worker-2'],
    all: false,
  });

  const msgs = [
    { seq: 1, agent: 'coordinator', text: '[channel] created #backend', control: { op: 'create', channel: 'backend', topic: 'API' } },
    { seq: 2, agent: 'api', text: 'Internal backend msg', channel: 'backend' },
    { seq: 3, agent: 'coordinator', text: 'Main broadcast' },
  ];
  const reg = channelRegistry(msgs);
  assert.equal(reg.length, 1);
  assert.equal(reg[0].name, 'backend');
  const recreated = channelRegistry([
    { seq: 1, agent: 'coordinator', control: { op: 'create', channel: 'archive-me', topic: 'old' } },
    { seq: 2, agent: 'coordinator', control: { op: 'archive', channel: 'archive-me' } },
    { seq: 3, agent: 'worker', control: { op: 'create', channel: 'archive-me', topic: 'new' } },
  ]);
  assert.deepEqual(recreated, [{ name: 'archive-me', topic: 'new', created_by: 'worker', archived: false }]);

  // A re-created channel is active again from its latest create, and it was
  // active in its first window too: the controls are replayed in order rather
  // than collapsed to the first create and the last archive.
  const windows = [
    { seq: 1, agent: 'coordinator', control: { op: 'create', channel: 'flip', topic: 'v1' } },
    { seq: 2, agent: 'api', text: 'first window' },
    { seq: 3, agent: 'coordinator', control: { op: 'archive', channel: 'flip' } },
    { seq: 4, agent: 'api', text: 'between windows' },
    { seq: 5, agent: 'coordinator', control: { op: 'create', channel: 'flip', topic: 'v2' } },
  ];
  const flip = channelRegistry(windows);
  assert.deepEqual(flip, [{ name: 'flip', topic: 'v2', created_by: 'coordinator', archived: false }]);
  const active = activeAt(flip, windows);
  assert.deepEqual(windows.map((_, i) => active[i]('flip')), [true, true, false, false, true]);
  // Archive at #3 counts from its own message; a channel archived at the head
  // (never re-created) is active only before it.
  const archivedOnly = channelRegistry(windows.slice(0, 3));
  assert.deepEqual(
    windows.slice(0, 3).map((_, i) => activeAt(archivedOnly, windows.slice(0, 3))[i]('flip')),
    [true, true, false],
  );

  const uiView = memberView('ui', 'worker', [], msgs, reg);
  assert.deepEqual(uiView.visible, [true, false, true]);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

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

    // ETag unchanged response
    const r3 = await swarmHandler({ etag: r1.etag });
    assert.equal(r3.unchanged, true);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('channels.mjs parses mentions, channel mentions, and member visibility', async () => {
  const { channelMentions, mentionsOf, memberView, channelRegistry } = await import(
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

  const uiView = memberView('ui', 'worker', [], msgs, reg);
  assert.deepEqual(uiView.visible, [true, false, true]);
});

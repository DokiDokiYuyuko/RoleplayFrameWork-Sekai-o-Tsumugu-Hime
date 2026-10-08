/** Synthetic conversation fixture and production UI checks. No real story is read. */
import assert from 'node:assert/strict';

const stamp = '2026-01-01T12:00:00Z';
const usage = { input_tokens: 50, output_tokens: 20, cached_tokens: 0 };
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

export function conversationFixture(snapshot, characters, json, emit) {
  let active = false;
  let failNext = false;
  const requests = [];
  let serial = 0;
  const make = (id, actor, content, seq) => ({
    id, actor, content, seq, kind: 'roleplay', session_id: snapshot.meta.id,
    turn: 1, scene_id: 'scene-conversation', status: 'final', variants: [], active_variant: null,
    visible_to: 'all', created_at: stamp, fingerprint: `${id}-original`, edited: false,
    mentions: [], generation_id: actor === 'player' ? null : `gen-${id}`, attempt_id: 'initial',
    generation_meta: actor === 'player' ? null : {
      generation_id: `gen-${id}`, message_id: id, model: 'example/model', engine_session_id: '', usage,
      injected_entry_ids: [], prompt_tokens_by_section: {},
      provenance: { mode: 'free', participants: characters.slice(0, 2).map(c => c.id),
        sources: [], trigger_message_ids: ['msg-conversation-player'], reply_to_message_ids: [] },
    },
  });
  function activate() {
    active = true;
    snapshot.messages.splice(0, snapshot.messages.length,
      make('msg-conversation-player', 'player', '请一起商量接下来怎么走。', 1),
      make('msg-conversation-first', characters[0].id, '先去河岸，确认石桥是否还能通行。', 2),
      make('msg-conversation-second', characters[1].id, '我赞同河岸路线，不过需要带上绳索。', 3),
      make('msg-conversation-third', characters[0].id, '好，我去取绳索，回来后一起出发。', 4));
    snapshot.meta.branch_revision = 10;
    snapshot.meta.player_identity_id = null;
    snapshot.active_scene_id = 'scene-conversation';
    snapshot.scenes = [{ id: snapshot.active_scene_id, title: '桥边', description: '虚构的验收场景。',
      turn_start: 1, turn_end: null, member_ids: characters.slice(0, 2).map(c => c.id), group_ids: [], created_at: stamp }];
    snapshot.conversation_runs = [];
  }
  function update(run) {
    run.updated_at = new Date().toISOString();
    snapshot.meta.branch_revision++;
    emit(snapshot.meta.id, 'conversation.run.updated', { run, branch_revision: snapshot.meta.branch_revision });
  }
  async function route(req, res, path) {
    if (!active) return false;
    const inspection = path.match(/^\/api\/v1\/sessions\/sess-demo1\/generation-inspections\/([^/]+)\/([^/]+)$/);
    if (inspection) {
      requests.push({ path, body: {} });
      const message = snapshot.messages.find(m => m.id === inspection[1]);
      json(res, { character_id: message.actor, turn: 1, mode: 'actual',
        message_id: message.id, generation_id: inspection[2], prompt: `Synthetic prompt for ${message.id}`,
        total_tokens: 50, tokens_by_section: {}, sections: [], injected_entry_ids: [] });
      return true;
    }
    if (path.endsWith('/model-requests')) { json(res, { requests: [] }); return true; }
    const messageRoute = path.match(/^\/api\/v1\/sessions\/sess-demo1\/messages\/([^/]+)\/(regenerate-one|regenerate-dependents|accept-dependencies)$/);
    const runRoute = path.match(/^\/api\/v1\/sessions\/sess-demo1\/conversation-runs(?:\/([^/]+))?(?:\/(pause|stop|resume))?$/);
    if (!messageRoute && !runRoute) return false;
    let body = {};
    if (req.method === 'POST') {
      const chunks = []; for await (const chunk of req) chunks.push(chunk);
      const text = Buffer.concat(chunks).toString();
      body = text ? JSON.parse(text) : {};
      requests.push({ path, body });
    }
    if (messageRoute) {
      const message = snapshot.messages.find(m => m.id === messageRoute[1]);
      if (!message) { json(res, { detail: 'Synthetic missing message' }, 404); return true; }
      if (body.expected_branch_revision !== snapshot.meta.branch_revision || body.expected_fingerprint !== message.fingerprint) {
        json(res, { detail: 'Synthetic revision conflict' }, 409); return true;
      }
      const original = structuredClone(message);
      const action = messageRoute[2];
      let changed;
      if (action === 'regenerate-one') {
        const identity = { generation_id: `gen-conversation-${++serial}`, attempt_id: `attempt-${serial}`, operation_id: body.operation_id };
        emit(snapshot.meta.id, 'message.pending', { message: { ...message, ...identity, status: 'pending', content: '' }, ...identity, character_id: message.actor, turn: 1 });
        await delay(220);
        if (failNext) {
          failNext = false;
          emit(snapshot.meta.id, 'message.error', { message_id: message.id, message: original, error: 'Synthetic generation failed; original retained', ...identity });
          json(res, { detail: 'Synthetic generation failed; original retained' }, 502); return true;
        }
        emit(snapshot.meta.id, 'message.delta', { message_id: message.id, delta: '新方案：', ...identity });
        await delay(700);
        if (!message.variants.length) message.variants.push({ id: `${message.id}-original`, content: message.content, generation_meta: message.generation_meta, hygiene: null, created_at: stamp });
        message.content = '新方案：先确认桥梁，再商量物资分工。';
        Object.assign(message, identity, { fingerprint: `${message.id}-replacement`, active_variant: message.variants.length });
        message.generation_meta = { ...message.generation_meta, ...identity };
        message.variants.push({ id: `${message.id}-replacement`, content: message.content, generation_meta: message.generation_meta, hygiene: null, created_at: stamp });
        const dependents = snapshot.messages.filter(m => m.seq > message.seq && m.actor !== 'player');
        for (const m of dependents) { m.dependency_stale = true; m.dependency_stale_sources = [message.id]; }
        changed = [message, ...dependents];
        emit(snapshot.meta.id, 'message.final', { message, ...identity });
      } else {
        changed = snapshot.messages.filter(m => m.seq >= message.seq && m.dependency_stale);
        for (const m of changed) {
          m.dependency_stale = false; m.dependency_stale_sources = [];
          if (action === 'regenerate-dependents') { m.content = '已更新：依据新的桥梁方案调整物资。'; m.fingerprint += '-updated'; }
        }
      }
      snapshot.meta.branch_revision++;
      for (const m of changed) emit(snapshot.meta.id, 'message.updated', { message: m, branch_revision: snapshot.meta.branch_revision });
      json(res, { messages: changed, branch_revision: snapshot.meta.branch_revision,
        affected_message_ids: changed.filter(m => m.id !== message.id).map(m => m.id), operation_id: body.operation_id });
      return true;
    }
    if (req.method === 'GET') {
      const run = snapshot.conversation_runs.find(r => r.id === runRoute[1]);
      json(res, runRoute[1] ? run : { runs: snapshot.conversation_runs }); return true;
    }
    if (!runRoute[1]) {
      const run = { id: `conversation-demo-${++serial}`, session_id: snapshot.meta.id, operation_id: body.operation_id,
        mode: 'observe', participant_ids: body.participant_ids, directive: body.directive,
        scene_id: snapshot.active_scene_id, player_identity_id: null, max_replies: body.max_replies, completed_replies: 0,
        status: 'running', stage: 'selecting', current_speaker_id: null, stop_reason: '', last_error: null,
        epoch: 1, pause_requested: false, stop_requested: false, last_committed_step_id: null,
        last_committed_message_id: null, cumulative_usage: { input_tokens: 0, output_tokens: 0, cached_tokens: 0 },
        steps: [], created_at: stamp, updated_at: stamp };
      snapshot.conversation_runs.push(run); update(run); json(res, run); return true;
    }
    const run = snapshot.conversation_runs.find(r => r.id === runRoute[1]);
    if (!run) { json(res, { detail: 'Synthetic missing run' }, 404); return true; }
    if (runRoute[2] === 'pause') { run.status = 'paused'; run.stage = 'boundary'; run.stop_reason = 'user_paused'; }
    if (runRoute[2] === 'stop') { run.status = 'cancelled'; run.stage = 'boundary'; run.stop_reason = 'user_stopped'; run.epoch++; }
    if (runRoute[2] === 'resume') {
      if (body.additional_replies != null) run.max_replies = run.completed_replies + body.additional_replies;
      run.status = 'running'; run.stage = 'selecting'; run.stop_reason = ''; run.epoch++;
    }
    update(run); json(res, run); return true;
  }
  function completeLast() {
    const run = snapshot.conversation_runs.at(-1);
    run.completed_replies = run.max_replies; run.status = 'completed'; run.stage = 'boundary'; run.stop_reason = 'max_replies';
    run.cumulative_usage = usage; update(run);
  }
  function pendingReply() {
    const run = snapshot.conversation_runs.at(-1);
    const message = make('msg-conversation-live', characters[0].id, '已经恢复的流式前缀。', 5);
    Object.assign(message, { status: 'pending', generation_id: 'gen-live', attempt_id: 'attempt-live', operation_id: run.operation_id });
    snapshot.messages.push(message);
    Object.assign(run, { stage: 'generating', current_speaker_id: message.actor, current_message_id: message.id,
      current_generation_id: message.generation_id, current_attempt_id: message.attempt_id });
    emit(snapshot.meta.id, 'message.pending', { message, generation_id: message.generation_id, attempt_id: message.attempt_id });
    update(run);
  }
  function continueReply() {
    const message = snapshot.messages.find(m => m.id === 'msg-conversation-live');
    const delta = '重新连接后继续逐段出现。';
    const offset = message.content.length;
    message.content += delta;
    emit(snapshot.meta.id, 'message.delta', { message_id: message.id, generation_id: message.generation_id,
      attempt_id: message.attempt_id, delta, offset });
  }
  function finishReply() {
    const run = snapshot.conversation_runs.at(-1);
    const message = snapshot.messages.find(m => m.id === 'msg-conversation-live');
    message.status = 'final';
    emit(snapshot.meta.id, 'message.final', { message, generation_id: message.generation_id, attempt_id: message.attempt_id });
    Object.assign(run, { stage: 'selecting', current_speaker_id: null, current_message_id: null,
      current_generation_id: null, current_attempt_id: null, completed_replies: 1 });
    update(run);
  }
  return { activate, route, requests, completeLast, pendingReply, continueReply, finishReply, failNext: () => { failNext = true; } };
}

async function openObservation(page) {
  await page.getByRole('button', { name: '更多输入工具', exact: true }).click();
  await page.getByRole('menuitem', { name: '设置旁观交谈', exact: true }).click();
}

export async function checkConversationUI({ fixture, snapshot, pageAt, open, shot, bounds, results }) {
  fixture.activate();
  const page = await pageAt(1920, 1080);
  await open(page, '/stories/sess-demo1/branches/sess-demo1', '[data-message-id="msg-conversation-first"]');
  const first = page.locator('[data-message-id="msg-conversation-first"]');
  const second = page.locator('[data-message-id="msg-conversation-second"]');
  const peerBefore = await second.locator('.bubble-frame__content').innerText();
  await first.getByRole('button', { name: '重新生成', exact: true }).click();
  await first.getByText('原候选已保留', { exact: true }).waitFor();
  assert.equal(await second.locator('.bubble-frame__content').innerText(), peerBefore, 'Peer bubble changed while target was pending');
  await shot(page, 'conversation-target-pending');
  await first.getByText('新方案：先确认桥梁，再商量物资分工。', { exact: true }).waitFor();
  await second.getByText('基于旧版本', { exact: true }).waitFor();
  assert.equal(await second.locator('.bubble-frame__content').innerText(), peerBefore);
  await shot(page, 'conversation-stale-dependency');
  await second.getByRole('button', { name: '继续保留', exact: true }).click();
  await second.locator('.conversation-dependency-notice').waitFor({ state: 'hidden' });
  assert.equal(await second.locator('.bubble-frame__content').innerText(), peerBefore);
  fixture.failNext();
  await first.getByRole('button', { name: '重新生成', exact: true }).click();
  await page.getByText(/Synthetic generation failed; original retained/).first().waitFor();
  await first.getByText('新方案：先确认桥梁，再商量物资分工。', { exact: true }).waitFor();
  results.push({ check: 'single reply streaming preserves peers, dependency acceptance and failure restoration', status: 'passed' });
  for (const id of ['msg-conversation-first', 'msg-conversation-third']) {
    const node = page.locator(`[data-message-id="${id}"]`);
    await node.getByRole('button', { name: '消息操作', exact: true }).click();
    await page.getByRole('menuitem', { name: '查看这条上下文', exact: true }).click();
    const inspector = page.getByTestId('assist-inspector');
    await inspector.getByText(/所选这次回应实际使用的上下文记录/).waitFor();
    assert.equal(await inspector.getAttribute('data-inspection-message-id'), id);
    assert.ok(fixture.requests.at(-1).path.includes(`/generation-inspections/${id}/`));
    await page.getByRole('button', { name: '收起创作工具', exact: true }).click();
  }
  results.push({ check: 'same speaker repeated in one turn opens distinct per-message generation inspection', status: 'passed' });
  await page.getByRole('button', { name: /^对象 ·/ }).click();
  const targets = page.getByRole('dialog', { name: '消息对象', exact: true });
  await targets.waitFor();
  await targets.locator('.v7-recipient-option').nth(1).click();
  await targets.locator('.v7-recipient-option').nth(2).click();
  await targets.getByLabel('多人回复方式').selectOption('free');
  await targets.getByLabel('自由交谈条数').fill('4');
  await targets.getByLabel('导演要求').fill('商量路线，遇到需要玩家决定的事情就停下来。');
  await page.keyboard.press('Escape');
  await openObservation(page);
  const observation = page.getByRole('dialog', { name: '旁观交谈', exact: true });
  await observation.getByLabel('旁观交谈条数').fill('4');
  await observation.getByLabel('旁观交谈方向').fill('商量路线，遇到需要玩家决定的事情就停下来。');
  const playerMessages = snapshot.messages.filter(m => m.actor === 'player').length;
  await observation.getByRole('button', { name: '让他们聊一会儿', exact: true }).click();
  const running = page.getByRole('region', { name: '交谈运行状态', exact: true });
  await running.getByText('交谈进行中', { exact: true }).waitFor();
  const start = fixture.requests.findLast(r => r.path.endsWith('/conversation-runs'));
  assert.deepEqual(start.body.participant_ids, ['char-demo0', 'char-demo1']);
  assert.equal(start.body.max_replies, 4);
  assert.ok(start.body.directive.includes('商量路线'));
  assert.equal(snapshot.messages.filter(m => m.actor === 'player').length, playerMessages, 'Observation invented a player message');
  fixture.pendingReply();
  await page.reload();
  const restoredPending = page.locator('[data-message-id="msg-conversation-live"]');
  await restoredPending.locator('.bubble-frame__content').filter({ hasText: '已经恢复的流式前缀。' }).waitFor();
  assert.equal(await restoredPending.getAttribute('data-message-status'), 'pending');
  await page.waitForTimeout(200);
  fixture.continueReply();
  await restoredPending.locator('.bubble-frame__content').filter({ hasText: '已经恢复的流式前缀。重新连接后继续逐段出现。' }).waitFor();
  assert.equal(await restoredPending.getAttribute('data-message-status'), 'pending');
  await shot(page, 'conversation-reloaded-live-prefix');
  fixture.finishReply();
  await page.waitForFunction(() => document.querySelector('[data-message-id="msg-conversation-live"]')?.getAttribute('data-message-status') === 'final');
  results.push({ check: 'running snapshot restores pending prefix and subsequent delta before final', status: 'passed' });
  await running.getByRole('button', { name: '暂停交谈', exact: true }).click();
  await running.getByText('已暂停', { exact: true }).waitFor();
  await shot(page, 'conversation-paused-desktop');
  await page.reload();
  await running.getByText('已暂停', { exact: true }).waitFor();
  await page.getByRole('button', { name: /^对象 ·/ }).click();
  assert.equal(await targets.getByLabel('多人回复方式').inputValue(), 'free');
  assert.equal(await targets.getByLabel('自由交谈条数').inputValue(), '4');
  await page.keyboard.press('Escape');
  await running.getByRole('button', { name: '继续交谈', exact: true }).click();
  await running.getByText('交谈进行中', { exact: true }).waitFor();
  fixture.completeLast();
  await running.waitFor({ state: 'hidden' });
  await openObservation(page);
  await observation.locator('.conversation-history > summary').click();
  await observation.getByText('本段已结束', { exact: true }).waitFor();
  const continued = page.waitForResponse(response => response.url().endsWith('/resume') && response.request().method() === 'POST');
  await observation.getByRole('button', { name: '再聊一段', exact: true }).click();
  await continued; // the fixture records the request before it answers
  assert.equal(fixture.requests.at(-1).body.additional_replies, 4);
  assert.equal(snapshot.conversation_runs.at(-1).max_replies, 8);
  await running.getByRole('button', { name: '立即停止交谈', exact: true }).click();
  await running.waitFor({ state: 'hidden' });
  await page.reload();
  assert.equal(await running.count(), 0, 'Stopped notices must stay out of the input after reload');
  await openObservation(page);
  await observation.locator('.conversation-history > summary').click();
  await observation.getByText('已停止', { exact: true }).waitFor();
  await page.keyboard.press('Escape');
  results.push({ check: 'observation participant pool, finite count, pause, reload, resume, additional segment and stop', status: 'passed' });
  await page.context().close();
  for (const width of [360, 390, 430]) {
    fixture.activate();
    const phone = await pageAt(width, 844, true);
    await open(phone, '/stories/sess-demo1/branches/sess-demo1', '.v7-message');
    await openObservation(phone);
    const choices = phone.getByRole('dialog', { name: '旁观交谈', exact: true });
    await choices.locator('.v7-recipient-option').nth(0).click();
    await choices.locator('.v7-recipient-option').nth(1).click();
    await choices.getByLabel('旁观交谈条数').fill('3');
    await bounds(phone, `conversation-options-phone-${width}`);
    await choices.getByRole('button', { name: '让他们聊一会儿', exact: true }).scrollIntoViewIfNeeded();
    await shot(phone, `conversation-options-phone-${width}`);
    await choices.getByRole('button', { name: '让他们聊一会儿', exact: true }).click();
    const strip = phone.getByRole('region', { name: '交谈运行状态', exact: true });
    await strip.getByText('交谈进行中', { exact: true }).waitFor();
    await strip.getByRole('button', { name: '干预交谈', exact: true }).click();
    await strip.getByText('已暂停', { exact: true }).waitFor();
    await phone.setViewportSize({ width, height: 460 });
    const draft = phone.locator('.v7-input-box textarea');
    await draft.fill('暂时不要过桥，先检查一下。');
    assert.equal(await draft.evaluate(el => document.activeElement === el), true);
    await bounds(phone, `conversation-intervene-phone-${width}`);
    await strip.getByRole('button', { name: '继续交谈', exact: true }).scrollIntoViewIfNeeded();
    await shot(phone, `conversation-keyboard-phone-${width}`);
    await strip.getByRole('button', { name: '继续交谈', exact: true }).click();
    await strip.getByRole('button', { name: '立即停止交谈', exact: true }).click();
    await strip.waitFor({ state: 'hidden' });
    assert.equal(await draft.inputValue(), '暂时不要过桥，先检查一下。');
    await phone.context().close();
    results.push({ check: `conversation mobile controls and retained draft ${width}`, status: 'passed' });
  }
}

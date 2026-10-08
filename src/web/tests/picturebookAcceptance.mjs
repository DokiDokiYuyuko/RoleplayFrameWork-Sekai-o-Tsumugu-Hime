/** Three opt-in picturebook prototypes, using only the caller's synthetic HTTP fixture. */
import assert from 'node:assert/strict';

const storyPath = '/stories/sess-demo1/branches/sess-demo1';
const libraryPath = '/library/characters';
const editorPath = '/library/characters/char-demo0';
const viewports = [
  { width: 1280, height: 900, mobile: false },
  { width: 1920, height: 1080, mobile: false },
  { width: 360, height: 844, mobile: true },
  { width: 390, height: 844, mobile: true },
  { width: 430, height: 932, mobile: true },
];

async function visible(locator, label) {
  assert.equal(await locator.isVisible(), true, `${label} must be visible`);
}
async function noOverflow(page, label) {
  const geometry = await page.evaluate(() => ({
    viewport: innerWidth,
    root: document.documentElement.scrollWidth,
    body: document.body.scrollWidth,
    route: document.querySelector('.tpl-content')?.getBoundingClientRect().toJSON(),
  }));
  assert.ok(geometry.root <= geometry.viewport + 1 && geometry.body <= geometry.viewport + 1,
    `${label}: horizontal overflow ${JSON.stringify(geometry)}`);
  return geometry;
}
async function mobileTouchTargets(locator, label) {
  const sizes = await locator.evaluateAll(elements => elements.map(element => {
    const box = element.getBoundingClientRect();
    return { name: element.getAttribute('aria-label') || element.textContent, width: box.width, height: box.height };
  }).filter(box => box.width > 0 && box.height > 0));
  assert.ok(sizes.length > 0, `${label}: primary touch controls exist`);
  for (const size of sizes) assert.ok(size.width >= 43.5 && size.height >= 43.5, `${label}: touch control below 44px ${JSON.stringify(size)}`);
  return sizes;
}
async function prototypeReady(page, theme) {
  await page.waitForFunction(themeId => document.documentElement.dataset.prototypeDesign === 'picturebook'
    && document.documentElement.dataset.theme === themeId, theme);
  await page.evaluate(() => document.fonts.ready);
}
async function focusChapter(page, id, mobile) {
  const directory = page.getByRole('navigation', { name: '角色手稿章节', exact: true });
  if (mobile) {
    await page.getByRole('button', { name: '章节目录', exact: true }).click();
    await visible(directory, 'mobile chapter directory');
  }
  const chapter = directory.locator(`a[href="#${id}"]`);
  await chapter.focus();
  assert.equal(await chapter.evaluate(element => document.activeElement === element), true, 'Directory must accept keyboard focus');
  await page.keyboard.press('Enter');
  await page.waitForFunction(target => document.activeElement?.id === target, id);
  const state = await page.locator(`#${id}`).evaluate(element => {
    const closed = [];
    for (let ancestor = element; ancestor; ancestor = ancestor.parentElement) {
      if (ancestor instanceof HTMLDetailsElement && !ancestor.open) closed.push(ancestor.id || ancestor.className);
    }
    return { closed, top: element.getBoundingClientRect().top, viewport: innerHeight, hash: location.hash };
  });
  assert.deepEqual(state.closed, [], 'Chapter navigation must reveal every closed details ancestor');
  assert.ok(state.top >= 0 && state.top < state.viewport, `Chapter did not scroll into view: ${JSON.stringify(state)}`);
  assert.equal(state.hash, '', 'Chapter navigation must preserve the route without creating a hash transition');
  if (mobile) assert.equal(await directory.isVisible(), false, 'Mobile directory closes after selection');
  return state;
}
async function assertStoryControls(page, replyMessageId, mobile) {
  const composer = page.locator('.story-composer');
  await visible(page.locator('.story-conversation.moonweave-frame'), 'continuous story reading and input window');
  for (const frame of [page.locator('.story-conversation')]) {
    const ornament = frame.locator(':scope > .moonweave-ornament');
    assert.equal(await ornament.getAttribute('aria-hidden'), 'true');
    const visibleCorners = await ornament.locator('.moonweave-ornament__corner').evaluateAll(elements => elements.filter(element => getComputedStyle(element).display !== 'none').length);
    assert.equal(visibleCorners, 2, 'Approved surface uses the diagonal pair of ornaments');
    assert.equal(await ornament.evaluate(element => getComputedStyle(element).pointerEvents), 'none', 'Ornaments never intercept writing or operations');
  }
  assert.equal(await page.locator('.story-workspace-title').count(), 1, 'Story and route share one title hierarchy');
  assert.equal(await page.locator('.story-workspace-header .v7-breadcrumb').count(), 0, 'Story title is not duplicated in an old breadcrumb row');
  await visible(page.locator('.story-connection'), 'actual connection state');
  assert.equal(await composer.locator('.story-composer-send.v7-btn-primary').count(), 1, 'Send adopts the common enamel action variant');
  await visible(composer.locator('.player-control-bar'), 'player identity');
  await visible(composer.getByLabel('回复模式', { exact: true }), 'reply mode');
  await visible(composer.getByRole('button', { name: /^对象 ·/ }), 'reply targets');
  for (const name of ['内心', '旁白', '代笔']) await visible(composer.getByRole('button', { name, exact: true }), name);
  const composition = await composer.evaluate(element => {
    const bounds = selector => element.querySelector(selector).getBoundingClientRect().toJSON();
    return { context: bounds('.story-composer-context'), surface: bounds('.story-composer-manuscript'), input: bounds('textarea.composer-autosize'),
      foundation: bounds('.story-composer-foundation'), send: bounds('.story-composer-send') };
  });
  assert.ok(composition.input.width >= composition.surface.width - 4, `Writing gets the full manuscript width: ${JSON.stringify(composition)}`);
  assert.ok(composition.context.bottom <= composition.surface.top + 1, 'Identity/mode/targets occupy their own information layer');
  assert.ok(composition.foundation.top >= composition.input.bottom - 1 && composition.send.top >= composition.input.bottom - 1,
    'Tools and primary send sit beneath the writing surface instead of narrowing its text');
  const reply = page.locator(`[data-message-id="${replyMessageId}"]`);
  for (const name of ['复制消息', '重新生成', '编辑', '消息操作', '上一个候选', '下一个候选']) {
    await visible(reply.getByRole('button', { name, exact: true }), `message ${name}`);
  }
  const iconActions = reply.locator('.v7-message-quick-actions .v7-message-quick-button, .v7-message-quick-actions .v7-message-actions-toggle');
  assert.equal(await iconActions.count(), 4, 'Applicable final reply has exactly four shortcuts including More');
  assert.deepEqual(await iconActions.evaluateAll(elements => elements.map(element => element.getAttribute('aria-label'))),
    ['复制消息', '重新生成', '编辑', '消息操作']);
  assert.ok((await iconActions.evaluateAll(elements => elements.map(element => !!element.querySelector('svg')))).every(Boolean), 'All four actions have a real icon');
  assert.deepEqual(await iconActions.evaluateAll(elements => elements.map(element => element.textContent.trim())), ['', '', '', ''], 'The four shortcuts are icons without visible button text');
  assert.equal(await reply.locator('.message-tool-strip .story-candidate-switch').count(), 1, 'Candidates share the message action strip');
  const channels = await page.locator('.story-message-channel').evaluateAll(elements => elements.map(element => {
    const frame = element.querySelector('.bubble-frame').getBoundingClientRect();
    const actions = element.querySelector('.message-action-bar').getBoundingClientRect();
    return { bubbleLeft: frame.left, actionLeft: actions.left, bubbleWidth: frame.width, actionWidth: actions.width };
  }));
  assert.ok(channels.length > 0, 'Channel alignment requires the synthetic inner/scene messages');
  for (const channel of channels) {
    assert.ok(Math.abs(channel.bubbleLeft - channel.actionLeft) <= 1 && Math.abs(channel.bubbleWidth - channel.actionWidth) <= 1,
      `Channel operations must share the corresponding bubble axis: ${JSON.stringify(channel)}`);
  }
  const avatarBounds = await reply.locator('.story-message-avatar').boundingBox();
  assert.ok(avatarBounds && Math.abs(avatarBounds.width - avatarBounds.height) <= 1,
    `Avatar frame must retain a square footprint instead of stretching with the long bubble: ${JSON.stringify(avatarBounds)}`);
  assert.equal(await reply.locator('.v7-message-quick-actions').evaluate(element => getComputedStyle(element).opacity), '1', 'Quick actions must not depend on hover');
  if (mobile) {
    const continueHeader = page.locator('.story-workspace-header .story-workspace-continue');
    assert.equal(await continueHeader.count(), 1, 'The desktop continuation control is present for responsive visibility');
    assert.equal(await continueHeader.isVisible(), false, 'Mobile continuation stays in the menu, keeping the header compact');
    assert.equal(await page.getByRole('navigation', { name: '底部主导航', exact: true }).count(), 0, 'The shell has no global bottom bar');
    await page.getByRole('button', { name: '更多故事操作', exact: true }).click();
    await visible(page.getByRole('menuitem', { name: '继续故事', exact: true }), 'continue story menu destination');
    await page.keyboard.press('Escape');
    await mobileTouchTargets(composer.locator('.story-composer-send, .story-composer-context button, .v7-input-tools button, .story-composer-reply-mode select'), 'story common controls');
    await mobileTouchTargets(reply.locator('.v7-message-quick-button, .v7-message-actions-toggle, .story-candidate-button'), 'message common controls');
  }
}
async function sharedMoonweaveMotion(page, shot, results) {
  const trigger = page.getByRole('button', { name: '更多故事操作', exact: true });
  const box = await trigger.boundingBox();
  assert.ok(box);
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  try {
    await page.locator('.mw-click-glow').first().waitFor({ timeout: 1500 });
    const layer = page.locator('.mw-click-effects');
    assert.equal(await layer.getAttribute('aria-hidden'), 'true');
    assert.equal(await layer.evaluate(element => getComputedStyle(element).pointerEvents), 'none');
  } finally { await page.mouse.up(); }
  await visible(page.getByRole('menuitem', { name: '剧情导航', exact: true }), 'click feedback never blocks the menu');
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => document.querySelectorAll('.mw-click-glow').length === 0, undefined, { timeout: 2000 });

  const input = page.getByRole('textbox', { name: '故事回应正文', exact: true });
  const draft = '合成转场草稿：灯火仍在桥边。';
  await input.fill(draft);
  await page.evaluate(() => { window.__mwAcceptanceRoute = document.querySelector('.tpl-content'); });
  const running = page.waitForFunction(() => window.__mwAcceptanceRoute?.getAnimations().some(animation =>
    animation.playState === 'running' && animation.effect?.getTiming().duration === 220), undefined, { timeout: 2500 });
  await page.getByRole('navigation', { name: '主导航', exact: true }).getByRole('link', { name: '角色', exact: true }).click();
  await running;
  await page.locator('.character-library-grid').waitFor();
  assert.equal(new URL(page.url()).pathname, libraryPath);
  assert.equal(await page.evaluate(() => document.querySelector('.tpl-content') === window.__mwAcceptanceRoute), true,
    'Presentation transition never remounts the route shell');
  await page.waitForFunction(() => window.__mwAcceptanceRoute.getAnimations().every(animation => animation.playState !== 'running'));
  await page.goBack();
  await input.waitFor();
  await page.waitForFunction(() => document.querySelector('.tpl-content').getAnimations().every(animation => animation.playState !== 'running'));
  assert.equal(await input.inputValue(), draft, 'A route transition preserves the branch/identity draft');
  await input.fill('');
  await page.evaluate(() => { delete window.__mwAcceptanceRoute; });
  await page.waitForFunction(() => document.querySelectorAll('.mw-click-glow').length === 0);
  await shot(page, 'picturebook-story-after-real-route-transition');
  results.push({ check: 'moonweave actual click feedback creates and clears; route animation finishes without remounting or losing drafts', status: 'passed' });
}
async function storyMotionFallback(page, shot, results) {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  try {
    const input = page.getByRole('textbox', { name: '故事回应正文', exact: true });
    await input.focus();
    const style = await input.evaluate(element => {
      const surface = element.closest('.story-composer-manuscript');
      const frame = surface && getComputedStyle(surface);
      return { focused: document.activeElement === element, surface: Boolean(surface),
        outline: frame?.outlineStyle, outlineWidth: frame ? parseFloat(frame.outlineWidth) : 0,
        editorAnimation: getComputedStyle(element).animationName,
        scroll: getComputedStyle(document.querySelector('.v7-message-list')).scrollBehavior };
    });
    assert.equal(style.focused, true);
    assert.equal(style.surface, true, 'Editing focus belongs to the enclosing manuscript surface');
    assert.notEqual(style.outline, 'none', 'Reduced motion preserves a visible editing focus');
    assert.ok(style.outlineWidth >= 2, `Reduced motion preserves at least a 2px manuscript focus outline: ${JSON.stringify(style)}`);
    assert.equal(style.editorAnimation, 'none');
    assert.equal(style.scroll, 'auto');
    await page.getByRole('button', { name: '创作工具', exact: true }).click();
    assert.equal(await page.locator('.mw-click-glow').count(), 0, 'Reduced motion suppresses active click decorations');
    const panel = page.getByTestId('assist-panel');
    await visible(panel, 'reduced motion tool panel');
    assert.equal(await panel.evaluate(element => getComputedStyle(element.parentElement).animationName), 'none');
    await page.getByRole('button', { name: '收起创作工具', exact: true }).click();
    assert.equal(await page.getByTestId('assist-panel').count(), 0);
    await shot(page, 'picturebook-story-reduced-motion-focus');
    results.push({ check: 'moonweave story reduced motion retains focus, direct scroll and usable tool panel', status: 'passed', style });
  } finally { await page.emulateMedia({ reducedMotion: 'no-preference' }); }
}
async function storyGeometry(page) {
  return page.evaluate(() => {
    const list = document.querySelector('.v7-message-list');
    const css = getComputedStyle(list);
    const composer = document.querySelector('.story-composer');
    const frames = [...document.querySelectorAll('.story-workspace .bubble-frame')];
    const content = document.querySelector('.v7-message-text .bubble-frame__content');
    const input = document.querySelector('.story-composer textarea');
    const conversation = document.querySelector('.story-conversation').getBoundingClientRect();
    const inputFrame = composer.getBoundingClientRect();
    return {
      reading: list.clientWidth - parseFloat(css.paddingLeft) - parseFloat(css.paddingRight),
      composer: composer.getBoundingClientRect().width,
      bubble: Math.max(...frames.map(element => element.getBoundingClientRect().width)),
      storyFont: getComputedStyle(content).fontFamily,
      inputFont: getComputedStyle(input).fontFamily,
      frame: conversation.toJSON(), inputFrame: inputFrame.toJSON(),
      list: list.getBoundingClientRect().toJSON(),
    };
  });
}
async function keyboardSend(page, viewport, theme, snapshot, shot, results) {
  const originalHeight = viewport.height;
  await page.setViewportSize({ width: viewport.width, height: 460 });
  await page.waitForFunction(() => parseInt(getComputedStyle(document.documentElement).getPropertyValue('--mrp-visual-height'), 10) <= 460);
  const input = page.getByRole('textbox', { name: '故事回应正文', exact: true });
  const content = `合成键盘验收 ${theme} ${viewport.width}：先确认桥边的灯火。`;
  let submitted = null;
  const endpoint = '**/api/v1/sessions/sess-demo1/messages';
  const handler = async route => {
    if (route.request().method() !== 'POST') return route.continue();
    submitted = route.request().postDataJSON();
    assert.equal(submitted.content, content);
    assert.ok(submitted.client_message_id, 'Synthetic send retains the client message identity');
    assert.equal(submitted.reply_mode, 'auto');
    const sequence = Math.max(...snapshot.messages.map(message => message.seq)) + 1;
    const message = { ...structuredClone(snapshot.messages.find(message => message.actor === 'player' && message.kind === 'roleplay')),
      id: submitted.client_message_id, content, seq: sequence,
      turn: Math.max(...snapshot.messages.map(message => message.turn)) + 1,
      status: 'final', fingerprint: submitted.client_message_id, variants: [], active_variant: null };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ messages: [message], errors: [] }) });
  };
  await page.route(endpoint, handler);
  try {
    await input.fill(Array.from({ length: 20 }, (_, index) => `键盘下的长草稿 ${index + 1}`).join('\n'));
    const longDraft = await input.evaluate(element => ({ height: element.getBoundingClientRect().height, scroll: element.scrollHeight,
      client: element.clientHeight, overflow: getComputedStyle(element).overflowY }));
    assert.ok(longDraft.height >= 64 && longDraft.height <= 85 && longDraft.scroll > longDraft.client && longDraft.overflow === 'auto',
      `Long keyboard drafts must scroll within the writing area while keeping the tools present: ${JSON.stringify(longDraft)}`);
    for (const name of ['内心', '旁白', '代笔', '更多输入工具']) {
      const tool = page.locator('.story-composer').getByRole('button', { name, exact: true });
      await visible(tool, `keyboard ${name}`);
      const toolBox = await tool.boundingBox();
      assert.ok(toolBox && toolBox.y >= 0 && toolBox.y + toolBox.height <= 461, `Keyboard viewport obscures ${name}: ${JSON.stringify(toolBox)}`);
    }
    await input.fill(content);
    assert.equal(await input.inputValue(), content);
    await input.focus();
    assert.equal(await input.evaluate(element => document.activeElement === element), true);
    const send = page.getByRole('button', { name: '发送', exact: true });
    assert.equal(await send.isEnabled(), true);
    const box = await send.boundingBox();
    assert.ok(box && box.y >= 0 && box.y + box.height <= 461, `Keyboard viewport obscures send: ${JSON.stringify(box)}`);
    const geometry = await noOverflow(page, `keyboard ${theme} ${viewport.width}`);
    await shot(page, `picturebook-${theme}-${viewport.width}-story-keyboard-draft`);
    const response = page.waitForResponse(response => response.url().endsWith('/sessions/sess-demo1/messages') && response.request().method() === 'POST');
    await send.click();
    assert.equal((await response).status(), 200);
    assert.ok(submitted, 'Send must reach the synthetic request boundary');
    const committed = page.locator(`[data-message-id="${submitted.client_message_id}"]`);
    await committed.waitFor();
    assert.equal(await committed.getAttribute('data-message-status'), 'final');
    assert.equal(await committed.locator('xpath=ancestor::*[contains(@class,"v7-message-item")][1]').getAttribute('data-story-arrival'), 'new', 'Only the newly committed message receives an arrival marker');
    assert.ok((await committed.innerText()).includes(content));
    assert.equal(await input.inputValue(), '');
    await shot(page, `picturebook-${theme}-${viewport.width}-story-keyboard-sent`);
    results.push({ check: `picturebook keyboard send ${theme} ${viewport.width}`, status: 'passed', geometry, evidence: 'simulated viewport, synthetic HTTP receipt' });
  } finally {
    await page.unroute(endpoint, handler);
    await page.setViewportSize({ width: viewport.width, height: originalHeight });
  }
}
async function messageOperations(page, reply, snapshot, base, shot, results) {
  const bubble = page.locator(`[data-message-id="${reply.id}"]`);
  for (const label of ['复制消息', '重新生成', '编辑', '消息操作']) {
    const action = bubble.getByRole('button', { name: label, exact: true });
    await action.focus();
    const tooltip = action.locator('..').getByRole('tooltip');
    await visible(tooltip, `${label} keyboard tooltip`);
    assert.equal(await action.evaluate(element => document.activeElement === element), true);
  }
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write'], { origin: base });
  await bubble.getByRole('button', { name: '复制消息', exact: true }).click();
  await bubble.getByRole('status').filter({ hasText: '已复制' }).waitFor();
  assert.equal(await page.evaluate(() => navigator.clipboard.readText()), reply.content);
  await bubble.getByRole('button', { name: '消息操作', exact: true }).click();
  await visible(page.getByRole('menuitem', { name: '书签', exact: true }), 'message bookmark remains reachable');
  await page.keyboard.press('Escape');
  await visible(bubble.getByRole('button', { name: '编辑', exact: true }), 'message edit is a direct icon action');
  await shot(page, 'picturebook-story-copy-feedback');
  const original = structuredClone(reply);
  const revision = snapshot.meta.branch_revision;
  const endpoint = `**/api/v1/sessions/sess-demo1/messages/${reply.id}`;
  const requests = [];
  const handler = async route => {
    if (route.request().method() !== 'PATCH') return route.continue();
    const body = route.request().postDataJSON();
    requests.push(body);
    assert.equal(body.expected_branch_revision, snapshot.meta.branch_revision);
    assert.equal(body.expected_fingerprint, reply.fingerprint);
    assert.ok([0, 1].includes(body.active_variant));
    reply.active_variant = body.active_variant;
    reply.content = reply.variants[body.active_variant].content;
    reply.fingerprint = `${reply.id}-picturebook-${body.active_variant}`;
    snapshot.meta.branch_revision++;
    await route.fulfill({ status: 200, contentType: 'application/json',
      headers: { 'X-Command-ID': route.request().headers()['x-operation-id'], 'X-Branch-Revision': String(snapshot.meta.branch_revision) },
      body: JSON.stringify(reply) });
  };
  await page.route(endpoint, handler);
  try {
    for (const [name, index] of [['下一个候选', 1], ['上一个候选', 0]]) {
      const response = page.waitForResponse(response => response.url().endsWith(`/messages/${reply.id}`) && response.request().method() === 'PATCH');
      await bubble.getByRole('button', { name, exact: true }).click();
      assert.equal((await response).status(), 200);
      await page.waitForFunction(({ id, content }) => document.querySelector(`[data-message-id="${id}"] .bubble-frame__content`)?.textContent === content,
        { id: reply.id, content: reply.variants[index].content });
      assert.equal(requests.at(-1).active_variant, index);
      await shot(page, `picturebook-story-candidate-${index + 1}`);
    }
    assert.equal(requests.length, 2);
    results.push({ check: 'picturebook persistent copy, more menu and candidate controls execute their synthetic operations', status: 'passed' });
  } finally {
    await page.unroute(endpoint, handler);
    Object.assign(reply, original);
    snapshot.meta.branch_revision = revision;
  }
}

async function characterRelatedFlow(page, mobile, shot, results) {
  const toggle = page.locator('.character-focus-toggle');
  const rail = page.getByRole('complementary', { name: '角色相关资料', exact: true });
  const initiallyFocused = await toggle.getAttribute('aria-pressed') === 'true';
  if (initiallyFocused) await toggle.click();
  await visible(rail, 'related character fields');
  const personality = rail.getByLabel('性格', { exact: true });
  const original = await personality.inputValue();
  const draft = `${original} 合成资料栏草稿。`;
  await personality.fill(draft);
  const widthBefore = (await page.locator('.character-editor-columns').boundingBox()).width;
  await toggle.click();
  assert.equal(await rail.isVisible(), false, 'Focus mode hides the related rail');
  if (!mobile) assert.ok((await page.locator('.character-editor-columns').boundingBox()).width > widthBefore + 250,
    'Focus mode returns the related column space to editing');
  await focusChapter(page, 'character-personality-relationships', mobile);
  assert.equal(await page.getByTestId('card-personality').inputValue(), draft, 'Related and chapter edits share one draft');
  await page.getByTestId('card-personality').fill(original);
  await toggle.click();
  assert.equal(await personality.inputValue(), original, 'Chapter edit is immediately reflected in related fields');
  await shot(page, `picturebook-character-related-shared-draft-${mobile ? 'mobile' : 'desktop'}`);
  if (initiallyFocused) await toggle.click();
  await focusChapter(page, 'character-manuscript', mobile);
  results.push({ check: `approved character focus and shared related draft ${mobile ? 'mobile' : 'desktop'}`, status: 'passed' });
}

async function characterDraftFlow(page, characters, shot, results) {
  const description = page.getByTestId('card-description');
  const save = page.getByRole('button', { name: '保存', exact: true });
  const saved = '合成人物手稿：守着月桥的向导，记得每一盏远行的灯。';
  await description.fill(saved);
  const response = page.waitForResponse(response => response.url().endsWith('/characters/char-demo0') && response.request().method() === 'PATCH');
  await save.click();
  const committed = await response;
  assert.equal(committed.status(), 200);
  assert.equal(committed.request().postDataJSON().card.description, saved);
  await page.locator('.character-save-notice').waitFor();
  assert.equal(new URL(page.url()).pathname, editorPath, 'Saving keeps the editor page open');
  assert.equal(await description.inputValue(), saved);
  assert.equal(characters[0].card.description, saved, 'Successful save must reach the synthetic durable resource');
  await shot(page, 'picturebook-character-save-stays-open');
  results.push({ check: 'picturebook character save remains on independent editor page', status: 'passed' });

  const draft = '合成未发送手稿：她在雨夜听见钟声，仍为过桥的人留着灯。';
  await description.fill(draft);
  await page.waitForFunction(value => JSON.parse(localStorage.getItem('mrp.character-editor.char-demo0') || 'null')?.card?.description === value, draft);
  // A changed remote snapshot must be reviewed; it must never erase the independent local draft.
  characters[0].card.description = '合成后台更新：桥上的新记录。';
  characters[0].revision++;
  const acceptReload = dialog => dialog.accept();
  page.on('dialog', acceptReload);
  try { await page.reload(); } finally { page.off('dialog', acceptReload); }
  await page.getByTestId('card-description').waitFor();
  await page.getByRole('button', { name: '恢复草稿', exact: true }).waitFor();
  assert.ok((await page.locator('.creation-notice').first().innerText()).includes('资料已更新'));
  await page.getByRole('button', { name: '恢复草稿', exact: true }).click();
  assert.equal(await page.getByTestId('card-description').inputValue(), draft);
  await shot(page, 'picturebook-character-draft-after-refresh');
  results.push({ check: 'picturebook character draft survives reload and changed remote snapshot with explicit recovery', status: 'passed' });

  const endpoint = '**/api/v1/characters/char-demo0';
  let conflictRequest = null;
  const conflict = async route => {
    if (route.request().method() !== 'PATCH') return route.continue();
    conflictRequest = route.request().postDataJSON();
    await route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ detail: 'Synthetic revision conflict: draft remains editable' }) });
  };
  await page.route(endpoint, conflict);
  try {
    await page.getByRole('button', { name: '保存', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: '保存失败' }).waitFor();
    assert.equal(conflictRequest.card.description, draft);
    assert.equal(conflictRequest.expected_revision, characters[0].revision);
    assert.equal(await page.getByTestId('card-description').inputValue(), draft);
    assert.equal(new URL(page.url()).pathname, editorPath);
    assert.notEqual(characters[0].card.description, draft, 'Conflict must not write the resource');
    await shot(page, 'picturebook-character-409-draft-retained');
    results.push({ check: 'picturebook character CAS conflict keeps unsaved manuscript', status: 'passed' });
  } finally { await page.unroute(endpoint, conflict); }

  const offline = async route => route.request().method() === 'PATCH' ? route.abort('failed') : route.continue();
  await page.route(endpoint, offline);
  try {
    await page.getByRole('button', { name: '保存', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: '保存失败' }).waitFor();
    assert.equal(await page.getByTestId('card-description').inputValue(), draft);
    assert.equal(new URL(page.url()).pathname, editorPath);
    await shot(page, 'picturebook-character-network-failure-draft-retained');
    results.push({ check: 'picturebook character network failure keeps unsaved manuscript', status: 'passed' });
  } finally { await page.unroute(endpoint, offline); }
  // Explicit cancellation retains the editor; successful retry clears the local draft.
  await page.getByRole('button', { name: '返回角色库', exact: true }).click();
  const exit = page.getByRole('dialog', { name: '离开角色手稿？', exact: true });
  await exit.waitFor();
  await exit.getByRole('button', { name: '取消', exact: true }).click();
  assert.equal(await page.getByTestId('card-description').inputValue(), draft);
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await page.locator('.character-save-notice').waitFor();
  assert.equal(characters[0].card.description, draft);
  assert.equal(await page.evaluate(() => localStorage.getItem('mrp.character-editor.char-demo0')), null);
  await shot(page, 'picturebook-character-retry-saved');
}

async function desktopZoomEquivalent(page, kind, theme, replyId, shot, results) {
  const originalViewport = page.viewportSize();
  // 1280×900 at 125% browser scale has a 1024×720 CSS viewport. This is a
  // layout/interaction simulation, not evidence of changing the browser zoom UI.
  await page.setViewportSize({ width: 1024, height: 720 });
  try {
    if (kind === 'story') {
      await assertStoryControls(page, replyId, false);
      const input = page.getByRole('textbox', { name: '故事回应正文', exact: true });
      await input.fill('合成125%缩放输入：确认下一盏灯的位置。');
      await input.focus();
      assert.equal(await input.evaluate(element => document.activeElement === element), true);
      const send = page.getByRole('button', { name: '发送', exact: true });
      assert.equal(await send.isEnabled(), true);
      const rect = await send.boundingBox();
      assert.ok(rect && rect.y >= 0 && rect.y + rect.height <= 721, 'Equivalent zoom must keep send in the viewport');
      await page.getByTestId('assist-toggle').click();
      await visible(page.locator('.v7-assist-panel'), 'zoom creation tools');
      await noOverflow(page, `zoom creation tools ${theme}`);
      await page.getByTestId('assist-toggle').click();
      await input.fill('');
    } else if (kind === 'characters') {
      await visible(page.getByLabel('搜索角色', { exact: true }), 'zoom library search');
      await visible(page.getByTestId('character-create'), 'zoom new character action');
      await visible(page.getByTestId('character-card').first().getByRole('button', { name: '编辑', exact: true }), 'zoom direct character edit');
    } else {
      await focusChapter(page, 'character-manuscript', false);
      const input = page.getByTestId('card-description');
      const original = await input.inputValue();
      await input.fill(`${original}\n合成125%缩放键盘输入。`);
      await input.focus();
      assert.equal(await input.evaluate(element => document.activeElement === element), true);
      const save = page.getByRole('button', { name: '保存', exact: true });
      assert.equal(await save.isEnabled(), true);
      const rect = await save.boundingBox();
      assert.ok(rect && rect.y >= 0 && rect.y + rect.height <= 721, 'Equivalent zoom must keep save in the viewport');
      await input.fill(original);
    }
    const geometry = await noOverflow(page, `125 percent equivalent ${kind} ${theme}`);
    await shot(page, `picturebook-${theme}-desktop-zoom125-equivalent-${kind}`);
    results.push({ check: `picturebook desktop 125% equivalent ${kind} ${theme}`, status: 'passed',
      geometry, evidence: '1024×720 CSS viewport equivalent to 1280×900 at 125%; browser zoom UI not operated' });
  } finally { await page.setViewportSize(originalViewport); }
}

async function filteredEditorReturn(page, open, characters, shot, results) {
  const filters = { q: '测试', tag: '测试', world: 'unassigned', sort: 'name' };
  const search = new URLSearchParams(filters).toString();
  await open(page, `${libraryPath}?${search}`, '.character-library-grid');
  const button = page.getByTestId('character-card').filter({ has: page.getByRole('heading', { name: characters[0].card.name, exact: true }) })
    .getByRole('button', { name: '编辑', exact: true });
  await button.scrollIntoViewIfNeeded();
  // Record the actual nearest scroll owner, rather than assume the page or shell owns scrolling.
  const before = await button.evaluate(element => {
    let owner = element.parentElement;
    while (owner && !(owner.scrollHeight > owner.clientHeight && /auto|scroll/.test(getComputedStyle(owner).overflowY))) owner = owner.parentElement;
    owner ??= document.scrollingElement;
    const max = Math.max(0, owner.scrollHeight - owner.clientHeight);
    owner.scrollTop = Math.min(140, max);
    element.scrollIntoView({ block: 'center', behavior: 'auto' });
    return { scrollTop: owner.scrollTop, owner: owner.className || owner.tagName };
  });
  assert.ok(before.scrollTop > 0, 'Filtered return must exercise a nonzero list scroll position');
  await button.focus();
  await button.click();
  await page.getByTestId('card-description').waitFor();
  assert.equal(new URL(page.url()).search, `?${search}`, 'Editor retains the list query in its route');
  const input = page.getByTestId('card-description');
  const value = `${await input.inputValue()}\n合成筛选返回验收：人物正文保存后继续编辑。`;
  await input.fill(value);
  const response = page.waitForResponse(response => response.url().endsWith('/characters/char-demo0') && response.request().method() === 'PATCH');
  await page.getByRole('button', { name: '保存', exact: true }).click();
  assert.equal((await response).status(), 200);
  await page.locator('.character-save-notice').waitFor();
  await page.getByRole('button', { name: '返回角色库', exact: true }).click();
  await page.getByTestId('character-card').first().waitFor();
  assert.equal(new URL(page.url()).search, `?${search}`);
  assert.equal(await page.getByLabel('搜索角色', { exact: true }).inputValue(), filters.q);
  assert.equal(await page.getByLabel('角色标签筛选', { exact: true }).inputValue(), filters.tag);
  assert.equal(await page.getByLabel('角色世界筛选', { exact: true }).inputValue(), filters.world);
  assert.equal(await page.getByLabel('角色排序', { exact: true }).inputValue(), filters.sort);
  const returned = page.getByTestId('character-card').filter({ has: page.getByRole('heading', { name: characters[0].card.name, exact: true }) })
    .getByRole('button', { name: '编辑', exact: true });
  await page.waitForFunction(name => document.activeElement?.closest('[data-testid="character-card"]')?.querySelector('h3')?.textContent === name,
    characters[0].card.name);
  const after = await returned.evaluate(element => {
    let owner = element.parentElement;
    while (owner && !(owner.scrollHeight > owner.clientHeight && /auto|scroll/.test(getComputedStyle(owner).overflowY))) owner = owner.parentElement;
    owner ??= document.scrollingElement;
    return { scrollTop: owner.scrollTop, focused: document.activeElement === element, owner: owner.className || owner.tagName };
  });
  assert.equal(after.focused, true, 'Return restores focus to the original edit button');
  assert.ok(Math.abs(after.scrollTop - before.scrollTop) <= 2, `Return restores the list scroll position: ${JSON.stringify({ before, after })}`);
  await shot(page, 'picturebook-character-filtered-return-focus');
  results.push({ check: 'picturebook editor save and return retain list filters, edit focus and scroll position', status: 'passed', before, after });
  await open(page, libraryPath, '.character-library-grid');
}

async function createCharacterSuccess(page, characters, shot, results) {
  const countBefore = characters.length;
  const name = '合成月桥信使';
  const manuscript = '仅用于隔离验收的合成角色：在月桥两岸递送灯火与回信，熟悉河岸的小径。';
  await page.getByTestId('card-name').fill(name);
  await page.getByTestId('card-description').fill(manuscript);
  const create = page.getByRole('button', { name: '创建角色', exact: true });
  assert.equal(await create.isEnabled(), true);
  const response = page.waitForResponse(response => response.url().endsWith('/characters/import') && response.request().method() === 'POST');
  await create.click();
  const receipt = await response;
  assert.equal(receipt.status(), 200);
  const created = await receipt.json();
  assert.ok(created.id && created.card, 'Character import returns the committed raw Character');
  assert.equal(created.card.name, name);
  assert.equal(created.card.description, manuscript);
  await page.waitForURL(url => url.pathname === `${libraryPath}/${encodeURIComponent(created.id)}`);
  await page.getByTestId('card-description').waitFor();
  assert.equal(await page.getByTestId('card-name').inputValue(), name);
  assert.equal(await page.getByTestId('card-description').inputValue(), manuscript);
  assert.equal(characters.length, countBefore + 1, 'Creation durably adds exactly one synthetic character');
  assert.equal(characters.find(character => character.id === created.id)?.card.description, manuscript);
  assert.equal(await page.getByRole('dialog').count(), 0);
  await shot(page, 'picturebook-character-created-independent-editor');
  await page.getByRole('button', { name: '返回角色库', exact: true }).click();
  await page.getByTestId('character-card').filter({ has: page.getByRole('heading', { name, exact: true }) }).waitFor();
  assert.equal(await page.getByTestId('character-card').count(), countBefore + 1);
  await shot(page, 'picturebook-character-created-in-library');
  results.push({ check: 'picturebook successful creation opens the committed editor and appears in the library exactly once', status: 'passed', characterId: created.id });
}

async function mobileThemeFailure(page, theme, appearance, shot, results) {
  const other = theme === 'iris-light' ? 'iris-night' : 'iris-light';
  const labelFor = value => value === 'iris-light' ? '切换为深色外观' : '切换为亮色外观';
  // On phones the theme toggle lives in the navigation drawer; the failure alert shows in the single top bar.
  const toggleTheme = async from => {
    await page.getByRole('button', { name: '打开导航', exact: true }).click();
    const nav = page.getByRole('navigation', { name: '主导航', exact: true });
    await nav.getByRole('button', { name: labelFor(from), exact: true }).click();
    return async () => { await page.keyboard.press('Escape'); await nav.waitFor({ state: 'hidden' }); };
  };
  const endpoint = '**/api/v1/settings';
  let rejected = 0;
  const unavailable = async route => {
    if (route.request().method() !== 'PATCH') return route.continue();
    rejected++;
    assert.equal(route.request().postDataJSON().appearance.theme_id, other);
    await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Synthetic appearance save unavailable' }) });
  };
  await page.route(endpoint, unavailable);
  try {
    const closeDrawer = await toggleTheme(theme);
    await closeDrawer();
    await page.locator('.tpl-alert--bar').waitFor();
    await prototypeReady(page, theme);
    assert.equal(rejected, 1);
    assert.equal(appearance.theme_id, theme, 'Failed appearance PATCH does not commit the synthetic settings');
    const error = page.locator('.tpl-alert--bar');
    assert.equal(await error.getAttribute('role'), 'alert');
    const rect = await error.boundingBox();
    assert.ok(rect && rect.y >= 0 && rect.y + rect.height <= page.viewportSize().height, 'Appearance failure is visible outside the hidden drawer');
    await shot(page, 'picturebook-mobile-theme-save-failure-rollback');
  } finally { await page.unroute(endpoint, unavailable); }
  for (const [from, to] of [[theme, other], [other, theme]]) {
    const response = page.waitForResponse(response => response.url().endsWith('/api/v1/settings') && response.request().method() === 'PATCH');
    const closeDrawer = await toggleTheme(from);
    assert.equal((await response).status(), 200);
    await prototypeReady(page, to);
    assert.equal(appearance.theme_id, to);
    await closeDrawer();
    assert.equal(await page.locator('.tpl-alert--bar').isVisible(), false);
    await shot(page, `picturebook-mobile-theme-retry-${to}`);
  }
  results.push({ check: 'picturebook mobile theme save failure exposes alert, rolls back and retries successfully', status: 'passed', viewport: page.viewportSize() });
}

async function characterReadFailure(page, open, shot, results) {
  const endpoint = '**/api/v1/characters/char-demo0';
  let failedReads = 0;
  const offline = async route => {
    if (route.request().method() !== 'GET') return route.continue();
    failedReads++;
    await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Synthetic character unavailable' }) });
  };
  await page.route(endpoint, offline);
  try {
    await open(page, editorPath, '.character-editor-loading .page-load-state');
    const failure = page.getByRole('alert').filter({ hasText: '角色读取失败' });
    await failure.waitFor();
    assert.ok(failedReads > 0);
    assert.equal(await page.getByTestId('card-description').count(), 0, 'Failed read does not present an editable empty replacement');
    await visible(page.getByRole('button', { name: '重新读取', exact: true }), 'character read retry');
    await visible(page.getByRole('button', { name: '返回角色库', exact: true }), 'character read fallback');
    await noOverflow(page, 'character read failure');
    await shot(page, 'picturebook-character-read-fallback');
  } finally { await page.unroute(endpoint, offline); }
  await page.getByRole('button', { name: '重新读取', exact: true }).click();
  await page.getByTestId('card-description').waitFor();
  assert.equal(await page.locator('.character-editor-loading').count(), 0);
  await shot(page, 'picturebook-character-read-recovered');
  results.push({ check: 'picturebook failed character read keeps a retry/return fallback and recovers without a blank editor', status: 'passed' });
}

export async function checkPicturebookUI({ pageAt, open, shot, results, appearance, characters, snapshot, base }) {
  assert.ok(base.startsWith('http://127.0.0.1:'), 'Acceptance remains on the caller synthetic loopback fixture');
  const originalAppearance = structuredClone(appearance);
  const originalCharacters = structuredClone(characters);
  const originalSnapshot = structuredClone(snapshot);
  const reply = snapshot.messages.findLast(message => message.actor !== 'player' && message.actor !== 'director' && message.kind === 'roleplay');
  assert.ok(reply, 'The synthetic fixture needs a character reply');
  const longContent = '月光映在桥栏上，远处的灯火逐盏亮起。向导摊开旧地图，指着河岸尽头的石阶，提醒同行的人慢些走。'.repeat(9);
  reply.content = longContent;
  reply.variants = [
    { id: `${reply.id}-pb0`, content: longContent, created_at: reply.created_at },
    { id: `${reply.id}-pb1`, content: '合成候选：我们先沿灯火去桥边，确认河岸的路。', created_at: reply.created_at },
  ];
  reply.active_variant = 0;
  snapshot.active_scene_id = 'scene-approved-layout';
  reply.scene_id = snapshot.active_scene_id;
  snapshot.scenes = [{ id: snapshot.active_scene_id, title: '灯塔石阶', description: '独立合成验收场景：退潮后的石阶通往旧灯塔。',
    turn_start: 1, turn_end: null, member_ids: characters.slice(0, 2).map(character => character.id), group_ids: [], created_at: reply.created_at }];
  try {
    for (const theme of ['iris-light', 'iris-night']) {
      Object.assign(appearance, { theme_id: theme, visual_style_id: 'picturebook', typography_id: 'picturebook', decoration_id: 'none', bubble_style_id: 'plain', cursor_id: 'native', trail_id: 'none', click_effect_id: 'none' });
      for (const viewport of viewports) {
        const page = await pageAt(viewport.width, viewport.height, viewport.mobile);
        const label = `${theme}-${viewport.width}`;
        try {
          await open(page, storyPath, '.story-workspace .v7-message');
          await prototypeReady(page, theme);
          await assertStoryControls(page, reply.id, viewport.mobile);
          const reading = await storyGeometry(page);
          assert.ok(reading.frame.width - reading.reading <= 82 && reading.frame.width - reading.composer <= 50,
            `Reading and input use their shared workspace: ${JSON.stringify(reading)}`);
          assert.ok(reading.inputFrame.left >= reading.frame.left && reading.inputFrame.right <= reading.frame.right + 1,
            'Input stays inside the continuous reading frame');
          assert.ok(Math.abs((reading.inputFrame.left + reading.inputFrame.right) / 2 - (reading.frame.left + reading.frame.right) / 2) <= 2,
            'Reading frame and composer share the same axis');
          assert.ok(reading.bubble <= reading.reading + 1, `Bubble fits available reading space: ${JSON.stringify(reading)}`);
          assert.ok(/sans-serif/i.test(reading.storyFont), `Approved Demo uses a clear UI story face: ${reading.storyFont}`);
          assert.equal(reading.inputFont, reading.storyFont, 'Approved story and editing use the same clear body family');
          if (!viewport.mobile) assert.ok(reading.inputFrame.height <= 230, `Default composer leaves room for reading: ${JSON.stringify(reading.inputFrame)}`);
          const storyBounds = await noOverflow(page, `story ${label}`);
          await shot(page, `picturebook-${label}-story`);
          results.push({ check: `picturebook story ${label}`, status: 'passed', geometry: storyBounds, reading });
          if (viewport.width === 1280 || viewport.width === 390) {
            const toggle = page.getByRole('button', { name: '故事资料', exact: true });
            if (viewport.mobile) {
              assert.equal(await toggle.getAttribute('aria-expanded'), 'false');
              await toggle.click();
              await visible(page.getByRole('complementary', { name: '故事资料', exact: true }), 'mobile story context');
              await visible(page.getByRole('heading', { name: '灯塔石阶', exact: true }), 'actual scene from synthetic snapshot');
              await page.getByRole('button', { name: '收起故事资料', exact: true }).click();
            } else {
              await visible(page.getByRole('heading', { name: '灯塔石阶', exact: true }), 'actual scene from synthetic snapshot');
              const widthBefore = (await page.locator('.story-conversation').boundingBox()).width;
              await toggle.click();
              assert.equal(await page.getByRole('complementary', { name: '故事资料', exact: true }).count(), 0);
              assert.ok((await page.locator('.story-conversation').boundingBox()).width > widthBefore + 220, 'Closing context returns its space to conversation');
              await toggle.click();
            }
            await noOverflow(page, `context toggle ${label}`);
            results.push({ check: `approved story context toggle ${label}`, status: 'passed' });
          }
          if (theme === 'iris-light' && viewport.width === 1280) {
            await messageOperations(page, reply, snapshot, base, shot, results);
            await sharedMoonweaveMotion(page, shot, results);
            await storyMotionFallback(page, shot, results);
          }
          if (viewport.width === 1280) await desktopZoomEquivalent(page, 'story', theme, reply.id, shot, results);
          if (viewport.mobile) await keyboardSend(page, viewport, theme, snapshot, shot, results);

          await open(page, libraryPath, '.character-library-grid');
          await prototypeReady(page, theme);
          assert.equal(await page.getByTestId('character-card').count(), characters.length);
          const libraryBounds = await noOverflow(page, `characters ${label}`);
          await visible(page.getByTestId('character-create'), 'create character destination');
          if (viewport.mobile) await mobileTouchTargets(page.locator('.character-library-actions > button, .character-library-tools > summary, [data-character-edit]'), `library ${label}`);
          await shot(page, `picturebook-${label}-characters`);
          results.push({ check: `picturebook character library ${label}`, status: 'passed', geometry: libraryBounds });
          if (viewport.width === 1280) await desktopZoomEquivalent(page, 'characters', theme, reply.id, shot, results);
          if (theme === 'iris-light' && viewport.width === 390) await mobileThemeFailure(page, theme, appearance, shot, results);
          await page.getByTestId('character-card').filter({ has: page.getByRole('heading', { name: characters[0].card.name, exact: true }) }).getByRole('button', { name: '编辑', exact: true }).click();
          await page.getByTestId('card-description').waitFor();
          assert.equal(new URL(page.url()).pathname, editorPath, 'Character edit directly opens its own route');
          await prototypeReady(page, theme);
          assert.equal(await page.getByRole('dialog').count(), 0, 'Independent editor is not a nested dialog');
          if (viewport.mobile) await mobileTouchTargets(page.locator('.character-editor-navigation > button, .character-editor-commit > button, .character-editor-commit > .character-editor-more > summary'), `editor ${label}`);
          const editorBounds = await noOverflow(page, `editor ${label}`);
          const editorLayout = await page.locator('.character-editor-page').evaluate(element => ({
            page: element.getBoundingClientRect().toJSON(),
            route: document.querySelector('.tpl-content').getBoundingClientRect().toJSON(),
            main: element.querySelector('.character-editor-columns').getBoundingClientRect().toJSON(),
            input: element.querySelector('[data-testid="card-description"]').getBoundingClientRect().toJSON(),
          }));
          if (viewport.width >= 1280) {
            assert.ok(editorLayout.page.left - editorLayout.route.left <= 26 && editorLayout.route.right - editorLayout.page.right <= 26,
              `Editor uses the available page instead of a centered width cap: ${JSON.stringify(editorLayout)}`);
            assert.ok(editorLayout.input.width >= editorLayout.main.width - 86 && editorLayout.input.width <= editorLayout.main.width,
              `Manuscript uses its column with normal insets: ${JSON.stringify(editorLayout)}`);
          }
          const firstManuscript = await page.getByTestId('card-description').boundingBox();
          if (viewport.mobile) {
            assert.ok(firstManuscript && firstManuscript.y >= 0 && firstManuscript.y < 440, `Mobile prioritizes manuscript on first screen: ${JSON.stringify(firstManuscript)}`);
            if (viewport.width === 390) assert.ok(firstManuscript.y <= 400, `390px manuscript first-screen position: ${JSON.stringify(firstManuscript)}`);
          }
          await shot(page, `picturebook-${label}-character-editor`);
          if (theme === 'iris-light' && (viewport.width === 1280 || viewport.width === 390)) await characterRelatedFlow(page, viewport.mobile, shot, results);
          const chapter = await focusChapter(page, 'character-appearance-ability', viewport.mobile);
          await shot(page, `picturebook-${label}-character-chapter-focus`);
          if (viewport.width === 390) {
            await focusChapter(page, 'character-affiliation', true);
            assert.equal(await page.locator('#character-affiliation').evaluate(element => element.open), true);
            await visible(page.locator('#character-affiliation').getByLabel('来源世界', { exact: true }), 'world affiliation select after chapter navigation');
            await shot(page, `picturebook-${label}-character-affiliation-focus`);
          }
          await focusChapter(page, 'character-manuscript', viewport.mobile);
          if (viewport.width === 1280) await desktopZoomEquivalent(page, 'editor', theme, reply.id, shot, results);
          results.push({ check: `picturebook character editor ${label}`, status: 'passed', geometry: editorBounds, chapter, manuscriptBounds: firstManuscript });
          if (theme === 'iris-light' && viewport.width === 1280) await characterDraftFlow(page, characters, shot, results);
          await page.getByRole('button', { name: '返回角色库', exact: true }).click();
          await page.getByTestId('character-create').waitFor();
          if (theme === 'iris-light' && viewport.width === 1280) await filteredEditorReturn(page, open, characters, shot, results);
          await page.getByTestId('character-create').click();
          await page.getByTestId('card-description').waitFor();
          assert.equal(new URL(page.url()).pathname, `${libraryPath}/new`, 'Creation has an independent editor route');
          assert.equal(await page.getByRole('dialog').count(), 0);
          assert.equal(await page.getByTestId('card-name').inputValue(), '');
          assert.equal(await page.getByRole('button', { name: '创建角色', exact: true }).isEnabled(), false, 'Unnamed creation cannot save');
          await shot(page, `picturebook-${label}-new-character`);
          results.push({ check: `picturebook new character route ${label}`, status: 'passed' });
          if (theme === 'iris-light' && viewport.width === 1280) {
            await createCharacterSuccess(page, characters, shot, results);
            await characterReadFailure(page, open, shot, results);
          }
        } catch (cause) {
          await shot(page, `picturebook-${label}-failure`).catch(() => {});
          results.push({ check: `picturebook ${label}`, status: 'failed', reason: cause.message });
          throw cause;
        } finally { await page.context().close(); }
      }
    }
    assert.ok(base.startsWith('http://127.0.0.1:'), 'Acceptance remains on the caller synthetic loopback fixture');
  } finally {
    for (const key of Object.keys(appearance)) delete appearance[key];
    Object.assign(appearance, originalAppearance);
    characters.splice(originalCharacters.length);
    characters.forEach((character, index) => {
      for (const key of Object.keys(character)) delete character[key];
      Object.assign(character, originalCharacters[index]);
    });
    for (const key of Object.keys(snapshot)) delete snapshot[key];
    Object.assign(snapshot, originalSnapshot);
    snapshot.characters = originalSnapshot.characters.map(character => characters.find(item => item.id === character.id) ?? character);
  }
}

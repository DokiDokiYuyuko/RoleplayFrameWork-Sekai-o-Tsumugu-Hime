/** Real built-UI checks against tools/acceptance/development_preview.py only.
 * Start a fresh synthetic fixture; set UI_ACCEPTANCE_URL, UI_ACCEPTANCE_OUT,
 * PLAYWRIGHT_MODULE (if needed) and UI_BROWSER_CHANNEL. Never use a live story server.
 */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.UI_ACCEPTANCE_URL || 'http://127.0.0.1:8015';
const root = fileURLToPath(new URL('../../../', import.meta.url));
assert.ok(process.env.UI_ACCEPTANCE_OUT, 'Set UI_ACCEPTANCE_OUT outside the checkout');
const output = resolve(process.env.UI_ACCEPTANCE_OUT);
const relativeOutput = relative(root, output);
assert.ok(relativeOutput === '..' || relativeOutput.startsWith(`..${process.platform === 'win32' ? '\\' : '/'}`), 'Keep screenshots outside source');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true, ...(process.env.UI_BROWSER_CHANNEL ? { channel: process.env.UI_BROWSER_CHANNEL } : {}) });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.setDefaultTimeout(12_000);
const results = [], errors = [];
let checkIndex = 0;
page.on('pageerror', (error) => errors.push(error.message));
page.on('dialog', (dialog) => dialog.accept());
const json = async (path) => {
  const response = await page.request.get(base + path); assert.ok(response.ok(), `${path}: ${response.status()}`); return response.json();
};
const check = async (name, run) => {
  if (++checkIndex < Number(process.env.UI_ACCEPTANCE_FROM || 1)) return;
  await run(); results.push({ name, ok: true }); console.log(`PASS ${name}`);
};
const screenshot = (name) => page.screenshot({ path: resolve(output, `${name}.png`), fullPage: true });
const expectVisible = async (locator) => { await locator.waitFor({ state: 'visible' }); };
let fixture, storyPath, originalBranch;
try {
  fixture = await json('/api/v1/acceptance-fixture');
  assert.equal(fixture.fixture, 'mrp.development.synthetic', 'Refusing a real server');
  storyPath = `/stories/${fixture.story_id}/branches/${fixture.branch_id}`;
  const before = await json(`/api/v1/sessions/${fixture.branch_id}`);
  if (!process.env.UI_ACCEPTANCE_FROM) assert.equal(before.turn_runs.at(-1).status, 'failed', 'Start a fresh fixture for deterministic checks');
  await page.goto(base + storyPath);
  await check('普通回合失败后保留输入与已完成回应', async () => {
    await expectVisible(page.getByText('本轮尚未完成 · 已保存 1/2 条回应', { exact: true }));
    await expectVisible(page.getByText('带好地图，我们讨论北岸的路线。', { exact: true }));
    assert.ok((await page.locator('[data-message-id]').count()) <= 100);
    await screenshot('recovery-desktop');
  });
  await check('早期消息深链接跨分页并在刷新后定位', async () => {
    await page.goto(base + storyPath + `?message=${before.messages[1].id}`);
    await expectVisible(page.locator(`#message-${before.messages[1].id}`));
    await page.reload(); await expectVisible(page.locator(`#message-${before.messages[1].id}`));
    await page.getByRole('button', { name: '回到最新', exact: true }).click();
    await expectVisible(page.getByText('带好地图，我们讨论北岸的路线。', { exact: true }));
    await page.waitForFunction(() => {
      const list = document.querySelector('.v7-message-list');
      return list && list.scrollHeight - list.scrollTop - list.clientHeight < 2;
    });
    assert.equal(new URL(page.url()).searchParams.has('message'), false, 'Latest view must not restore an old anchor after reload');
    assert.equal(await page.getByRole('button', { name: '回到最新', exact: true }).count(), 0);
    assert.equal(await page.locator('.v7-message-list .sticky').count(), 0);
    await page.reload();
    await expectVisible(page.getByText('带好地图，我们讨论北岸的路线。', { exact: true }));
    await page.waitForFunction(() => {
      const list = document.querySelector('.v7-message-list');
      return list && list.scrollHeight - list.scrollTop - list.clientHeight < 2;
    });
    await page.locator('.v7-message-list').evaluate(list => { list.scrollTop = list.scrollHeight / 2; });
    await expectVisible(page.getByRole('button', { name: '回到最新', exact: true }));
    await page.getByRole('button', { name: '回到最新', exact: true }).click();
    await page.waitForFunction(() => {
      const list = document.querySelector('.v7-message-list');
      return list && list.scrollHeight - list.scrollTop - list.clientHeight < 2;
    });
    await page.getByRole('button', { name: '回到最新', exact: true }).waitFor({ state: 'hidden' });
    await page.locator('.v7-message-list').evaluate(list => { list.scrollTop = 0; });
    const earlier = page.getByRole('button', { name: '查看更早消息', exact: true });
    await expectVisible(earlier);
    assert.equal(await earlier.evaluate(button => getComputedStyle(button.parentElement).position), 'static');
    await earlier.click();
    await expectVisible(page.locator(`#message-${before.messages[0].id}`));
    await expectVisible(page.getByRole('button', { name: '查看后续消息', exact: true }));
    await page.getByRole('button', { name: '回到最新', exact: true }).click();
    await page.waitForFunction(() => {
      const list = document.querySelector('.v7-message-list');
      return list && list.scrollHeight - list.scrollTop - list.clientHeight < 2;
    });
    await screenshot('history-controls-latest');
  });
  await check('补完剩余回应不重复已保存回应', async () => {
    const completed = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/resume'));
    await page.getByRole('button', { name: '补完剩余回应', exact: true }).click();
    const response = await completed; assert.ok(response.ok()); assert.equal((await response.json()).turn_run.status, 'completed');
    await page.getByText('本轮尚未完成 · 已保存 1/2 条回应', { exact: true }).waitFor({ state: 'hidden' });
    const after = await json(`/api/v1/sessions/${fixture.branch_id}`);
    assert.equal(after.turn_runs.at(-1).status, 'completed');
    assert.equal(after.messages.length, before.messages.length + 1);
    assert.deepEqual(after.messages.slice(0, before.messages.length).map(m => m.id), before.messages.map(m => m.id));
  });
  await check('路线回顾带未完成事项与消息来源，键盘关闭', async () => {
    await page.getByRole('button', { name: '继续故事', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '继续故事' });
    await expectVisible(dialog.getByText('明天归还小铜铃。', { exact: false }).first());
    assert.ok(await dialog.getByRole('button', { name: /查看原消息/ }).count());
    await screenshot('review-desktop');
    await page.keyboard.press('Escape'); await dialog.waitFor({ state: 'hidden' });
    await page.waitForFunction(() => document.activeElement?.textContent === '继续故事');
  });
  await check('从原消息开线后比较共同锚点', async () => {
    const anchor = before.messages.findLast(m => m.actor === 'player');
    const bubble = page.locator(`[data-message-id="${anchor.id}"]`);
    await bubble.getByRole('button', { name: '消息操作', exact: true }).click();
    await page.getByRole('menuitem', { name: '新世界线', exact: true }).click();
    const fork = page.getByRole('dialog', { name: '从此处开新世界线' });
    await fork.getByLabel('路线名称', { exact: true }).fill('保留原设定 · 合成分线');
    await fork.getByRole('button', { name: '确定', exact: true }).click();
    await page.waitForURL(url => url.pathname.split('/').at(-1) !== fixture.branch_id);
    originalBranch = new URL(page.url()).pathname.split('/').at(-1);
    await page.getByRole('button', { name: '继续故事', exact: true }).click();
    const review = page.getByRole('dialog', { name: '继续故事' });
    await review.getByRole('button', { name: '路线分歧', exact: true }).click();
    await review.getByLabel('比较路线', { exact: true }).selectOption(fixture.branch_id);
    await expectVisible(review.getByText('共同锚点', { exact: true }));
    await expectVisible(review.getByText(anchor.content, { exact: true }).first());
    await screenshot('route-comparison-desktop');
    await page.goto(base + storyPath);
  });
  await check('选择单项新版设定只更新本路线', async () => {
    await page.getByRole('button', { name: '继续故事', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '继续故事' });
    await dialog.getByRole('button', { name: '设定更新', exact: true }).click();
    await expectVisible(dialog.getByText('熟悉海岸，新版修订：擅长游泳，但不会替旅客决定路线。', { exact: true }));
    await dialog.getByRole('checkbox', { name: '灯塔守卫 · 描述', exact: true }).check();
    await dialog.getByRole('button', { name: '应用 1 项到本路线', exact: true }).click();
    await dialog.getByRole('button', { name: '应用 0 项到本路线', exact: true }).waitFor({ state: 'visible' });
    const after = await json(`/api/v1/sessions/${fixture.branch_id}`);
    assert.match(after.characters.find(c => c.id === fixture.guard_id).card.description, /新版修订/);
    assert.equal(after.meta.world_core_brief, before.meta.world_core_brief);
    if (originalBranch) {
      const old = await json(`/api/v1/sessions/${originalBranch}`);
      assert.match(old.characters.find(c => c.id === fixture.guard_id).card.description, /旧设定：不能游泳/);
    }
    await dialog.getByRole('button', { name: '关闭继续故事', exact: true }).click();
  });
  await check('核对原输入、选择修订并保留旧候选', async () => {
    const current = await json(`/api/v1/sessions/${fixture.branch_id}`);
    const target = current.messages.findLast(m => m.actor === fixture.guard_id && m.generation_meta);
    const bubble = page.locator(`[data-message-id="${target.id}"]`);
    await bubble.getByRole('button', { name: '消息操作', exact: true }).click();
    await page.getByRole('menuitem', { name: '查看这条上下文', exact: true }).click();
    await page.getByRole('button', { name: '核对与修订依据', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '核对与修订依据', exact: true });
    await expectVisible(dialog.getByRole('button', { name: /角色设定与示例/ }).first());
    await dialog.getByRole('button', { name: /角色设定与示例/ }).first().click();
    await expectVisible(dialog.getByText(/旧设定：不能游泳/).first());
    await expectVisible(dialog.getByText(/新版修订：擅长游泳/).first());
    await dialog.getByRole('button', { name: '核对可用于这条回应的修订', exact: true }).click();
    await dialog.locator('label').filter({ hasText: '灯塔守卫 · 描述' }).getByRole('checkbox').check();
    await dialog.getByRole('button', { name: '用选定修订生成新候选', exact: true }).click();
    await expectVisible(dialog.getByText('已按选定修订生成新候选，旧候选仍可查看。后续受影响的回应需要逐项核对。', { exact: true }));
    const after = await json(`/api/v1/sessions/${fixture.branch_id}`);
    const corrected = after.messages.find(m => m.id === target.id);
    assert.ok(corrected.variants.length >= 2);
    assert.ok(corrected.variants.some(v => v.generation_meta?.generation_id === target.generation_meta.generation_id));
    await screenshot('correction-desktop');
    await page.keyboard.press('Escape');
  });
  await check('剧情导航使用人物名称并定位搜索结果', async () => {
    await page.getByRole('button', { name: '更多故事操作', exact: true }).click();
    await page.getByRole('menuitem', { name: '剧情导航' }).click();
    await page.getByLabel('人物', { exact: true }).selectOption(fixture.guard_id);
    await page.getByPlaceholder('搜索故事正文').fill('合成历史 2：');
    await page.getByRole('button', { name: '搜索', exact: true }).click();
    const result = page.getByRole('button', { name: /合成历史 2：/ }).first();
    await result.click(); await expectVisible(page.getByText('合成历史 2：我们在灯塔核对路线，约定明天归还小铜铃。', { exact: true }));
  });
  await check('刷新后恢复角色编辑草稿', async () => {
    await page.goto(base + `/library/characters/${fixture.guard_id}`);
    await page.getByTestId('card-description').fill('合成草稿：尚未保存的海岸描述。');
    await page.waitForFunction(key => localStorage.getItem(key)?.includes('合成草稿'), `mrp.character-editor.${fixture.guard_id}`);
    await page.reload(); await page.getByRole('button', { name: '恢复草稿', exact: true }).click();
    assert.equal(await page.getByTestId('card-description').inputValue(), '合成草稿：尚未保存的海岸描述。');
    const saved = await json(`/api/v1/characters/${fixture.guard_id}`);
    assert.ok(!saved.card.description.includes('合成草稿'));
    await page.reload(); await page.getByRole('button', { name: '丢弃草稿', exact: true }).click();
    assert.equal(await page.evaluate(key => localStorage.getItem(key), `mrp.character-editor.${fixture.guard_id}`), null);
  });
  await check('ST单角色迁入预检、人物映射与候选保留', async () => {
    await page.goto(base + '/'); await page.getByRole('button', { name: '迁入酒馆聊天', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '迁入酒馆聊天', exact: true });
    const rows = [{ user_name: '旅客', character_name: '灯塔守卫' }, { name: '旅客', is_user: true, mes: '还记得小铜铃吗？' },
      { name: '灯塔守卫', is_user: false, mes: '记得，是你的铜铃。', swipes: ['记得，是你的铜铃。', '我把小铜铃收在抽屉里。'], swipe_id: 0, extra: { bias: 'synthetic unsupported field' } }];
    await dialog.getByLabel('聊天文件', { exact: true }).setInputFiles({ name: '合成迁入.jsonl', mimeType: 'application/jsonl', buffer: Buffer.from(rows.map(r => JSON.stringify(r)).join('\n')) });
    await expectVisible(dialog.getByText(/识别到 2 条消息/));
    await dialog.getByLabel('映射到角色', { exact: true }).selectOption(fixture.guard_id);
    assert.equal(await dialog.getByRole('button', { name: '迁入为新故事', exact: true }).isDisabled(), true);
    await dialog.getByRole('checkbox').check(); await dialog.getByRole('button', { name: '迁入为新故事', exact: true }).click();
    await expectVisible(page.getByText('还记得小铜铃吗？', { exact: true }));
    const branch = new URL(page.url()).pathname.split('/').at(-1);
    const imported = await json(`/api/v1/sessions/${branch}`);
    assert.equal(imported.messages.filter(m => m.actor !== 'player' && m.kind === 'roleplay')[0].variants.length, 2);
    await screenshot('chat-import-desktop');
  });
  await check('个人库恢复清单说明备份范围与密钥边界', async () => {
    await page.goto(base + '/settings'); await page.getByText('个人库恢复', { exact: true }).click();
    await expectVisible(page.getByRole('heading', { name: '个人库恢复清单', exact: true }));
    await expectVisible(page.getByRole('heading', { name: '需要另行配置', exact: true }));
    assert.ok(await page.getByRole('table').count());
    const archive = await page.request.get(base + '/api/v1/acceptance-library-backup'); assert.ok(archive.ok());
    await page.getByLabel('校验已有私密库备份').setInputFiles({ name: '合成私密库.zip', mimeType: 'application/zip', buffer: await archive.body() });
    await expectVisible(page.getByText(/清单及文件校验通过/));
  });
  await check('390px视口与对话框没有横向溢出', async () => {
    await page.setViewportSize({ width: 390, height: 844 }); await page.goto(base + storyPath);
    await page.locator('[data-message-id]').first().waitFor({ state: 'attached' });
    await page.waitForFunction(() => {
      const list = document.querySelector('.v7-message-list');
      return list && list.scrollHeight - list.scrollTop - list.clientHeight < 2;
    });
    assert.equal(await page.getByRole('button', { name: '回到最新', exact: true }).count(), 0);
    await page.locator('.v7-message-list').evaluate(list => { list.scrollTop = list.scrollHeight / 2; });
    const jump = page.getByRole('button', { name: '回到最新', exact: true });
    await expectVisible(jump);
    const jumpBox = await jump.boundingBox();
    assert.ok(jumpBox && jumpBox.width < 140 && jumpBox.x >= 0 && jumpBox.x + jumpBox.width <= 390, JSON.stringify(jumpBox));
    await screenshot('history-controls-mobile');
    await jump.click();
    await page.waitForFunction(() => {
      const list = document.querySelector('.v7-message-list');
      return list && list.scrollHeight - list.scrollTop - list.clientHeight < 2;
    });
    await jump.waitFor({ state: 'hidden' });
    await page.getByRole('button', { name: '继续故事', exact: true }).click();
    await expectVisible(page.getByRole('dialog', { name: '继续故事' }));
    const overflow = await page.evaluate(() => ({ width: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth }));
    assert.ok(overflow.scroll <= overflow.width + 1, JSON.stringify(overflow));
    await screenshot('review-mobile-viewport');
    await page.keyboard.press('Escape');
    assert.equal(await page.getByRole('dialog').count(), 0);
  });
  await check('深色主题的路线面板使用主题表面与文字', async () => {
    const saved = await page.request.patch(base + '/api/v1/settings', {
      headers: { origin: new URL(base).origin }, data: { appearance: { theme_id: 'dark' } },
    }); assert.ok(saved.ok());
    await page.setViewportSize({ width: 1440, height: 1000 }); await page.goto(base + storyPath);
    await page.getByRole('button', { name: '继续故事', exact: true }).click();
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark');
    const panel = page.getByRole('dialog', { name: '继续故事' });
    await expectVisible(panel.getByText('近期原消息', { exact: true }));
    const colors = await panel.evaluate(el => ({ text: getComputedStyle(el).color, surface: getComputedStyle(el.querySelector('aside')).backgroundColor }));
    assert.notEqual(colors.text, colors.surface);
    await screenshot('review-dark'); await page.keyboard.press('Escape');
  });
  await check('旧服务缺少接口时提示重启，重试能恢复路线回顾', async () => {
    await page.goto(base + storyPath);
    const unavailable = '**/api/v1/branches/*/review?*';
    await page.route(unavailable, route => route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'not found' }) }));
    await page.getByRole('button', { name: '继续故事', exact: true }).click();
    const panel = page.getByRole('dialog', { name: '继续故事' });
    await expectVisible(panel.getByText('当前服务尚未加载此功能。请在没有生成任务时重新启动织界之姬，然后重试。', { exact: true }));
    await page.unroute(unavailable);
    await panel.getByRole('button', { name: '重试', exact: true }).click();
    await expectVisible(panel.getByText('近期原消息', { exact: true }));
    assert.equal(await panel.getByRole('alert').count(), 0);
    const unavailableUpdates = '**/api/v1/branches/*/asset-updates';
    await page.route(unavailableUpdates, route => route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'not found' }) }));
    await panel.getByRole('button', { name: '设定更新', exact: true }).click();
    await expectVisible(panel.getByText('当前服务尚未加载此功能。请在没有生成任务时重新启动织界之姬，然后重试。', { exact: true }));
    assert.equal(await panel.getByRole('button', { name: /^应用 \d+ 项到本路线$/ }).count(), 0, 'Unavailable updates must not pretend to contain zero fields');
    await page.unroute(unavailableUpdates);
    await panel.getByRole('button', { name: '重试', exact: true }).click();
    await panel.getByRole('alert').waitFor({ state: 'hidden' });
    await expectVisible(panel.getByRole('button', { name: /^应用 \d+ 项到本路线$/ }));
    assert.equal(await panel.getByRole('button', { name: /^应用 \d+ 项到本路线$/ }).count(), 1);
    await page.keyboard.press('Escape');
  });
  assert.deepEqual(errors, [], 'Unexpected browser runtime errors');
} catch (error) {
  await screenshot('failure'); console.error((await page.locator('[role="dialog"]').allTextContents()).join('\n').slice(0, 3000)); throw error;
} finally {
  await writeFile(resolve(output, 'development-ui-results.json'), JSON.stringify({ fixture: fixture?.fixture, results, errors }, null, 2));
  await browser.close();
}

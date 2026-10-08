/** Built UI + real HTTP jobs against disposable development_preview.py only. */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = fileURLToPath(new URL('../../', import.meta.url));
const base = process.env.UI_ACCEPTANCE_URL;
assert.ok(base && process.env.UI_ACCEPTANCE_OUT);
const output = resolve(process.env.UI_ACCEPTANCE_OUT);
assert.ok(relative(root, output).startsWith('..'), 'Artifacts must stay outside source');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true, ...(process.env.UI_BROWSER_CHANNEL ? { channel: process.env.UI_BROWSER_CHANNEL } : {}) });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, extraHTTPHeaders: { Origin: base } });
const page = await context.newPage();
page.setDefaultTimeout(15000);
const results = [], errors = [];
page.on('pageerror', error => errors.push(error.message));
const get = async path => { const r = await context.request.get(base + path); assert.ok(r.ok(), `${path}: ${r.status()}`); return r.json(); };
const check = async (name, run) => { await run(); results.push({ name, ok: true }); console.log('PASS ' + name); };
const shot = name => page.screenshot({ path: resolve(output, name + '.png'), fullPage: true });
const wait = async fn => { for (let i = 0; i < 160; i++) { const value = await fn(); if (value) return value; await page.waitForTimeout(100); } throw new Error('Synthetic operation did not finish'); };
const panel = () => page.getByRole('region', { name: '世界原稿', exact: true });
let fixture, worldId, firstBook, original;
try {
  fixture = await get('/api/v1/acceptance-fixture');
  assert.equal(fixture.fixture, 'mrp.development.synthetic', 'Refusing a live server');
  await check('名字和长原稿同次保存，零整理调用', async () => {
    await page.goto(base + '/worlds');
    await page.getByRole('button', { name: '新建世界', exact: true }).first().click();
    const dialog = page.getByRole('dialog', { name: '新建世界', exact: true });
    await dialog.getByLabel('世界名称', { exact: true }).fill('合成创作验收世界');
    original = '  合成潮晶只在满月高潮时蓄能，黎明之后能量散尽。\n\n海盐会扰乱潮晶。\n' + '示例旅人沿着海岸行走。\n'.repeat(120) + '  '; 
    await dialog.getByLabel('世界原稿', { exact: true }).fill(original);
    await dialog.getByRole('button', { name: '创建世界', exact: true }).click();
    await page.waitForURL(/\/worlds\/world-/);
    worldId = new URL(page.url()).pathname.split('/').at(-1);
    const world = await get('/api/v1/worlds/' + worldId);
    assert.equal(world.archive_records[0].body, original);
    assert.equal(world.runtime_policy, 'raw');
    assert.deepEqual((await get('/api/v1/acceptance-fixture')).authoring_calls, fixture.authoring_calls);
    assert.equal(await panel().getByLabel('世界设定原稿', { exact: true }).inputValue(), original);
    await shot('world-free-desktop');
  });
  await check('刷新恢复原稿草稿与手动保存，原文检查无模型调用', async () => {
    const body = panel().getByLabel('世界设定原稿', { exact: true });
    await body.fill(original + '\n合成未保存追加段。');
    await page.reload();
    assert.equal(await panel().getByLabel('世界设定原稿', { exact: true }).inputValue(), original + '\n合成未保存追加段。');
    await panel().getByRole('button', { name: '保存原稿', exact: true }).click();
    await panel().getByText('原稿已保存。', { exact: false }).waitFor();
    original += '\n合成未保存追加段。';
    const before = (await get('/api/v1/acceptance-fixture')).authoring_calls;
    const details = panel().locator('details').filter({ has: page.locator('summary').filter({ hasText: '新故事如何使用这些资料' }) });
    await details.locator('summary').first().click();
    await panel().getByRole('button', { name: '检查实际上下文（不生成回复）', exact: true }).click();
    await panel().getByText('查看完整输入', { exact: true }).waitFor();
    assert.deepEqual((await get('/api/v1/acceptance-fixture')).authoring_calls, before);
  });
  await check('补写候选过期保护、可编辑采用和撤销', async () => {
    const details = panel().locator('details').filter({ has: page.locator('summary').filter({ hasText: '构思辅助：补写指定部分' }) });
    await details.locator('summary').first().click();
    await panel().getByLabel('想补哪一部分？', { exact: true }).fill('只补充冬季灯笼节。');
    await panel().getByRole('button', { name: '生成补写候选', exact: true }).click();
    await panel().getByLabel('新增设定候选', { exact: true }).waitFor();
    await panel().getByLabel('世界设定原稿', { exact: true }).fill(original + '\n生成期间手改。');
    assert.equal(await panel().getByRole('button', { name: '采用到原稿草稿', exact: true }).isDisabled(), true);
    assert.equal((await get('/api/v1/worlds/' + worldId)).archive_records[0].body, original);
    await panel().getByLabel('世界设定原稿', { exact: true }).fill(original);
    await panel().getByLabel('新增设定候选', { exact: true }).fill('采用的合成新增节庆。');
    await panel().getByRole('button', { name: '采用到原稿草稿', exact: true }).click();
    assert.ok((await panel().getByLabel('世界设定原稿', { exact: true }).inputValue()).endsWith('采用的合成新增节庆。'));
    await panel().getByRole('button', { name: '撤销补写采用', exact: true }).click();
    assert.equal(await panel().getByLabel('世界设定原稿', { exact: true }).inputValue(), original);
  });
  await check('原稿、核心、词条集中手改后一次采用，实际请求无重复原稿', async () => {
    await panel().getByRole('button', { name: '帮我整理核心与世界书', exact: true }).click();
    const review = page.getByRole('region', { name: '集中核对设定', exact: true });
    await review.getByLabel('待审核心', { exact: true }).waitFor();
    await review.getByLabel('待审核心', { exact: true }).fill('合成手修核心：只有潮汐规则每轮常驻。');
    await review.getByRole('button', { name: '保存此项修改', exact: true }).click();
    await review.getByRole('button', { name: '一次采用 3 项', exact: true }).waitFor();
    await shot('world-review-desktop');
    await review.getByRole('button', { name: '一次采用 3 项', exact: true }).click();
    await review.getByText('已采用到世界。', { exact: false }).waitFor();
    const world = await get('/api/v1/worlds/' + worldId);
    assert.equal(world.core_brief, '合成手修核心：只有潮汐规则每轮常驻。');
    assert.equal(world.archive_records[0].body, original);
    assert.equal(world.lorebook_ids.length, 1); firstBook = world.lorebook_ids[0];
    const previewResponse = await context.request.post(base + '/api/v1/worlds/' + worldId + '/runtime-preview', { data: { message: '看看潮晶。' } });
    assert.ok(previewResponse.ok());
    const preview = await previewResponse.json();
    assert.equal(preview.valid, true); assert.ok(preview.prompt.includes('合成手修核心'));
    assert.ok(!preview.prompt.includes('示例旅人沿着海岸行走。'));
    assert.ok(preview.prompt.includes('合成潮晶规则'));
  });
  await check('选中已有书做增量更新，保留 UID 和书身份', async () => {
    const before = await get('/api/v1/lorebooks/' + firstBook);
    await panel().getByLabel('世界设定原稿', { exact: true }).fill(original + '\n潮晶修订：海盐只影响未充满的潮晶。');
    await panel().getByRole('button', { name: '保存原稿', exact: true }).click();
    await panel().getByText('原稿已保存。', { exact: false }).waitFor();
    await panel().getByRole('button', { name: '帮我整理核心与世界书', exact: true }).click();
    const review = page.getByRole('region', { name: '集中核对设定', exact: true });
    await review.getByRole('button', { name: '一次采用 3 项', exact: true }).waitFor();
    await review.getByRole('button', { name: '一次采用 3 项', exact: true }).click();
    await review.getByText('已采用到世界。', { exact: false }).waitFor();
    const world = await get('/api/v1/worlds/' + worldId);
    assert.deepEqual(world.lorebook_ids, [firstBook]);
    const book = await get('/api/v1/lorebooks/' + firstBook);
    assert.deepEqual(book.entries.map(row => row.uid), before.entries.map(row => row.uid));
  });
  await check('九主题与桌面、360/390/430窄屏布局及键盘新建', async () => {
    for (const theme of ['astral', 'dark', 'light', 'midnight', 'rose-night', 'amber-night', 'forest', 'lavender', 'sakura']) {
      const response = await context.request.patch(base + '/api/v1/settings', { data: { appearance: { theme_id: theme } } }); assert.ok(response.ok());
      for (const width of [1440, 360, 390, 430]) {
        await page.setViewportSize({ width, height: width === 1440 ? 1000 : 840 });
        await page.goto(base + '/worlds/' + worldId); await panel().waitFor();
        await page.waitForFunction(id => document.documentElement.dataset.theme === id, theme);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, `${theme}/${width}: document overflow`);
        await shot(`world-${theme}-${width}`);
      }
    }
    await page.goto(base + '/worlds');
    await page.getByRole('button', { name: '新建世界', exact: true }).first().focus(); await page.keyboard.press('Enter');
    const dialog = page.getByRole('dialog', { name: '新建世界', exact: true }); await dialog.waitFor();
    await page.keyboard.press('Escape'); await dialog.waitFor({ state: 'hidden' });
    assert.equal(await page.evaluate(() => document.activeElement?.textContent.includes('新建世界')), true);
  });
  assert.deepEqual(errors, []);
} catch (cause) {
  results.push({ failure: String(cause), ok: false }); await shot('failure'); throw cause;
} finally {
  await writeFile(resolve(output, 'report.json'), JSON.stringify({ results, errors, synthetic: true, real_model_quality: 'not tested', real_phone: 'not tested' }, null, 2));
  await browser.close();
}

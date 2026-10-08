/** Built-UI boundary acceptance against a disposable, explicitly marked fixture. */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, writeFile, readdir, readFile } from 'node:fs/promises';
import { resolve, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { deflateSync } from 'node:zlib';

const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = fileURLToPath(new URL('../../', import.meta.url));
const base = process.env.UI_ACCEPTANCE_URL;
assert.ok(base && process.env.UI_ACCEPTANCE_OUT, 'Set the synthetic fixture URL and a private output directory');
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(base).hostname), 'Only a loopback fixture is allowed');
const output = resolve(process.env.UI_ACCEPTANCE_OUT);
assert.ok(relative(root, output).startsWith('..'), 'Evidence must stay outside source');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true, ...(process.env.UI_BROWSER_CHANNEL ? { channel: process.env.UI_BROWSER_CHANNEL } : {}) });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, extraHTTPHeaders: { Origin: base } });
const page = await context.newPage();
page.setDefaultTimeout(15000);
const pageErrors = [], checks = [], sourceHits = [], interceptedRequests = [], actualJobs = [], uiAuthoringRequests = [], realRuntimePatches = [], screenshots = [];
let captureRealPatches = false;
page.on('pageerror', error => pageErrors.push(error.message));
page.on('request', request => {
  if (request.method() === 'POST' && /\/(derive-world-runtime|generate|analyze|regenerate|resume-world-runtime)$/.test(request.url())) uiAuthoringRequests.push({ url: request.url(), method: request.method() });
  if (captureRealPatches && request.method() === 'PATCH' && request.url().endsWith('/runtime-draft')) realRuntimePatches.push(request.postDataJSON());
});
const json = async (path, method = 'GET', data) => {
  const response = await context.request.fetch(base + path, { method, ...(data === undefined ? {} : { data }) });
  assert.ok(response.ok(), `${method} ${path}: ${response.status()} ${await response.text()}`);
  return response.json();
};
const check = async (name, evidence, run) => {
  try { await run(); checks.push({ name, evidence, ok: true }); console.log('PASS ' + name); }
  catch (error) { checks.push({ name, evidence, ok: false, error: error.message }); throw error; }
};
const until = async (read, test, message, timeout = 20000) => {
  const limit = Date.now() + timeout;
  while (Date.now() < limit) { const value = await read(); if (test(value)) return value; await page.waitForTimeout(150); }
  assert.fail(message);
};
const openDetails = async text => {
  const summary = page.locator('summary').filter({ hasText: text }).first();
  await summary.waitFor();
  if (!(await summary.locator('..').evaluate(element => element.open))) await summary.click();
};
const panel = () => page.getByRole('region', { name: '世界原稿', exact: true });
const review = () => page.getByRole('region', { name: '集中核对设定', exact: true });
const shot = async name => { await page.screenshot({ path: resolve(output, name + '.png'), fullPage: true }); screenshots.push(name + '.png'); };
const checkSources = async (name, expected, excluded = []) => {
  await openDetails('本次使用的资料');
  const texts = await review().locator('summary').filter({ hasText: /^本次使用的资料（/ }).locator('..').locator('.world-source-quote pre').allTextContents();
  assert.equal(texts.length, expected.length, 'Frozen source count');
  for (const marker of expected) assert.equal(texts.filter(text => text.includes(marker)).length, 1, `Source hit count: ${marker}`);
  for (const marker of excluded) assert.equal(texts.filter(text => text.includes(marker)).length, 0, `Excluded source leaked: ${marker}`);
  sourceHits.push({ name, count: texts.length, expected: expected.map(marker => ({ marker, hits: 1 })), excluded: excluded.map(marker => ({ marker, hits: 0 })) });
};
const createWorld = (title, body) => json('/api/v1/worlds', 'POST', { title, manuscript_body: body, manuscript_visibility: 'public' });
const syntheticPng = () => {
  const chunk = (name, data) => {
    const bytes = Buffer.concat([Buffer.from(name), data]); let crc = 0xffffffff;
    for (const byte of bytes) { crc ^= byte; for (let i = 0; i < 8; i++) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0); }
    const size = Buffer.alloc(4), checksum = Buffer.alloc(4); size.writeUInt32BE(data.length); checksum.writeUInt32BE((crc ^ 0xffffffff) >>> 0);
    return Buffer.concat([size, bytes, checksum]);
  };
  const header = Buffer.alloc(13); header.writeUInt32BE(1, 0); header.writeUInt32BE(1, 4); header[8] = 8; header[9] = 2;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header), chunk('IDAT', deflateSync(Buffer.from([0, 20, 40, 80]))), chunk('IEND', Buffer.alloc(0))]);
};
const createJob = async (world, source) => {
  const job = await json('/api/v1/asset-import-jobs', 'POST', { source, world_id: world.id, target_kind: 'world', intent: 'organize', source_visibility: 'public' });
  actualJobs.push(job.id);
  await json(`/api/v1/asset-import-jobs/${job.id}/derive-world-runtime`, 'POST', { expected_bundle_revision: job.bundle_revision ?? 1, source_ids: ['manuscript'] });
  const ready = await until(() => json(`/api/v1/asset-import-jobs/${job.id}`), row => ['review', 'failed'].includes(row.bundle?.status), 'Synthetic organizing did not finish', 45000);
  assert.equal(ready.bundle.status, 'review', JSON.stringify(ready.bundle.errors));
  return ready;
};

let identity, after, failure;
try {
  identity = await json('/api/v1/acceptance-fixture');
  assert.equal(identity.fixture, 'mrp.development.synthetic', 'Refusing to mutate an actual service');
  const stamp = Date.now().toString(36);
  await check('新建角色默认可见头像入口，不必展开形象字段', 'built UI / no authoring requests', async () => {
    await page.goto(base + '/library/characters');
    await page.getByRole('button', { name: /新建角色/ }).click();
    const editor = page.locator('.v7-character-editor[role="dialog"]');
    await editor.waitFor();
    assert.equal(await editor.getByText('选择头像', { exact: true }).isVisible(), true);
    assert.equal(await page.getByTestId('card-description').isVisible(), true);
    assert.equal(await page.getByTestId('card-appearance').isVisible(), false);
    await shot('character-default-avatar');
  });
  await check('工坊头像默认可见，上传失败重试只创建一个角色且保存当前名字', 'actual HTTP / synthetic PNG / first upload route-injected 503', async () => {
    await page.goto(base + '/workshop/character');
    const editor = page.locator('.v7-character-editor[role="dialog"]'); await editor.waitFor();
    assert.equal(await editor.getByText('选择头像', { exact: true }).isVisible(), true);
    assert.equal(await page.getByTestId('card-appearance').isVisible(), false);
    const originalName = '合成头像重试-' + stamp, revisedName = originalName + '-当前名字';
    await page.getByTestId('card-name').fill(originalName); await page.getByTestId('card-description').fill('仅供合成头像保存边界验收。');
    await editor.getByLabel('选择角色头像文件', { exact: true }).setInputFiles({ name: 'synthetic.png', mimeType: 'image/png', buffer: syntheticPng() });
    let uploads = 0;
    const imageRoute = '**/api/v1/characters/*/avatar';
    const failFirst = async route => { if (route.request().method() === 'POST' && ++uploads === 1) await route.fulfill({ status: 503, json: { detail: '合成头像上传中断' } }); else await route.continue(); };
    await page.route(imageRoute, failFirst);
    const countBefore = (await json('/api/v1/characters')).length;
    await editor.getByRole('button', { name: '创建角色', exact: true }).click();
    await editor.getByText('保存失败：', { exact: false }).waitFor();
    const created = (await json('/api/v1/characters')).filter(row => row.card.name === originalName); assert.equal(created.length, 1);
    await page.getByTestId('card-name').fill(revisedName);
    await editor.getByRole('button', { name: '创建角色', exact: true }).click(); await editor.waitFor({ state: 'hidden' });
    const all = await json('/api/v1/characters'); assert.equal(all.length, countBefore + 1);
    const saved = await json('/api/v1/characters/' + created[0].id); assert.equal(saved.card.name, revisedName); assert.ok(saved.card.avatar_path);
    const avatar = await context.request.get(base + '/api/v1/characters/' + saved.id + '/avatar'); assert.equal(avatar.status(), 200); assert.ok(avatar.headers()['content-type'].startsWith('image/png'));
    assert.equal(uploads, 2); await page.unroute(imageRoute, failFirst); await shot('workshop-avatar-retry');
  });
  await check('服务器只改可见范围，刷新仍保留正文草稿并禁止保存与整理', 'actual HTTP / localStorage / built UI', async () => {
    const body = '合成可见性原稿-' + stamp + '\n灯塔居民在潮落时过桥。';
    const world = await createWorld('合成可见性边界-' + stamp, body);
    const before = uiAuthoringRequests.length;
    await page.goto(base + '/worlds/' + world.id);
    await panel().getByLabel('世界设定原稿', { exact: true }).fill(body + '\n本地手写，尚未保存。');
    await page.waitForFunction(({ key, text }) => JSON.parse(localStorage.getItem(key) || 'null')?.body === text,
      { key: 'mrp.world.manuscript.' + world.id, text: body + '\n本地手写，尚未保存。' });
    await json(`/api/v1/worlds/${world.id}/manuscript`, 'POST', { expected_revision: world.revision, body, visibility: 'private' });
    await page.reload();
    await panel().getByText('已保存的原稿或可见范围在别处发生变化。', { exact: false }).waitFor();
    assert.equal(await panel().getByLabel('世界设定原稿', { exact: true }).inputValue(), body + '\n本地手写，尚未保存。');
    assert.equal(await panel().getByRole('button', { name: '保存原稿', exact: true }).isDisabled(), true);
    assert.equal(await panel().getByRole('button', { name: '帮我整理核心与世界书', exact: true }).isDisabled(), true);
    const saved = await json('/api/v1/worlds/' + world.id);
    const record = saved.archive_records.find(row => row.id === saved.manuscript_archive_id);
    assert.equal(record.body, body); assert.equal(record.visibility, 'private');
    assert.equal(uiAuthoringRequests.length, before, 'Direct manuscript saving must not request model generation');
    await shot('visibility-conflict');
  });

  const recoveryBody = '合成恢复原稿-' + stamp + '\n潮晶只在满月高潮蓄能，黎明后能量散尽。';
  const world = await createWorld('合成任务恢复-' + stamp, recoveryBody);
  let extensionId;
  await check('补写候选手改后刷新与历史恢复保持文本，空候选仍为已就绪', 'actual HTTP / synthetic authoring worker / localStorage / built UI', async () => {
    await page.goto(base + '/worlds/' + world.id);
    await openDetails('构思辅助：补写指定部分');
    await panel().getByLabel('想补哪一部分？', { exact: false }).fill('只补合成冬季灯笼节');
    await panel().getByRole('button', { name: '生成补写候选', exact: true }).click();
    const candidate = panel().getByLabel('新增设定候选', { exact: true });
    await candidate.waitFor();
    assert.ok((await candidate.inputValue()).includes('新增合成设定'));
    const extension = (await json('/api/v1/asset-import-jobs')).find(row => row.world_id === world.id && row.intent === 'extend');
    extensionId = extension.id; actualJobs.push(extension.id);
    const edited = '手工修改的合成补写：灯笼只点一盏，不能覆盖。';
    await candidate.fill(edited);
    await page.waitForFunction(({ key, text }) => JSON.parse(localStorage.getItem(key) || 'null')?.text === text,
      { key: 'mrp.world.extension.' + world.id, text: edited });
    await page.reload(); await openDetails('构思辅助：补写指定部分');
    assert.equal(await candidate.inputValue(), edited);
    await openDetails('恢复此世界的整理与补写任务');
    await panel().getByRole('button', { name: /^补写 ·/ }).click();
    await panel().getByText('已恢复补写任务。候选只适用于任务开始时的原稿。', { exact: true }).waitFor();
    await page.waitForTimeout(2100);
    assert.equal(await candidate.inputValue(), edited, 'History restore/poll must preserve ready local edits');
    await candidate.fill('');
    await page.waitForFunction(key => { const value = JSON.parse(localStorage.getItem(key) || 'null'); return value?.text === '' && value.ready; }, 'mrp.world.extension.' + world.id);
    await page.reload(); await openDetails('构思辅助：补写指定部分');
    await candidate.waitFor(); assert.equal(await candidate.inputValue(), '');
    await openDetails('恢复此世界的整理与补写任务');
    await panel().getByRole('button', { name: /^补写 ·/ }).click();
    await panel().getByText('已恢复补写任务。候选只适用于任务开始时的原稿。', { exact: true }).waitFor();
    await page.waitForTimeout(1400);
    assert.equal(await candidate.inputValue(), '', 'Empty edited candidate must not be mistaken for pending generation');
    assert.equal(await panel().getByRole('button', { name: '采用到原稿草稿', exact: true }).isDisabled(), true);
    assert.equal((await json('/api/v1/worlds/' + world.id)).archive_records[0].body, recoveryBody, 'Generating/editing never saves manuscript');
    await shot('extension-restored');
  });

  let jobA, jobB;
  await check('同世界多个任务恢复独立候选，资料命中仅为明确选定原稿', 'actual HTTP / synthetic authoring worker / built UI', async () => {
    const other = await createWorld('另一个合成世界-' + stamp, '另一世界私密标记-' + stamp);
    await json(`/api/v1/worlds/${other.id}/manuscript`, 'POST', { expected_revision: other.revision, body: '另一世界私密标记-' + stamp, visibility: 'private' });
    await json(`/api/v1/worlds/${world.id}/archive`, 'POST', { expected_revision: world.revision, record: { kind: 'background', title: '未选合成作者资料', body: '同世界未选私密标记-' + stamp, visibility: 'private' } });
    jobA = await createJob(world, '合成任务甲-' + stamp + '\n潮晶只在满月高潮蓄能，黎明能量散尽。');
    jobB = await createJob(world, '合成任务乙-' + stamp + '\n沿海旅人先观察潮晶，再讨论桥梁。');
    await page.goto(base + '/worlds/' + world.id);
    await openDetails('恢复此世界的整理与补写任务（3）');
    await panel().getByRole('button', { name: new RegExp('整理 · 合成任务甲-' + stamp) }).click();
    await review().getByLabel('待审核心', { exact: true }).waitFor();
    await checkSources('restore A', ['合成任务甲-' + stamp], ['合成任务乙-' + stamp, '另一世界私密标记-' + stamp, '同世界未选私密标记-' + stamp]);
    await review().getByLabel('待审核心', { exact: true }).fill('甲任务的本地候选手改，不能带到乙。');
    await panel().getByRole('button', { name: new RegExp('整理 · 合成任务乙-' + stamp) }).click();
    await until(() => review().getByLabel('待审核心', { exact: true }).inputValue(), text => !text.includes('甲任务的本地候选手改'), 'Switching jobs kept stale local edits');
    await checkSources('restore B', ['合成任务乙-' + stamp], ['合成任务甲-' + stamp, '另一世界私密标记-' + stamp, '同世界未选私密标记-' + stamp]);
    await page.reload(); await review().getByLabel('待审核心', { exact: true }).waitFor();
    await checkSources('reload B', ['合成任务乙-' + stamp], ['合成任务甲-' + stamp]);
    assert.equal(await page.evaluate(id => localStorage.getItem('mrp.world.job.' + id), world.id), jobB.id);
    const ids = (await json('/api/v1/asset-import-jobs')).filter(row => row.world_id === world.id).map(row => row.id);
    for (const id of [extensionId, jobA.id, jobB.id]) assert.ok(ids.includes(id), 'Older server task disappeared');
    await shot('multiple-jobs');
  });

  const entryName = '合成潮晶规则';
  const entryArticle = () => review().locator('.world-review-proposal').filter({ has: page.getByLabel('待审条目：' + entryName, { exact: true }) });
  const readEntry = async () => (await json('/api/v1/asset-import-jobs/' + jobA.id)).bundle.entry_proposals.find(row => row.payload.comment === entryName);
  const saveEntry = async () => {
    await entryArticle().getByRole('button', { name: '保存此项修改', exact: true }).click();
    await until(() => entryArticle().getByRole('button', { name: '保存此项修改', exact: true }).count(), count => count === 0, 'Entry edits were not saved');
    return readEntry();
  };
  await check('真实高级触发编辑单次保存正文与试例，保留隐藏字段，四条件经生产引擎检查', 'actual HTTP PATCH / production trigger engine / built UI', async () => {
    const initial = await readEntry();
    jobA = await json(`/api/v1/asset-import-jobs/${jobA.id}/runtime-draft`, 'PATCH', { expected_bundle_revision: jobA.bundle.revision, proposal_id: initial.id,
      payload: { ...initial.payload, extensions: { ...initial.payload.extensions, 'boundary.keep': { synthetic: true } }, plugin_boundary: 'unshown synthetic field', probability: 100 } });
    await page.goto(base + '/library/imports/' + jobA.id);
    await entryArticle().waitFor();
    await entryArticle().locator('summary').filter({ hasText: '触发与顺序设置' }).click();
    captureRealPatches = true;
    const entry = entryArticle();
    await entry.getByLabel('触发关键词', { exact: true }).fill('灯笼\n港灯');
    await entry.getByLabel('二级关键词', { exact: true }).fill('冬夜\n节庆');
    await entry.getByRole('checkbox', { name: '使用二级关键词条件', exact: true }).check();
    await entry.getByLabel('二级关键词条件', { exact: true }).selectOption('3');
    await entry.getByLabel('条目顺序', { exact: true }).fill('37');
    await entry.getByLabel('条目放置位置', { exact: true }).selectOption('at_depth');
    await entry.getByLabel('条目对话深度', { exact: true }).fill('2');
    const revisedBody = '合成手修条目：港口灯笼只在冬夜节庆点亮，用于夜间引路。';
    await entry.getByLabel('待审条目：' + entryName, { exact: true }).fill(revisedBody);
    await entry.getByLabel('应该触发的试例', { exact: true }).fill('冬夜节庆，我们点亮灯笼。');
    await entry.getByLabel('不该触发的试例', { exact: true }).fill('冬夜，我们收好灯笼。');
    assert.equal(await review().getByRole('button', { name: /^一次采用/ }).isDisabled(), true);
    let saved = await saveEntry();
    assert.equal(realRuntimePatches.length, 1, 'Body, settings and examples need one PATCH');
    assert.equal(realRuntimePatches[0].payload.content, revisedBody);
    assert.deepEqual(realRuntimePatches[0].positive_examples, ['冬夜节庆，我们点亮灯笼。']);
    assert.equal(saved.payload.content, revisedBody); assert.deepEqual(saved.payload.keys, ['灯笼', '港灯']);
    assert.deepEqual(saved.payload.secondary_keys, ['冬夜', '节庆']);
    assert.equal(saved.payload.order, 37); assert.equal(saved.payload.anchor, 'at_depth'); assert.equal(saved.payload.depth, 2);
    assert.deepEqual(saved.payload.extensions['boundary.keep'], { synthetic: true }); assert.equal(saved.payload.plugin_boundary, 'unshown synthetic field');
    assert.equal(saved.payload.probability, 100); assert.deepEqual(saved.source_refs, initial.source_refs);
    assert.equal(saved.simulation.valid, true);
    const examples = [
      { logic: '0', positive: '冬夜点亮港灯。', negative: '点亮港灯。' },
      { logic: '1', positive: '冬夜点亮港灯。', negative: '冬夜节庆点亮港灯。' },
      { logic: '2', positive: '点亮港灯。', negative: '冬夜点亮港灯。' },
      { logic: '3', positive: '冬夜节庆点亮港灯。', negative: '冬夜点亮港灯。' },
    ];
    for (const example of examples) {
      await entry.getByLabel('二级关键词条件', { exact: true }).selectOption(example.logic);
      await entry.getByLabel('应该触发的试例', { exact: true }).fill(example.positive);
      await entry.getByLabel('不该触发的试例', { exact: true }).fill(example.negative);
      saved = await saveEntry();
      assert.equal(saved.payload.selective_logic, Number(example.logic)); assert.equal(saved.simulation.valid, true);
      assert.equal(saved.simulation.positive[0].triggered, true); assert.equal(saved.simulation.negative[0].triggered, false);
    }
    assert.equal(await entry.getByRole('checkbox', { name: '每轮常驻，不等待关键词', exact: true }).count(), 0, 'Always-on facts belong in core');
    await entry.getByRole('checkbox', { name: '启用条目', exact: true }).uncheck();
    saved = await saveEntry(); assert.equal(saved.payload.enabled, false); assert.equal(saved.simulation.valid, true);
    await entry.getByText('停用检查通过：条目不会由试例触发', { exact: true }).waitFor();
    assert.ok(saved.simulation.positive.every(row => !row.triggered));
    await entry.getByRole('checkbox', { name: '启用条目', exact: true }).check();
    await entry.getByLabel('不该触发的试例', { exact: true }).fill('冬夜节庆点亮港灯。');
    saved = await saveEntry(); assert.equal(saved.simulation.valid, false);
    await entry.getByText('触发试例检查未通过，请核对关键词和试例', { exact: true }).waitFor();
    await entry.getByLabel('不该触发的试例', { exact: true }).fill('冬夜点亮港灯。');
    saved = await saveEntry(); assert.equal(saved.simulation.valid, true);
    await entry.getByText('触发试例检查通过', { exact: true }).waitFor();
    assert.equal(await review().getByRole('button', { name: /^一次采用/ }).isDisabled(), false);
    assert.deepEqual(saved.payload.extensions['boundary.keep'], { synthetic: true }); assert.equal(saved.payload.plugin_boundary, 'unshown synthetic field');
    await shot('advanced-trigger-check');
    captureRealPatches = false;
  });

  await check('后台仅改高级字段或试例，轮询保留手改但禁止覆盖；撤销恢复新版本', 'actual HTTP concurrent edits / built UI polling / baseline checks', async () => {
    const article = entryArticle();
    await article.getByLabel('触发关键词', { exact: true }).fill('本地手改灯笼');
    let live = await json('/api/v1/asset-import-jobs/' + jobA.id);
    let proposal = live.bundle.entry_proposals.find(row => row.payload.comment === entryName);
    await json(`/api/v1/asset-import-jobs/${jobA.id}/runtime-draft`, 'PATCH', { expected_bundle_revision: live.bundle.revision, proposal_id: proposal.id, payload: { ...proposal.payload, order: 99 } });
    await review().getByRole('button', { name: '刷新状态', exact: true }).click();
    await article.getByText('此项候选或来源已在别处更新；你的编辑仍保留。', { exact: false }).waitFor();
    assert.equal(await article.getByLabel('触发关键词', { exact: true }).inputValue(), '本地手改灯笼');
    assert.equal(await article.getByRole('button', { name: '保存此项修改', exact: true }).isDisabled(), true);
    assert.equal(await review().getByRole('button', { name: /^一次采用/ }).isDisabled(), true);
    await article.getByRole('button', { name: '撤销本次编辑', exact: true }).click();
    assert.equal(await article.getByLabel('条目顺序', { exact: true }).inputValue(), '99');
    assert.equal(await article.getByLabel('触发关键词', { exact: true }).inputValue(), '灯笼\n港灯');
    await article.getByLabel('应该触发的试例', { exact: true }).fill('本地冬夜节庆点亮灯笼，保留这句。');
    live = await json('/api/v1/asset-import-jobs/' + jobA.id);
    proposal = live.bundle.entry_proposals.find(row => row.payload.comment === entryName);
    await json(`/api/v1/asset-import-jobs/${jobA.id}/runtime-draft`, 'PATCH', { expected_bundle_revision: live.bundle.revision, proposal_id: proposal.id, negative_examples: ['另一处的新反例：我们回家。'] });
    await page.waitForTimeout(2200);
    await article.getByText('此项候选或来源已在别处更新；你的编辑仍保留。', { exact: false }).waitFor();
    assert.equal(await article.getByLabel('应该触发的试例', { exact: true }).inputValue(), '本地冬夜节庆点亮灯笼，保留这句。');
    assert.equal(await article.getByRole('button', { name: '保存此项修改', exact: true }).isDisabled(), true);
    await shot('advanced-stale-baseline');
    await article.getByRole('button', { name: '撤销本次编辑', exact: true }).click();
    assert.equal(await article.getByLabel('不该触发的试例', { exact: true }).inputValue(), '另一处的新反例：我们回家。');
  });

  await check('高级触发控件九主题、三窄屏和桌面布局均无横向越界', 'built UI / synthetic settings / screenshots', async () => {
    const themeDir = resolve(root, 'src/web/src/appearance/packs');
    const files = (await readdir(themeDir)).filter(file => file.endsWith('.json')); assert.equal(files.length, 9);
    for (const file of files) {
      const theme = JSON.parse(await readFile(resolve(themeDir, file), 'utf8'));
      await json('/api/v1/settings', 'PATCH', { appearance: { theme_id: theme.id } });
      for (const width of [360, 390, 430, 1440]) {
        await page.setViewportSize({ width, height: width === 1440 ? 1000 : 900 });
        await page.goto(base + '/library/imports/' + jobA.id); await entryArticle().waitFor();
        await page.waitForFunction(id => document.documentElement.dataset.theme === id, theme.id);
        await entryArticle().locator('summary').filter({ hasText: '触发与顺序设置' }).click();
        await shot(`advanced-${theme.id}-${width}`);
        const outside = await review().evaluate(element => {
          const box = element.getBoundingClientRect();
          return [...element.querySelectorAll('button,input,textarea,select,summary')].filter(node => node.getClientRects().length).filter(node => { const rect = node.getBoundingClientRect(); return rect.left < box.left - 2 || rect.right > box.right + 2; }).map(node => node.getAttribute('aria-label') || node.textContent || node.tagName);
        });
        assert.deepEqual(outside, [], `Advanced controls overflow: ${theme.id} / ${width}`);
      }
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
  });

  // The next checks inject only marked synthetic responses into the built UI.
  // They exercise request ordering and authorization, not real model quality.
  const beforeMock = uiAuthoringRequests.length;
  let virtual = structuredClone(jobA);
  virtual.status = 'needs_review';
  virtual.bundle = {
    revision: 101, status: 'failed', review_digest: 'synthetic-public', world_draft: {},
    core_proposal: { id: 'core', content: '公开合成核心-' + stamp, runtime_scope: 'shared', source_refs: [{ source_id: 'manuscript', quote: '公开合成依据' }] },
    entry_proposals: [], source_snapshot: [{ id: 'manuscript', title: '公开合成原稿', visibility: 'public', audience: 'story', content: '公开合成依据-' + stamp }], errors: [],
  };
  let delayedGet = null, armDelay = false;
  const mockURL = `${base}/api/v1/asset-import-jobs/${jobA.id}`;
  const routePattern = new RegExp('^' + mockURL.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '(?:/.*)?$');
  await page.route(routePattern, async route => {
    const request = route.request(), path = new URL(request.url()).pathname.slice(new URL(mockURL).pathname.length), method = request.method();
    if (!path && method === 'GET') {
      const snapshot = structuredClone(virtual);
      if (armDelay) { armDelay = false; delayedGet = { route, snapshot }; return; }
      await route.fulfill({ json: snapshot }); return;
    }
    if (path === '/derive-world-runtime' && method === 'POST') {
      interceptedRequests.push({ method, path, input: request.postDataJSON(), syntheticResponse: true });
      virtual.bundle = { ...virtual.bundle, revision: 102, status: 'review', review_digest: 'synthetic-private',
        core_proposal: { id: 'core', content: '私密合成核心-' + stamp, runtime_scope: 'author', source_refs: [{ source_id: 'manuscript', quote: '私密合成依据' }] },
        source_snapshot: [{ id: 'manuscript', title: '私密合成原稿', visibility: 'private', audience: 'author', content: '私密合成依据-' + stamp }] };
      await route.fulfill({ json: virtual }); return;
    }
    if (path === '/runtime-draft' && method === 'PATCH') {
      const input = request.postDataJSON();
      interceptedRequests.push({ method, path, input, syntheticResponse: true });
      virtual.bundle.revision += 1;
      if (input.proposal_id) {
        const proposal = virtual.bundle.entry_proposals.find(row => row.id === input.proposal_id);
        proposal.action = input.action; proposal.target_uid = input.target_uid; delete proposal.conflict_reason;
      } else if (input.core_content !== undefined) virtual.bundle.core_proposal.content = input.core_content;
      await route.fulfill({ json: virtual }); return;
    }
    await route.continue();
  });

  await check('同一任务重整由公开转作者资料，旧供故事使用授权不继承', 'browser route-injected synthetic job revisions / built UI', async () => {
    await page.goto(base + '/library/imports/' + jobA.id);
    await review().getByLabel('待审核心', { exact: true }).waitFor();
    const core = () => review().locator('.world-review-proposal').filter({ has: page.getByLabel('待审核心', { exact: true }) });
    assert.equal(await core().getByRole('checkbox', { name: '供故事使用', exact: true }).isChecked(), true);
    await review().getByRole('button', { name: '重试整理', exact: true }).click();
    await until(() => review().getByLabel('待审核心', { exact: true }).inputValue(), text => text.startsWith('私密合成核心-'), 'New private bundle did not display');
    assert.equal(await core().getByRole('checkbox', { name: '供故事使用', exact: true }).isChecked(), false);
    await checkSources('private rederive', ['私密合成依据-' + stamp], ['公开合成依据-' + stamp]);
    assert.equal(await review().locator('.world-source-quote strong').filter({ hasText: '作者资料' }).count(), 1);
    await review().getByRole('button', { name: '刷新状态', exact: true }).click();
    await page.waitForTimeout(100);
    assert.equal(await core().getByRole('checkbox', { name: '供故事使用', exact: true }).isChecked(), false);
    await shot('private-rederive');
  });

  await check('歧义条目必须先选具体目标，PATCH 只携带选定 target_uid', 'browser route-injected ambiguous proposal / real browser PATCH capture', async () => {
    virtual.bundle.revision += 1;
    virtual.bundle.entry_proposals = [{ id: 'entry-boundary', runtime_scope: 'author', action: 'keep', proposed_action: 'replace', conflict_reason: '合成歧义：两个现有条目均匹配',
      payload: { comment: '合成歧义潮晶条目', keys: ['潮晶'], content: '合成新增潮晶规则' }, source_refs: [{ source_id: 'manuscript', quote: '私密合成依据' }],
      candidate_targets: [{ uid: 7, content: '合成原条目甲', keys: ['潮晶'] }, { uid: 8, content: '合成原条目乙', keys: ['潮晶'] }, { uid: 9, content: '已被其他候选选用', reserved: true }] }];
    await review().getByRole('button', { name: '刷新状态', exact: true }).click();
    const target = review().getByLabel('更新目标：合成歧义潮晶条目', { exact: true }); await target.waitFor();
    const confirm = review().getByRole('button', { name: '确认更新选定条目', exact: true });
    assert.equal(await confirm.isDisabled(), true);
    assert.equal(await target.locator('option[value="9"]').evaluate(option => option.disabled), true);
    const ambiguous = review().locator('.world-review-proposal').filter({ has: page.getByLabel('待审条目：合成歧义潮晶条目', { exact: true }) });
    await ambiguous.locator('summary').filter({ hasText: '比较可更新的条目原文' }).click();
    assert.equal(await ambiguous.locator('.world-source-quote pre').filter({ hasText: '合成原条目甲' }).isVisible(), true);
    assert.equal(await ambiguous.locator('.world-source-quote pre').filter({ hasText: '合成原条目乙' }).isVisible(), true);
    await ambiguous.locator('summary').filter({ hasText: '触发与顺序设置' }).click();
    await ambiguous.getByLabel('条目顺序', { exact: true }).fill('101');
    await target.selectOption('8');
    assert.equal(await confirm.isDisabled(), true, 'Unsaved advanced fields must block conflict resolution');
    await ambiguous.getByRole('button', { name: '撤销本次编辑', exact: true }).click();
    await target.selectOption('8');
    await confirm.click();
    await until(() => review().locator('.world-review-proposal').filter({ has: page.getByLabel('待审条目：合成歧义潮晶条目', { exact: true }) }).innerText(), text => text.includes('原条目 8'), 'Selected target was not reflected');
    const patch = interceptedRequests.filter(row => row.path === '/runtime-draft').at(-1).input;
    assert.equal(patch.target_uid, 8); assert.equal(patch.proposal_id, 'entry-boundary'); assert.equal(patch.resolve_conflict, true); assert.equal(patch.action, 'replace');
    assert.equal(virtual.bundle.entry_proposals[0].target_uid, 8);
    await checkSources('explicit ambiguous target', ['私密合成依据-' + stamp], ['公开合成依据-' + stamp]);
    await shot('explicit-target');
  });

  await check('旧轮询 GET 迟到不能覆盖已保存候选', 'browser route-injected delayed GET and PATCH / built UI', async () => {
    armDelay = true;
    await until(() => Promise.resolve(delayedGet), value => value !== null, 'No polling request entered delay', 6000);
    await review().getByLabel('待审核心', { exact: true }).fill('PATCH 后的合成核心，不得倒退。');
    const core = review().locator('.world-review-proposal').filter({ has: page.getByLabel('待审核心', { exact: true }) });
    await core.getByRole('button', { name: '保存此项修改', exact: true }).click();
    await until(() => core.getByRole('button', { name: '保存此项修改', exact: true }).count(), count => count === 0, 'Saving candidate did not complete');
    await delayedGet.route.fulfill({ json: delayedGet.snapshot }); delayedGet = null;
    await page.waitForTimeout(350);
    assert.equal(await review().getByLabel('待审核心', { exact: true }).inputValue(), 'PATCH 后的合成核心，不得倒退。');
    assert.equal(await core.getByRole('checkbox', { name: '供故事使用', exact: true }).isChecked(), false);
    await shot('late-poll-protected');
  });
  await check('后台重生成移除候选时，正文、高级字段与试例可复制恢复', 'browser route-injected proposal replacement / built UI', async () => {
    const article = review().locator('.world-review-proposal').filter({ has: page.getByLabel('待审条目：合成歧义潮晶条目', { exact: true }) });
    await article.getByLabel('待审条目：合成歧义潮晶条目', { exact: true }).fill('旧候选手写正文，重新生成也保留。');
    await article.getByLabel('触发关键词', { exact: true }).fill('旧候选手写关键词');
    await article.getByLabel('应该触发的试例', { exact: true }).fill('旧候选手写试例');
    virtual.bundle.entry_proposals = []; virtual.bundle.revision += 1;
    await review().getByRole('button', { name: '刷新状态', exact: true }).click();
    const preserved = review().getByLabel('旧候选修改：entry-boundary', { exact: true }); await preserved.waitFor();
    const value = await preserved.inputValue();
    for (const text of ['旧候选手写正文', '旧候选手写关键词', '旧候选手写试例']) assert.ok(value.includes(text));
    assert.equal(await review().getByRole('button', { name: /^一次采用/ }).isDisabled(), true);
    await shot('regenerated-candidate-recovery');
    await review().getByRole('button', { name: '放弃这项旧编辑', exact: true }).click();
    assert.equal(await preserved.count(), 0);
    assert.equal(await review().getByRole('button', { name: /^一次采用/ }).isDisabled(), false);
  });
  const mockAuthoring = uiAuthoringRequests.slice(beforeMock);
  assert.equal(mockAuthoring.length, 1, 'Only the deliberately intercepted rederive may request authoring');
  assert.equal(interceptedRequests.filter(row => row.path === '/derive-world-runtime').length, 1, 'The rederive was fulfilled with synthetic data');
  assert.deepEqual(pageErrors, [], 'Browser page errors');
} catch (error) {
  failure = error;
  await shot('failure').catch(() => {});
} finally {
  after = await json('/api/v1/acceptance-fixture').catch(() => null);
  await writeFile(resolve(output, 'report.json'), JSON.stringify({ fixture: identity, finalFixture: after, checks, sourceHits, interceptedRequests, realRuntimePatches, actualJobs, uiAuthoringRequests, screenshots, pageErrors,
    paidModelCalls: 0, limits: 'Synthetic authoring and injected responses only; no actual user validation or real model quality claim.', failure: failure?.message }, null, 2));
  await context.close(); await browser.close();
}
if (failure) throw failure;
console.log(`Completed ${checks.length} world boundary checks. Evidence: ${output}`);

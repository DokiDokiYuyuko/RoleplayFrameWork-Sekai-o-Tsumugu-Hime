/** One real HTTP/browser share check using a disposable synthetic fixture. */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { inflateRawSync } from 'node:zlib';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = fileURLToPath(new URL('../../', import.meta.url));
const base = process.env.UI_ACCEPTANCE_URL;
assert.ok(base && process.env.UI_ACCEPTANCE_OUT, 'Set synthetic fixture URL and output');
const output = resolve(process.env.UI_ACCEPTANCE_OUT);
assert.ok(relative(root, output).startsWith('..'), 'Keep evidence outside source');
await mkdir(output, { recursive: true });

function unzip(bytes) {
  let end = bytes.length - 22;
  while (end >= 0 && bytes.readUInt32LE(end) !== 0x06054b50) end--;
  assert.ok(end >= 0, 'ZIP directory exists');
  const result = new Map();
  let offset = bytes.readUInt32LE(end + 16);
  for (let n = 0; n < bytes.readUInt16LE(end + 10); n++) {
    assert.equal(bytes.readUInt32LE(offset), 0x02014b50);
    const method = bytes.readUInt16LE(offset + 10), size = bytes.readUInt32LE(offset + 20);
    const nameSize = bytes.readUInt16LE(offset + 28), extraSize = bytes.readUInt16LE(offset + 30), commentSize = bytes.readUInt16LE(offset + 32);
    const name = bytes.subarray(offset + 46, offset + 46 + nameSize).toString('utf8');
    const local = bytes.readUInt32LE(offset + 42);
    assert.equal(bytes.readUInt32LE(local), 0x04034b50);
    const start = local + 30 + bytes.readUInt16LE(local + 26) + bytes.readUInt16LE(local + 28);
    const data = bytes.subarray(start, start + size);
    assert.ok(method === 0 || method === 8, 'Supported ZIP method');
    result.set(name, (method === 8 ? inflateRawSync(data) : data).toString('utf8'));
    offset += 46 + nameSize + extraSize + commentSize;
  }
  return result;
}

const browser = await chromium.launch({ headless: true, channel: process.env.UI_BROWSER_CHANNEL || 'msedge' });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, extraHTTPHeaders: { Origin: base }, acceptDownloads: true });
const page = await context.newPage();
const errors = [], results = [];
page.on('pageerror', error => errors.push(error.message));
const get = async path => {
  const response = await context.request.get(base + path); assert.ok(response.ok(), await response.text()); return response.json();
};
try {
  const fixture = await get('/api/v1/chat-library-fixture');
  assert.equal(fixture.fixture, 'mrp.chat-library.synthetic');
  const [characterId] = fixture.character_ids, [bookId] = fixture.book_ids;
  const beforeCharacter = await get(`/api/v1/characters/${characterId}`), beforeBook = await get(`/api/v1/lorebooks/${bookId}`);
  await page.goto(base + '/library/characters');
  const card = page.getByTestId('character-card').filter({ has: page.getByRole('heading', { name: '合成向导', exact: true }) });
  await card.waitFor();
  await page.getByRole('button', { name: '选择素材', exact: true }).click();
  await card.getByRole('checkbox').check();
  await page.getByRole('button', { name: '导出所选', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '导出所选素材', exact: true });
  await dialog.getByText('将排除 2 条作者专用词条（含角色内嵌世界书）。', { exact: true }).waitFor();
  await dialog.getByText('将移除 2 条词条的原稿引用与整理记录（含角色内嵌世界书）；可分享正文、编号与规则保留。', { exact: true }).waitFor();
  assert.ok((await dialog.innerText()).includes('这是对外分享的副本'));
  await page.screenshot({ path: resolve(output, 'selected-share-preview.png') });
  results.push('Real preview displays author-entry and source-record exclusions including embedded books');
  const downloadPending = page.waitForEvent('download');
  await dialog.getByRole('button', { name: '下载迁移包', exact: true }).click();
  const download = await downloadPending, saved = resolve(output, 'synthetic-selected-share.zip');
  await download.saveAs(saved);
  const files = unzip(await readFile(saved));
  assert.ok(![...files.values()].join('\n').includes(fixture.private_marker), 'Known private author marker absent from all ZIP members');
  const sharedBook = JSON.parse(files.get(`lorebooks/${bookId}.json`));
  const entries = Object.values(sharedBook.entries);
  assert.equal(entries.length, 1); assert.equal(entries[0].uid, 1);
  assert.equal(entries[0].content, '合成灯塔设定。'); assert.deepEqual(entries[0]['vendor.custom_rule'], { retain: true });
  const sharedCard = JSON.parse(files.get(`characters/${characterId}.json`)).data;
  const embedded = sharedCard.extensions.character_book.entries;
  assert.equal(embedded.length, 1); assert.equal(embedded[0].id, 21);
  assert.deepEqual(embedded[0].extensions['vendor.custom_rule'], { retain: true });
  assert.ok(!('authoring_source' in JSON.parse(files.get(`characters/${characterId}.mrp.json`))));
  results.push('Downloaded ZIP excludes author-only bodies, source quotations and generation records; public content, IDs and unknown rules remain');
  const privateResponse = await context.request.get(base + '/api/v1/bundle/export');
  assert.ok(privateResponse.ok());
  const privateFiles = unzip(await privateResponse.body());
  assert.ok([...privateFiles.values()].join('\n').includes(fixture.private_marker));
  assert.equal(Object.values(JSON.parse(privateFiles.get(`lorebooks/${bookId}.json`)).entries).length, 2);
  assert.equal(JSON.parse(privateFiles.get(`characters/${characterId}.json`)).data.extensions.character_book.entries.length, 2);
  assert.ok(JSON.parse(privateFiles.get(`characters/${characterId}.mrp.json`)).authoring_source.text);
  assert.deepEqual(await get(`/api/v1/characters/${characterId}`), beforeCharacter);
  assert.deepEqual(await get(`/api/v1/lorebooks/${bookId}`), beforeBook);
  assert.deepEqual(errors, []);
  results.push('Private export retains author material and provenance; local character and lorebook remain identical');
  await writeFile(resolve(output, 'report.json'), JSON.stringify({ passed: true, evidence: 'Disposable real HTTP API and production UI with synthetic data', results, errors }, null, 2));
  console.log(JSON.stringify({ passed: true, checks: results.length, zip: 'synthetic-selected-share.zip' }));
} catch (cause) {
  await page.screenshot({ path: resolve(output, 'failure.png') });
  await writeFile(resolve(output, 'failure.txt'), String(cause) + '\n' + await page.locator('body').innerText());
  throw cause;
} finally {
  await browser.close();
}

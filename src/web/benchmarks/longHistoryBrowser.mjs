/** Desktop browser sample; use a synthetic development_preview with 10,000 messages. */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { performance as timer } from 'node:perf_hooks';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.UI_ACCEPTANCE_URL || 'http://127.0.0.1:8016';
const root = fileURLToPath(new URL('../../../', import.meta.url));
assert.ok(process.env.UI_ACCEPTANCE_OUT, 'Choose a report directory outside source');
const output = resolve(process.env.UI_ACCEPTANCE_OUT);
assert.ok(relative(root, output).startsWith('..'), 'Keep results outside source');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true, ...(process.env.UI_BROWSER_CHANNEL ? { channel: process.env.UI_BROWSER_CHANNEL } : {}) });
let page;
try {
  page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const read = async (path) => { const r = await page.request.get(base + path); assert.ok(r.ok()); return r.json(); };
  const fixture = await read('/api/v1/acceptance-fixture');
  assert.equal(fixture.fixture, 'mrp.development.synthetic', 'Refusing a real server');
  const state = await read(`/api/v1/sessions/${fixture.branch_id}`);
  assert.ok(state.messages.length >= 10000);
  const path = `/stories/${fixture.story_id}/branches/${fixture.branch_id}`;
  const load = [];
  for (let n = 0; n < 3; n++) {
    const start = timer.now(); await page.goto(base + path);
    await page.locator(`[data-message-id="${state.messages.at(-1).id}"]`).waitFor({ timeout: 60000 });
    load.push(Math.round(timer.now() - start)); assert.ok(await page.locator('[data-message-id]').count() <= 100);
  }
  let start = timer.now(); await page.getByRole('button', { name: '更早消息', exact: true }).click();
  await page.getByText(`第 ${state.messages.length - 199}–${state.messages.length - 100} 条 / 共 ${state.messages.length} 条`, { exact: true }).waitFor();
  const pageMs = Math.round(timer.now() - start);
  start = timer.now(); await page.goto(base + path + `?message=${state.messages[1].id}`);
  await page.locator(`#message-${state.messages[1].id}`).waitFor({ timeout: 60000 });
  const data = { kind: 'synthetic_desktop_browser', viewport: [1440, 1000], total_messages: state.messages.length,
    rendered_bubbles: await page.locator('[data-message-id]').count(), load_ms: load, page_ms: pageMs,
    early_message_locate_ms: Math.round(timer.now() - start),
    limits: 'One local run: includes route fetch/render. No phone, real model, scroll FPS or broad speed guarantee.' };
  await writeFile(resolve(output, 'long-history-browser.json'), JSON.stringify(data, null, 2));
  await page.screenshot({ path: resolve(output, 'long-history-desktop.png') });
  console.log(JSON.stringify(data));
} catch (error) {
  await page?.screenshot({ path: resolve(output, 'long-history-failure.png') });
  console.error({ url: page?.url(), status: await page?.getByRole('status').allTextContents() });
  throw error;
} finally { await browser.close(); }

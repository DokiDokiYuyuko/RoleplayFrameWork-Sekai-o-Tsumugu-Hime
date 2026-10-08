import assert from 'node:assert/strict';
import { readdir, readFile, mkdir, writeFile } from 'node:fs/promises';
import { resolve, relative, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gzipSync } from 'node:zlib';
import { staticBundleClosure } from '../build/bundleGraph.mjs';
const here = dirname(fileURLToPath(import.meta.url));
const dist = resolve(process.env.UI_ACCEPTANCE_DIST || resolve(here, '../dist'));
assert.ok(process.env.UI_ACCEPTANCE_OUT, 'Choose an evidence directory outside checkout');
const output = resolve(process.env.UI_ACCEPTANCE_OUT);
assert.ok(relative(resolve(here, '../../..'), output).startsWith('..'));
const entries = await readdir(resolve(dist, 'assets'));
const sources = new Map();
const files = await Promise.all(entries.filter(name => name.endsWith('.js')).map(async name => {
  const bytes = await readFile(resolve(dist, 'assets', name));
  sources.set(name, bytes.toString('utf8'));
  return { name, bytes: bytes.length, gzip_bytes: gzipSync(bytes).length };
}));
// Lazy routes may produce another index-* shared chunk. Read the actual HTML
// entry rather than accidentally granting that shared chunk the entry budget.
const html = await readFile(resolve(dist, 'index.html'), 'utf8');
const entry = html.match(/<script\b[^>]*src="([^"]+)"/);
assert.ok(entry, 'Production HTML must reference its JavaScript entry');
const entryName = new URL(entry[1], 'http://isolated.invalid').pathname.split('/').pop();
const main = files.find(file => file.name === entryName);
// Phase 3 all chunks were approximately 440 kB gzip; retain a fixed 10% headroom.
const budgets = { main_gzip_bytes: 185000, all_js_gzip_bytes: 485000, individual_route_gzip_bytes: 80000 };
assert.ok(main, 'Production entry chunk must exist');
const initialNames = await staticBundleClosure(entryName, sources);
const initialFiles = files.filter(file => initialNames.includes(file.name));
const initialGzip = initialFiles.reduce((value, file) => value + file.gzip_bytes, 0);
assert.ok(initialGzip <= budgets.main_gzip_bytes, 'Entry static dependency closure exceeds phase 3 baseline plus measured headroom');
const sum = files.reduce((value, file) => value + file.gzip_bytes, 0);
assert.ok(sum <= budgets.all_js_gzip_bytes, 'Total production JavaScript exceeds fixed budget');
for (const file of files.filter(file => file !== main)) assert.ok(file.gzip_bytes <= budgets.individual_route_gzip_bytes, `${file.name} exceeds route budget`);
await mkdir(output, { recursive: true });
const result = { passed: true, budgets, main, initial_gzip_bytes: initialGzip, initial_files: initialFiles, all_js_gzip_bytes: sum, files, measured_at: new Date().toISOString() };
await writeFile(resolve(output, 'bundle-budget.json'), JSON.stringify(result, null, 2));
console.log(JSON.stringify({ passed: true, main_gzip_bytes: main.gzip_bytes, initial_gzip_bytes: initialGzip, all_js_gzip_bytes: sum, chunks: files.length }));

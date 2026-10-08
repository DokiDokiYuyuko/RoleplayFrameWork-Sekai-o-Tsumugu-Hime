/** Offline structural benchmark. MessageBubble and browser layout are not measured. */
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { performance } from 'node:perf_hooks';
import ts from 'typescript';

function load(source, imports, jsx = false) {
  const exports = {};
  const code = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
    jsx: jsx ? ts.JsxEmit.ReactJSX : undefined,
  }}).outputText;
  new Function('require', 'exports', code)((name) => {
    if (!(name in imports)) throw new Error(`Missing benchmark import: ${name}`);
    return imports[name];
  }, exports);
  return exports;
}
const source = readFileSync(new URL('../src/components/MessageList.tsx', import.meta.url), 'utf8');
const inputGroups = load(readFileSync(new URL('../src/utils/playerInputGroups.ts', import.meta.url), 'utf8'), {});
let state, bookmarks, elementCount, bubbleCount, itemCount, bookmarkComparisons;
const element = (type, props, key) => {
  elementCount++;
  if (type === 'MessageBubble') bubbleCount++;
  if (props?.id?.startsWith('message-')) itemCount++;
  return { type, props, key };
};
const { MessageList } = load(source, {
  react: { useMemo: (fn) => fn(), useRef: (value) => ({ current: value }), useLayoutEffect() {},
    useState: (value) => [typeof value === 'function' ? value() : value, () => {}] },
  'react/jsx-runtime': { jsx: element, jsxs: element },
  '../store/chatStore': { useChatStore: (selector) => selector(state) },
  './MessageBubble': { MessageBubble: 'MessageBubble' },
  '../utils/playerInputGroups': inputGroups,
  '@tanstack/react-query': { useQuery: () => ({ data: bookmarks }), useQueryClient: () => ({ invalidateQueries() {} }) },
  '../api/client': { api: {} },
}, true);

const rows = [];
for (const count of [1000, 5000, 10000]) {
  state = { currentSessionId: 'synthetic', sessions: [{ id: 'synthetic', story_id: 'synthetic' }],
    messages: Array.from({ length: count }, (_, index) => ({ id: `m-${index}`, seq: index,
      turn: Math.floor(index / 2), actor: index % 2 ? 'guide' : 'player', kind: 'roleplay',
      content: '仅供离线测试的合成剧情。'.repeat(20), status: 'final', scene_id: `scene-${Math.floor(index / 50)}` })) };
  for (const bookmarkCount of [0, Math.floor(count / 10)]) {
    bookmarks = Array.from({ length: bookmarkCount }, (_, index) => ({ branch_id: 'synthetic',
      get message_id() { bookmarkComparisons++; return `m-${index * 10}`; }}));
    const samples = [];
    for (let repeat = 0; repeat < 15; repeat++) {
      elementCount = bubbleCount = itemCount = bookmarkComparisons = 0;
      const start = performance.now();
      MessageList({});
      samples.push(performance.now() - start);
    }
    samples.sort((a, b) => a - b);
    rows.push({ message_count: count, bookmark_count: bookmarkCount,
      jsx_elements: elementCount, message_items: itemCount, bubble_placeholders: bubbleCount,
      bookmark_comparisons: bookmarkComparisons,
      render_ms_median: Number(samples[7].toFixed(3)), render_ms_p95: Number(samples[14].toFixed(3)) });
  }
}
process.stdout.write(JSON.stringify({ kind: 'offline_structure_baseline',
  source_sha256: createHash('sha256').update(source).digest('hex'), node: process.version,
  measured: 'MessageList hooks, grouping, JSX outer elements, bookmark matching',
  excluded: 'MessageBubble internals, DOM, CSS, layout, browser frames, phone', rows }, null, 2) + '\n');

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { resolve, relative } from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';
import { load, receiptModule, metadataModule } from './storySupport.mjs';
const require = createRequire(import.meta.url);
const root = fileURLToPath(new URL('../src', import.meta.url));
function files(directory) { return readdirSync(directory, { withFileTypes: true }).flatMap(entry => entry.isDirectory()
  ? files(resolve(directory, entry.name)) : /\.tsx?$/.test(entry.name) ? [resolve(directory, entry.name)] : []); }

test('feature and UI layers have no raw fetch or cross-feature story setState escape', () => {
  const violations = [];
  for (const file of files(root)) {
    const path = relative(root, file).replaceAll('\\', '/');
    if (path.startsWith('api/') || path === 'store/chatStore.ts' || path === 'features/stories/storyRuntime.ts') continue;
    const tree = ts.createSourceFile(file, readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true);
    function visit(node) {
      if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier) && /\.(?:tsx)$/.test(path)) {
        if (/\/api\/client$/.test(node.moduleSpecifier.text)) violations.push(`${path}:private-client-import`);
        const names = node.importClause?.namedBindings;
        if (names && ts.isNamedImports(names) && names.elements.some(element => ['jsonRequest', 'storyCommand', 'uploadStoryCommand', 'queryStoryCommand', 'lanRequest'].includes(element.propertyName?.text ?? element.name.text))) violations.push(`${path}:low-level-transport`);
      }
      if (ts.isCallExpression(node)) {
        const expression = node.expression;
        if (ts.isIdentifier(expression) && expression.text === 'fetch') violations.push(`${path}:fetch`);
        if (ts.isPropertyAccessExpression(expression) && expression.name.text === 'setState'
          && ts.isIdentifier(expression.expression) && /^use.*Store$/.test(expression.expression.text)) violations.push(`${path}:setState`);
      }
      ts.forEachChild(node, visit);
    }
    visit(tree);
  }
  assert.deepEqual(violations, []);
});

test('all backend-covered story command entry points use durable command transport, including corrected and file imports', () => {
  const expected = new Set(['deleteStory', 'deleteBranch', 'restoreStory', 'purgeStory', 'setStoryPromptPreset', 'updateMemory', 'deleteMemory', 'consolidateAsync', 'rememberEvent', 'createSession', 'startScenario', 'forkBranch', 'forkSave', 'patchBranch', 'createBookmark',
    'patchBookmark', 'deleteBookmark', 'createStoryEvent', 'updateStoryEvent', 'deleteStoryEvent', 'importStory',
    'addStoryParticipant', 'createGroup', 'patchGroup', 'leaveGroup', 'replyGroup', 'swipeGroup', 'swipeMessage',
    'regenerateOne', 'regenerateDependents', 'acceptDependencies', 'regenerateMessage', 'recheckHygiene',
    'createPinnedFact', 'updatePinnedFact', 'deletePinnedFact', 'sceneSwitch', 'confirmDirector', 'rejectDirector',
    'continueMessage', 'characterAction', 'createSave', 'restoreSave', 'patchNarrative', 'patchSessionOptions',
    'patchSessionSetup', 'editMessage', 'editInputGroup', 'switchVariant', 'deleteMessage']);
  const api = ts.createSourceFile('client.ts', readFileSync(resolve(root, 'api/client.ts'), 'utf8'), ts.ScriptTarget.Latest, true);
  function visit(node) {
    if (ts.isPropertyAssignment(node) && expected.has(node.name.getText(api))) {
      const transports = [];
      function calls(child) {
        if (ts.isCallExpression(child) && ts.isIdentifier(child.expression)) {
          if (['storyCommand', 'uploadStoryCommand', 'queryStoryCommand'].includes(child.expression.text)) transports.push(child.expression.text);
          if (child.expression.text === 'jfetch' && child.arguments[1] && ts.isObjectLiteralExpression(child.arguments[1])) {
            const method = child.arguments[1].properties.find(property => ts.isPropertyAssignment(property) && property.name.getText(api) === 'method');
            assert.equal(method, undefined, `${node.name.getText(api)} escaped the durable mutation transport`);
          }
        }
        ts.forEachChild(child, calls);
      }
      calls(node.initializer); assert.ok(transports.length, `${node.name.getText(api)} has no durable command`); expected.delete(node.name.getText(api));
    }
    ts.forEachChild(node, visit);
  }
  visit(api); assert.deepEqual([...expected], []);
  for (const [path, suffix, expectedTransport] of [
    ['features/stories/storyReviewApi.ts', '/regenerate-corrected', 'storyCommand'],
    ['features/stories/storyReviewApi.ts', '/api/v1/chat-import/st/file', 'uploadStoryCommand'],
    ['features/stories/storyReviewApi.ts', '/asset-updates/apply', 'storyCommand'],
  ]) {
    const tree = ts.createSourceFile(path, readFileSync(resolve(root, path), 'utf8'), ts.ScriptTarget.Latest, true); let found = 0;
    function check(node) {
      if (ts.isCallExpression(node) && node.arguments[0]) {
        const arg = node.arguments[0];
        const text = ts.isStringLiteral(arg) ? arg.text : ts.isTemplateExpression(arg) ? arg.templateSpans.at(-1).literal.text : '';
        if (text === suffix) { found++; assert.equal(node.expression.getText(tree), expectedTransport, path); }
      }
      ts.forEachChild(node, check);
    }
    check(tree); assert.equal(found, 1, `${path} missing command entry point`);
  }
});

test('remote summary cache deduplicates consumers without mutating installed detail revision', async () => {
  const { QueryClient } = require('@tanstack/react-query');
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  let calls = 0, release;
  const result = new Promise(resolve => { release = resolve; });
  const api = { listSessions: () => { calls++; return result; }, listStories: async () => [], getWorldline: async () => ({ branches: [] }) };
  const queries = load('../src/features/stories/storyQueries.ts', { '../../api/client': { api }, '../../queryClient': { queryClient } });
  const first = queries.loadSessionSummaries(), second = queries.loadSessionSummaries();
  release([{ id: 'A', branch_revision: 99 }]);
  assert.deepEqual(await first, await second); assert.equal(calls, 1);
  assert.equal(queryClient.getQueryData(['story-sessions'])[0].branch_revision, 99);
  assert.deepEqual(queries.storyQueries.branches('story').queryKey, ['worldline', 'story', '']);
  queryClient.clear();
});

test('runtime applies messages as receipts and protects optimistic rollback and branch ABA', () => {
  let epoch = 1, state = { currentSessionId: 'A', messages: [], sessionCharacters: [], sessions: [{ id: 'A', branch_revision: 3, options_style: 'mixed' }] };
  const events = [], store = { getState: () => ({ ...state, handleSseEvent: event => events.push(event) }),
    setState: value => { state = { ...state, ...(typeof value === 'function' ? value(state) : value) }; } };
  const { storyRuntime } = load('../src/features/stories/storyRuntime.ts', {
    '../../store/chatStore': { useChatStore: store, storyBranchEpoch: () => epoch },
    '../../api/adapters': { toView: value => value }, '../../utils/commandReceipt': receiptModule, './storySessionMetadata': metadataModule,
  });
  const owns = storyRuntime.capture('A'); epoch += 2; assert.equal(owns(), false);
  const rollback = storyRuntime.optimisticSessionPatch('A', { options_style: 'action' });
  state.sessions[0] = { ...state.sessions[0], branch_revision: 4, options_style: 'dialogue' };
  rollback(); assert.equal(state.sessions[0].options_style, 'dialogue');
  storyRuntime.applyView('A', { session: { id: 'A', branch_revision: 2 }, characters: [], groups: [], scenes: [], lorebooks: [] });
  assert.equal(state.sessions[0].branch_revision, 4);
  const message = receiptModule.attachCommandReceipt({ id: 'm', session_id: 'A' }, { command_id: 'operation', branch_revision: 3 });
  storyRuntime.applyMessage('A', message);
  assert.equal(events[0].delivery_source, 'http'); assert.equal(events[0].branch_revision, 3);
  assert.equal(events[0].outbox_event_id, 'command:operation:m');
});

test('incomplete command review names the operation and cannot review a different current branch', () => {
  const { commandReviewContext } = load('../src/features/stories/commandReviewContext.ts');
  const pending = { path: '/api/v1/sessions/A/messages/m/swipe', method: 'POST', id: 'old' };
  const sessions = [{ id: 'A', title: 'Synthetic story', branch_name: 'Original route' }];
  assert.equal(commandReviewContext(pending, 'A', sessions), '「Original route」的重新生成回复');
  assert.equal(commandReviewContext(pending, 'B', sessions), null);
  assert.equal(commandReviewContext({ ...pending, path: '/api/v1/sessions' }, 'B', sessions), '新建故事');
  assert.equal(commandReviewContext({ ...pending, path: '/api/v1/stories/import' }, 'B', sessions), '导入故事');
});


test('shared resource reads use canonical Query factories and modal ownership belongs to Radix primitives', () => {
  const violations = [];
  const shared = new Set(['listCharacters', 'listLorebooks', 'getSettings', 'listScenarios', 'listPromptPresets', 'listWorlds', 'getWorld', 'listWorldOwners', 'listWorldCovers', 'listVoiceProfiles', 'getTTSStatus', 'listCardSources', 'listCardInspirationJobs', 'listLorebookAgentJobs']);
  for (const file of files(root).filter(file => file.endsWith('.tsx'))) {
    const path = relative(root, file).replaceAll('\\', '/');
    const tree = ts.createSourceFile(file, readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true);
    function visit(node) {
      if (ts.isPropertyAssignment(node) && node.name.getText(tree) === 'queryFn') {
        const expression = node.initializer;
        if (ts.isPropertyAccessExpression(expression) && shared.has(expression.name.text)) violations.push(`${path}:private-query-key:${expression.name.text}`);
        if (ts.isArrowFunction(expression)) {
          function calls(child) { if (ts.isCallExpression(child) && ts.isPropertyAccessExpression(child.expression) && shared.has(child.expression.name.text)) violations.push(`${path}:private-query-key:${child.expression.name.text}`); ts.forEachChild(child, calls); }
          calls(expression.body);
        }
      }
      if (ts.isJsxAttribute(node) && ['role', 'aria-modal'].includes(node.name.getText(tree)) && node.initializer && ts.isStringLiteral(node.initializer) && ['dialog', 'true'].includes(node.initializer.text)) violations.push(`${path}:handmade-modal`);
      ts.forEachChild(node, visit);
    } visit(tree);
  }
  assert.deepEqual(violations, []);
});

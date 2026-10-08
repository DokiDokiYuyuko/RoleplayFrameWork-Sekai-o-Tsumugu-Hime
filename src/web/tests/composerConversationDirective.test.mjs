import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
import * as preferences from '../src/utils/conversationPreferences.js';
import * as conversationDisplay from '../src/utils/conversationDisplay.js';

test('message-object director requirement reaches free composer submission and stays out of ordinary mode', () => {
  const oldStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  const values = new Map([
    ['draft.branch-a.identity-a', '合成玩家回应'],
    [preferences.conversationPreferenceKey('branch-a', 'identity-a'), JSON.stringify({ recipients: ['npc-a'], replyMode: 'free', maxReplies: 8 })],
  ]);
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: {
    getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value),
  } });
  try {
    const hooks = [], submissions = [];
    let cursor = 0;
    const useState = (initial) => {
      const index = cursor++;
      if (!(index in hooks)) hooks[index] = typeof initial === 'function' ? initial() : initial;
      return [hooks[index], (next) => { hooks[index] = typeof next === 'function' ? next(hooks[index]) : next; }];
    };
    const useRef = (initial) => {
      const index = cursor++;
      if (!(index in hooks)) hooks[index] = { current: initial };
      return hooks[index];
    };
    const jsx = (type, props) => ({ type, props });
    const identity = { id: 'identity-a', person_id: 'controlled-person', name: '合成玩家' };
    const state = { currentSessionId: 'branch-a', snapshotLoadedSessionId: 'branch-a', sending: false, switchingPlayer: false,
      sessionGroups: [], activeScene: { id: 'scene-a', group_ids: [] }, pendingDirector: null,
      conversationRuns: [], conversationActionBusy: null,
      sessions: [{ id: 'branch-a', character_ids: ['npc-a'], player_identity_id: 'identity-a', player_identities: [identity] }],
      sendMessage: (...args) => { submissions.push(args); return Promise.resolve(); } };
    const useChatStore = (selector) => selector ? selector(state) : state;
    useChatStore.getState = () => state;
    useChatStore.setState = (next) => Object.assign(state, typeof next === 'function' ? next(state) : next);
    const composerState = { prefill: null, draftBackup: null, clearDraftBackup() {} };
    const useComposerStore = (selector) => selector(composerState);
    useComposerStore.getState = () => composerState;
    useComposerStore.setState = () => {};
    const imports = {
      '../design-system/Button': { Button: 'button' },
      '../design-system/PublicArt': { PublicArt: 'public-art' },
      react: { useState, useRef, useMemo: (fn) => fn(), useEffect() {} },
      'radix-ui': { Popover: { Root: 'popover', Anchor: 'popover-anchor', Trigger: 'popover-trigger', Portal: 'popover-portal', Content: 'popover-content', Close: 'popover-close' },
        DropdownMenu: { Root: 'dropdown', Trigger: 'dropdown-trigger', Portal: 'dropdown-portal', Content: 'dropdown-content', Item: 'dropdown-item' } },
      'react/jsx-runtime': { jsx, jsxs: jsx, Fragment: 'fragment' },
      '../store/chatStore': { useChatStore, isConversationActive: () => false },
      '../store/characterStore': { useCharacterStore: (selector) => selector({ action() {} }) },
      '../store/useActiveCharacters': { useActiveCharacters: () => [{ id: 'npc-a', aliases: [], color: 'slate', present: true, muted: false, card: { name: '合成人物' } }] },
      '../store/composerStore': { useComposerStore },
      '../appearance/Ornament': { Ornament: 'ornament' },
      './WritingAssistant': { WritingAssistant: 'writing-assistant' }, './common': { Avatar: 'avatar', Switch: 'switch', COLOR_CLASSES: { slate: { dot: '' } } },
      './PlayerSwitchDialog': { PlayerSwitchDialog: 'player-switch' }, './PlayerRoleProfile': { PlayerRoleProfile: 'player-profile' },
      '../utils/playerIdentity.js': { resolvePlayerIdentity: () => identity, currentPlayerAvatarUrl: () => '', playerDraftKey: (sid, id) => `draft.${sid}.${id}` },
      '../utils/conversationDisplay.js': conversationDisplay,
      './ResponseStylePicker': { ResponseStylePicker: 'response-style-picker' },
      '../design-system/Icon': { Brain: 'brain', Clapperboard: 'clapperboard', MessageCircle: 'message-circle', Sparkles: 'sparkles', MoreHorizontal: 'more', Send: 'send', LoaderCircle: 'loader' },
      '../utils/composerTargets.js': {},
      '../utils/channels': { hasUnclosedChannelMarker: () => false, parseChannelParts: (text) => [{ kind: 'roleplay', text }] },
      './ComposerPanel': { ComposerPanel: 'composer-panel' },
      './useAutoSizeTextarea': { useAutoSizeTextarea() {} },
      './useComposerInput': { useComposerInput: () => ({ onKeyDown() {} }) },
      '../utils/conversationPreferences.js': preferences,
      './ConversationRunPanel': { ConversationRunPanel: 'conversation-run-panel' }, '../conversation.css': {},
    };
    const exports = {};
    const compiled = ts.transpileModule(readFileSync(new URL('../src/components/Composer.tsx', import.meta.url), 'utf8'),
      { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
    new Function('require', 'exports', compiled)((name) => { assert.ok(name in imports, `Unexpected import ${name}`); return imports[name]; }, exports);
    const render = () => { cursor = 0; return exports.Composer(); };
    const find = (node, predicate) => {
      if (!node || typeof node !== 'object') return null;
      if (Array.isArray(node)) { for (const child of node) { const result = find(child, predicate); if (result) return result; } return null; }
      if (predicate(node)) return node;
      return find(node.props?.children, predicate);
    };
    const labelled = (tree, label) => {
      const node = find(tree, (item) => item.props?.['aria-label'] === label);
      assert.ok(node, `Missing composer control ${label}`); return node;
    };
    let tree = render();
    assert.equal(labelled(tree, '回复模式').props.value, 'free');
    assert.ok(labelled(tree, '故事回应正文'));
    find(tree, (item) => item.type === 'button' && item.props?.title?.startsWith('定向给')).props.onClick(); tree = render();
    labelled(tree, '导演要求').props.onChange({ target: { value: ' 让所选人物围绕合成线索交换情报 ' } }); tree = render();
    find(tree, (item) => item.type === 'form').props.onSubmit({ preventDefault() {} });
    assert.equal(submissions.length, 1);
    assert.deepEqual(submissions[0], ['合成玩家回应', ['npc-a'], undefined, 'free', 8, '让所选人物围绕合成线索交换情报']);
    tree = render(); find(tree, (item) => item.type === 'button' && item.props?.title?.startsWith('定向给')).props.onClick(); tree = render();
    labelled(tree, '回复模式').props.onChange({ target: { value: 'serial' } }); tree = render();
    assert.equal(labelled(tree, '多人回复方式').props.value, 'serial');
    const body = find(tree, (item) => item.type === 'textarea' && item.props.placeholder?.startsWith('写下'));
    body.props.onChange({ currentTarget: { value: '合成普通回应', selectionStart: 6 } }); tree = render();
    find(tree, (item) => item.type === 'form').props.onSubmit({ preventDefault() {} });
    assert.equal(submissions[1][3], 'serial'); assert.equal(submissions[1][5], undefined);
  } finally {
    if (oldStorage) Object.defineProperty(globalThis, 'localStorage', oldStorage);
    else delete globalThis.localStorage;
  }
});

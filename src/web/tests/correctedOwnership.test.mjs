import test from 'node:test';
import assert from 'node:assert/strict';
import { load } from './storySupport.mjs';
const flush = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };

function harness(command, refresh) {
  const hooks = [], effects = [], writes = []; let cursor = 0;
  let props = { messageId: 'message-a', generationId: 'generation-a', onClose() {} };
  const state = { currentSessionId: 'branch-a', refreshSession: refresh };
  const react = {
    useState(value) { const index = cursor++; if (!(index in hooks)) hooks[index] = typeof value === 'function' ? value() : value;
      return [hooks[index], update => { hooks[index] = typeof update === 'function' ? update(hooks[index]) : update; writes.push(index); }]; },
    useRef(value) { const index = cursor++; return hooks[index] ??= { current: value }; },
    useEffect(effect, deps) { const index = cursor++, previous = hooks[index];
      if (!previous || deps.some((value, n) => value !== previous[n])) { hooks[index] = deps; effects.push(effect); } },
  };
  const jsx = (type, props) => ({ type, props });
  const component = load('../src/components/ContextCorrectionPanel.tsx', {
    react, 'react/jsx-runtime': { jsx, jsxs: jsx }, 'react-router': { Link: 'link' }, 'lucide-react': { X: 'icon' },
    '@tanstack/react-query': { useQuery: options => ({ refetch: async () => {}, isPending: false, isFetching: false,
      data: options.queryKey[0] === 'correction-options' ? { generation_id: props.generationId, static_changes: [{ key: 'description', label: 'Synthetic description', before: 'old', after: 'new' }], memories: [], notes: [], expected_branch_revision: 2 } : { corrections: [], notes: [], sources: [], changed_sections: [] } }) },
    '../features/stories/storyUiStore': {}, '../features/stories/storyRuntime': { storyRuntime: { capture: () => () => true } },
    '../utils/clientId.js': { createClientId: () => 'synthetic-operation' }, '../api/client': { api: {} }, '../api/request': {},
    '../features/stories/storyReviewApi': { storyReviewApi: { regenerateCorrected: command } }, '../store/chatStore': { useChatStore: Object.assign(selector => selector(state), { getState: () => state }) },
    '../design-system/WorkflowDialog': { WorkflowDialog: 'dialog' }, './StoryContinuityPanel': {},
  }).ContextCorrectionPanel;
  const text = node => Array.isArray(node) ? node.map(text).join('') : node && typeof node === 'object' ? text(node.props?.children) : String(node ?? '');
  function render(next) {
    if (next) props = { ...props, ...next }; cursor = 0;
    const tree = component(props); effects.splice(0).forEach(effect => effect());
    const nodes = []; function visit(node) { if (Array.isArray(node)) return node.forEach(visit); if (!node || typeof node !== 'object') return; nodes.push(node); visit(node.props?.children); } visit(tree);
    return nodes;
  }
  const button = (nodes, label) => nodes.find(node => node.type === 'button' && text(node) === label);
  function start() {
    button(render(), '核对可用于这条回应的修订').props.onClick();
    render().find(node => node.type === 'input' && node.props.type === 'checkbox').props.onChange({ target: { checked: true } });
    button(render(), '用选定修订生成新候选').props.onClick();
  }
  return { start, render, writes };
}

test('corrected request rejection cannot overwrite UI after same-branch generation ABA', async () => {
  const pending = deferred(); let refreshes = 0;
  const h = harness(() => pending.promise, async () => { refreshes++; });
  h.start(); h.render({ generationId: 'generation-b' }); h.render({ generationId: 'generation-a' });
  const writes = h.writes.length; pending.reject(new Error('Old context failed')); await flush();
  assert.equal(h.writes.length, writes); assert.equal(refreshes, 0);
});

test('corrected success rechecks message ownership after the authoritative refresh completes', async () => {
  const refresh = deferred(); let refreshes = 0;
  const h = harness(async () => ({}), async () => { refreshes++; await refresh.promise; });
  h.start(); await flush(); assert.equal(refreshes, 1);
  h.render({ messageId: 'message-b', generationId: 'generation-b' }); const writes = h.writes.length;
  refresh.resolve(); await flush(); assert.equal(h.writes.length, writes);
});

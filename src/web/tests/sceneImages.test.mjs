import test from 'node:test';
import assert from 'node:assert/strict';
import { load, receiptModule, metadataModule } from './storySupport.mjs';

test('scene image receipt is scoped to current scene, revision and branch epoch', () => {
  let state = { currentSessionId: 'branch', activeScene: { id: 'scene', title: 'unchanged', builtin_image_id: null }, sessions: [{ id: 'branch', branch_revision: 3 }] };
  let epoch = 1;
  const store = { getState: () => state, setState: change => { state = { ...state, ...(typeof change === 'function' ? change(state) : change) }; } };
  const { storyRuntime } = load('../src/features/stories/storyRuntime.ts', {
    '../../utils/commandReceipt': receiptModule, '../../store/chatStore': { useChatStore: store, storyBranchEpoch: () => epoch },
    '../../api/adapters': { toView: x => x }, './storySessionMetadata': metadataModule,
  });
  const scene = revision => receiptModule.attachCommandReceipt({ id: 'scene', title: 'must not replace title', builtin_image_id: 'rain-alley' }, { command_id: 'image-operation', branch_revision: revision });
  const owns = storyRuntime.capture('branch');
  assert.equal(storyRuntime.applySceneImage('other', scene(4)), false);
  assert.equal(storyRuntime.applySceneImage('branch', scene(2)), false);
  state.activeScene.id = 'switched';
  assert.equal(storyRuntime.applySceneImage('branch', scene(4)), false);
  state.activeScene.id = 'scene';
  assert.equal(storyRuntime.applySceneImage('branch', scene(4)), true);
  assert.equal(state.activeScene.builtin_image_id, 'rain-alley');
  assert.equal(state.activeScene.title, 'unchanged');
  assert.equal(state.sessions[0].branch_revision, 3, 'Image metadata must not advance the installed message watermark');
  assert.equal(storyRuntime.sceneImageRevision('branch', 'scene'), 4);
  assert.equal(storyRuntime.applySceneImage('branch', scene(3)), false);
  epoch++;
  assert.equal(owns(), false, 'A return to the same branch must not revive an old request');
});

function pickerHarness() {
  const states = []; let cursor = 0, owns = true, failed = true;
  const requests = [], installed = [];
  const jsx = (type, props) => ({ type, props });
  const useState = initial => { const n = cursor++; if (!(n in states)) states[n] = initial; return [states[n], value => { states[n] = value; }]; };
  const { SceneImagePicker } = load('../src/features/stories/SceneImagePicker.tsx', {
    react: { useState }, 'react/jsx-runtime': { jsx, jsxs: jsx, Fragment: 'fragment' },
    '../../design-system/Button': { Button: 'button' }, '../../design-system/SurfaceDialog': { SurfaceDialog: 'dialog' },
    '../../appearance/moonweaveAssets': { BUILTIN_SCENE_IMAGES: [{ id: 'rain-alley', name: '小巷', variant: '雨', image_url: '/public/scene.webp' }], resolveSceneImage: () => '' },
    './storiesClient': { storiesClient: { patchSceneImage: async (...args) => { requests.push(args); if (failed) throw new Error('Synthetic failed save'); return { id: 'scene', builtin_image_id: args[2].builtin_image_id }; } } },
    './storyRuntime': { storyRuntime: { capture: () => () => owns, sceneImageRevision: () => 3, applySceneImage: (...args) => { installed.push(args); return true; } } },
    './scene-images.css': {},
  });
  const scene = { id: 'scene', title: 'Synthetic scene', builtin_image_id: null };
  const render = () => { cursor = 0; return SceneImagePicker({ scene, branchId: 'branch', revision: 3 }); };
  function find(node, predicate) { if (!node || typeof node !== 'object') return null; if (Array.isArray(node)) { for (const x of node) { const got = find(x, predicate); if (got) return got; } return null; } return predicate(node) ? node : find(node.props?.children, predicate); }
  return { render, find, requests, installed, scene, recover: () => { failed = false; }, leave: () => { owns = false; } };
}

test('scene picker failures preserve committed image and retry the same selection', async () => {
  const h = pickerHarness();
  h.find(h.render(), x => x.type === 'button').props.onClick();
  h.find(h.render(), x => x.props?.className === 'scene-image-picker__choice' && x.props.children?.[1]).props.onClick();
  h.find(h.render(), x => x.props?.children === '保存场景图').props.onClick();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.scene.builtin_image_id, null);
  assert.equal(h.installed.length, 0);
  assert.ok(h.find(h.render(), x => x.props?.role === 'alert'));
  h.recover();
  h.find(h.render(), x => x.props?.children === '重试保存').props.onClick();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(h.requests[0], h.requests[1]);
  assert.equal(h.installed.length, 1);
  assert.equal(h.find(h.render(), x => x.type === 'dialog'), null);
});

test('late scene picker success from a departed branch cannot install', async () => {
  const h = pickerHarness(); h.recover();
  h.find(h.render(), x => x.type === 'button').props.onClick();
  h.find(h.render(), x => x.props?.children === '保存场景图').props.onClick(); h.leave();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.installed.length, 0);
});

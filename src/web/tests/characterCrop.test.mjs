import test from 'node:test';
import assert from 'node:assert/strict';
import { load } from './storySupport.mjs';
const { cropGeometry, cropOutput, renderCharacterCrop } = load('../src/features/library/characterCrop.ts');

test('portrait output is 720 × 1280 and avatar output is square at 512', () => {
  assert.deepEqual(cropOutput('portrait'), { width: 720, height: 1280 });
  assert.deepEqual(cropOutput('avatar'), { width: 512, height: 512 });
});
test('center, zoom and keyboard offsets always keep the selection inside the source', () => {
  for (const [width,height] of [[1600,900],[900,1600],[32,32]]) for (const kind of ['avatar','portrait']) for (const zoom of [1,2,4]) for (const offset of [-3,-1,0,1,3]) {
    const crop = cropGeometry(width,height,kind,zoom,offset,-offset);
    assert.ok(crop.sx >= -1e-8 && crop.sy >= -1e-8);
    assert.ok(crop.sx + crop.sw <= width + 1e-8 && crop.sy + crop.sh <= height + 1e-8);
    assert.ok(Math.abs(crop.sw / crop.sh - crop.width / crop.height) < 1e-8);
  }
});
test('browser canvas receives actual output dimensions and the bounded selected rectangle', async () => {
  const calls = [];
  const canvas = { getContext: () => ({ drawImage: (...args) => calls.push(args) }), toBlob: fn => fn(new Blob(['synthetic-pixels'], { type: 'image/png' })) };
  const previous = globalThis.document;
  globalThis.document = { createElement: name => { assert.equal(name,'canvas'); return canvas; } };
  try {
    const image = { naturalWidth: 1600, naturalHeight: 900 };
    const file = await renderCharacterCrop(image,'portrait',2,.5,-.3);
    assert.equal(canvas.width,720); assert.equal(canvas.height,1280);
    assert.equal(file.type,'image/png');
    assert.deepEqual(calls[0].slice(-4), [0,0,720,1280]);
    assert.equal(calls[0][0],image);
  } finally { globalThis.document = previous; }
});
test('canvas encoding failure rejects instead of passing an empty file to the upload owner', async () => {
  const previous = globalThis.document;
  globalThis.document = { createElement: () => ({ getContext: () => ({ drawImage() {} }), toBlob: fn => fn(null) }) };
  try { await assert.rejects(renderCharacterCrop({ naturalWidth: 512, naturalHeight: 512 },'avatar',1,0,0), /裁剪失败/); }
  finally { globalThis.document = previous; }
});

function dialogHarness(upload) {
  const states = []; let cursor = 0;
  const image = { naturalWidth: 1600, naturalHeight: 900 };
  const react = {
    useState(initial) { const i = cursor++; if (!(i in states)) states[i] = initial; return [states[i], next => { states[i] = typeof next === 'function' ? next(states[i]) : next; }]; },
    useRef(initial) { const i = cursor++; if (!(i in states)) states[i] = { current: initial }; return states[i]; }, useEffect() {},
  };
  let cancels = 0, cropCalls = 0;
  const { CharacterCropDialog } = load('../src/components/CharacterCropDialog.tsx', {
    react, '../design-system/SurfaceDialog': { SurfaceDialog: 'dialog' }, '../design-system/Button': { Button: 'button' }, './character-editor.css': {},
    '../features/library/characterCrop': { cropOutput, cropGeometry, async renderCharacterCrop() { cropCalls++; return { width:512,height:512 }; } },
  });
  let tree;
  const find = (node,predicate) => {
    if (!node || typeof node !== 'object') return;
    if (Array.isArray(node)) { for (const child of node) { const hit = find(child,predicate); if (hit) return hit; } return; }
    if (predicate(node)) return node;
    return find(node.props?.children,predicate);
  };
  return {
    render() { cursor = 0; tree = CharacterCropDialog({ source:'synthetic.png', kind:'avatar', onCancel: () => cancels++, onSave:upload }); },
    ready() { states[0] = 'synthetic.png'; this.render(); const node = this.get(n => n.type === 'img'); node.ref.current = image; node.props.onLoad(); this.render(); },
    get(predicate) { const hit = find(tree,predicate); assert.ok(hit); return hit; },
    counts() { return { cancels,cropCalls }; },
  };
}
test('cancel never crops or uploads a selected image', () => {
  let uploads = 0; const h = dialogHarness(async () => uploads++); h.render();
  h.get(n => n.props?.children === '取消').props.onClick();
  assert.equal(uploads,0); assert.deepEqual(h.counts(),{ cancels:1,cropCalls:0 });
});
test('failed save keeps the selection open and retry closes only after durable upload', async () => {
  let uploads = 0; const h = dialogHarness(async () => { if (++uploads === 1) throw new Error('合成保存失败'); }); h.render(); h.ready();
  h.get(n => n.props?.['aria-label'] === '裁剪缩放').props.onChange({ target:{ value:'2' } }); h.render();
  // The handler starts the asynchronous operation; allow its awaited upload to settle.
  h.get(n => n.props?.children === '保存裁剪').props.onClick(); await new Promise(resolve => setTimeout(resolve,0)); h.render();
  assert.match(h.get(n => n.props?.role === 'alert').props.children,/选框已保留/);
  assert.equal(h.get(n => n.props?.['aria-label'] === '裁剪缩放').props.value,2);
  assert.equal(h.counts().cancels,0);
  h.get(n => n.props?.children === '保存裁剪').props.onClick(); await new Promise(resolve => setTimeout(resolve,0));
  assert.equal(uploads,2); assert.equal(h.counts().cancels,1);
});


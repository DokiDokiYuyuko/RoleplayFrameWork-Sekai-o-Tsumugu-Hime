import test from 'node:test';
import assert from 'node:assert/strict';
import { load } from './storySupport.mjs';
function moduleFor(size = '40') {
  return load('../src/components/common.tsx', {
    '../design-system/SurfaceDialog': { SurfaceDialog: 'dialog' }, '../design-system/Icon': { X: 'icon' },
    '../design-system/Avatar': { Avatar: 'shared-avatar' },
    '../design-system/internal/useAppearanceAttribute': { useAppearanceAttribute: () => size },
  });
}
test('compatibility avatar retains source precedence and encoded character asset identity', () => {
  const { Avatar } = moduleFor();
  assert.equal(Avatar({name:'合成',color:'rose',characterId:'test /id',imageVersion:4}).props.src, '/api/v1/characters/test%20%2Fid/avatar?v=4');
  assert.equal(Avatar({name:'合成',color:'rose',characterId:'ignored',src:'/synthetic.png'}).props.src, '/synthetic.png');
});
test('compatibility avatar maps compact sizes and follows the global dialogue size', () => {
  const { Avatar } = moduleFor('56');
  assert.equal(Avatar({name:'合成',color:'rose'}).props.size,56);
  for (const [size,diameter] of [['xs',24],['sm',32]]) { const node=Avatar({name:'合成',color:'rose',size}); assert.equal(node.props.size,diameter); assert.equal(node.props.dense,true); }
});
test('compatibility avatar preserves clickable control and accessible title', () => {
  const click=()=>{};
  const node=moduleFor().Avatar({name:'合成',color:'rose',onClick:click,title:'查看合成资料'});
  assert.equal(node.type,'button'); assert.equal(node.props.type,'button'); assert.equal(node.props.onClick,click);
  assert.equal(node.props['aria-label'],'查看合成资料'); assert.equal(node.props.children.type,'shared-avatar');
});

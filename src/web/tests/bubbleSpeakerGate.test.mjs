import test from 'node:test';
import assert from 'node:assert/strict';
import { load } from './storySupport.mjs';
const styles = load('../src/appearance/bubbleStyles.ts');
const { BubbleFrame } = load('../src/appearance/BubbleFrame.tsx', {
  react: { useState: () => [null, () => {}] },
  './store': { useAppearanceStore: (selector) => selector({ preferences: { bubble_style_id: 'star-track' } }) },
  './bubbleStyles': styles,
  '../design-system/PublicArt': { PublicArt: 'public-art' },
});
function images(node) {
  if (!node || typeof node !== 'object') return [];
  return [...(node.type === 'public-art' ? [node] : []), ...[node.props?.children].flat(5).flatMap(images)];
}
test('generated bubble artwork is limited to NPC speech and four decorated styles', () => {
  for (const speaker of ['player', 'inner', 'scene', 'npc']) for (const styleId of styles.BUBBLE_STYLES.map(s => s.id)) {
    const node = BubbleFrame({ speaker, styleId, children: '合成正文' });
    const expected = speaker === 'npc' && styleId !== 'plain' ? 2 : 0;
    assert.equal(images(node).length, expected, `${speaker}/${styleId}`);
    assert.equal(node.props.style === undefined, expected === 0, `${speaker}/${styleId} has no generated geometry without artwork`);
  }
});
test('NPC corner and end dimensions preserve the approved native ratios', () => {
  const node = BubbleFrame({ speaker:'npc', styleId:'moonlight', children:'合成正文' });
  assert.equal(node.props.style['--bubble-cw'],'39.49px');
  assert.equal(node.props.style['--bubble-ch'],'40px');
  assert.equal(node.props.style['--bubble-ew'],'37.81px');
  assert.equal(node.props.style['--bubble-eh'],'24px');
});

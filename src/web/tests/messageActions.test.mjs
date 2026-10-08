import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const hasClass = (node, name) => typeof node.props?.className === 'string' && node.props.className.split(/\s+/).includes(name);
function render(actions,externalError,onDismissError,options={}) {
  const states=[],exports={};
  const jsx=(type,props,key)=>({type,props,key});
  const dropdown=Object.fromEntries(['Root','Trigger','Portal','Content','Item','Sub','SubTrigger','SubContent','RadioGroup','RadioItem','ItemIndicator'].map(name=>[name,`dropdown-${name}`]));
  const imports={'../design-system/Button':{Button:'button'},react:{useState:()=>[null,value=>states.push(value)]},'react/jsx-runtime':{jsx,jsxs:jsx,Fragment:'fragment'},
    'radix-ui':{DropdownMenu:dropdown},'../design-system/Icon':{AudioLines:'audio',LoaderCircle:'loading',MoreHorizontal:'more',Volume2:'volume'},'./message-actions.css':{}};
  const code=ts.transpileModule(readFileSync(new URL('../src/components/MessageActionBar.tsx',import.meta.url),'utf8'),
    {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;
  new Function('require','exports',code)(name=>imports[name],exports);
  const tree=exports.MessageActionBar({actions,externalError,onDismissError,...options});
  const all=[];const visit=node=>{if(Array.isArray(node))return node.forEach(visit);if(!node||typeof node!=='object')return;all.push(node);visit(node.props?.children);};visit(tree);
  return {all,states};
}
test('stable action IDs keep bookmarks reachable and disabled quick actions inert regardless of wording',async()=>{
  let count=0;
  const {all}=render([{id:'copy',label:'复制到剪贴板',onClick:()=>count++},{id:'regenerate',label:'再试一次',disabled:true,onClick:()=>count++},
    {id:'bookmark',label:'书签',onClick:()=>count++},{id:'edit',label:'编辑',disabled:true,onClick:()=>count++}]);
  const quick=all.filter(node=>node.type==='button'&&hasClass(node,'v7-message-quick-button'));
  assert.deepEqual(quick.map(node=>node.props['aria-label']),['复制到剪贴板','再试一次']);
  assert.ok(all.some(node=>node.type==='button'&&node.props['aria-label']==='书签'));
  const edit=all.find(node=>node.type==='button'&&node.props['aria-label']==='编辑');assert.equal(edit.props.disabled,true);
  quick[1].props.onClick();edit.props.onClick();await Promise.resolve();assert.equal(count,0);
  const historical=render([{id:'regenerate',label:'重新生成',quick:false,disabled:true,title:'仅最近一轮可用',onClick:()=>count++}]).all;
  assert.equal(historical.filter(node=>node.type==='button'&&hasClass(node,'v7-message-quick-button')).length,0);
  assert.ok(historical.some(node=>node.type==='button'&&node.props['aria-description']==='仅最近一轮可用'));
});
test('clipboard rejection is actionable and remote errors remain announced on special bubble paths',async()=>{
  const {all,states}=render([{id:'copy',label:'复制',onClick:()=>Promise.reject(new Error('denied'))}], '远端合成失败');
  all.find(node=>node.type==='button'&&node.props['aria-label']==='复制').props.onClick();await new Promise(resolve=>setTimeout(resolve,0));
  assert.deepEqual(states.at(-1),{error:true,text:'复制失败，可选中正文后手动复制。'});
  assert.ok(all.some(node=>node.props?.role==='alert'));
  assert.ok(all.some(node=>node.props?.children==='远端合成失败'));
});

test('message operation error remains readable until explicitly dismissed', () => {
  let dismissed = 0;
  const { all } = render([], '合成操作失败，正文仍保留', () => dismissed++);
  const button = all.find(node => node.type === 'button' && node.props['aria-label'] === '关闭消息操作错误');
  assert.ok(button);
  assert.equal(dismissed, 0);
  button.props.onClick();
  assert.equal(dismissed, 1);
});

test('story final reply has ordered icon-only copy, regenerate, edit and more while speech stays in the menu', async () => {
  let spoken = 0;
  const action = (id, label) => ({ id, label, icon: { type: `icon-${id}`, props: {} }, onClick: () => {} });
  const speech = { enabled: true, busy: false, playing: false, emotion: '', onEmotionChange: () => {}, onSpeak: () => spoken++, onEnded: () => {}, audioRef: { current: null }, audioUrl: null };
  const { all } = render([action('edit', '编辑'), action('bookmark', '书签'), action('regenerate', '重新生成'), action('copy', '复制消息')], null, null, { compactIcons: true, speech });
  const shortcuts = all.filter(node => hasClass(node, 'v7-message-quick-button') || node.type === 'dropdown-Trigger');
  assert.deepEqual(shortcuts.map(node => node.props['aria-label']), ['复制消息', '重新生成', '编辑', '消息操作']);
  for (const button of shortcuts) {
    const children = Array.isArray(button.props.children) ? button.props.children : [button.props.children];
    assert.equal(children.some(child => typeof child === 'string' && child.trim()), false, 'No text shortcut labels');
    assert.ok(button.props.title, 'Pointer users have a name');
  }
  assert.equal(all.filter(node => node.type === 'button' && node.props['aria-label'] === '编辑').length, 1, 'Edit is not duplicated in more');
  assert.ok(all.some(node => node.props?.role === 'tooltip' && node.props.children === '更多'));
  const speak = all.find(node => node.type === 'button' && node.props['aria-label'] === '朗读这条消息');
  assert.ok(speak && !hasClass(speak, 'v7-message-quick-button'));
  speak.props.onClick(); await Promise.resolve(); assert.equal(spoken, 1);
  assert.ok(all.some(node => node.type === 'dropdown-SubTrigger'), 'Speech emotion remains reachable');
  assert.ok(all.some(node => node.type === 'button' && node.props['aria-label'] === '书签'));
});

test('story preserves action applicability and disabled reasons are keyboard reachable', async () => {
  let invoked = 0;
  const { all } = render([{ id: 'copy', label: '复制消息', onClick: () => {} }, { id: 'edit', label: '编辑', title: '请等待当前生成结束', disabled: true, onClick: () => invoked++ },
    { id: 'regenerate', label: '重新生成', quick: false, disabled: true, title: '仅最近一轮可用', onClick: () => invoked++ }], null, null, { compactIcons: true });
  const quick = all.filter(node => node.type === 'button' && hasClass(node, 'v7-message-quick-button'));
  assert.deepEqual(quick.map(node => node.props['aria-label']), ['复制消息', '编辑']);
  const focusHost = all.find(node => hasClass(node, 'message-action-hint-host') && node.props.tabIndex === 0);
  assert.equal(focusHost.props['aria-label'], '编辑：请等待当前生成结束');
  quick[1].props.onClick(); await Promise.resolve(); assert.equal(invoked, 0);
  assert.ok(all.some(node => node.type === 'button' && node.props['aria-description'] === '仅最近一轮可用'));
});

test('simple chat default retains text shortcuts, edit in more and speech shortcut', () => {
  const { all } = render([{ id: 'copy', label: '复制消息', onClick: () => {} }, { id: 'edit', label: '编辑', onClick: () => {} }], null, null,
    { speech: { enabled: true, busy: false, playing: false, emotion: '', onEmotionChange: () => {}, onSpeak: () => {}, onEnded: () => {}, audioRef: { current: null }, audioUrl: null } });
  const quick = all.filter(node => node.type === 'button' && hasClass(node, 'v7-message-quick-button'));
  assert.deepEqual(quick.map(node => node.props['aria-label']), ['复制消息', '朗读这条消息']);
  const edit = all.find(node => node.type === 'button' && node.props['aria-label'] === '编辑');
  assert.ok(edit && !hasClass(edit, 'v7-message-quick-button'));
  assert.ok(all.some(node => node.type === 'span' && node.props.children === '复制'));
});

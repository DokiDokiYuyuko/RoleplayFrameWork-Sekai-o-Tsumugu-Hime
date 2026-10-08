import test from 'node:test';
import assert from 'node:assert/strict';
import { load } from './storySupport.mjs';
import * as creationUtilities from '../src/utils/characterCreation.js';

function controller(character, presentation = 'page', overrides = {}) {
  let revision = character?.revision ?? 1;
  const events = [];
  const created = { id: 'synthetic-created', revision: 1, card: { name: '合成向导' }, color: 'violet' };
  const store = {
    characters: character ? [character] : [],
    upsert(value) { store.characters = [value]; },
    async uploadMedia(id, kind) { events.push(['upload', id, kind]); await overrides.upload?.(kind); },
    async updateCharacter(id, patch) {
      events.push(['update', id, patch]);
      if (overrides.update) await overrides.update(patch);
      revision++;
      store.characters = store.characters.map(value => value.id === id ? { ...value, revision } : value);
    },
  };
  const useCharacterStore = () => store;
  useCharacterStore.getState = () => store;
  const { CharacterEditorModal } = load('../src/components/CharacterEditorModal.tsx', {
    react: { useRef: value => ({ current: value }), useState: value => [value, () => {}] },
    './CharacterEditor': { default: () => null },
    '../store/characterStore': { useCharacterStore },
    '../api/characterCreation': { characterCreation: { async create(card, world) { events.push(['create', card, world]); return created; } } },
  });
  const element = CharacterEditorModal({ character, presentation, onClose: () => events.push(['close']), onCreated: value => events.push(['created', value]) });
  return { props: element.props, events, store };
}
const payload = { card: { name: '合成向导', description: '保留正文', extensions: { synthetic: true } }, aliases: ['向导'], source_world_id: 'synthetic-world', runtime: { model: '', base_url: '', max_tokens: 0 } };

test('page creation reports the durable character without closing the editor', async () => {
  const { props, events } = controller();
  assert.equal(props.presentation, 'page');
  await props.onSave(payload);
  assert.equal(events.some(row => row[0] === 'created'), false, 'route navigation waits until the editor has cleared its local draft');
  props.onSaveSucceeded(false);
  assert.equal(events.filter(row => row[0] === 'create').length, 1);
  assert.equal(events.filter(row => row[0] === 'created').length, 1);
  assert.equal(events.some(row => row[0] === 'close'), false);
  assert.deepEqual(events.find(row => row[0] === 'create')[1].extensions, { synthetic: true });
});
test('compatible dialog creation still closes only after commit', async () => {
  const { props, events } = controller(undefined, 'dialog');
  await props.onSave(payload);
  assert.equal(events.some(row => row[0] === 'close'), false);
  props.onSaveSucceeded(false, false);
  assert.deepEqual(events.slice(-2).map(row => row[0]), ['created', 'close']);
});
test('existing page save freezes CAS, advances editor revision and keeps the page open', async () => {
  const character = { id: 'synthetic-existing', revision: 8, aliases: [], card: payload.card, llm: { sampling: { temperature: .7 } } };
  const { props, events } = controller(character);
  await props.onSave(payload);
  const patch = events.find(row => row[0] === 'update')[2];
  assert.equal(patch.expected_revision, 8);
  assert.equal(patch.sampling.temperature, .7);
  assert.equal(props.getCharacterRevision(), 9);
  assert.equal(events.some(row => row[0] === 'close'), false);
});
test('a conflict rejects save without advancing the revision or closing', async () => {
  const character = { id: 'synthetic-existing', revision: 8, card: payload.card, llm: {} };
  const { props, events } = controller(character, 'page', { update: async () => { throw new Error('合成版本冲突'); } });
  await assert.rejects(props.onSave(payload), /版本冲突/);
  assert.equal(props.getCharacterRevision(), 8);
  assert.equal(events.some(row => row[0] === 'close'), false);
});
test('media failure keeps the created identity for retry and does not create a second character', async () => {
  let count = 0;
  const { props, events } = controller(undefined, 'page', { upload: async () => { if (++count === 1) throw new Error('合成立绘故障'); } });
  props.onFullBodyPicked({ name: 'synthetic.webp' });
  await assert.rejects(props.onSave(payload), /立绘故障/);
  assert.equal(events.some(row => row[0] === 'created'), false);
  await props.onSave(payload);
  props.onSaveSucceeded(false);
  assert.equal(events.filter(row => row[0] === 'create').length, 1);
  assert.equal(events.filter(row => row[0] === 'upload').length, 2);
  assert.equal(events.filter(row => row[0] === 'created').length, 1);
});

test('editor read refreshes the library media revision and never replaces a newer cache revision', async () => {
  const state = { characters: [{ id: 'synthetic-existing', revision: 12, card: { name: '较新缓存' } }], upsert(value) { if (value.revision >= state.characters[0].revision) state.characters = [value]; } };
  const { readCharacterForEditing } = load('../src/features/library/characterEditing.ts', {
    './libraryClient': { libraryClient: { getCharacter: async () => ({ id: 'synthetic-existing', revision: 11, card: { name: '较旧读取' } }) } },
    '../../api/adapters': { toView: value => ({ ...value, color: 'violet' }) },
    '../../store/characterStore': { useCharacterStore: { getState: () => state } },
  });
  const snapshot = await readCharacterForEditing('synthetic-existing');
  assert.equal(snapshot.revision, 12);
  assert.equal(snapshot.card.name, '较新缓存');
});
const { revealCharacterChapter } = load('../src/features/library/characterEditorNavigation.ts');
test('chapter navigation reveals every closed ancestor, then scrolls and focuses the exact target', () => {
  const calls = [];
  const outside = { tagName: 'DETAILS', open: false };
  const outer = { tagName: 'DETAILS', open: false, parentElement: null };
  const inner = { tagName: 'DETAILS', open: false, parentElement: outer };
  const target = { tagName: 'DIV', parentElement: inner, hasAttribute: () => false,
    scrollIntoView(options) { calls.push(['scroll', options, inner.open, outer.open]); },
    focus(options) { calls.push(['focus', options]); } };
  assert.equal(revealCharacterChapter(target), true);
  assert.equal(inner.open, true);
  assert.equal(outer.open, true);
  assert.equal(outside.open, false);
  assert.equal(target.tabIndex, -1);
  assert.deepEqual(calls, [['scroll', { block: 'start', behavior: 'auto' }, true, true], ['focus', { preventScroll: true }]]);
});
test('a details chapter opens itself and keeps an existing focus contract; missing chapters are safe', () => {
  const target = { tagName: 'DETAILS', open: false, parentElement: null, tabIndex: 0,
    hasAttribute: () => true, scrollIntoView() {}, focus() {} };
  assert.equal(revealCharacterChapter(target), true);
  assert.equal(target.open, true);
  assert.equal(target.tabIndex, 0);
  assert.equal(revealCharacterChapter(null), false);
});
const { characterListScrollOwner, rememberCharacterListPosition, restoreCharacterListPosition } = load('../src/features/library/characterListPosition.ts');
function listPositionHarness({ visible = true } = {}) {
  const calls = [];
  const owner = { overflowY: 'auto', scrollTop: 481, scrollLeft: 3, parentElement: null,
    scrollTo(value) { calls.push(['scroll', value.top, value.left]); this.scrollTop = value.top; this.scrollLeft = value.left; },
    getBoundingClientRect: () => ({ top: 0, bottom: 900 }) };
  const wrapper = { overflowY: 'visible', parentElement: owner };
  const button = { getAttribute: () => 'synthetic-card', focus(options) { calls.push(['focus', options.preventScroll]); },
    getBoundingClientRect: () => ({ top: 300, bottom: 330 }), scrollIntoView() { calls.push(['reveal']); } };
  const search = { focus() { calls.push(['search-focus']); }, getBoundingClientRect: () => ({ top: 0, bottom: 40 }), scrollIntoView() {} };
  const list = { parentElement: wrapper, ownerDocument: { defaultView: { getComputedStyle: node => ({ overflowY: node.overflowY }) }, scrollingElement: null },
    querySelectorAll: () => visible ? [button] : [], querySelector: () => search };
  return { list, owner, button, calls };
}
test('returning to a filtered list restores its actual scrolling owner and the original edit focus', () => {
  const { list, owner, calls } = listPositionHarness();
  assert.equal(characterListScrollOwner(list), owner);
  rememberCharacterListPosition(list, 'q=synthetic&sort=name', 'synthetic-card');
  owner.scrollTop = 0; owner.scrollLeft = 0;
  assert.equal(restoreCharacterListPosition(list, 'q=synthetic&sort=name'), true);
  assert.equal(owner.scrollTop, 481);
  assert.equal(owner.scrollLeft, 3);
  assert.deepEqual(calls, [['scroll', 481, 3], ['focus', true]]);
});
test('list position is scoped by filters and a removed or filtered-out card falls back to search', () => {
  const { list, owner, calls } = listPositionHarness({ visible: false });
  rememberCharacterListPosition(list, 'q=synthetic-other', 'synthetic-card');
  assert.equal(restoreCharacterListPosition(list, 'q=not-visited'), false);
  owner.scrollTop = 0;
  assert.equal(restoreCharacterListPosition(list, 'q=synthetic-other'), true);
  assert.equal(owner.scrollTop, 481);
  assert.deepEqual(calls, [['scroll', 481, 3], ['search-focus']]);
});


function authoringWorkspace(presentation = 'page', overrides = {}) {
  const slots = []; let cursor = 0;
  const react = {
    useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial; return [slots[index], value => { slots[index] = typeof value === 'function' ? value(slots[index]) : value; }]; },
    useRef(initial) { const index = cursor++; if (!(index in slots)) slots[index] = { current: initial }; return slots[index]; },
    useEffect() {},
  };
  const primitive = { Root: 'primitive-root', Trigger: 'primitive-trigger', Content: 'primitive-content', Portal: 'primitive-portal', Close: 'primitive-close' };
  const { default: CharacterEditor } = load('../src/components/CharacterEditor.tsx', {
    react, 'radix-ui': { Popover: primitive, Collapsible: primitive }, 'react-router': { useNavigate: () => () => {} }, '@tanstack/react-query': { useQuery: () => ({ data: [{ id: 'world-a', title: '合成世界' }] }) },
    '../design-system/Workbench': { WorkbenchDesk: 'desk' }, '../design-system/Tabs': { Tabs: 'tabs', TabsList: 'tablist', TabsTrigger: 'tab' }, '../design-system/Menu': { Menu: 'menu' }, '../design-system/Tag': { Tag: 'tag' }, '../design-system/IconButton': { IconButton: 'iconbutton' },
    '../design-system/Icon': {}, '../appearance/Ornament': { Ornament: 'ornament' },
    '../features/library/characterEditorNavigation': { revealCharacterChapter() {} }, './character-editor-chapters.css': {},
    '../queryClient': { queryClient: {} }, '../features/resources/resourceQueries': { resourceQueries: { worlds: () => ({}) } },
    '../design-system/SurfaceDialog': { SurfaceDialog: 'dialog' }, '../features/library/libraryClient': { libraryClient: {} },
    '../store/characterStore': {}, '../api/Api': {}, '../design-system/ConfirmDialog': { ConfirmDialog: 'confirm-dialog' },
    '../api/characterCreation': { characterCreation: overrides.services || {} }, '../utils/characterCreation.js': creationUtilities,
    '../utils/clientId.js': { createClientId: () => 'synthetic' }, './character-creation.css': {}, './character-editor.css': {}, '../design-system/Button': { Button: 'button' }, '../design-system/TextInput': { TextInput: 'input' }, '../design-system/Textarea': { Textarea: 'textarea' }, '../design-system/Select': { Select: 'select' }, '../design-system/Checkbox': { Checkbox: 'checkbox' }, '../design-system/Avatar': { Avatar: 'avatar' }, './CharacterCropDialog': { CharacterCropDialog: 'crop-dialog' }, '../appearance/moonweaveAssets': { moonweaveAsset: path => path },
  });
  const card = { name: '合成角色', description: '原稿', personality: '原性格', traits: '原特质', traits_label: '能力', appearance: '外貌', scenario: '背景', first_mes: '开场', mes_example: '示例', alternate_greetings: [], tags: ['合成'], creator: '', character_version: '', creator_notes: '', extensions: { synthetic: true } };
  const props = { presentation, initialCard: card, initialAliases: [], initialRuntime: { model: '', base_url: '', max_tokens: 0 }, onSave: async () => {}, onClose() {}, ...overrides };
  let tree;
  const collect = (element, predicate, result = []) => { if (!element || typeof element !== 'object') return result; if (Array.isArray(element)) { element.forEach(child => collect(child, predicate, result)); return result; } if (predicate(element)) result.push(element); collect(element.props?.children, predicate, result); return result; };
  return { render() { cursor = 0; tree = CharacterEditor(props); }, find(predicate) { const hit = collect(tree, predicate)[0]; assert.ok(hit, 'workspace control exists'); return hit; }, all(predicate) { return collect(tree, predicate); }, props };
}
const change = (h, testid, value) => { h.find(n => n.props?.['data-testid'] === testid).props.onChange({ target: { value } }); h.render(); };
const saveButton = h => h.find(n => n.props?.className === 'character-action-primary' && n.props?.['aria-busy'] !== undefined);
const chapter = (h, id) => { h.find(n => n.props?.href === `#${id}`).props.onClick({ preventDefault() {} }); h.render(); };
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => { resolve=a; reject=b; }); return { promise, resolve, reject }; };

test('wide desktop has eight editable chapters, one title name and a read-only overview', () => {
  const h=authoringWorkspace();h.render();
  const links=h.all(n=>n.type==='a' && n.props?.href?.startsWith('#character-'));
  assert.equal(links.length,8);
  assert.equal(h.all(n=>n.props?.['data-testid']==='card-name').length,1);
  assert.equal(h.all(n=>n.props?.id?.startsWith('related-')).length,0);
  assert.equal(h.all(n=>n.props?.id==='character-affiliation').length,0);
  assert.equal(h.find(n=>n.props?.id==='character-source-world').props.value,'');
  assert.equal(h.find(n=>n.props?.id==='character-related').props['aria-label'],'速览、人设预览与试聊');
});
test('focus mode exposes chapter09 and preserves the shared draft', () => {
  const h=authoringWorkspace();h.render();change(h,'card-traits','保留能力草稿');
  h.find(n=>n.props?.className==='character-focus-toggle').props.onClick();h.render();
  assert.equal(h.all(n=>n.props?.id==='character-related').length,0);
  assert.equal(h.all(n=>n.type==='a' && n.props?.href?.startsWith('#character-')).length,9);
  chapter(h,'character-preview');assert.equal(h.find(n=>n.props?.id==='character-preview').props.hidden,false);
  chapter(h,'character-traits');assert.equal(h.find(n=>n.props?.['data-testid']==='card-traits').props.value,'保留能力草稿');
});
test('all nine chapters retain field values and use one numbered heading', () => {
  const h=authoringWorkspace();h.render();h.find(n=>n.props?.className==='character-focus-toggle').props.onClick();h.render();
  const ids=['character-manuscript','character-appearance-ability','character-traits','character-personality-relationships','character-opening-voice','character-profile','character-prompts','character-runtime','character-preview'];
  for (const [i,id] of ids.entries()) { chapter(h,id);assert.equal(h.find(n=>n.props?.id===id).props.hidden,false);assert.equal(h.find(n=>n.props?.className?.startsWith('character-panel-heading')).props.children[0].props.children,String(i+1).padStart(2,'0')); }
  chapter(h,'character-appearance-ability');change(h,'card-appearance','切节保留外貌');chapter(h,'character-profile');chapter(h,'character-appearance-ability');assert.equal(h.find(n=>n.props?.['data-testid']==='card-appearance').props.value,'切节保留外貌');
});
test('dialog contains nine tabs and exactly one visible chapter, hidden host fields are removed', () => {
  const h=authoringWorkspace('dialog',{hideRuntime:true,hideSourceWorld:true});h.render();
  const strip=h.find(n=>n.props?.['aria-label']==='角色手稿章节');
  assert.equal(strip.props.children.length,8);
  assert.equal(h.all(n=>n.props?.id==='character-source-world' || n.props?.id==='character-runtime').length,0);
  assert.equal(h.all(n=>n.props?.className?.startsWith('character-editor-chapter') && !n.props.hidden).length,1);
  h.find(n=>n.type==='tabs' && n.props?.value==='character-manuscript').props.onValueChange('character-preview');h.render();
  assert.equal(h.find(n=>n.props?.id==='character-preview').props.hidden,false);
});
test('missing required name flags the title input and never moves to another chapter', async()=>{
  const h=authoringWorkspace();h.render();change(h,'card-name','');await saveButton(h).props.onClick();h.render();
  assert.equal(h.find(n=>n.props?.['data-testid']==='card-name').props['aria-invalid'],true);
  assert.equal(h.find(n=>n.props?.id==='character-manuscript').props.hidden,false);
});
test('successful save only advances the click-time baseline and tells its host about later edits',async()=>{
  const request=deferred();let submitted,finish;
  const h=authoringWorkspace('dialog',{onSave:async payload=>{submitted=payload;await request.promise;},onSaveSucceeded:(...args)=>{finish=args;}});h.render();
  change(h,'card-name','提交名字');const pending=saveButton(h).props.onClick();h.render();change(h,'card-name','提交后继续修改');request.resolve();await pending;h.render();
  assert.equal(submitted.card.name,'提交名字');assert.deepEqual(finish,[false,true]);assert.equal(h.find(n=>n.props?.['data-testid']==='card-name').props.value,'提交后继续修改');assert.equal(h.find(n=>n.props?.className==='character-editor-save-state').props['data-save-state'],'dirty');
});
test('save retry keeps all fields and extensions after a revision conflict',async()=>{
  let attempts=0;const receipts=[];
  const h=authoringWorkspace('page',{onSave:async payload=>{receipts.push(payload);if(++attempts===1)throw new Error('合成版本冲突');}});h.render();change(h,'card-description','失败仍保留');await saveButton(h).props.onClick();h.render();
  assert.equal(h.find(n=>n.props?.['data-testid']==='card-description').props.value,'失败仍保留');assert.equal(h.find(n=>n.props?.className==='character-editor-save-state').props['data-save-state'],'error');
  await h.find(n=>n.props?.children==='重试保存').props.onClick();h.render();assert.deepEqual(receipts[0],receipts[1]);assert.deepEqual(receipts[1].card.extensions,{synthetic:true});
});
test('double save dispatches one command while it is pending',async()=>{
  const request=deferred();let count=0;const h=authoringWorkspace('page',{onSave:async()=>{count++;await request.promise;}});h.render();const button=saveButton(h);const pending=button.props.onClick();await button.props.onClick();assert.equal(count,1);request.resolve();await pending;
});
test('saving an external draft never clears subsequent typing',async()=>{
  const request=deferred();let submitted;const h=authoringWorkspace('dialog',{onSaveDraft:async payload=>{submitted=payload;await request.promise;}});h.render();change(h,'card-name','草稿提交');const pending=h.find(n=>n.props?.children==='保存草稿').props.onClick();h.render();change(h,'card-name','草稿之后输入');request.resolve();await pending;await Promise.resolve();h.render();assert.equal(submitted.card.name,'草稿提交');assert.equal(h.find(n=>n.props?.className==='character-editor-save-state').props['data-save-state'],'dirty');
});
test('AI request cancellation prevents a late candidate from installing',async()=>{
  const request=deferred();const h=authoringWorkspace('page',{services:{edit:async()=>request.promise}});h.render();h.find(n=>n.props?.children==='AI 改写' && n.props?.variant==='secondary').props.onClick();h.render();h.find(n=>n.props?.['aria-label']==='AI 改写指令').props.onChange({target:{value:'简洁'}});h.render();const pending=h.find(n=>n.props?.children==='确认').props.onClick();h.render();h.find(n=>n.props?.children==='取消' && n.type==='button').props.onClick();h.render();request.resolve({value:'迟到候选'});await pending;h.render();assert.equal(h.all(n=>n.props?.['aria-label']==='编辑 AI 候选').length,0);assert.equal(h.find(n=>n.props?.['data-testid']==='card-description').props.value,'原稿');
});
test('a candidate cannot overwrite a field changed while the request was running',async()=>{
  const request=deferred();const h=authoringWorkspace('page',{services:{edit:async()=>request.promise}});h.render();h.find(n=>n.props?.children==='AI 改写' && n.props?.variant==='secondary').props.onClick();h.render();h.find(n=>n.props?.['aria-label']==='AI 改写指令').props.onChange({target:{value:'简洁'}});h.render();const pending=h.find(n=>n.props?.children==='确认').props.onClick();h.render();change(h,'card-description','保留人工修改');request.resolve({value:'旧候选'});await pending;h.render();assert.equal(h.find(n=>n.props?.children==='采用').props.disabled,true);assert.equal(h.find(n=>n.props?.['data-testid']==='card-description').props.value,'保留人工修改');
});
test('copy save preserves the original unsaved draft and returns copy semantics',async()=>{
  let submitted;const h=authoringWorkspace('page',{characterId:'synthetic-feedback',onSave:async payload=>{submitted=payload;}});h.render();change(h,'card-name','原角色未保存');await h.find(n=>n.type==='menu').props.items.find(i=>i.id==='copy').onSelect();await Promise.resolve();h.render();assert.equal(submitted.asCopy,true);assert.equal(h.find(n=>n.props?.className==='character-editor-save-state').props['data-save-state'],'dirty');
});
test('dialog creation with later edits retains its committed identity for the next save',async()=>{
  const {props,events}=controller(undefined,'dialog');await props.onSave(payload);props.onSaveSucceeded(false,true);assert.equal(events.some(row=>row[0]==='close'),false);await props.onSave({...payload,card:{...payload.card,name:'已提交后继续修改'}});props.onSaveSucceeded(false,false);assert.equal(events.filter(row=>row[0]==='create').length,1);assert.equal(events.filter(row=>row[0]==='created').length,1);assert.equal(events.filter(row=>row[0]==='close').length,1);
});
test('retained creation can clear aliases and runtime overrides on its second save',async()=>{
  const {props,events}=controller(undefined,'dialog');
  await props.onSave(payload);props.onSaveSucceeded(false,true);
  await props.onSave({...payload,aliases:[],runtime:{model:'',base_url:'',max_tokens:0}});
  const updates=events.filter(row=>row[0]==='update');
  assert.deepEqual(updates.at(-1)[2].aliases,[]);
  assert.equal(updates.at(-1)[2].model,'');assert.equal(updates.at(-1)[2].base_url,'');
  assert.equal(events.filter(row=>row[0]==='create').length,1);
});

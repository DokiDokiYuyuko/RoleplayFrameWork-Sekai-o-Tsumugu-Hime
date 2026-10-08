/** Isolated production-UI acceptance. Requires Playwright + an installed browser.
 * UI_ACCEPTANCE_OUT must point outside the source checkout. No real server/data is read.
 * Optional PLAYWRIGHT_MODULE and UI_BROWSER_CHANNEL select the local browser runtime.
 */
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';
import { readFile, readdir, mkdir, writeFile } from 'node:fs/promises';
import { resolve, extname, relative, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { conversationFixture, checkConversationUI } from './conversationAcceptance.mjs';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const here = dirname(fileURLToPath(import.meta.url));
const dist = resolve(process.env.UI_ACCEPTANCE_DIST || resolve(here, '../dist')), sourceRoot = resolve(here, '../../..');
assert.ok(process.env.UI_ACCEPTANCE_OUT, 'Set UI_ACCEPTANCE_OUT outside the source checkout');
const output = resolve(process.env.UI_ACCEPTANCE_OUT);
assert.ok(relative(sourceRoot, output).startsWith('..'), 'Screenshots must stay outside the source checkout');
await mkdir(output, { recursive: true });
const stamp = '2026-01-01T12:00:00Z';
const generation = { temperature: null, top_p: null, frequency_penalty: null, presence_penalty: null, max_output_tokens: null };
const appearance = { theme_id: 'astral', typography_id: 'mincho', decoration_id: 'celestial', cursor_id: 'star', trail_id: 'iridescent', effect_intensity: .65, density: 'comfortable', bubble_style_id: 'star-track' };
const requirementsOne = process.env.UI_ACCEPTANCE_ONLY_REQUIREMENTS_1 === '1';
const roundThree = process.env.UI_ACCEPTANCE_ONLY_ROUND3 === '1';
const productionDesign = process.env.UI_ACCEPTANCE_ONLY_PRODUCTION_DESIGN === '1' || requirementsOne || roundThree;
if (productionDesign) {
  assert.ok(process.env.UI_ACCEPTANCE_DIST, 'Production acceptance requires UI_ACCEPTANCE_DIST frozen outside checkout');
  assert.ok(relative(sourceRoot, dist).startsWith('..'), 'Production acceptance must use an external frozen dist');
}
const designFixture = { failCharacterSave: false, failSettingsSave: false, failSceneSave: false, uploads: [], characterWrites: [], settingsWrites: [], sceneWrites: [], sceneCommits: 0, chatWrites: [] };
const designSceneReceipts = new Map();
if (productionDesign) Object.assign(appearance, { theme_id: 'iris-light', visual_style_id: 'picturebook', typography_id: 'picturebook', decoration_id: 'rich', cursor_id: 'system', trail_id: 'none', reading_width: 'wide', reading_font_size: 18 });
const settings = { engine: 'openrouter', thinking: 'off', gateway: 'https://example.invalid/api/v1', model: 'example/model', auxiliary_model: 'example/model', model_provider: '', auxiliary_provider: '', provider_allow_fallbacks: true, appearance, generation,
  response_styles: [], response_styles_revision: 0, gateway_profiles: {}, break_armor_prompts: [], active_break_armor_prompt_id: null, break_armor_prompt: '', break_armor_mode: 'opening', break_armor_interval: 10, context_limit_override: null, hygiene_enabled: false,
  tts: { enabled: false }, info: { configured_providers: [], configured: false, model_catalog: null, context_limit: null } };
const characters = Array.from({ length: 12 }, (_, n) => ({ id: `char-demo${n}`, revision: 1, created_at: stamp, updated_at: `2026-01-01T12:00:${String(12 - n).padStart(2, '0')}Z`, aliases: [], lorebook_uids: [], enabled: true, present: true, in_scene: true, muted: false, talkativeness: .5, interject: false, follow_up: false, memory_enabled: true, llm: { model: 'example/model' },
  card: { name: ['向导', '守卫', '旅人'][n % 3] + (n + 1), description: '用于隔离页面验收的虚构角色。', personality: '沉着细心', appearance: '', traits: '', traits_label: '能力与特质', scenario: '', first_mes: '', mes_example: '', system_prompt: '', post_history_instructions: '', tags: ['测试'], alternate_greetings: [], extensions: {} } }));
// Rich and sparse cards exercise real information density without private data.
Object.assign(characters[0].card, {
  description: '用于隔离页面验收的虚构角色。向导负责记录沿途的道路、天气和旧地图上的符号，出发前会和同行者核对路线。她熟悉海岸的潮汐与山间的岔路，遇到不确定的标记会停下来观察，再把发现记进随身手册。'.repeat(3),
  personality: '沉着细心，愿意倾听同行者的意见；面对陌生路线时先观察再行动。',
  traits_label: '专长', traits: '辨认星象与潮汐，绘制地图，整理旅途中的线索和口述记录。',
  appearance: '浅色披风与耐磨长靴，腰侧挂着地图筒和一盏小灯。',
  scenario: '受托带领旅行者前往海岸灯塔，寻找一封没有署名的来信。',
});
characters[1].card.personality = '';
if(requirementsOne) characters[5].card.name='用于检查头像与姓名布局不会相互遮挡的超长合成角色名称：星灯地图记录者与潮汐守望者';
function storyMessage(id, actor, content, seq, kind = 'roleplay') { return { id, actor, content, seq, kind, session_id: 'sess-demo1', turn: Math.ceil(seq / 2), status: 'final', variants: [], active_variant: null, visible_to: 'all', created_at: stamp, fingerprint: id, edited: false }; }
const storyMessages = [storyMessage('msg-demo1', 'char-demo0', '地图上标着两条道路。我们可以沿河岸前行，也可以先去灯塔看看。', 1), storyMessage('msg-demo2', 'player', '先到灯塔。\n路上留意地图上的旧标记。', 2), storyMessage('msg-demo3', 'player', '希望今天能找到那条路。', 3, 'inner'), storyMessage('msg-demo4', 'player', '远处的灯火渐渐亮起。', 4, 'scene'), storyMessage('msg-demo5', 'char-demo1', '我会走在前面。\n\n灯塔附近有一段狭窄石阶，下过雨后会有些湿滑。我们先确认脚下的路，再继续前进。', 5)];
const snapshot = { meta: { id: 'sess-demo1', story_id: 'sess-demo1', title: '星灯之路', branch_name: '沿河前行', character_ids: characters.slice(0, 2).map((c) => c.id), branch_revision: 1, created_at: stamp, player_persona: '', options_enabled: false, streaming_enabled: true }, characters: characters.slice(0, 2), messages: storyMessages, lorebooks: [], groups: [], scenes: [], active_scene_id: null, pinned_facts: [], player_identities: [], player_people: {}, event_cursor: null };
if (productionDesign) {
  snapshot.active_scene_id = 'scene-design';
  snapshot.scenes = [{ id: 'scene-design', title: '灯塔前庭', description: '合成场景，不改变角色或故事正文。', builtin_image_id: null, turn_start: 1, turn_end: null, member_ids: characters.slice(0, 2).map(c => c.id), group_ids: [], created_at: stamp }];
}
const memories = [{ id: 'memory-demo', character_id: 'char-demo0', content: '已确认的灯塔路线', revision: 1, turn_start: 1, turn_end: 3, category: 'experience', important: false, source_message_ids: ['msg-demo1'], created_at: stamp }];
const memoryAttempts = [], memoryReceipts = new Map(); let memoryCommits = 0;
const trash = [{ story_id: 'trash-demo', title: '合成回收故事', generation_id: 'trash-generation-1', branch_count: 1, save_count: 0, deleted_at: stamp }];
const purgeAttempts = []; let purgeCleanupReady = false;
const sessions = [{ ...snapshot.meta, turn: 3, character_names: ['向导1', '守卫2'] }];
const stories = Array.from({ length: 8 }, (_, n) => ({ story_id: `sess-demo${n + 1}`, title: ['星灯之路', '云海来信', '月下书馆', '远方的钟声'][n % 4] + (n ? ` ${n + 1}` : ''), latest_branch_id: n ? `sess-demo${n + 1}` : 'sess-demo1', branch_count: 1, event_count: 0, save_count: 0, updated_at: stamp, created_at: stamp, character_ids: characters.slice(0, 2).map((c) => c.id) }));
const worlds = Array.from({ length: 8 }, (_, n) => ({ id: `world-demo${n}`, title: `星海档案 ${n + 1}`, description: '虚构的世界概览，用来检查列表列数与布局。', revision: 1, created_at: stamp, updated_at: stamp, archive_count: 2, background_count: 1, biology_count: 1, lorebook_ids: [], tags: [] }));
if (productionDesign) {
  const catalog = JSON.parse(await readFile(resolve(here, '../src/appearance/moonweaveCatalog.json'), 'utf8'));
  worlds.forEach((world, index) => Object.assign(world, { lorebook_count: 1, cover_id: index === 7 ? null : `moonweave-${catalog.covers[index % catalog.covers.length].id}` }));
}
const designBooks = [{ id: 'book-design', name: '灯塔与潮汐', description: '独立验收的合成世界书', revision: 1, created_at: stamp, updated_at: stamp, tags: ['合成'], scan_depth: 4, token_budget: 1000, recursive_scanning: false, entries: Array.from({ length: 14 }, (_, n) => ({ uid: n + 1, keys: [`灯塔${n + 1}`], secondary_keys: [], content: '仅用于隔离验收的虚构档案。', comment: `沿岸记录 ${n + 1}`, enabled: true, constant: false, selective: false, selective_logic: 0, order: n, anchor: 'near', depth: 4, probability: 100, extensions: {} })) }];
const plainMessage = (id, role, content) => ({ id, role, content, created_at: stamp, variants: [], active_variant: null, usage: {}, variant_usages: [] });
function plainChat(id) { return { id, created_at: stamp, updated_at: stamp, title: id === 'chat-demo1' ? '地图与旅途' : '另一段聊天', gateway: settings.gateway, model: settings.model, provider: '', provider_allow_fallbacks: true, system_prompt: '', generation, messages: [plainMessage(`${id}-user`, 'user', '请解释怎样读这张地图。'), plainMessage(`${id}-assistant`, 'assistant', '先找到方向与比例尺，再确认当前位置。\n\n地图上的蓝线表示河流，细线表示道路。你可以先标记目的地，再按路口逐段确认行进方向。\n\n长段文字应该保持舒适的阅读宽度，操作按钮始终在正文之外。')], generations: [], input_price_per_million: null, output_price_per_million: null, price_currency: 'USD' }; }
const chats = { 'chat-demo1': plainChat('chat-demo1'), 'chat-demo2': plainChat('chat-demo2') };
chats['chat-demo1'].messages[1].variants = [chats['chat-demo1'].messages[1].content, '另一个用于验收的候选回复。'];
chats['chat-demo1'].messages[1].active_variant = 0;
const jobs = Array.from({ length: 14 }, (_, n) => ({ id: `job-demo${n}`, title: `资料整理 ${n + 1}：把原稿中的人物与场景转换为可编辑素材`, status: n % 3 ? 'saved' : 'needs_review', updated_at: stamp }));
const reuseDraft={id:'draft-reuse',kind:'character',title:'合成角色审阅',payload:structuredClone(characters[0].card),aliases:[],status:'needs_review',revision:1,error:'',warnings:[],evidence:'仅供共享入口合成验收。',source_start:null,source_end:null,schema_fingerprint:'synthetic',world_id:null,target_asset_id:null,target_revision:null,saved_asset_id:null,source_refs:[],field_history:[]};
const reuseImport={id:'job-reuse',title:'合成导入复用验收',status:'needs_review',target_kind:'character',world_id:null,source_length:16,candidates:[],drafts:[structuredClone(reuseDraft)],errors:[],created_at:stamp,updated_at:stamp};
const reuseInspiration={id:'inspiration-reuse',requirement:'合成灵感复用验收',status:'review',stage:'审阅',drafts:[structuredClone(reuseDraft)],errors:[],attempt:1,brief_revision:1,agent_enabled:false,agent_messages:[],references:[],created_at:stamp,updated_at:stamp};
const streams = new Map(), allSockets = new Set(), metrics = { maxStreams: 0, abortedReads: 0, reads: {} }, results = [], errors = [];
const media = new Map();
// Synthetic aspect-ratio fixtures, never copied from a user's character artwork.
function syntheticPortrait(index) {
  const dimensions = [[320, 480], [400, 400], [560, 320]][index % 3];
  const [width, height] = dimensions;
  const [paper, ink, accent] = [['#eddde6', '#4d3655', '#b77896'], ['#d7e9eb', '#2a4857', '#679baa'], ['#eee2ce', '#4a435c', '#b99a68']][index % 3];
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 320 480" preserveAspectRatio="xMidYMid meet"><rect width="320" height="480" fill="${paper}"/><path d="M28 418V136a132 132 0 0 1 264 0v282" fill="none" stroke="${accent}" stroke-width="2"/><circle cx="160" cy="170" r="84" fill="${accent}" opacity=".22"/><path d="M70 407q10-124 90-124t90 124" fill="${ink}"/><path d="M99 190q-5-99 61-99t61 99l-14 79H113Z" fill="${ink}"/><ellipse cx="160" cy="207" rx="43" ry="57" fill="${paper}"/><path d="M116 186q-2-72 44-72 45 0 46 72-31-13-38-43-13 30-52 43" fill="${ink}"/><path d="m124 291 36 53 36-53M160 344v64" fill="none" stroke="${accent}" stroke-width="3"/><path d="m160 367 10 13-10 13-10-13Z" fill="${accent}"/><text x="160" y="449" text-anchor="middle" fill="${ink}" font-family="sans-serif" font-size="15">合成比例样图 · ${index + 1}</text></svg>`;
}
const testImage = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAFAAAACgCAIAAAAU4XzjAAABD0lEQVR4nOXOQQEAIBCAMCSuIQxlQlucD5Zga59LicRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjMRIjL8D0x4HFALaN6VcSwAAAABJRU5ErkJggg==', 'base64');
let simpleFailure = false, storyFailure = false, slowChat = false, hangChat = false, writingFailure = false;
const writingRequests = [];
let setupFailure = false, setupConflict = false; const setupWrites = []; const setupOperations = []; const setupReceipts = new Map(); let setupCommits = 0;
const creationOperations = [], creationReceipts = new Map(), importOperations = [], importReceipts = new Map();
let creationCommits = 0, importCommits = 0;
let creationIncomplete = false; const incompleteCreationIds = new Set();
function json(res, body, code = 200, headers = {}) { if (res.destroyed) return; res.writeHead(code, { 'content-type': 'application/json', ...headers }); res.end(JSON.stringify(body)); }
function emit(key, type, data) { for (const res of streams.get(key) ?? []) res.write(`event: ${type}\ndata: ${JSON.stringify(data)}\n\n`); }
const conversation = conversationFixture(snapshot, characters, json, emit);
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.woff2': 'font/woff2' };
// This fixture is deliberately opt-in; it cannot change any historical acceptance route.
const roundThreeFixture = roundThree ? (await import('./roundThreeFixture.mjs')).roundThreeFixture({ characters, worlds, designBooks, snapshot, stamp, json }) : null;
const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://localhost'), path = url.pathname;
    if (path.endsWith('/events') && path.startsWith('/api/')) {
      res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' }); res.write(': connected\n\n');
      const key = path.includes('simple-chats') ? path.split('/')[4] : 'sess-demo1';
      if (!streams.has(key)) streams.set(key, new Set()); streams.get(key).add(res);
      metrics.maxStreams = Math.max(metrics.maxStreams, [...streams.values()].reduce((n, set) => n + set.size, 0));
      const timer = setInterval(() => { if (!res.destroyed) res.write(': heartbeat\n\n'); }, 1000);
      res.on('close', () => { clearInterval(timer); streams.get(key).delete(res); }); return;
    }
    if (path.startsWith('/api/')) {
      if (req.method === 'GET') metrics.reads[path] = (metrics.reads[path] ?? 0) + 1;
      if (roundThreeFixture && await roundThreeFixture.route(req, res, path, url)) return;
      if (await conversation.route(req, res, path)) return;
      if(requirementsOne) {
        if(path==='/api/v1/card-sources') return json(res,[]);
        if(path==='/api/v1/card-inspiration-jobs') return json(res,[reuseInspiration]);
        if(path==='/api/v1/card-inspiration-jobs/inspiration-reuse') return json(res,reuseInspiration);
        if(path==='/api/v1/asset-import-jobs/job-reuse') return json(res,reuseImport);
        if(path==='/api/v1/asset-import-jobs/job-reuse/source') return json(res,{source:'仅供共享入口合成验收。'});
      }
      if (productionDesign && path === '/api/v1/sessions/sess-demo1/scenes') return json(res, snapshot.scenes);
      if (productionDesign && path === '/api/v1/sessions/sess-demo1/scenes/scene-design/image' && req.method === 'PATCH') {
        let raw = ''; for await (const chunk of req) raw += chunk; const body = JSON.parse(raw), id = req.headers['x-operation-id'];
        assert.ok(id, 'Scene image write requires stable command identity');
        designFixture.sceneWrites.push({ id, body: structuredClone(body) });
        const previous = designSceneReceipts.get(id);
        if (previous) { assert.deepEqual(body, previous.body); return json(res, previous.result, 200, previous.headers); }
        if (designFixture.failSceneSave) return json(res, { detail: '合成场景图保存失败' }, 503);
        assert.equal(body.expected_branch_revision, snapshot.meta.branch_revision);
        snapshot.scenes[0].builtin_image_id = body.builtin_image_id; snapshot.meta.branch_revision++; designFixture.sceneCommits++;
        const result = structuredClone(snapshot.scenes[0]), headers = { 'X-Command-ID': id, 'X-Branch-Revision': String(snapshot.meta.branch_revision) };
        designSceneReceipts.set(id, { body, result, headers }); return json(res, result, 200, headers);
      }
      if (path === '/api/v1/settings') {
        if (req.method === 'PATCH') { let raw = ''; for await (const chunk of req) raw += chunk; const body = JSON.parse(raw); if (productionDesign) { designFixture.settingsWrites.push(structuredClone(body)); if (designFixture.failSettingsSave) return json(res, { detail: '合成偏好保存失败' }, 503); } Object.assign(appearance, body.appearance ?? {}); }
        return json(res, settings);
      }
      if (/\/settings\/(models|providers)/.test(path)) return json(res, { models: [], providers: [], context_length: null });
      if (path === '/api/v1/lan/status') return json(res, { enabled: false, paired: true, is_local: true });
      if (path === '/api/v1/characters') return json(res, characters);
      if (path === '/api/v1/characters/persona-preview') return json(res, { persona: 'Synthetic preview', tokens: 8 });
      if (path === '/api/v1/characters/import' && req.method === 'POST') {
        const chunks = []; for await (const chunk of req) chunks.push(chunk);
        const form = await new Response(Buffer.concat(chunks), { headers: { 'content-type': req.headers['content-type'] } }).formData();
        const document = JSON.parse(await form.get('file').text());
        const card = document.data ?? document;
        assert.ok(card.name?.trim(), 'Synthetic character creation requires a name');
        const character = { ...structuredClone(characters[0]), id: `char-created-${characters.length}`, revision: 1,
          aliases: [], source_world_id: form.get('source_world_id') || null, authoring_source: null,
          llm: { model: 'example/model', inherit_model: true, inherit_base_url: true, sampling: { max_tokens: 0 } },
          card: { ...card, avatar_path: null } };
        characters.push(character);
        return json(res, character);
      }
      const characterPath = path.match(/^\/api\/v1\/characters\/([^/]+)(?:\/(avatar|full-body))?$/);
      if (characterPath) {
        const char = characters.find((c) => c.id === characterPath[1]);
        if (!char) return json(res, {}, 404);
        if (characterPath[2]) {
          if (req.method === 'POST') {
            {
              const chunks = []; for await (const chunk of req) chunks.push(chunk);
              const form = await new Response(Buffer.concat(chunks), { headers: { 'content-type': req.headers['content-type'] } }).formData();
              const expected = form.get('expected_revision');
              if (expected && Number(expected) !== char.revision) return json(res, { detail: 'Synthetic revision conflict' }, 409);
              const file = form.get('file'), bytes = Buffer.from(await file.arrayBuffer());
              assert.equal(bytes.subarray(1, 4).toString(), 'PNG');
              designFixture.uploads.push({ kind: characterPath[2], width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20), size: bytes.length });
              media.set(path, bytes);
              if (characterPath[2] === 'avatar') { char.revision++; char.card.avatar_path = `${char.id}/avatar.png`; }
              return json(res, { revision: char.revision, avatar_path: char.card.avatar_path ?? null });
            }
          }
          if (!media.has(path)) {
            const fixture = char.id.match(/^char-demo(\d+)$/);
            if(requirementsOne&&fixture) {
              const index=Number(fixture[1]), state=index%6;
              if(state===4) { res.writeHead(200,{'content-type':'image/svg+xml'}); res.end('<invalid-synthetic-image>'); return; }
              const exists=characterPath[2]==='avatar'?[0,1,5].includes(state):[0,2,5].includes(state);
              if(!exists) return json(res,{},404);
              const art=syntheticPortrait(index+(characterPath[2]==='full-body'?10:0)).replace('合成比例样图',characterPath[2]==='full-body'?'合成立绘 BODY':'合成头像 AVATAR');
              res.writeHead(200,{'content-type':'image/svg+xml'}); res.end(art); return;
            }
            if (characterPath[2] === 'avatar' && fixture && Number(fixture[1]) % 4 !== 3) {
              res.writeHead(200, { 'content-type': 'image/svg+xml' }); res.end(syntheticPortrait(Number(fixture[1]))); return;
            }
            return json(res, {}, 404);
          }
          res.writeHead(200, { 'content-type': 'image/png' }); res.end(media.get(path)); return;
        }
        if (req.method === 'PATCH') {
          let raw = ''; for await (const chunk of req) raw += chunk; const body = JSON.parse(raw);
          if (productionDesign) { designFixture.characterWrites.push(structuredClone(body)); if (designFixture.failCharacterSave) return json(res, { detail: '合成保存失败：原稿未提交' }, 503); }
          if (body.expected_revision !== char.revision) return json(res, { detail: 'Synthetic revision conflict' }, 409);
          char.card = { ...char.card, ...body.card, avatar_path: char.card.avatar_path }; char.revision++;
          for (const key of ['aliases', 'source_world_id', 'authoring_source', 'llm']) if (key in body) char[key] = body[key];
        }
        return json(res, char);
      }

      if (path === '/api/v1/sessions/sess-demo1/memory/records') return json(res, { records: memories });
      if (path === '/api/v1/sessions/sess-demo1/memory/windows') return json(res, { windows: [] });
      if (path === '/api/v1/characters/char-demo0/memories/memory-demo') {
        if (req.method === 'PATCH') {
          const id = req.headers['x-operation-id']; assert.ok(id); assert.ok(req.headers['content-type'].startsWith('application/json'));
          let raw = ''; for await (const chunk of req) raw += chunk; const body = JSON.parse(raw); memoryAttempts.push({ id, body, query: url.search });
          const previous = memoryReceipts.get(id); if (previous) { assert.deepEqual(body, previous.body); return json(res, previous.result, 200, previous.headers); }
          assert.equal(body.expected_revision, memories[0].revision); assert.equal(body.expected_branch_revision, snapshot.meta.branch_revision);
          Object.assign(memories[0], body); memories[0].revision++; memoryCommits++;
          const result = structuredClone(memories[0]), headers = { 'X-Command-ID': id, 'X-Branch-Revision': String(snapshot.meta.branch_revision + 1) }; memoryReceipts.set(id, { body, result, headers }); return json(res, result, 200, headers);
        }
      }
      if (path === '/api/v1/stories/trash') return json(res, trash);
      if (path === '/api/v1/stories/trash/trash-demo' && req.method === 'DELETE') { const id = req.headers['x-operation-id']; assert.ok(id); purgeAttempts.push({ id, generation: url.searchParams.get('generation_id') }); if (purgeCleanupReady) return json(res, { ok: true, story_id: 'trash-demo', generation_id: trash[0].generation_id, branch_revision: 2, cleanup_pending: true }, 200, { 'X-Command-ID': id, 'X-Branch-Revision': '2' }); return json(res, { detail: { code: 'trash_generation_conflict', message: '回收故事已改变，请核对后再操作。' } }, 409); }
      if (productionDesign && path.startsWith('/api/v1/lorebooks/')) return json(res, designBooks.find(book => book.id === path.split('/')[4]) ?? {}, 200);
      if (path === '/api/v1/lorebooks') return json(res, productionDesign ? designBooks : []);
      if (path === '/api/v1/prompt-presets') return json(res, []);
      if (productionDesign && path === '/api/v1/world-covers') return json(res, []);
      if (productionDesign && /^\/api\/v1\/worlds\/[^/]+$/.test(path)) {
        const world = worlds.find(world => world.id === path.split('/')[4]);
        if (!world) return json(res, { detail: 'Synthetic world missing' }, 404);
        if (req.method === 'PATCH') {
          let raw = ''; for await (const chunk of req) raw += chunk; const body = JSON.parse(raw);
          if (body.expected_revision !== world.revision) return json(res, { detail: 'Synthetic world revision conflict' }, 409);
          Object.assign(world, body); world.revision++;
        }
        return json(res, { ...world, archived: world.archived ?? false, core_brief: world.core_brief ?? '用于隔离验收的虚构世界规则。', runtime_policy: 'compiled', archive_records: [], lorebook_ids: world.id === 'world-demo0' ? ['book-design'] : [] });
      }
      if (path === '/api/v1/worlds') {
        if (productionDesign && req.method === 'POST') {
          let raw = ''; for await (const chunk of req) raw += chunk; const body = JSON.parse(raw);
          assert.ok(body.title?.trim(), 'Synthetic world title required');
          const world = { ...body, id: `world-created-${worlds.length}`, revision: 1, archived: false, core_brief: body.core_brief ?? '', archive_records: [], lorebook_ids: [], created_at: stamp, updated_at: stamp, runtime_policy: 'compiled' };
          worlds.push({ ...world, background_count: 0, biology_count: 0, lorebook_count: 0 }); return json(res, world);
        }
        return json(res, worlds);
      }
      if (path === '/api/v1/worlds/lorebook-owners') return json(res, {});
      if (path === '/api/v1/stories') return json(res, stories);
      if (path === '/api/v1/stories/backup-status') return json(res, { errors: {} });
      if (path.endsWith('/worldline')) return json(res, { branches: [{ id: 'sess-demo1', title: '沿河前行', name: '沿河前行', branch_name: '沿河前行', parent_branch_id: null, events: [], message_count: 5, updated_at: stamp }], events: [], edges: [], next_cursor: null });
      if (path === '/api/v1/sessions' && req.method === 'POST') {
        assert.ok(req.headers['content-type']?.startsWith('application/json'));
        const chunks = []; for await (const chunk of req) chunks.push(chunk);
        const body = JSON.parse(Buffer.concat(chunks).toString()), id = req.headers['x-operation-id']; assert.ok(id);
        creationOperations.push({ id, body, content_type: req.headers['content-type'] });
        if (creationIncomplete || incompleteCreationIds.has(id)) {
          creationIncomplete = false; incompleteCreationIds.add(id);
          return json(res, { detail: { code: 'operation_incomplete', message: '前次创建未完整提交，请先核对结果。' } }, 409);
        }
        const previous = creationReceipts.get(id);
        if (previous) { assert.deepEqual(body, previous.body); return json(res, previous.reply, 200, previous.headers); }
        creationCommits++;
        const reply = { ...snapshot, meta: { ...snapshot.meta, title: body.title } };
        const headers = { 'X-Command-ID': id, 'X-Branch-Revision': String(snapshot.meta.branch_revision) };
        creationReceipts.set(id, { body, reply, headers }); return json(res, reply, 200, headers);
      }
      if (path === '/api/v1/stories/import' && req.method === 'POST') {
        assert.ok(req.headers['content-type']?.startsWith('multipart/form-data; boundary='));
        const chunks = []; for await (const chunk of req) chunks.push(chunk);
        const form = await new Response(Buffer.concat(chunks), { headers: { 'content-type': req.headers['content-type'] } }).formData();
        const file = form.get('file'), digest = createHash('sha256').update(Buffer.from(await file.arrayBuffer())).digest('hex');
        const id = req.headers['x-operation-id']; assert.ok(id);
        importOperations.push({ id, digest, content_type: req.headers['content-type'], file_name: file.name });
        const previous = importReceipts.get(id);
        if (previous) { assert.equal(digest, previous.digest); return json(res, previous.reply, 200, previous.headers); }
        importCommits++;
        const reply = { story_id: 'sess-demo1', branches: [{ old_id: 'synthetic', new_id: 'sess-demo1' }], saves: [], memory_records: 0, missing_assets: [], credential_fields_redacted: 0, note: 'Synthetic import' };
        const headers = { 'X-Command-ID': id, 'X-Branch-Revision': String(snapshot.meta.branch_revision) };
        importReceipts.set(id, { digest, reply, headers }); return json(res, reply, 200, headers);
      }
      if (path === '/api/v1/sessions') return json(res, sessions);
      if (process.env.UI_ACCEPTANCE_SERVE === '1' && path === '/api/v1/sessions/sess-demo1/messages' && req.method === 'POST') {
        let raw = ''; for await (const chunk of req) raw += chunk; const body = JSON.parse(raw);
        const sequence = Math.max(0, ...snapshot.messages.map(message => message.seq)) + 1;
        const message = storyMessage(body.client_message_id, 'player', body.content, sequence, body.kind ?? 'roleplay');
        const reply = storyMessage(`preview-reply-${sequence}`, 'char-demo0', '【合成预览回复】已收到你的输入。这条回复用于检查气泡与操作，不调用真实模型。', sequence + 1);
        snapshot.messages.push(message, reply); snapshot.meta.branch_revision++;
        return json(res, { messages: [message, reply], errors: [], branch_revision: snapshot.meta.branch_revision });
      }
      if (path === '/api/v1/sessions/sess-demo1/assist/draft' && req.method === 'POST') {
        const chunks = []; for await (const chunk of req) chunks.push(chunk);
        const body = JSON.parse(Buffer.concat(chunks).toString()); writingRequests.push(body);
        await new Promise(done => setTimeout(done, 350));
        if (writingFailure) return json(res, { detail: 'Synthetic writing model offline' }, 502);
        return json(res, { source: 'draft', branch_id: 'sess-demo1', branch_revision: 1,
          player_identity_id: null, context_version: 'synthetic-context', stale: false, warnings: [],
          options: [1,2,3].map(i => ({ title: `路线方案 ${i}`, text: `方案 ${i}：` + '沿河道路提供不同的旅行选择，说明通行条件、代价与限制。'.repeat(8), mention_character_id: null })) });
      }
      if (path === '/api/v1/sessions/sess-demo1/setup') {
        if (setupFailure) { setupFailure = false; return json(res, { detail: 'Synthetic settings unavailable' }, 503); }
        return json(res, { meta: { ...snapshot.meta, lorebook_ids: ['book-bound'] }, characters: snapshot.characters,
          lorebooks: [{ id: 'book-bound', name: '已绑定的合成世界书', entries: [], entry_count: 17, source_format: 'embedded' }] });
      }
      if (path === '/api/v1/sessions/sess-demo1' && req.method === 'PATCH') {
        if (!req.headers['content-type']?.startsWith('application/json')) return json(res, { detail: 'JSON content type required' }, 422);
        const chunks = []; for await (const chunk of req) chunks.push(chunk);
        const body = JSON.parse(Buffer.concat(chunks).toString()); setupWrites.push(body);
        const operationId = req.headers['x-operation-id']; assert.ok(operationId, 'Settings require an operation ID');
        setupOperations.push({ id: operationId, body, content_type: req.headers['content-type'] });
        const previous = setupReceipts.get(operationId);
        if (previous) { assert.deepEqual(body, previous.body); return json(res, previous.reply, 200, previous.headers); }
        if (setupConflict) { setupConflict = false; snapshot.meta.branch_revision++; return json(res, { code: 'branch_revision_conflict', detail: 'Synthetic revision conflict' }, 409); }
        assert.equal(body.expected_branch_revision, snapshot.meta.branch_revision);
        Object.assign(snapshot.meta, body); snapshot.meta.branch_revision++; setupCommits++;
        const reply = { branch_revision: snapshot.meta.branch_revision };
        const headers = { 'X-Command-ID': operationId, 'X-Branch-Revision': String(snapshot.meta.branch_revision) };
        setupReceipts.set(operationId, { body, reply, headers }); return json(res, reply, 200, headers);
      }
      if (path === '/api/v1/sessions/sess-demo1' || path === '/api/v1/sessions/sess-demo1/view') {
        if (storyFailure) { storyFailure = false; return json(res, { detail: '隔离测试：路线读取失败' }, 503); }
        if (path.endsWith('/view')) {
          let messages = snapshot.messages;
          if (process.env.UI_ACCEPTANCE_ONLY_PHASE4 && messages.length > 50) {
            const around = url.searchParams.get('around'), before = Number(url.searchParams.get('before_seq'));
            if (around) { const index = messages.findIndex(message => message.id === around); messages = messages.slice(Math.max(0, index - 25), Math.max(0, index - 25) + 50); }
            else messages = before ? messages.filter(message => message.seq < before).slice(-50) : messages.slice(-50);
          }
          return json(res, { ...snapshot, messages, session: { ...snapshot.meta,
          persona: snapshot.meta.player_persona ?? '', turn: Math.max(0, ...snapshot.messages.map(message => message.turn)),
          pinned_facts: snapshot.pinned_facts, player_identities: snapshot.player_identities, player_people: snapshot.player_people },
          next_before_seq: messages[0]?.seq > 1 ? messages[0].seq : null, latest_seq: Math.max(-1, ...snapshot.messages.map(message => message.seq)) });
        }
        return json(res, snapshot);
      }
      if (path.includes('bookmarks') || path.endsWith('/cost')) return json(res, []);
      if (path === '/api/v1/simple-chats') return json(res, { chats: Object.values(chats).map((chat) => ({ id: chat.id, title: chat.title, model: chat.model, gateway: chat.gateway, updated_at: stamp, message_count: chat.messages.length })) });
      const match = path.match(/^\/api\/v1\/simple-chats\/([^/]+)$/);
      if (match) {
        if (req.method === 'PATCH') { let raw=''; for await(const chunk of req) raw+=chunk; const body=JSON.parse(raw); Object.assign(chats[match[1]],body); if(productionDesign) designFixture.chatWrites.push(structuredClone(body)); }
        if (simpleFailure) { simpleFailure = false; return json(res, { detail: '隔离测试：聊天读取失败' }, 503); }
        if (match[1] === 'chat-demo1' && (slowChat || hangChat)) {
          res.on('close', () => { if (!res.writableEnded) metrics.abortedReads++; });
          if (hangChat) return;
          await new Promise((done) => setTimeout(done, 1600));
        }
        return json(res, chats[match[1]]);
      }
      const send = path.match(/^\/api\/v1\/simple-chats\/([^/]+)\/messages$/);
      if (send && req.method === 'POST') {
        let raw = ''; for await (const chunk of req) raw += chunk; const data = JSON.parse(raw), chat = chats[send[1]];
        chat.messages.push(plainMessage(data.message_id ?? data.client_message_id ?? 'msg-demo-user-new', 'user', data.content));
        const id = 'msg-demo-stream'; emit(chat.id, 'message.pending', { id });
        await new Promise((done) => setTimeout(done, 200)); emit(chat.id, 'message.delta', { id, delta: '这是正在逐段出现的回复。' });
        await new Promise((done) => setTimeout(done, 500)); emit(chat.id, 'message.delta', { id, delta: '继续确认地图上的路线。' });
        await new Promise((done) => setTimeout(done, 1200));
        const msg = plainMessage(id, 'assistant', '这是正在逐段出现的回复。继续确认地图上的路线。'); chat.messages.push(msg); emit(chat.id, 'message.final', { message: msg }); return json(res, chat);
      }
      if (path === '/api/v1/asset-import-jobs') return json(res, jobs);
      return json(res, []);
    }
    const file = resolve(dist, `.${decodeURIComponent(path)}`);
    if (relative(dist, file).startsWith('..')) return json(res, {}, 403);
    const target = extname(file) ? file : resolve(dist, 'index.html');
    try { const bytes = await readFile(target); res.writeHead(200, { 'content-type': mime[extname(target)] ?? 'application/octet-stream' }); res.end(bytes); }
    catch { res.writeHead(404); res.end(); }
  } catch (cause) { errors.push(cause.message); json(res, { detail: 'Isolated fixture failure' }, 500); }
});
server.on('connection', (socket) => { allSockets.add(socket); socket.on('close', () => allSockets.delete(socket)); });
await new Promise((done) => server.listen(0, '127.0.0.1', done));
const base = `http://127.0.0.1:${server.address().port}`;
// A local, in-memory preview uses this exact synthetic server and frozen dist.
// It never proxies an API request to a live application or model provider.
if (process.env.UI_ACCEPTANCE_SERVE === '1') {
  // Manual prototype review also needs the existing in-memory regeneration
  // handlers; otherwise the generic fixture fallback is an empty response.
  if (!productionDesign) {
    conversation.activate();
    Object.assign(appearance, { theme_id: 'iris-light', visual_style_id: 'picturebook', typography_id: 'picturebook', decoration_id: 'none', bubble_style_id: 'plain', cursor_id: 'system', trail_id: 'none' });
  }
  console.log(JSON.stringify({ synthetic_preview: base, pid: process.pid, dist }));
  await new Promise(() => {});
}
const browser = await chromium.launch({ headless: true, channel: process.env.UI_BROWSER_CHANNEL || 'msedge' });
const contexts = [];
async function pageAt(width, height = 900, mobile = false) {
  const context = await browser.newContext({ viewport: { width, height }, isMobile: mobile, hasTouch: mobile, deviceScaleFactor: 1 }); contexts.push(context);
  if (productionDesign) await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.protocol === 'data:' || url.protocol === 'blob:' || url.origin === base) return route.continue();
    errors.push(`Forbidden external request: ${url.origin}`); return route.abort();
  });
  const page = await context.newPage(); page.on('pageerror', (error) => errors.push(error.message)); return page;
}
async function open(page, path, ready) { await page.goto(base + path); try { await page.locator(ready).first().waitFor({ timeout: 20000 }); } catch (cause) { await page.screenshot({ path: resolve(output, 'failure-page.png') }); await writeFile(resolve(output, 'failure-page.txt'), (await page.locator('body').innerText()) + '\n' + (await page.locator('pre').allTextContents()).join('\n')); throw cause; } await page.evaluate(() => document.fonts.ready); }
async function shot(page, name) { await page.screenshot({ path: resolve(output, `${name}.png`), fullPage: false, animations: 'disabled' }); }
async function bounds(page, label) {
  const info = await page.evaluate(() => ({ width: innerWidth, body: document.documentElement.scrollWidth, main: document.querySelector('.tpl-main')?.getBoundingClientRect().toJSON() }));
  assert.ok(info.body <= info.width + 1, `${label}: horizontal page overflow ${JSON.stringify(info)}`); results.push({ check: label, ...info });
}
async function checkSimpleEdit(page) {
  const original = structuredClone(chats['chat-demo1']);
  const assistant = page.locator('.sc-assistant').first();
  await assistant.getByRole('button', { name: '消息操作', exact: true }).click();
  await page.getByRole('menuitem', { name: '编辑', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '编辑聊天消息', exact: true });
  try {
    await dialog.waitFor({ timeout: 5000 });
    // React's textarea initial text affects exact getByLabel matching; its
    // accessible textbox name remains the visible field label.
    assert.ok((await dialog.getByRole('textbox', { name: '消息内容', exact: true }).inputValue()).includes('比例尺'));
    const futureCount = original.messages.length - 2;
    if (futureCount > 0) await dialog.getByText(`保存会移除这条消息之后的 ${futureCount} 条对话。`, { exact: true }).waitFor();
    await dialog.getByText('这条回复的 2 个候选将替换为本次编辑内容。', { exact: true }).waitFor();
    await dialog.getByRole('button', { name: '取消', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.deepEqual(chats['chat-demo1'], original, 'Cancel must preserve messages and all candidates');
  } catch (cause) {
    await shot(page, 'simple-edit-failure');
    await writeFile(resolve(output, 'simple-edit-failure.txt'), await page.locator('body').innerText());
    throw cause;
  }
}
try {
  if (roundThree) {
    const { checkRoundThreeProductionUI } = await import('./roundThreeProductionAcceptance.mjs');
    await checkRoundThreeProductionUI({ pageAt, open, shot, bounds, results, appearance, characters, snapshot, base, output, designFixture, fixture: roundThreeFixture });
  } else if (requirementsOne) {
    const { checkRequirementsOneUI } = await import('./requirementsOneAcceptance.mjs');
    await checkRequirementsOneUI({ pageAt, open, shot, bounds, results, appearance, characters, snapshot, base, output, designFixture, testImage });
  } else if (productionDesign) {
    const { checkProductionDesignUI } = await import('./productionDesignAcceptance.mjs');
    await checkProductionDesignUI({ pageAt, open, shot, bounds, results, appearance, characters, snapshot, base, output, designFixture, testImage });
  } else if (process.env.UI_ACCEPTANCE_ONLY_PICTUREBOOK) {
    const { checkPicturebookUI } = await import('./picturebookAcceptance.mjs');
    await checkPicturebookUI({ pageAt, open, shot, results, appearance, characters, snapshot, base });
  } else if (process.env.UI_ACCEPTANCE_ONLY_KEYBOARD) {
    const phone = await pageAt(360, 844, true);
    await open(phone, '/simple-chats/chat-demo1', '.sc-bubble'); await phone.setViewportSize({ width: 360, height: 460 });
    await phone.waitForFunction(() => parseInt(getComputedStyle(document.documentElement).getPropertyValue('--mrp-visual-height'), 10) <= 460);
    await shot(phone, 'keyboard-evidence');
    const geometry = await phone.evaluate(() => Object.fromEntries(['.app-shell','.tpl-main','.tpl-content','.sc-layout','.sc-main','.sc-messages','.sc-compose'].map(selector => { const el=document.querySelector(selector), rect=el.getBoundingClientRect(), css=getComputedStyle(el); return [selector,{y:rect.y,height:rect.height,bottom:rect.bottom,flex:css.flex,minHeight:css.minHeight,heightCss:css.height,overflow:css.overflow}]; })));
    await writeFile(resolve(output, 'keyboard-geometry.json'), JSON.stringify(geometry, null, 2));
    const send = await phone.getByRole('button', { name: '发送', exact: true }).boundingBox(); assert.ok(send && send.y + send.height <= 460, `Keyboard viewport obscures composer: ${JSON.stringify(send)}`);
    results.push({ check: 'keyboard viewport 460', status: 'passed', geometry });
  } else if (process.env.UI_ACCEPTANCE_ONLY_PHASE4) {
    const page = await pageAt(1280, 900);
    const timings = [];
    for (let i = 0; i < 3; i++) {
      const started = performance.now();
      await open(page, '/library/characters', '.workspace-card-grid');
      timings.push(performance.now() - started);
      assert.ok(timings.at(-1) < 8000, 'Isolated cold route exceeds 8 second budget');
    }
    const count = metrics.reads['/api/v1/characters'];
    assert.equal(count, 3, 'Bootstrap and mounted facade must share exactly one library read per cold page');
    const card = page.getByTestId('character-card').filter({ has: page.getByRole('heading', { name: characters[0].card.name, exact: true }) });
    const editTrigger = card.getByRole('button', { name: '编辑', exact: true }); await editTrigger.click();
    const editor = page.locator('.character-editor-page'); await editor.waitFor();
    const directory = editor.getByRole('navigation', { name: '角色手稿章节', exact: true });
    await directory.getByText('形象与资料', { exact: true }).click();
    const name = editor.getByTestId('card-name'); await name.fill('保留未保存草稿');
    await directory.getByText('人物原稿', { exact: true }).click();
    await directory.locator('a[href="#character-manuscript"]').focus();
    const editorKeyboard = [];
    for (let i = 0; i < 40; i++) {
      await page.keyboard.press('Tab');
      const focus = await page.evaluate(() => { const el = document.activeElement; return { tag: el?.tagName, testid: el?.getAttribute('data-testid'), name: el?.getAttribute('aria-label') ?? el?.textContent?.slice(0, 100), inEditor: Boolean(el?.closest('.character-editor-page')) }; });
      editorKeyboard.push(focus);
      if (focus.testid === 'card-description' && focus.inEditor) break;
    }
    assert.ok(editorKeyboard.some(step => step.tag === 'TEXTAREA' && step.testid === 'card-description' && step.inEditor), 'Tab reaches the actual manuscript field');
    const chapter = editor.getByRole('navigation', { name: '角色手稿章节', exact: true }).locator('a[href="#character-profile"]');
    await chapter.focus(); assert.equal(await chapter.evaluate(el => document.activeElement === el), true, 'Chapter navigation remains keyboard reachable');
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => { const section = document.getElementById('character-profile'); return section && (section === document.activeElement || section.contains(document.activeElement)); }, undefined, { timeout: 5000 });
    assert.equal(await editor.locator('#character-profile').evaluate(el => el === document.activeElement || el.contains(document.activeElement)), true, 'Keyboard chapter navigation focuses its exact section');
    // A standalone page must allow focus to leave its document. Only its
    // confirmation dialog traps focus; Shift+Tab returns to the last editor control.
    if (!editorKeyboard.at(-1).inEditor) {
      await page.keyboard.press('Shift+Tab');
      assert.equal(await editor.evaluate(el => el.contains(document.activeElement)), true, 'Reverse Tab re-enters the editor from the document edge');
    }
    await writeFile(resolve(output, 'editor-keyboard.json'), JSON.stringify(editorKeyboard, null, 2));
    await page.getByRole('button', { name: '返回角色库', exact: true }).click();
    const discard = page.getByRole('dialog', { name: '离开角色手稿？', exact: true }); await discard.waitFor();
    for (let i = 0; i < 8; i++) { await page.keyboard.press('Tab'); assert.equal(await discard.evaluate(el => el.contains(document.activeElement)), true, 'Leave confirmation retains its keyboard focus trap'); }
    await shot(page, 'editor-leave-draft-confirm-desktop');
    await page.keyboard.press('Escape'); await discard.waitFor({ state: 'hidden' });
    assert.equal(await name.inputValue(), '保留未保存草稿');
    assert.equal(await editor.evaluate(el => el.contains(document.activeElement)), true, 'Cancellation returns focus inside editor');
    await page.getByRole('button', { name: '返回角色库', exact: true }).click(); await discard.waitFor(); await discard.getByRole('button', { name: '保留草稿并离开', exact: true }).click(); await editor.waitFor({ state: 'hidden' });
    await page.waitForFunction(() => document.activeElement?.getAttribute('data-character-edit') === 'char-demo0');
    assert.equal(await editTrigger.evaluate(el => document.activeElement === el), true, 'Editor closes back to its card action');
    results.push({ check: 'canonical library Query dedup; independent editor draft survives confirmation Escape/cancel; confirmation focus trap and card focus return', status: 'passed', timings_ms: timings });
    await open(page, '/settings/connection', '.v7-ai-settings-card');
    const model = page.getByRole('combobox', { name: '主模型 · 角色回复', exact: true }); await model.fill('example/player-draft');
    const beforeSettings = metrics.reads['/api/v1/settings'];
    await page.waitForTimeout(16000); settings.model = 'example/remote-refresh';
    await page.evaluate(() => window.dispatchEvent(new Event('visibilitychange')));
    await page.waitForFunction(() => true); await page.waitForTimeout(600);
    assert.ok(metrics.reads['/api/v1/settings'] > beforeSettings, 'Actual active Query background refresh must occur');
    assert.equal(await model.inputValue(), 'example/player-draft'); await shot(page, 'settings-background-refresh-draft');
    results.push({ check: 'active Query background settings refresh preserves edited connection draft', status: 'passed' });
    await open(page, '/stories/sess-demo1/branches/sess-demo1', '.v7-message');
    await page.getByRole('button', { name: '更多故事操作', exact: true }).click();
    await page.getByRole('menuitem').filter({ hasText: '角色记忆' }).click();
    const memory = page.getByRole('dialog', { name: '角色记忆', exact: true }); await memory.waitFor();
    await memory.getByText('已确认的灯塔路线', { exact: true }).waitFor();
    await memory.getByRole('button', { name: '纠正', exact: true }).click(); await memory.getByLabel('记忆内容').fill('核对后的记忆草稿');
    let dropMemory = true;
    await page.route('**/api/v1/characters/char-demo0/memories/memory-demo?*', async route => { const response = await route.fetch(); if (dropMemory) { dropMemory = false; return route.abort('failed'); } return route.fulfill({ response }); });
    await memory.getByRole('button', { name: '保存', exact: true }).click(); await memory.getByRole('alert').filter({ hasText: /fetch|Failed/i }).waitFor();
    assert.equal(await memory.getByLabel('记忆内容').inputValue(), '核对后的记忆草稿');
    await memory.getByRole('button', { name: '保存', exact: true }).click(); await memory.getByLabel('记忆内容').waitFor({ state: 'hidden' });
    assert.equal(memoryCommits, 1); assert.equal(memoryAttempts.length, 2); assert.equal(memoryAttempts[0].id, memoryAttempts[1].id); assert.deepEqual(memoryAttempts[0].body, memoryAttempts[1].body);
    await shot(page, 'memory-edit-replay'); results.push({ check: 'manual memory edit lost response preserves original record/branch CAS, ID and draft; one commit', status: 'passed' });
    await memory.getByRole('button', { name: '关闭角色记忆', exact: true }).click();
    await open(page, '/stories', '.home-story-grid'); await page.getByRole('button', { name: '回收区', exact: true }).click();
    await page.getByRole('button', { name: '永久清除', exact: true }).click();
    const purge = page.getByRole('dialog', { name: '永久清除故事', exact: true }); await purge.waitFor();
    await purge.getByText(/独立历史备份不随之删除/).waitFor();
    trash[0].generation_id = 'trash-generation-2';
    await purge.getByRole('button', { name: '确认删除', exact: true }).click(); await purge.getByRole('alert').waitFor();
    assert.equal(purgeAttempts[0].generation, 'trash-generation-1');
    await shot(page, 'purge-cas-conflict-backups-preserved'); await page.keyboard.press('Escape'); await purge.waitFor({ state: 'hidden' });
    assert.equal(purgeAttempts.length, 1);
    purgeCleanupReady = true; await page.reload(); await page.locator('.home-story-grid').waitFor(); await page.getByRole('button', { name: '回收区', exact: true }).click(); await page.getByRole('button', { name: '永久清除', exact: true }).click(); await purge.waitFor();
    await purge.getByRole('button', { name: '确认删除', exact: true }).click(); await purge.waitFor({ state: 'hidden' });
    await page.getByText('故事变更已保存，后台清理待恢复；独立历史备份仍保留。', { exact: true }).waitFor();
    assert.equal(purgeAttempts.length, 2); assert.equal(purgeAttempts[1].generation, 'trash-generation-2'); assert.notEqual(purgeAttempts[1].id, purgeAttempts[0].id);
    await shot(page, 'purge-cleanup-pending-success');
    results.push({ check: 'purge uses reviewed trash generation; conflict remains visible, cancel does not resubmit, independent backups preserved', status: 'passed' });
    const renderBudgets = [];
    for (const count of [100, 1000]) {
      snapshot.messages = Array.from({ length: count }, (_, index) => storyMessage(`performance-${index + 1}`, index % 2 ? 'player' : 'char-demo0', `合成故事第 ${index + 1} 条消息。` + '保持窗口工作量。'.repeat(5), index + 1));
      const begin = performance.now(); await open(page, '/stories/sess-demo1/branches/sess-demo1', '.v7-message');
      await page.locator(`#message-performance-${count}`).waitFor();
      assert.equal(await page.locator('.v7-message-item').count(), 50);
      await page.getByRole('button', { name: '查看更早消息', exact: true }).click();
      await page.locator(`#message-performance-${count - 99}`).waitFor();
      assert.ok(await page.locator('.v7-message-item').count() <= 100, 'Historical paging must retain the 100-item DOM ceiling');
      await open(page, '/stories/sess-demo1/branches/sess-demo1?message=performance-1', '.v7-message');
      await page.locator('#message-performance-1').waitFor();
      const dom = await page.locator('.v7-message-item').count(); assert.ok(dom <= 100);
      renderBudgets.push({ history_messages: count, dom_messages: dom, navigation_ms: performance.now() - begin, viewport: '1280x900', page_size: 50, max_dom: 100 });
    }
    await shot(page, 'story-1000-history-located-window');
    results.push({ check: 'fixed environment 100/1000-message history: bounded initial window, earlier paging and locate navigation retain 100-item DOM ceiling', status: 'passed', measurements: renderBudgets });
    await writeFile(resolve(output, 'story-render-budget.json'), JSON.stringify(renderBudgets, null, 2));
    await writeFile(resolve(output, 'resource-command-evidence.json'), JSON.stringify({ memoryAttempts, memoryCommits, purgeAttempts, reads: metrics.reads }, null, 2));
  } else if (process.env.UI_ACCEPTANCE_ONLY_ARCHITECTURE) {
    const page = await pageAt(1280, 900);
    await open(page, '/stories/sess-demo1/branches/sess-demo1', '.v7-message');
    await page.getByRole('button', { name: '打开故事列表', exact: true }).click();
    setupFailure = true;
    const trigger = page.getByRole('button', { name: '沿河前行的路线操作', exact: true });
    await trigger.click(); await page.getByRole('menuitem', { name: '编辑设置' }).click();
    const dialog = page.getByRole('dialog', { name: '会话设置', exact: true }); await dialog.waitFor();
    await dialog.getByRole('button', { name: '重新读取设置', exact: true }).waitFor();
    assert.ok(await dialog.getByRole('button', { name: '保存设置', exact: true }).isDisabled());
    assert.equal(setupWrites.length, 0);
    await shot(page, 'setup-error-desktop');
    await dialog.getByRole('button', { name: '重新读取设置', exact: true }).click();
    await dialog.getByText('17 条', { exact: false }).waitFor();
    assert.equal(await page.getByRole('dialog').count(), 1);
    const title = dialog.locator('input').first(); await title.fill('保留当前修改');
    setupConflict = true; await dialog.getByRole('button', { name: '保存设置', exact: true }).click();
    await dialog.getByRole('button', { name: '重新读取最新设置', exact: true }).waitFor();
    assert.equal(await title.inputValue(), '保留当前修改');
    assert.deepEqual(setupWrites[0].lorebook_ids, ['book-bound']);
    assert.equal(setupWrites[0].expected_branch_revision, 1);
    await dialog.locator('.overflow-y-auto').evaluate(element => { element.scrollTop = 0; });
    await shot(page, 'setup-conflict-desktop');
    await dialog.getByRole('button', { name: '重新读取最新设置', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('[role="dialog"] input')?.value === '星灯之路');
    await dialog.locator('.overflow-y-auto').evaluate(element => { element.scrollTop = 0; });
    await shot(page, 'setup-ready-desktop');
    await title.fill('响应丢失后保留的草稿');
    let dropResponse = true;
    await page.route('**/api/v1/sessions/sess-demo1', async route => {
      if (route.request().method() !== 'PATCH') return route.continue();
      const response = await route.fetch();
      if (dropResponse) { dropResponse = false; return route.abort('failed'); }
      return route.fulfill({ response });
    });
    const commitsBefore = setupCommits, requestsBefore = setupOperations.length;
    await dialog.getByRole('button', { name: '保存设置', exact: true }).click();
    await dialog.getByRole('alert').filter({ hasText: /fetch|Failed/i }).waitFor();
    assert.equal(await title.inputValue(), '响应丢失后保留的草稿');
    assert.ok(await dialog.getByRole('button', { name: '保存设置', exact: true }).isEnabled());
    await dialog.locator('.overflow-y-auto').evaluate(element => { element.scrollTop = 0; });
    await shot(page, 'setup-lost-response-draft');
    await dialog.getByRole('button', { name: '保存设置', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.equal(setupCommits - commitsBefore, 1);
    const attempts = setupOperations.slice(requestsBefore);
    assert.equal(attempts.length, 2); assert.equal(attempts[0].id, attempts[1].id);
    assert.deepEqual(attempts[0].body, attempts[1].body);
    assert.equal(attempts[0].body.expected_branch_revision, 2);
    assert.ok(attempts.every(attempt => attempt.content_type.startsWith('application/json')));
    await writeFile(resolve(output, 'setup-operation-replay.json'), JSON.stringify({ attempts, committed_writes: setupCommits - commitsBefore }, null, 2));
    results.push({ check: 'settings response aborted after commit; manual retry preserves draft, ID, original CAS and JSON type; one committed write', status: 'passed' });
    assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), '沿河前行的路线操作');
    await open(page, '/workshop/character', '.v7-lba-workshop-tabs');
    assert.ok(new URL(page.url()).pathname.startsWith('/create'));
    results.push({ check: 'setup error/retry/no empty write/conflict/draft/revision/bound books/focus return/canonical route', status: 'passed' });
    await open(page, '/stories/sess-demo1/branches/sess-demo1', '.v7-message');
    await page.getByRole('button', { name: '打开故事列表', exact: true }).click();
    await page.locator('.v7-sidebar-new').click();
    const createDialog = page.getByRole('dialog', { name: '新建会话', exact: true }); await createDialog.waitFor();
    await createDialog.locator('input').first().fill('创建响应丢失后的草稿');
    await createDialog.getByRole('button').filter({ hasText: characters[0].card.name }).first().click();
    let dropCreation = true;
    await page.route('**/api/v1/sessions', async route => {
      if (route.request().method() !== 'POST') return route.continue();
      const response = await route.fetch();
      if (dropCreation) { dropCreation = false; return route.abort('failed'); }
      return route.fulfill({ response });
    });
    const createButton = createDialog.getByRole('button', { name: '开始对话', exact: true });
    await createButton.click(); await createDialog.getByRole('alert').filter({ hasText: /fetch|Failed/i }).waitFor();
    assert.equal(await createDialog.locator('input').first().inputValue(), '创建响应丢失后的草稿');
    await shot(page, 'creation-lost-response-draft');
    await createButton.click(); await createDialog.waitFor({ state: 'hidden' });
    assert.equal(creationCommits, 1); assert.equal(creationOperations.length, 2);
    assert.equal(creationOperations[0].id, creationOperations[1].id); assert.deepEqual(creationOperations[0].body, creationOperations[1].body);
    results.push({ check: 'story creation response loss preserves draft and one stable operation; one branch creation', status: 'passed' });
    if (!(await page.locator('.v7-sidebar-import').isVisible())) await page.getByRole('button', { name: '打开故事列表', exact: true }).click();
    let dropImport = true;
    await page.route('**/api/v1/stories/import', async route => {
      const response = await route.fetch();
      if (dropImport) { dropImport = false; return route.abort('failed'); }
      return route.fulfill({ response });
    });
    const upload = page.locator('.v7-sidebar-import input[type="file"]');
    await upload.setInputFiles({ name: 'synthetic-first.zip', mimeType: 'application/zip', buffer: Buffer.from('synthetic archive fixture') });
    await page.locator('.v7-session-sidebar').getByText(/fetch|Failed/i).waitFor();
    await shot(page, 'import-lost-response');
    await upload.setInputFiles({ name: 'synthetic-renamed.zip', mimeType: 'application/zip', buffer: Buffer.from('synthetic archive fixture') });
    await page.locator('.v7-session-sidebar').waitFor({ state: 'hidden' });
    assert.equal(importCommits, 1); assert.equal(importOperations.length, 2);
    assert.equal(importOperations[0].id, importOperations[1].id); assert.equal(importOperations[0].digest, importOperations[1].digest);
    assert.equal(importOperations[1].file_name, 'synthetic-first.zip');
    await page.getByRole('button', { name: '打开故事列表', exact: true }).click();
    await shot(page, 'import-replay-completed');
    await writeFile(resolve(output, 'creation-import-replay.json'), JSON.stringify({ creation: { attempts: creationOperations, commits: creationCommits }, import: { attempts: importOperations, commits: importCommits } }, null, 2));
    results.push({ check: 'renamed identical binary import retries original multipart and ID after response loss; one import', status: 'passed' });
    if (!(await page.locator('.v7-sidebar-new').isVisible())) await page.getByRole('button', { name: '打开故事列表', exact: true }).click();
    const operationCount = creationOperations.length;
    creationIncomplete = true;
    await page.locator('.v7-sidebar-new').click();
    const incompleteDialog = page.getByRole('dialog', { name: '新建会话', exact: true }); await incompleteDialog.waitFor();
    await incompleteDialog.locator('input').first().fill('已核对的新尝试');
    await incompleteDialog.getByRole('button').filter({ hasText: characters[0].card.name }).first().click();
    await incompleteDialog.getByRole('button', { name: '开始对话', exact: true }).click();
    await incompleteDialog.getByText('前次创建未完整提交，请先核对结果。', { exact: false }).waitFor();
    await incompleteDialog.getByRole('button', { name: '取消', exact: true }).click();
    const recovery = page.getByRole('button', { name: '已核对结果，准备新尝试', exact: true }); await recovery.waitFor();
    assert.equal(creationOperations.length, operationCount + 1);
    await shot(page, 'incomplete-recovery-desktop');
    await recovery.click();
    const reviewDialog = page.getByRole('dialog', { name: '开始一次新的尝试？', exact: true }); await reviewDialog.waitFor();
    assert.ok(await reviewDialog.getByText(/新建故事/).count());
    await shot(page, 'incomplete-review-confirm-desktop');
    await page.keyboard.press('Escape'); await reviewDialog.waitFor({ state: 'hidden' });
    assert.equal(await recovery.evaluate(button => document.activeElement === button), true);
    assert.equal(creationOperations.length, operationCount + 1);
    await recovery.click(); await reviewDialog.waitFor();
    await reviewDialog.getByRole('button', { name: '取消', exact: true }).click(); await reviewDialog.waitFor({ state: 'hidden' });
    assert.ok(await recovery.isVisible()); assert.equal(creationOperations.length, operationCount + 1);
    await page.setViewportSize({ width: 390, height: 844 }); await shot(page, 'incomplete-recovery-narrow');
    const recoveryBox = await recovery.boundingBox(); assert.ok(recoveryBox.x >= 0 && recoveryBox.x + recoveryBox.width <= 390);
    await recovery.click(); await reviewDialog.waitFor(); await shot(page, 'incomplete-review-confirm-narrow');
    await reviewDialog.getByRole('button', { name: '我已核对，允许新尝试', exact: true }).click();
    await recovery.waitFor({ state: 'hidden' }); assert.equal(creationOperations.length, operationCount + 1, 'review must not submit');
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.locator('.v7-sidebar-new').click();
    const newAttempt = page.getByRole('dialog', { name: '新建会话', exact: true }); await newAttempt.waitFor();
    await newAttempt.locator('input').first().fill('已核对的新尝试');
    await newAttempt.getByRole('button').filter({ hasText: characters[0].card.name }).first().click();
    await newAttempt.getByRole('button', { name: '开始对话', exact: true }).click(); await newAttempt.waitFor({ state: 'hidden' });
    assert.equal(creationOperations.length, operationCount + 2);
    assert.notEqual(creationOperations.at(-1).id, creationOperations.at(-2).id);
    assert.deepEqual(creationOperations.at(-1).body, creationOperations.at(-2).body);
    await writeFile(resolve(output, 'incomplete-review.json'), JSON.stringify({ attempts: creationOperations.slice(operationCount), review_submissions: 0, escape_preserves_identity: true, cancel_preserves_identity: true, focus_return: true }, null, 2));
    results.push({ check: 'incomplete recovery names operation; Escape/cancel retain ID and return focus; explicit review submits nothing; next manual action receives new ID', status: 'passed' });
  } else if (process.env.UI_ACCEPTANCE_ONLY_SIMPLE_EDIT) {
    const page = await pageAt(1920, 1080);
    await open(page, '/simple-chats/chat-demo1', '.sc-bubble');
    await checkSimpleEdit(page);
    results.push({ check: 'simple chat edit fresh-read preview and cancel', status: 'passed' });
  } else {
  if (!process.env.UI_ACCEPTANCE_ONLY_CONVERSATION) {
  for (const width of [1280, 1920, 2560]) {
    const page = await pageAt(width, width === 2560 ? 1440 : 1080);
    for (const [path, ready, name] of [['/stories', '.home-story-grid', 'stories'], ['/library/characters', '.workspace-card-grid', 'characters'], ['/worlds', '.worlds-card-grid', 'worlds'], ['/library/imports', '.asset-import-layout', 'imports'], ['/settings/connection', '.v7-ai-settings-card', 'settings'], ['/simple-chats/chat-demo1', '.sc-bubble', 'chat'], ['/stories/sess-demo1/branches/sess-demo1', '.v7-message', 'story-chat']]) {
      await open(page, path, ready); await bounds(page, `${name}-${width}`); await shot(page, `${name}-${width}`);
      if (['stories', 'imports', 'settings'].includes(name)) {
        const gap = await page.locator(name === 'stories' ? '.story-home' : name === 'imports' ? '.asset-import-page' : '.v7-settings').evaluate((el) => { const main = document.querySelector('.tpl-main').getBoundingClientRect(), box = el.getBoundingClientRect(); return { left: box.left - main.left, right: main.right - box.right }; });
        assert.ok(Math.abs(gap.left) <= 1 && Math.abs(gap.right) <= 1, `${name}: outer container still centered ${JSON.stringify(gap)}`);
      }
    }
    await page.context().close();
  }
  const page = await pageAt(1920, 1080);
  await open(page, '/library/characters', '.workspace-card-grid');
  const editCharacter = async () => {
    await page.getByTestId('character-card').filter({ has: page.getByRole('heading', { name: characters[0].card.name, exact: true }) }).getByRole('button', { name: '编辑', exact: true }).click();
    await page.waitForURL((url) => url.pathname === `/library/characters/${characters[0].id}`);
    const editor = page.locator('.character-editor-page'); await editor.waitFor();
    assert.equal(await page.getByRole('dialog').count(), 0, 'The editor is a routed page, not a dialog');
    return editor;
  };
  let editor = await editCharacter();
  await editor.getByRole('navigation', { name: '角色手稿章节', exact: true }).getByText('形象与资料', { exact: true }).click();
  await editor.getByTestId('card-name').fill('Guide with preserved draft');
  await editor.getByRole('navigation', { name: '角色手稿章节', exact: true }).getByText('形象与资料', { exact: true }).click();
  await editor.getByLabel('选择角色头像文件').setInputFiles({ name: 'avatar.png', mimeType: 'image/png', buffer: testImage });
  await page.getByRole('dialog', { name: '裁剪角色头像', exact: true }).getByRole('button', { name: '保存裁剪', exact: true }).click();
  await page.getByRole('dialog', { name: '裁剪角色头像', exact: true }).waitFor({ state: 'hidden' });
  assert.equal(characters[0].revision, 2, 'Committed avatar upload advances only its library revision before card save');
  assert.equal(await editor.getByTestId('card-name').inputValue(), 'Guide with preserved draft');
  await editor.getByRole('button', { name: '保存', exact: true }).click();
  for (let n = 0; n < 30 && characters[0].card.name !== 'Guide with preserved draft'; n++) await page.waitForTimeout(50);
  assert.equal(characters[0].card.name, 'Guide with preserved draft'); assert.equal(characters[0].revision, 3);
  assert.equal(await editor.getByText(/保存失败/).count(), 0);
  assert.equal(new URL(page.url()).pathname, `/library/characters/${characters[0].id}`, 'A successful save stays on the editor page');
  await editor.getByRole('navigation', { name: '角色手稿章节', exact: true }).getByText('形象与资料', { exact: true }).click();
  await editor.getByLabel('选择全身立绘文件').setInputFiles({ name: 'body.png', mimeType: 'image/png', buffer: testImage });
  await page.getByRole('dialog', { name: '裁剪角色画像', exact: true }).getByRole('button', { name: '保存裁剪', exact: true }).click();
  await editor.getByText('全身立绘已上传', { exact: true }).waitFor();
  assert.equal(characters[0].revision, 3);
  await editor.locator('img[alt$="的全身立绘"]').waitFor();
  assert.ok(await editor.locator('img[alt$="的全身立绘"]').evaluate((el) => el.complete && el.naturalWidth === 720 && el.naturalHeight === 1280));
  await shot(page, 'character-media-upload');
  await editor.getByRole('button', { name: '返回角色库', exact: true }).click();
  await page.waitForURL((url) => url.pathname === '/library/characters'); await editor.waitFor({ state: 'detached' });
  results.push({ check: 'upload avatar, preserve draft, save without conflict and independently upload full body', status: 'passed' });
  await open(page, '/stories/sess-demo1/branches/sess-demo1', '.v7-message');
  const composer = page.locator('textarea[placeholder^="写下你的回应"]');
  await composer.fill('（旁白：我展开地图）请大家看这里。');
  const targetsBefore = await page.getByRole('button', { name: /^对象 ·/ }).textContent();
  await page.getByTestId('draft-assist').click();
  const writing = page.getByRole('dialog', { name: '故事代笔', exact: true }); await writing.waitFor();
  assert.equal(await writing.getByLabel('代笔当前草稿').inputValue(), '（旁白：我展开地图）请大家看这里。');
  await writing.getByLabel('写作要求').fill('请构思三份路线方案，给出通行条件，不回答 NPC 的午餐问题。');
  await writing.getByLabel('代笔输出类型').selectOption('material');
  await writing.getByRole('button', { name: '生成三份方案', exact: true }).click();
  await writing.locator('.writing-proposal').first().waitFor();
  assert.equal(writingRequests.at(-1).output_type, 'material');
  assert.ok(writingRequests.at(-1).draft_text.includes('我展开地图'));
  assert.equal(await writing.locator('.writing-proposal').count(), 3);
  await shot(page, 'writing-desktop');
  await writing.getByRole('button', { name: '替换草稿', exact: true }).first().click();
  await writing.getByRole('button', { name: '关闭代笔' }).click();
  assert.ok((await composer.inputValue()).startsWith('方案 1：'));
  assert.equal(await page.getByRole('button', { name: /^对象 ·/ }).textContent(), targetsBefore);
  await page.getByRole('button', { name: '撤销代笔填入' }).click();
  assert.equal(await composer.inputValue(), '（旁白：我展开地图）请大家看这里。');
  await composer.fill('这段是生成后新写的草稿，必须保留。');
  await page.getByTestId('draft-assist').click(); await writing.waitFor();
  assert.equal(await writing.locator('.writing-proposal').count(), 3);
  await writing.getByRole('button', { name: '替换草稿', exact: true }).first().click();
  await writing.locator('.writing-confirm').waitFor();
  assert.equal(await composer.inputValue(), '这段是生成后新写的草稿，必须保留。');
  await writing.getByRole('button', { name: '保留当前输入' }).click();
  await writing.getByRole('button', { name: '选择并调整', exact: true }).first().click();
  await writing.getByLabel('写作要求').fill('保留选中方案，增加不同的代价。');
  assert.ok(await writing.getByRole('button', { name: '替换草稿', exact: true }).first().isDisabled());
  await writing.getByRole('button', { name: '重新生成三份方案' }).click();
  await writing.getByRole('button', { name: '重新生成三份方案' }).waitFor();
  assert.ok(writingRequests.at(-1).base_candidate.startsWith('方案 1：'));
  await writing.getByRole('button', { name: '追加到草稿', exact: true }).first().click();
  await writing.getByRole('button', { name: '关闭代笔' }).click();
  assert.ok((await composer.inputValue()).startsWith('这段是生成后新写的草稿，必须保留。\n\n方案 1：'));
  await page.getByTestId('draft-assist').click(); await writing.waitFor(); writingFailure = true;
  await writing.getByRole('button', { name: '重新生成三份方案' }).click();
  await writing.getByRole('alert').filter({ hasText: 'Synthetic writing model offline' }).waitFor();
  assert.equal(await writing.getByLabel('写作要求').inputValue(), '保留选中方案，增加不同的代价。');
  await shot(page, 'writing-visible-error'); writingFailure = false;
  await writing.getByRole('button', { name: '关闭代笔' }).click();
  results.push({ check: 'independent writing, full material, adoption, undo, draft conflict, adjustment and visible failure', status: 'passed' });
  await open(page, '/settings/preferences', '.bubble-style-options');
  for (const style of ['star-track', 'book-note', 'crystal', 'moonlight', 'plain']) {
    await page.locator(`.bubble-style-option:has([data-bubble-style="${style}"])`).click();
    await page.waitForFunction((id) => document.documentElement.dataset.bubbleStyle === id, style);
    await page.getByText('外观会自动保存', { exact: true }).waitFor();
    assert.equal(appearance.bubble_style_id, style);
    await page.locator('.bubble-style-preview').scrollIntoViewIfNeeded(); await shot(page, `bubble-${style}`);
    await open(page, '/simple-chats/chat-demo1', '.sc-bubble');
    // Approved design §10.3 gives simple-chat assistants plain reading blocks.
    // Five frame preferences still persist and render in the story's scope below.
    assert.ok((await page.locator('.sc-assistant .sc-bubble').first().innerText()).includes('比例尺'));
    assert.equal(await page.locator('.sc-assistant .bubble-frame__corner-art, .sc-assistant .bubble-frame__end-art').count(), 0, 'Simple-chat assistants stay plain without story frame artwork');
    assert.equal(await page.locator('.sc-user .sc-bubble').first().evaluate((el) => { const css=getComputedStyle(el); return css.textAlign==='left'||(css.textAlign==='start'&&css.direction==='ltr'); }), true, 'LTR user text is physically left aligned');
    assert.equal(await page.locator('.sc-assistant').first().getByRole('button', { name: '消息操作', exact: true }).isVisible(), true, 'Plain replies retain their real message operations');
    await open(page, '/stories/sess-demo1/branches/sess-demo1', '.v7-message');
    assert.equal(await page.locator('.v7-message-text').first().getAttribute('data-bubble-style'), style);
    assert.equal(await page.locator('[data-speaker="player"] .bubble-frame__content').first().evaluate((el) => { const css=getComputedStyle(el); return css.textAlign==='left'||(css.textAlign==='start'&&css.direction==='ltr'); }), true, 'LTR story player text is physically left aligned');
    assert.ok(await page.locator('[data-speaker="inner"]').count()); assert.ok(await page.locator('[data-speaker="scene"]').count());
    await open(page, '/settings/preferences', '.bubble-style-options');
  }
  for (const theme of ['light', 'dark']) {
    appearance.theme_id = theme; appearance.bubble_style_id = 'moonlight';
    await open(page, '/simple-chats/chat-demo1', '.sc-bubble'); await shot(page, `chat-${theme}`);
  }
  appearance.theme_id = 'astral'; appearance.bubble_style_id = 'star-track';
  simpleFailure = true; await open(page, '/simple-chats/chat-demo1', '.page-load-state[role="alert"]');
  assert.equal(await page.locator('.sc-empty').count(), 0); await shot(page, 'chat-read-error');
  await page.getByRole('button', { name: '重新读取' }).click(); await page.locator('.sc-bubble').first().waitFor();
  storyFailure = true; await open(page, '/stories/sess-demo1/branches/sess-demo1', '.page-load-state[role="alert"]');
  await page.getByRole('button', { name: '重新读取' }).click(); await page.locator('.v7-message').first().waitFor();
  slowChat = true; await page.goto(base + '/simple-chats/chat-demo1'); await page.locator('.sc-list-item').first().waitFor();
  await page.locator('.sc-list-item[href="/simple-chats/chat-demo2"]').click(); await page.locator('.sc-bubble').first().waitFor();
  await new Promise((done) => setTimeout(done, 1900)); assert.ok((await page.locator('.sc-head h1').textContent()).includes('另一段聊天')); slowChat = false;
  hangChat = true; await open(page, '/simple-chats/chat-demo1', '.page-load-state[role="alert"]'); assert.ok((await page.locator('.page-load-state').textContent()).includes('15 秒')); hangChat = false;
  await page.getByRole('button', { name: '重新读取' }).click(); await page.locator('.sc-bubble').first().waitFor();
  await page.locator('.sc-compose textarea').fill('继续说明'); await page.getByRole('button', { name: '发送', exact: true }).click();
  await page.locator('.sc-bubble').filter({ hasText: '这是正在逐段出现的回复。' }).first().waitFor(); await shot(page, 'chat-streaming');
  await page.getByRole('button', { name: '发送', exact: true }).waitFor();
  for (let n = 0; n < 4; n++) {
    await page.locator('.tpl-nav a.ui-navitem[href="/"]').click(); await page.locator('.home-story-grid').waitFor();
    await page.goBack(); await page.locator('.sc-bubble').first().waitFor();
  }
  const single = [...streams.values()].reduce((n, set) => n + set.size, 0); assert.ok(single <= 1, `subscriptions accumulated: ${single}`);
  await open(page, '/settings/preferences', '.bubble-style-options');
  await page.locator('#appearance-cursor').click(); await page.getByRole('option', { name: '星芒指针', exact: true }).click(); await page.waitForFunction(() => document.documentElement.dataset.cursor === 'star');
  const buttonCursor = await page.locator('.bubble-style-option').first().evaluate((el) => getComputedStyle(el).cursor);
  assert.ok(buttonCursor.includes('star-hand.svg'), `native button cursor: ${buttonCursor}`);
  const sliderCursor = await page.locator('#appearance-intensity').evaluate((el) => getComputedStyle(el).cursor);
  assert.ok(sliderCursor !== 'none', `Slider must retain an operable native cursor: ${sliderCursor}`);
  await open(page, '/simple-chats/chat-demo1', '.sc-bubble');
  const firstAssistant = page.locator('.sc-assistant').first();
  await checkSimpleEdit(page);
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write'], { origin: base });
  await firstAssistant.getByRole('button', { name: '复制', exact: true }).click();
  assert.ok((await page.evaluate(() => navigator.clipboard.readText())).includes('比例尺'));
  assert.equal(await firstAssistant.getByRole('button', { name: '下一候选', exact: true }).count(), 1);
  await page.evaluate(() => { document.body.style.zoom = '1.25'; }); await bounds(page, 'chat-zoom-125');
  await shot(page, 'chat-zoom-125'); await page.evaluate(() => { document.body.style.zoom = ''; });
  assert.equal(await page.locator('.sc-compose textarea').evaluate((el) => getComputedStyle(el).cursor), 'text');
  await page.emulateMedia({ reducedMotion: 'reduce' }); await page.locator('.pointer-effects').waitFor({ state: 'hidden' });
  await page.context().close();
  const tabsContext = await browser.newContext({ viewport: { width: 1280, height: 900 } }); contexts.push(tabsContext);
  const tabs = await Promise.all([tabsContext.newPage(), tabsContext.newPage(), tabsContext.newPage()]);
  await Promise.all([open(tabs[0], '/simple-chats/chat-demo1', '.sc-bubble'), open(tabs[1], '/simple-chats/chat-demo2', '.sc-bubble'), open(tabs[2], '/stories/sess-demo1/branches/sess-demo1', '.v7-message')]);
  await tabs[0].waitForFunction(() => document.querySelector('.sc-head')?.textContent.includes('地图与旅途'));
  for (let attempt = 0; attempt < 30 && [...streams.values()].reduce((n, set) => n + set.size, 0) < 3; attempt++) await tabs[0].waitForTimeout(100);
  assert.equal([...streams.values()].reduce((n, set) => n + set.size, 0), 3, 'Each of the three tabs should keep exactly its current subscription');
  for (const tab of tabs) await open(tab, '/stories', '.home-story-grid');
  await tabs[0].waitForTimeout(300);
  assert.equal([...streams.values()].reduce((n, set) => n + set.size, 0), 0, 'All subscriptions should close when every tab leaves a chat');
  results.push({ check: 'three-tab concurrent reads and subscriptions release', status: 'passed' });
  await tabsContext.close();
  const failedModule = await pageAt(1280, 900);
  let refuseModule = true;
  const settingsModules=[];
  for(const file of await readdir(resolve(dist,'assets'))) if(file.endsWith('.js')&&(await readFile(resolve(dist,'assets',file),'utf8')).includes('appearance-theme-grid')) settingsModules.push(file);
  assert.equal(settingsModules.length,1,'Locate the actual lazy settings module in the frozen build, independent of chunk naming');
  let refusedReads=0;
  await failedModule.route(`**/assets/${settingsModules[0]}`, (route) => { if(refuseModule) { refusedReads++; return route.abort('failed'); } return route.continue(); });
  await failedModule.goto(base + '/settings/preferences');
  await failedModule.locator('.v7-route-error').waitFor(); await shot(failedModule, 'module-read-error');
  assert.ok(refusedReads>0,'Module failure must actually intercept the delivered settings chunk');
  refuseModule = false;
  await failedModule.getByRole('button', { name: '重新加载页面', exact: true }).click(); await failedModule.locator('.bubble-style-options').waitFor();
  results.push({ check: 'page module failure has manual recovery and reload succeeds', status: 'passed' });
  await failedModule.context().close();
  for (const width of process.env.UI_ACCEPTANCE_DESKTOP_ONLY ? [] : [360, 390, 430]) {
    const phone = await pageAt(width, 844, true);
    for (const [path, ready, name] of [['/stories', '.home-story-grid', 'stories'], ['/library/characters', '.workspace-card-grid', 'characters'], ['/worlds', '.worlds-card-grid', 'worlds'], ['/library/imports', '.asset-import-layout', 'imports'], ['/settings/preferences', '.bubble-style-options', 'preferences'], ['/settings/connection', '.v7-ai-settings-card', 'connection'], ['/simple-chats/chat-demo1', '.sc-bubble', 'chat'], ['/stories/sess-demo1/branches/sess-demo1', '.v7-message', 'story-chat']]) {
      await open(phone, path, ready); await bounds(phone, `${name}-phone-${width}`); await shot(phone, `${name}-phone-${width}`);
    }
    await open(phone, '/stories/sess-demo1/branches/sess-demo1', '.v7-message');
    await phone.getByTestId('draft-assist').click();
    const phoneWriting = phone.getByRole('dialog', { name: '故事代笔', exact: true }); await phoneWriting.waitFor();
    await phoneWriting.getByLabel('写作要求').fill('提供三份旅行素材');
    await phoneWriting.getByRole('button', { name: '生成三份方案', exact: true }).click();
    await phoneWriting.locator('.writing-proposal').first().waitFor();
    await bounds(phone, `writing-phone-${width}`);
    const writingBounds = await phoneWriting.boundingBox();
    assert.ok(writingBounds.x >= 0 && writingBounds.x + writingBounds.width <= width + 1);
    await shot(phone, `writing-phone-${width}`);
    await phone.setViewportSize({ width, height: 460 });
    await phoneWriting.getByRole('button', { name: '重新生成三份方案' }).scrollIntoViewIfNeeded();
    const writingButton = await phoneWriting.getByRole('button', { name: '重新生成三份方案' }).boundingBox();
    assert.ok(writingButton.y >= 0 && writingButton.y + writingButton.height <= 460);
    await shot(phone, `writing-keyboard-${width}`);
    await phoneWriting.getByRole('button', { name: '关闭代笔' }).click();
    await phone.setViewportSize({ width, height: 844 });
    await open(phone, '/library/characters', '.workspace-card-grid');
    await phone.getByTestId('character-create').click();
    await phone.waitForURL((url) => url.pathname === '/library/characters/new');
    const phoneEditor = phone.locator('.character-editor-page'); await phoneEditor.waitFor();
    assert.equal(await phone.getByRole('dialog').count(), 0, 'The new-character editor is a routed page');
    await bounds(phone, `character-editor-phone-${width}`);
    assert.ok(await phoneEditor.getByRole('heading', { name: '新建角色', exact: true }).evaluate((el) => {
      const r = el.getBoundingClientRect(); return Boolean(document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)?.closest('.character-editor-page'));
    }), 'The shell bar and drawer must not cover the editor title');
    assert.equal(await phone.getByRole('navigation', { name: '底部主导航', exact: true }).count(), 0, 'The phone shell has no bottom navigation');
    assert.ok(await phone.evaluate(() => Boolean(document.elementFromPoint(innerWidth / 2, innerHeight - 10)?.closest('.character-editor-page'))), 'The editor reaches the bottom of the screen');
    await shot(phone, `character-editor-phone-${width}`);
    await phoneEditor.getByRole('button', { name: '返回角色库', exact: true }).click();
    await phone.waitForURL((url) => url.pathname === '/library/characters');
    await open(phone, '/simple-chats/chat-demo1', '.sc-bubble'); await phone.setViewportSize({ width, height: 460 });
    await phone.waitForFunction(() => parseInt(getComputedStyle(document.documentElement).getPropertyValue('--mrp-visual-height'), 10) <= 460);
    const send = await phone.getByRole('button', { name: '发送', exact: true }).boundingBox(); assert.ok(send && send.y + send.height <= 460, `Keyboard viewport obscures composer: ${JSON.stringify(send)}`);
    await shot(phone, `chat-keyboard-${width}`); await phone.context().close();
  }
  }
  snapshot.player_identities = [{ id: 'identity-avatar-demo', person_id: 'person-avatar-demo', source_character_id: characters[0].id,
    name: '合成控制人物', persona: '验收用人物', character: characters[0], avatar_ref: null, media_captured: true, start_seq: 0 }];
  snapshot.meta.player_identity_id = 'identity-avatar-demo';
  const repaired = await pageAt(1440, 1000);
  await open(repaired, '/stories/sess-demo1/branches/sess-demo1', '.v7-message');
  const recoveredAvatar = repaired.locator('.player-control-person img');
  await recoveredAvatar.waitFor();
  await repaired.waitForFunction(() => document.querySelector('.player-control-person img')?.naturalWidth > 0);
  assert.ok((await recoveredAvatar.getAttribute('src')).includes(`/characters/${characters[0].id}/avatar`));
  assert.equal(snapshot.player_identities[0].avatar_ref, null, 'Display recovery must not rewrite the historical snapshot');
  await shot(repaired, 'current-control-avatar-recovered');
  results.push({ check: 'current control recovers library avatar while frozen media stays unchanged', status: 'passed' });
  await repaired.getByTestId('assist-toggle').click();
  await repaired.getByRole('button', { name: '固定信息', exact: true }).click();
  await repaired.getByText('与记忆有什么区别？', { exact: true }).click();
  assert.ok((await repaired.getByTestId('pinned-facts-panel').textContent()).includes('消息操作'));
  await shot(repaired, 'fixed-facts-guidance');
  await repaired.getByRole('button', { name: '收起创作工具', exact: true }).click();
  for (const theme of ['astral', 'dark', 'light', 'midnight', 'rose-night', 'amber-night', 'forest', 'lavender', 'sakura']) {
    appearance.theme_id = theme;
    await repaired.reload();
    await repaired.getByTestId('draft-assist').click();
    const themedWriting = repaired.getByRole('dialog', { name: '故事代笔', exact: true });
    await themedWriting.waitFor();
    await repaired.waitForFunction(id => document.documentElement.dataset.theme === id, theme);
    const primary = themedWriting.locator('.writing-footer button');
    const paint = () => primary.evaluate(button => {
      const sample = document.createElement('span'); document.body.append(sample);
      const probe = (css) => { sample.style.cssText = css; const style = getComputedStyle(sample); return { color: style.color, backgroundColor: style.backgroundColor, backgroundImage: style.backgroundImage }; };
      const actual = getComputedStyle(button);
      const result = { disabled: button.disabled, text: actual.color, background: actual.backgroundColor, image: actual.backgroundImage,
        muted: probe('color:var(--mw-muted);background:var(--mw-soft)'), gold: probe('color:var(--mw-on-primary);background-image:var(--mw-enamel)') };
      sample.remove(); return result;
    });
    // Without a request the primary action is disabled and uses the quiet pair (an earlier step may have left a request in the draft).
    await themedWriting.getByLabel('写作要求').fill('');
    await repaired.waitForFunction(() => document.querySelector('.writing-footer button')?.disabled === true);
    const idle = await paint();
    assert.equal(idle.disabled, true);
    assert.equal(idle.text, idle.muted.color);
    // The disabled fill is a pack-dependent legacy surface, so only the absence of the gold gradient is pinned.
    assert.equal(idle.image, 'none', `disabled primary has no gold gradient (${theme})`);
    // With a request it is enabled and painted from the gold primary tokens with readable text.
    await themedWriting.getByLabel('写作要求').fill('补充几种不同的行动方案');
    await repaired.waitForFunction(() => { const b = document.querySelector('.writing-footer button'); return b && !b.disabled; });
    const active = await paint();
    assert.equal(active.disabled, false);
    assert.equal(active.text, active.gold.color, `enabled primary text uses --mw-on-primary (${theme})`);
    assert.equal(active.image, active.gold.backgroundImage, `enabled primary is painted from the --mw-enamel gradient (${theme})`);
    const channels = (value) => (value.match(/rgb\((\d+), (\d+), (\d+)\)/g) ?? []).map((part) => part.match(/\d+/g).map(Number));
    const luminance = ([r, g, b]) => { const f = (v) => { v /= 255; return v <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; }; return .2126 * f(r) + .7152 * f(g) + .0722 * f(b); };
    const text = channels(active.text)[0], stops = channels(active.image);
    assert.equal(stops.length, 3, `gold gradient has three stops (${theme})`);
    for (const stop of stops) {
      const [hi, lo] = [luminance(text), luminance(stop)].sort((x, y) => y - x);
      assert.ok((hi + .05) / (lo + .05) >= 4.5, `enabled primary text is readable on every gold stop (${theme}): ${active.text} on rgb(${stop})`);
    }
    await bounds(repaired, `writing-theme-${theme}`); await shot(repaired, `writing-theme-${theme}`);
    await themedWriting.getByRole('button', { name: '关闭代笔', exact: true }).click();
    results.push({ check: `writing shared theme colors ${theme}`, status: 'passed' });
  }
  await repaired.context().close(); appearance.theme_id = 'astral';
  await checkConversationUI({ fixture: conversation, snapshot, pageAt, open, shot, bounds, results });
  }
  assert.deepEqual(errors, []);
  if (!productionDesign && !process.env.UI_ACCEPTANCE_ONLY_PICTUREBOOK && !process.env.UI_ACCEPTANCE_ONLY_SIMPLE_EDIT && !process.env.UI_ACCEPTANCE_ONLY_ARCHITECTURE && !process.env.UI_ACCEPTANCE_ONLY_PHASE4) results.push({ check: 'read failures, timeout, retry, stale read, streaming, themes, bubbles and subscription lifecycle', status: 'passed', ...metrics });
  await writeFile(resolve(output, 'results.json'), JSON.stringify(results, null, 2)); console.log(JSON.stringify({ passed: true, screenshots: 'outside checkout', checks: results.length, metrics }));
} catch (cause) {
  const activePage = contexts.flatMap(context => context.pages()).filter(page => !page.isClosed()).at(-1);
  let focus = null;
  if (activePage) {
    await activePage.screenshot({ path: resolve(output, 'failure-active.png'), animations: 'disabled' }).catch(() => {});
    focus = await activePage.evaluate(() => ({ tag: document.activeElement?.tagName, id: document.activeElement?.id, label: document.activeElement?.getAttribute('aria-label'), testid: document.activeElement?.getAttribute('data-testid'), editorSection: document.activeElement?.closest('[id^="character-"]')?.id })).catch(() => null);
  }
  await writeFile(resolve(output, 'failure.json'), JSON.stringify({ message: cause.message, errors, focus, results }, null, 2)); throw cause;
}
finally { for (const context of contexts) await context.close().catch(() => {}); await browser.close(); for (const socket of allSockets) socket.destroy(); await new Promise((done) => server.close(done)); }

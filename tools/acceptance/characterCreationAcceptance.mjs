/** Synthetic built-UI acceptance only. Never point this at a user's running service. */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, writeFile, readdir, readFile } from 'node:fs/promises';
import { resolve, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = fileURLToPath(new URL('../../', import.meta.url));
const base = process.env.UI_ACCEPTANCE_URL;
assert.ok(base && process.env.UI_ACCEPTANCE_OUT, 'Set fixture URL and a private output directory');
const output = resolve(process.env.UI_ACCEPTANCE_OUT);
assert.ok(relative(root, output).startsWith('..'), 'Output must stay outside source');
await mkdir(output,{recursive:true});
const browser = await chromium.launch({headless:true,args:['--host-resolver-rules=MAP character-fixture.test 127.0.0.1','--no-proxy-server'],...(process.env.UI_BROWSER_CHANNEL ? {channel:process.env.UI_BROWSER_CHANNEL} : {})});
const context = await browser.newContext({viewport:{width:1440,height:1000},extraHTTPHeaders:{Origin:base}});
const page = await context.newPage();
page.setDefaultTimeout(15000);
const errors=[], results=[], requests=[];
page.on('pageerror',e=>errors.push(e.message));
page.on('request',request=>{ if(request.url().endsWith('/characters/preview-turn')) requests.push(request.postDataJSON()); });
page.on('dialog',dialog=>dialog.accept());
const get = async path => {const response=await context.request.get(base+path);assert.ok(response.ok(),path);return response.json();};
const check=async(name,run)=>{await run();results.push({name,ok:true});console.log('PASS '+name);};
const editor=()=>page.locator('.v7-character-editor[role="dialog"]');
const shot=async(name)=>page.screenshot({path:resolve(output,name+'.png'),fullPage:true});
let fixture;
try {
  fixture=await get('/api/v1/acceptance-fixture');
  assert.equal(fixture.fixture,'mrp.development.synthetic','Refusing a real server');
  const worlds=await get('/api/v1/worlds');
  const worldId=worlds[0].id;
  const original='  合成原稿\n守塔人喜欢观察星空，声音平静。\n  ';
  const createdName='合成自由原稿角色-'+Date.now().toString(36);
  let createdId;
  await check('非安全 HTTP 域缺少 randomUUID 仍可建立独立草稿',async()=>{
    const plainOrigin='http://character-fixture.test:'+new URL(base).port;
    const plain=await browser.newContext({extraHTTPHeaders:{Origin:plainOrigin}});
    // Present the built application on an actual non-secure browser origin.
    // Proxy only to our marked loopback fixture, keeping production Host/LAN
    // authorization intact while exercising the browser's missing UUID API.
    await plain.route(plainOrigin+'/**',async route=>{const headers={...route.request().headers(),origin:base};delete headers.host;const response=await route.fetch({url:route.request().url().replace(plainOrigin,base),headers});await route.fulfill({response});});
    const tab=await plain.newPage();tab.on('pageerror',error=>errors.push(error.message));
    await tab.goto(plainOrigin+'/workshop/character');await tab.getByTestId('card-name').waitFor({timeout:5000}).catch(async error=>{await tab.screenshot({path:resolve(output,'nonsecure-failure.png')});console.log(await tab.locator('body').innerText());throw error;});
    assert.deepEqual(await tab.evaluate(()=>({secure:isSecureContext,uuid:typeof crypto.randomUUID})),{secure:false,uuid:'undefined'});
    await tab.getByTestId('card-name').fill('非安全合成草稿');await tab.getByTestId('card-description').fill('Plain HTTP fixture');await tab.waitForTimeout(450);
    assert.equal(await tab.evaluate(()=>Object.keys(localStorage).filter(key=>key.startsWith('mrp.character-editor.new:')).length),1);
    await plain.close();
  });
  await check('来源世界入口和自由正文零 AI 保存',async()=>{
    const modelRequests=[];page.on('request',request=>{if(/characters\/(generate|ai-edit|preview-turn)$/.test(request.url()))modelRequests.push(request.url());});
    await page.goto(base+'/workshop/character?source_world='+worldId);
    await editor().waitFor();
    assert.equal(await editor().getByLabel('来源世界',{exact:true}).inputValue(),worldId);
    await page.getByTestId('card-name').fill(createdName);
    await page.getByTestId('card-description').fill(original);
    assert.equal(await page.getByTestId('card-appearance').isVisible(),false);
    await editor().getByRole('button',{name:'创建角色',exact:true}).click();
    await editor().waitFor({state:'hidden'});
    const created=(await get('/api/v1/characters')).find(row=>row.card.name===createdName);
    assert.equal(created.card.description,original);assert.equal(created.source_world_id,worldId);createdId=created.id;
    assert.equal(modelRequests.length,0);
  });
  await check('旧卡结构字段折叠后仍完整保留',async()=>{
    const patch=await context.request.patch(base+'/api/v1/characters/'+createdId,{data:{card:{appearance:'银发合成外貌',traits:'辨识潮汐',personality:'平静',system_prompt:'合成高级约束',tags:['合成标签'],extensions:{foreign:'keep'}}}});assert.ok(patch.ok(),await patch.text());
    await page.goto(base+'/library/characters/'+createdId);await editor().waitFor();
    assert.equal(await page.getByTestId('card-appearance').isVisible(),false);
    await page.getByTestId('card-description').fill('手工精修自由正文');
    await Promise.all([page.waitForResponse(response=>response.url().endsWith('/characters/'+createdId)&&response.request().method()==='PATCH'),editor().getByRole('button',{name:'保存',exact:true}).click()]);
    const card=(await get('/api/v1/characters/'+createdId)).card;
    for(const [key,value] of Object.entries({appearance:'银发合成外貌',traits:'辨识潮汐',personality:'平静',system_prompt:'合成高级约束'}))assert.equal(card[key],value);
    assert.equal(card.extensions.foreign,'keep');
  });
  await check('真实 HTTP 试聊累计历史，六轮后停止',async()=>{
    requests.length=0;
    for(let i=0;i<6;i++){
      await editor().getByPlaceholder('输入消息…').fill('合成提问 '+(i+1));
      await editor().getByRole('button',{name:'发送',exact:true}).click();
      await editor().getByText('合成口吻回应：第 '+(i+1)+' 轮；记得你说的 合成提问 1。',{exact:true}).waitFor();
    }
    assert.deepEqual(requests.map(row=>row.history.length),[0,2,4,6,8,10]);
    assert.equal(requests[5].history[0].content,'合成提问 1');
    assert.equal(requests[5].history[1].role,'assistant');
    assert.equal(await editor().getByRole('button',{name:'发送',exact:true}).isDisabled(),true);
    await shot('trial-desktop');
  });
  await check('示例预览追加与重复采用不重复',async()=>{
    await editor().getByRole('button',{name:'保存为口吻示例',exact:true}).first().click();
    const example=await editor().getByLabel('口吻示例预览').inputValue();
    await editor().getByRole('button',{name:'采用并追加示例',exact:true}).click();
    await editor().getByText('这段试聊基于修改前的人设。',{exact:false}).waitFor();
    await editor().getByRole('button',{name:'保存为口吻示例',exact:true}).first().click();
    await editor().getByRole('button',{name:'采用并追加示例',exact:true}).click();
    await editor().getByText('这个示例已存在，没有重复追加。',{exact:true}).waitFor();
    await editor().locator('summary').filter({hasText:'精修结构字段与开场'}).click();
    assert.equal(await page.getByTestId('card-mes-example').inputValue(),example);
    assert.equal(await editor().getByRole('button',{name:'发送',exact:true}).isDisabled(),true);
  });
  await check('错误独立保留输入且不写入下一轮历史',async()=>{
    await editor().getByRole('button',{name:'按当前人设重新试聊',exact:true}).click();
    await editor().getByPlaceholder('输入消息…').fill('__FAIL_TRIAL__');
    await editor().getByRole('button',{name:'发送',exact:true}).click();
    await editor().getByText('试聊失败：',{exact:false}).waitFor();
    assert.equal(await editor().getByPlaceholder('输入消息…').inputValue(),'__FAIL_TRIAL__');
    await editor().getByPlaceholder('输入消息…').fill('合成恢复');
    await editor().getByRole('button',{name:'发送',exact:true}).click();
    await editor().getByText('合成口吻回应：第 1 轮；记得你说的 合成恢复。',{exact:true}).waitFor();
    assert.deepEqual(requests.at(-1).history,[]);
  });
  await check('AI 期间手改使候选过期，不能覆盖',async()=>{
    await page.getByTestId('card-description').fill('合成请求前正文');
    await editor().getByRole('button',{name:'AI 改写',exact:true}).first().click();
    await editor().getByPlaceholder('改写指令，如：更简洁、突出毒舌属性').fill('__SLOW_AI__');
    await editor().getByRole('button',{name:'确认',exact:true}).click();
    await page.getByTestId('card-description').fill('生成期间的手工修订');
    await editor().getByLabel('编辑 AI 候选').waitFor();
    assert.equal(await editor().getByRole('button',{name:'采用',exact:true}).isDisabled(),true);
    assert.equal(await page.getByTestId('card-description').inputValue(),'生成期间的手工修订');
    await editor().getByRole('button',{name:'放弃',exact:true}).click();
  });
  await check('试聊回复迟到时不进入修改后的人设历史',async()=>{
    await editor().getByRole('button',{name:'按当前人设重新试聊',exact:true}).click();
    let release,received;const resumed=new Promise(resolve=>{release=resolve;});const fetched=new Promise(resolve=>{received=resolve;});
    const intercept=async route=>{const response=await route.fetch();received();await resumed;await route.fulfill({response}).catch(()=>{});};
    await page.route('**/characters/preview-turn',intercept);
    await editor().getByPlaceholder('输入消息…').fill('合成迟到');await editor().getByRole('button',{name:'发送',exact:true}).click();await fetched;
    await page.getByTestId('card-description').fill('等待回复时手工更改了人设');
    release();await page.waitForTimeout(300);await page.unroute('**/characters/preview-turn',intercept);
    assert.equal(await editor().getByText('合成口吻回应：第 1 轮；记得你说的 合成迟到。',{exact:true}).count(),0);
    await editor().getByText('人设已改变，已停止接收旧版本回复。',{exact:false}).waitFor();
  });
  await check('选区候选可编辑且撤销保留未选全文',async()=>{
    const raw=page.getByTestId('card-description');await raw.fill('前文🌙旧口吻后文');
    await raw.focus();await raw.press('Home');for(let i=0;i<4;i++)await raw.press('ArrowRight');for(let i=0;i<3;i++)await raw.press('Shift+ArrowRight');
    const range=await raw.evaluate(el=>({start:el.selectionStart,end:el.selectionEnd,value:el.value}));
    assert.ok(range.end>range.start);
    await editor().getByRole('button',{name:'AI 改写',exact:true}).first().click();
    assert.equal(await editor().getByLabel('AI 改写范围').inputValue(),'selection');
    await editor().getByPlaceholder('改写指令，如：更简洁、突出毒舌属性').fill('合成精修选区');
    await editor().getByRole('button',{name:'确认',exact:true}).click();await editor().getByLabel('编辑 AI 候选').fill('采用的口吻');
    await editor().getByRole('button',{name:'采用',exact:true}).click();
    assert.equal(await raw.inputValue(),range.value.slice(0,range.start)+'采用的口吻'+range.value.slice(range.end));
    await editor().getByRole('button',{name:'撤销此次采用',exact:true}).click();assert.equal(await raw.inputValue(),range.value);
  });
  await check('自由正文冻结、勾选字段组合核对并采用到原角色',async()=>{
    const source='  合成角色整理原稿\n守塔人的未迁移经历与口吻。\n  ';
    const before=await get('/api/v1/characters/'+createdId);
    await page.getByTestId('card-description').fill(source);
    await editor().getByRole('button',{name:'整理正文为字段',exact:true}).click();
    const fields=editor().getByRole('group',{name:'本次整理的字段'});
    await fields.getByLabel('外貌',{exact:true}).uncheck();await fields.getByLabel('能力与实力',{exact:true}).uncheck();
    await editor().getByLabel('使用来源世界的作者资料作为创作参考',{exact:false}).uncheck();
    await editor().getByRole('button',{name:'生成整理候选',exact:true}).click();await page.waitForURL('**/library/imports/import-*');
    const jobId=new URL(page.url()).pathname.split('/').at(-1);
    let job;for(let i=0;i<30;i++){job=await get('/api/v1/asset-import-jobs/'+jobId);if(job.status==='needs_review')break;await page.waitForTimeout(100);}
    assert.equal(job.target_asset_id,createdId);assert.equal(job.target_revision,before.revision);assert.equal(job.reference_world_id,null);
    assert.deepEqual(job.selected_fields,['description','personality']);
    assert.equal((await get('/api/v1/asset-import-jobs/'+jobId+'/source')).source,source);
    assert.deepEqual((await get('/api/v1/characters/'+createdId)).card,before.card,'Generation must not change the character');
    await page.getByRole('link').filter({hasText:'人物卡 ·'}).click();await editor().waitFor();
    await editor().getByRole('button',{name:'确认并保存人物卡',exact:true}).click();
    await page.waitForURL('**/library/characters/'+createdId);await editor().waitFor();
    const saved=await get('/api/v1/characters/'+createdId);
    assert.equal(saved.authoring_source.text,source);assert.equal(saved.authoring_source.import_job_id,jobId);
    assert.equal(saved.card.description,'守护海岸，习惯先观察再行动。');assert.equal(saved.card.personality,'说话平静。');
    assert.equal(saved.card.appearance,before.card.appearance);assert.equal(saved.card.traits,before.card.traits);assert.equal(saved.card.system_prompt,before.card.system_prompt);assert.equal(saved.card.extensions.foreign,'keep');
    const persona=await context.request.post(base+'/api/v1/characters/persona-preview',{data:{card:saved.card}});assert.ok(persona.ok());assert.equal((await persona.json()).persona.includes('合成角色整理原稿'),false);
  });
  await check('两份新建草稿独立，并能选择恢复',async()=>{
    const a=await context.newPage(),b=await context.newPage();
    for(const [tab,name] of [[a,'独立合成甲'],[b,'独立合成乙']]){await tab.goto(base+'/workshop/character');await tab.getByTestId('card-name').fill(name);await tab.getByTestId('card-description').fill(name+'的原稿');await tab.waitForTimeout(450);}
    const keys=await b.evaluate(()=>Object.keys(localStorage).filter(key=>key.startsWith('mrp.character-editor.new:')).map(key=>({key,value:JSON.parse(localStorage.getItem(key))})).filter(row=>row.value.card.name.startsWith('独立合成')));
    assert.equal(keys.length,2);assert.notEqual(keys[0].key,keys[1].key);
    await b.reload();await b.getByLabel('选择恢复的角色草稿').selectOption(keys.find(row=>row.value.card.name==='独立合成甲').key);await b.getByRole('button',{name:'恢复草稿',exact:true}).click();assert.equal(await b.getByTestId('card-description').inputValue(),'独立合成甲的原稿');
    const c=await context.newPage();await c.goto(base+'/workshop/character');await c.getByTestId('card-name').fill('独立合成丙');await c.getByTestId('card-description').fill('独立保存的正文');await c.getByRole('button',{name:'创建角色',exact:true}).click();await c.locator('.v7-character-editor').waitFor({state:'hidden'});
    assert.deepEqual(await c.evaluate(preserved=>preserved.map(key=>localStorage.getItem(key)!==null),keys.map(row=>row.key)),[true,true],'Saving a new draft must not discard offered recovery drafts');
    await c.close();
    await a.close();await b.close();
  });
  await check('新角色整理保留未选高级字段、别名与独立模型配置',async()=>{
    await page.goto(base+'/workshop/character?source_world='+worldId);await editor().waitFor();
    await page.getByTestId('card-name').fill('新建组合合成角色');await page.getByTestId('card-description').fill('合成新角色完整原稿');
    await editor().locator('summary').filter({hasText:'精修结构字段与开场'}).click();
    await page.getByTestId('card-appearance').fill('新建种子外貌');await page.getByTestId('card-traits').fill('新建种子能力');await page.getByTestId('card-first-mes').fill('新建种子开场');
    await editor().locator('summary').filter({hasText:'形象、别名与标签'}).click();
    const aliases=editor().getByPlaceholder('输入别名后回车添加');await aliases.fill('合成别名');await aliases.press('Enter');
    await editor().locator('summary').filter({hasText:'运行时配置'}).click();
    await editor().getByPlaceholder('留空使用全局默认',{exact:true}).fill('synthetic/only');await editor().getByPlaceholder('https://openrouter.ai/api/v1',{exact:true}).fill('https://example.invalid/api/v1');
    await editor().getByRole('button',{name:'整理正文为字段',exact:true}).click();const fields=editor().getByRole('group',{name:'本次整理的字段'});
    await fields.getByLabel('外貌',{exact:true}).uncheck();await fields.getByLabel('能力与实力',{exact:true}).uncheck();
    await editor().getByRole('button',{name:'生成整理候选',exact:true}).click();await page.waitForURL('**/library/imports/import-*');
    const jobId=new URL(page.url()).pathname.split('/').at(-1);let job;for(let i=0;i<30;i++){job=await get('/api/v1/asset-import-jobs/'+jobId);if(job.status==='needs_review')break;await page.waitForTimeout(100);}
    assert.equal(job.reference_world_id,worldId);assert.equal(job.reference_snapshot.world_id,worldId);assert.deepEqual(job.character_aliases,['合成别名']);assert.equal(job.character_runtime.max_tokens,0);
    await page.getByRole('link').filter({hasText:'人物卡 ·'}).click();await editor().waitFor();assert.equal(await page.getByTestId('card-appearance').inputValue(),'新建种子外貌');assert.equal(await page.getByTestId('card-first-mes').inputValue(),'新建种子开场');
    await editor().getByRole('button',{name:'确认并保存人物卡',exact:true}).click();await page.waitForURL('**/library/characters/char-*');await editor().waitFor();
    const saved=await get('/api/v1/characters/'+new URL(page.url()).pathname.split('/').at(-1));
    assert.equal(saved.card.appearance,'新建种子外貌');assert.equal(saved.card.traits,'新建种子能力');assert.equal(saved.card.first_mes,'新建种子开场');assert.deepEqual(saved.aliases,['合成别名']);assert.equal(saved.llm.model,'synthetic/only');assert.equal(saved.llm.base_url,'https://example.invalid/api/v1');assert.equal(saved.llm.sampling.max_tokens,0);
  });
  await check('九主题与三窄屏宽度及桌面布局截图',async()=>{
    const themeDir=resolve(root,'src/web/src/appearance/packs');const files=(await readdir(themeDir)).filter(file=>file.endsWith('.json'));
    assert.equal(files.length,9);
    for(const file of files){const theme=JSON.parse(await readFile(resolve(themeDir,file),'utf8'));
      const response=await context.request.patch(base+'/api/v1/settings',{data:{appearance:{theme_id:theme.id}}});assert.ok(response.ok());
      for(const width of [360,390,430,1440]){await page.setViewportSize({width,height:width===1440?1000:900});await page.goto(base+'/library/characters/'+createdId);await editor().waitFor();await page.waitForFunction(id=>document.documentElement.dataset.theme===id,theme.id);
        await page.getByTestId('card-description').fill('合成主题布局核对：自由原稿、结构精修、口吻示例。');
        await shot(theme.id+'-'+width);
        const clipped=await editor().evaluate(el=>{const box=el.getBoundingClientRect();return [...el.querySelectorAll('button,input,textarea,select,summary')].filter(node=>node.getClientRects().length).filter(node=>{const r=node.getBoundingClientRect();return r.left<box.left-2||r.right>box.right+2;}).map(node=>node.textContent||node.getAttribute('aria-label')||node.tagName);});
        assert.deepEqual(clipped,[],theme.id+' '+width+' controls outside viewport');
      }
    }
  });
  assert.deepEqual(errors,[],'Unexpected browser errors');
  await writeFile(resolve(output,'report.json'),JSON.stringify({fixture:fixture.fixture,results,errors,historyRequestLengths:requests.map(row=>row.history.length),screenshots:36},null,2));
} catch(error) {
  await shot('failure').catch(()=>{});await writeFile(resolve(output,'failure.json'),JSON.stringify({results,errors,error:error.stack},null,2));throw error;
} finally {await browser.close();}

/** Built UI + disposable real HTTP fixture. Narrow viewports are emulation, not device tests. */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, writeFile, readdir } from 'node:fs/promises';
import { resolve, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root=fileURLToPath(new URL('../../',import.meta.url)), base=process.env.UI_ACCEPTANCE_URL;
assert.ok(base && process.env.UI_ACCEPTANCE_OUT,'Set synthetic fixture URL and output');
const output=resolve(process.env.UI_ACCEPTANCE_OUT);
assert.ok(relative(root,output).startsWith('..'),'Keep synthetic screenshots outside source');
await mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true,channel:process.env.UI_BROWSER_CHANNEL||'msedge'});
const context=await browser.newContext({viewport:{width:1440,height:1000},extraHTTPHeaders:{Origin:base},acceptDownloads:true});
const page=await context.newPage();page.setDefaultTimeout(15000);
const results=[],errors=[];page.on('pageerror',e=>errors.push(e.message));
const request=async(method,path,body)=>{const response=await context.request.fetch(base+path,{method,data:body});return response;};
const get=async(path)=>{const response=await request('GET',path);assert.ok(response.ok(),path+': '+await response.text());return response.json();};
const check=async(name,run)=>{await run();results.push({name,ok:true});console.log('PASS '+name);};
const shot=async(name)=>page.screenshot({path:resolve(output,name+'.png'),fullPage:false});
const open=async(path,ready)=>{await page.goto(base+path);await page.locator(ready).first().waitFor();await page.evaluate(()=>document.fonts.ready);};
const bounds=async(label)=>{const box=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));assert.ok(box.scroll<=box.width+1,`${label} overflow ${JSON.stringify(box)}`);};
const menuBounds=async()=>{const rect=await page.getByRole('menu').last().boundingBox();const viewport=page.viewportSize();assert.ok(rect&&rect.x>=-1&&rect.y>=-1&&rect.x+rect.width<=viewport.width+1&&rect.y+rect.height<=viewport.height+1,'menu inside viewport');};
let fixture;
try {
  fixture=await get('/api/v1/chat-library-fixture');assert.equal(fixture.fixture,'mrp.chat-library.synthetic');
  const storyPath=`/stories/${fixture.story_id}/branches/${fixture.branch_id}`;
  await check('every final render path exposes persistent copy and keyboard bookmark actions',async()=>{
    await open(storyPath,'.v7-message-stream');
    assert.equal(await page.getByRole('button',{name:'朗读这条消息',exact:true}).count(),0);
    for(const id of fixture.message_ids){
      const bubble=page.locator(`[data-message-id="${id}"]`), trigger=bubble.getByRole('button',{name:'消息操作',exact:true});
      await trigger.scrollIntoViewIfNeeded();assert.ok(await trigger.isVisible());
      assert.ok(await bubble.getByRole('button',{name:'复制消息',exact:true}).isVisible());
      await trigger.focus();await page.keyboard.press('Enter');await page.getByRole('menuitem',{name:'书签',exact:true}).waitFor();await menuBounds();
      await page.keyboard.press('Escape');await page.waitForFunction((messageId)=>document.querySelector(`[data-message-id="${messageId}"] [aria-label="消息操作"]`)===document.activeElement,id);
    }
    const id=fixture.message_ids[1],bubble=page.locator(`[data-message-id="${id}"]`);
    await bubble.getByRole('button',{name:'消息操作',exact:true}).click();await page.getByRole('menuitem',{name:'书签',exact:true}).click();
    const bookmarks=await get(`/api/v1/stories/${fixture.story_id}/bookmarks`);assert.ok(bookmarks.some(row=>row.message_id===id));
    await bubble.getByRole('button',{name:'消息操作',exact:true}).click();await page.getByRole('menuitem',{name:'取消书签',exact:true}).click();
    assert.equal((await get(`/api/v1/stories/${fixture.story_id}/bookmarks`)).length,0);
    await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw new Error('synthetic permission denied');}}}));
    await bubble.getByRole('button',{name:'复制消息',exact:true}).click();await page.getByText('复制失败，可选中正文后手动复制。').waitFor();
    await shot('message-actions-keyboard');
  });
  await check('simple edit previews lost tail and candidates, stale scope retains typed content',async()=>{
    await open(`/simple-chats/${fixture.chat_id}`,'.sc-message');
    await page.locator('.sc-message').nth(1).getByRole('button',{name:'消息操作',exact:true}).click();await page.getByRole('menuitem',{name:'编辑',exact:true}).click();
    const dialog=page.getByRole('dialog',{name:'编辑聊天消息'});await dialog.waitFor();
    await dialog.getByText('保存会移除这条消息之后的 2 条对话。').waitFor();await dialog.getByText('这条回复的 2 个候选将替换为本次编辑内容。').waitFor();
    await dialog.getByLabel('消息内容').fill('手工编辑仍保留');
    await request('PATCH',`/api/v1/simple-chats/${fixture.chat_id}`,{title:'合成聊天已变化'});
    await dialog.getByRole('button',{name:'保存并移除后续 2 条',exact:true}).click();await dialog.getByRole('alert').waitFor();
    assert.equal((await get(`/api/v1/simple-chats/${fixture.chat_id}`)).messages.length,4);
    assert.equal(await dialog.getByLabel('消息内容').inputValue(),'手工编辑仍保留');
    await dialog.getByRole('button',{name:'重新核对影响范围'}).click();await dialog.getByRole('button',{name:'保存并移除后续 2 条',exact:true}).waitFor();
    await shot('simple-edit-scope');
    await dialog.getByRole('button',{name:'保存并移除后续 2 条',exact:true}).click();await dialog.waitFor({state:'hidden'});
    const saved=await get(`/api/v1/simple-chats/${fixture.chat_id}`);assert.equal(saved.messages.length,2);assert.deepEqual(saved.messages[1].variants,['手工编辑仍保留']);
  });
  await check('enabled speech has a visible pause and keyboard emotion choice; synthesis and audio are synthetic stubs',async()=>{
    const speechRequests=[];
    await page.route('**/api/v1/settings',async route=>{
      if(route.request().method()!=='GET')return route.continue();
      const response=await route.fetch(),settings=await response.json();settings.tts={...settings.tts,enabled:true};await route.fulfill({response,json:settings});
    });
    await page.route('**/api/v1/tts/synthesize',async route=>{speechRequests.push(route.request().postDataJSON());await route.fulfill({status:200,contentType:'audio/wav',body:Buffer.from('synthetic')});});
    await open(storyPath,'.v7-message-stream');await page.evaluate(()=>{HTMLMediaElement.prototype.play=function(){return Promise.resolve();};});
    const bubble=page.locator(`[data-message-id="${fixture.message_ids.at(-1)}"]`);
    await bubble.getByRole('button',{name:'消息操作',exact:true}).click();await page.getByRole('menuitem',{name:'朗读语气'}).focus();await page.keyboard.press('ArrowRight');
    await page.getByRole('menuitemradio',{name:'开心',exact:true}).click();await bubble.getByRole('button',{name:'朗读这条消息',exact:true}).click();
    await bubble.getByRole('button',{name:'暂停朗读',exact:true}).waitFor();await page.mouse.move(1,1);assert.ok(await bubble.getByRole('button',{name:'暂停朗读',exact:true}).isVisible());
    assert.equal(speechRequests[0].emotion,'excited');await bubble.getByRole('button',{name:'暂停朗读',exact:true}).click();await bubble.getByRole('button',{name:'朗读这条消息',exact:true}).waitFor();await shot('enabled-speech-emotion');
    await page.unroute('**/api/v1/settings');await page.unroute('**/api/v1/tts/synthesize');
  });
  await check('story and composer secondary tools remain accessible through keyboard menus',async()=>{
    await open(storyPath,'.v7-message-stream');const more=page.getByRole('button',{name:'更多故事操作'});await more.focus();await page.keyboard.press('Enter');
    await page.getByRole('menuitem',{name:'剧情导航',exact:true}).click();await page.getByRole('button',{name:'关闭剧情导航',exact:true}).click();
    await page.getByRole('button', { name: '内心', exact: true }).waitFor();
    await page.getByRole('button', { name: '旁白', exact: true }).waitFor();
    const tools = page.getByRole('button', { name: '更多输入工具' });
    await tools.focus(); await page.keyboard.press('Enter');
    assert.equal(await page.getByRole('menuitem', { name: '插入内容', exact: true }).count(), 0);
    await page.keyboard.press('Escape');
    for(const name of ['旁观交谈','角色与点名']){
      const trigger=page.getByRole('button',{name:'更多输入工具'});await trigger.focus();await page.keyboard.press('Enter');await page.getByRole('menuitem',{name:name==='旁观交谈'?'设置旁观交谈':name,exact:true}).click();
      await page.getByRole('dialog',{name,exact:true}).waitFor();await page.getByRole('button',{name:`关闭${name}`,exact:true}).click();
    }
  });
  await check('stable selection counts hidden results and selected share export shows exact dependency scope',async()=>{
    await open('/library/characters','.workspace-card-grid');await page.getByRole('button',{name:'选择素材',exact:true}).click();
    await page.getByLabel('选择角色 合成向导').check();await page.getByLabel('选择角色 合成旅人').check();
    await page.getByRole('textbox',{name:'搜索角色',exact:true}).fill('渡鸦');await page.getByText('已选 2 项（1 项被筛选隐藏）').waitFor();
    assert.ok(page.url().includes('q='));
    await page.getByRole('button',{name:'导出所选',exact:true}).click();const dialog=page.getByRole('dialog',{name:'导出所选素材'});await dialog.waitFor();
    await dialog.getByText('世界书 · 星海设定（角色依赖）', {exact:true}).waitFor();await dialog.getByText('不包含私人创作原稿、世界原稿、角色全身图、故事与记忆。').waitFor();
    const response=page.waitForResponse(res=>res.url().endsWith('/bundle/export-selected')&&res.request().method()==='POST');
    const download=page.waitForEvent('download');await dialog.getByRole('button',{name:'下载迁移包',exact:true}).click();
    assert.equal((await response).status(),200);await(await download).saveAs(resolve(output,'synthetic-selected.zip'));
    await shot('selected-export-scope');await dialog.getByRole('button',{name:'关闭',exact:true}).click();
    await page.getByRole('button',{name:'清除筛选',exact:true}).click();
    const stale=await get('/api/v1/characters');await request('PATCH',`/api/v1/characters/${fixture.character_ids[1]}`,{card:{tags:['外部合成更新']},expected_revision:stale.find(row=>row.id===fixture.character_ids[1]).revision});
    await page.getByRole('textbox',{name:'批量角色标签',exact:true}).fill('批次');await page.getByRole('button',{name:'应用到所选 2 项',exact:true}).click();
    await page.getByText('已保存 1 项，失败 1 项。失败项仍被选中。').waitFor();assert.ok(await page.getByLabel('选择角色 合成旅人').isChecked());assert.equal(await page.getByLabel('选择角色 合成向导').isChecked(),false);
    await shot('tag-batch-partial-failure');
    await page.getByRole('combobox',{name:'角色世界筛选'}).selectOption(fixture.world_id);assert.equal(await page.getByTestId('character-card').count(),1);
    await page.getByRole('combobox',{name:'角色标签筛选'}).selectOption('星海');await page.getByRole('combobox',{name:'角色排序'}).selectOption('name');
    const filteredURL=page.url();await page.getByRole('button',{name:'编辑',exact:true}).first().click();await page.getByRole('dialog',{name:'编辑角色',exact:true}).waitFor();await page.getByRole('button',{name:'取消',exact:true}).click();assert.equal(page.url(),filteredURL);
  });
  await check('worldbook tag editing, single-world classification and selected book export',async()=>{
    await open('/library/lorebooks','.asset-lorebook-sidebar');await page.getByRole('combobox',{name:'世界书世界筛选'}).selectOption('unassigned');
    assert.equal(await page.getByRole('button',{name:/星海设定/}).count(),0);
    await page.getByRole('button',{name:'编辑',exact:true}).first().click();const dialog=page.getByRole('dialog',{name:'编辑世界书'});await dialog.waitFor();
    await dialog.getByLabel('世界书标签').fill('草原，图鉴');await dialog.getByRole('button',{name:'保存修改'}).click();await dialog.waitFor({state:'hidden'});
    await page.getByRole('combobox',{name:'世界书标签筛选'}).selectOption('图鉴');await page.getByRole('button',{name:'选择素材',exact:true}).click();await page.getByLabel('选择世界书 山谷设定').check();
    await page.getByRole('button',{name:'导出所选',exact:true}).click();await page.getByRole('dialog',{name:'导出所选素材'}).getByText('世界书 · 山谷设定',{exact:true}).waitFor();await shot('worldbook-selected-scope');await page.getByRole('button',{name:'关闭',exact:true}).click();
  });
  await check('scenario search tags and sort are route filters with recoverable empty results',async()=>{
    for(const [title,tags] of [['合成灯塔开场',['星海']],['合成山谷开场',['草原']]]){
      const response=await request('POST','/api/v1/scenarios',{title,tags,character_ids:[fixture.character_ids[0]],lorebook_ids:[]});assert.ok(response.ok());
    }
    await open('/library/scenarios','.asset-list-toolbar');await page.getByRole('textbox',{name:'搜索场景预设',exact:true}).fill('灯塔');
    await page.getByRole('heading',{name:'合成灯塔开场',exact:true}).waitFor();assert.equal(await page.getByRole('heading',{name:'合成山谷开场',exact:true}).count(),0);
    await page.getByRole('combobox',{name:'场景预设标签筛选'}).selectOption('草原');await page.getByText('没有符合筛选的场景预设。').waitFor();
    await page.getByRole('button',{name:'清除筛选',exact:true}).click();await page.getByRole('combobox',{name:'场景预设排序'}).selectOption('name');assert.ok(page.url().includes('sort=name'));await shot('scenario-query-tags-sort');
  });
  await check('nine themes at desktop and narrow emulated viewports keep actions and dialog within bounds',async()=>{
    const themes=(await readdir(resolve(root,'src/web/src/appearance/packs'))).filter(name=>name.endsWith('.json')).map(name=>name.slice(0,-5));
    const settings=await get('/api/v1/settings');
    for(const theme of themes){
      const response=await request('PATCH','/api/v1/settings',{appearance:{...settings.appearance,theme_id:theme}});assert.ok(response.ok());
      for(const width of [1440,360,390,430]){
        await page.setViewportSize({width,height:width===1440?1000:860});await open(storyPath,'.v7-message-stream');await bounds(`${theme}-${width}-story`);
        const bubble=page.locator(`[data-message-id="${fixture.message_ids.at(-1)}"]`);await bubble.getByRole('button',{name:'消息操作',exact:true}).click();await page.getByRole('menuitem',{name:'书签',exact:true}).waitFor();await menuBounds();await shot(`${theme}-${width}-message-menu`);await page.keyboard.press('Escape');
        await open('/library/characters','.workspace-card-grid');await bounds(`${theme}-${width}-characters`);await shot(`${theme}-${width}-library`);
        await open(`/simple-chats/${fixture.chat_id}`,'.sc-message');await page.locator('.sc-message').last().getByRole('button',{name:'消息操作',exact:true}).click();await page.getByRole('menuitem',{name:'编辑',exact:true}).click();await page.getByRole('dialog',{name:'编辑聊天消息'}).waitFor();await bounds(`${theme}-${width}-simple-dialog`);await shot(`${theme}-${width}-simple-edit`);
        const rect=await page.getByRole('dialog',{name:'编辑聊天消息'}).boundingBox();assert.ok(rect.x>=-1&&rect.x+rect.width<=width+1);
      }
    }
  });
  await check('touch emulation opens bookmark actions without hover and keeps 44px targets',async()=>{
    const touchContext=await browser.newContext({viewport:{width:390,height:860},hasTouch:true,isMobile:true,extraHTTPHeaders:{Origin:base}});
    const touchPage=await touchContext.newPage();touchPage.on('pageerror',e=>errors.push(e.message));
    try{
      await touchPage.goto(base+storyPath);const bubble=touchPage.locator(`[data-message-id="${fixture.message_ids.at(-1)}"]`),trigger=bubble.getByRole('button',{name:'消息操作',exact:true});
      await trigger.waitFor();assert.ok((await trigger.boundingBox()).height>=44);await trigger.tap();await touchPage.getByRole('menuitem',{name:'书签',exact:true}).tap();
      assert.ok((await get(`/api/v1/stories/${fixture.story_id}/bookmarks`)).some(row=>row.message_id===fixture.message_ids.at(-1)));
      await trigger.tap();await touchPage.getByRole('menuitem',{name:'取消书签',exact:true}).tap();await touchPage.screenshot({path:resolve(output,'touch-emulation-actions.png')});
    }finally{await touchContext.close();}
  });
  assert.deepEqual(errors,[]);console.log('PASS no browser runtime errors');
} catch(cause){await shot('failure');await writeFile(resolve(output,'failure.txt'),String(cause)+'\n'+await page.locator('body').innerText());throw cause;}
finally{await writeFile(resolve(output,'report.json'),JSON.stringify({fixture:fixture?.fixture,results,errors,narrowViewportEvidence:'Browser viewport emulation only; no physical phone evidence.'},null,2));await browser.close();}

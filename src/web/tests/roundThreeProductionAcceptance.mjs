/** Third-round acceptance of the actual React application, never of the HTML samples.
 * Run via UI_ACCEPTANCE_ONLY_ROUND3=1 with an external frozen UI_ACCEPTANCE_DIST.
 * UI_ROUND3_MODE=matrix|behaviors and UI_ROUND3_GROUPS=editor,library,create,import
 * select a targeted rerun without reclassifying an earlier failed batch as passed.
 */
import assert from 'node:assert/strict';
import { writeFile, readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { createHash } from 'node:crypto';

export async function checkRoundThreeProductionUI(h) {
  const { pageAt, open, shot, results, output, appearance, fixture, characters, designFixture, base } = h;
  const failures=[], measures=[], checks=[]; let current;
  const groups=new Set((process.env.UI_ROUND3_GROUPS || 'editor,library,create,import').split(','));
  assert.ok([...groups].every(group=>['editor','library','create','import'].includes(group)),'Unknown round-three test group');
  const mode=process.env.UI_ROUND3_MODE || 'all'; assert.ok(['all','matrix','behaviors'].includes(mode));
  const widths=(process.env.UI_ROUND3_WIDTHS || '1440,1920,2000,390').split(',').map(Number);
  assert.ok(widths.every(width=>[1440,1920,2000,390].includes(width)),'Use the approved third-round widths');
  const check=async(name,fn)=>{
    try {await fn();checks.push({name,status:'passed'});results.push({check:name,status:'passed'});}
    catch(error){const file=`failure-${name.replace(/[^a-z0-9]+/gi,'-').slice(0,100)}.png`;let focus=null;if(current&&!current.isClosed()){await current.screenshot({path:resolve(output,file),animations:'disabled'}).catch(()=>{});focus=await current.evaluate(()=>({tag:document.activeElement?.tagName,id:document.activeElement?.id,role:document.activeElement?.getAttribute('role'),label:document.activeElement?.getAttribute('aria-label'),section:document.activeElement?.closest('.character-editor-chapter')?.id})).catch(()=>null);}failures.push({name,message:error.message,file,focus});checks.push({name,status:'failed',message:error.message,file,focus});results.push({check:name,status:'failed',message:error.message,file,focus});}
    await writeFile(resolve(output,'round3-progress.json'),JSON.stringify({mode,groups:[...groups],failures,checks,measures},null,2));
  };
  const settle=async(page)=>{await page.evaluate(async()=>{await document.fonts.ready;await Promise.race([Promise.all([...document.images].filter(el=>el.getClientRects().length).map(el=>el.decode().catch(()=>{}))),new Promise(done=>setTimeout(done,1500))]);});await page.waitForTimeout(250);};
  const fresh=async(width=1920)=>{const page=await pageAt(width,width===390?844:1080,width===390);page.setDefaultTimeout(7000);current=page;return page;};
  const selectTab=async(page,name)=>{const item=page.getByRole('tab',{name,exact:true});if(await item.count())await item.click();else await page.getByRole('button',{name,exact:true}).click();await settle(page);};
  const chapterNavigation=page=>page.getByRole('navigation',{name:'角色手稿章节',exact:true});
  const chapters=page=>chapterNavigation(page).locator('a');
  const begins=text=>new RegExp('^'+text.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'));
  const identityPrefix=text=>new RegExp('^'+text.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'(?:\\s|$)');
  const choose=async(scope,name,option)=>{await scope.getByRole('combobox',{name,exact:true}).click();await current.getByRole('option',{name:option,exact:true}).click();};
  const fill=async(scope,name,value)=>{const field=scope.getByRole('textbox',{name:begins(name)});if(await field.count())await field.fill(value);else await scope.getByLabel(begins(name)).fill(value);};
  const readback=async(page,path)=>{const response=await page.request.get(base+path);assert.equal(response.status(),200);return response.json();};
  const saveFailure=async(page)=>{const trigger=page.getByRole('button',{name:'保存失败，查看原因并重试',exact:true});if(await trigger.count()){await trigger.click();await page.getByRole('alert').first().waitFor();}else await page.getByRole('alert').first().waitFor();};
  const editorDialog=async(page,dialog,hideRuntime)=>{
    await dialog.waitFor();const list=dialog.getByRole('tablist',{name:'角色手稿章节',exact:true}),tabs=list.getByRole('tab');
    assert.equal(await tabs.count(),hideRuntime?8:9,'The real shared editor exposes every host-permitted section');
    assert.equal(await dialog.getByTestId('card-name').count(),1,'The host also has a single name authority');
    for(let n=0;n<await tabs.count();n++){await tabs.nth(n).click();assert.equal(await dialog.locator('.character-editor-chapter:visible').count(),1,'Only the selected section is painted');}
    await tabs.first().focus();await page.waitForTimeout(50);await page.keyboard.press('End');
    await page.waitForFunction(()=>{const active=document.activeElement;return active?.matches('[role="tab"]')&&active===[...active.closest('[role="tablist"]').querySelectorAll('[role="tab"]')].at(-1);},null,{timeout:2000});
    await page.keyboard.press('Enter');assert.equal(await tabs.last().getAttribute('aria-selected'),'true','Keyboard End and activation select the last permitted section');
    await tabs.first().click();const box=await dialog.boundingBox();assert.ok(box.x>=-1&&box.x+box.width<=(await page.viewportSize()).width+1,'Shared production dialog stays inside the actual viewport');
  };
  const resetFailures=()=>{for(const key of ['failBookSave','failScenarioSave','failGeneration','failDraftSave','failCommit'])fixture.state[key]=false;designFixture.failCharacterSave=false;fixture.state.holdAgentRead=false;fixture.releaseAgentReads();};
  const setCheck=async(scope,name,value)=>{const input=scope.getByLabel(name,{exact:true});if(await input.isChecked()!==value)await input.locator('..').click();assert.equal(await input.isChecked(),value,'The actual clickable label toggles its native checkbox');};
  const pageGeometry=page=>page.evaluate(()=>{
    const visible=el=>{const r=el.getBoundingClientRect(),css=getComputedStyle(el);return r.width>0&&r.height>0&&css.display!=='none'&&css.visibility!=='hidden'&&Number(css.opacity)>0&&!el.closest('[hidden]');};
    const rect=el=>{const r=el.getBoundingClientRect();return{x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height};};
    const dialogs=[...document.querySelectorAll('[role="dialog"]')].filter(visible),scope=dialogs.at(-1)||document.querySelector('.tpl-main')||document.body;
    const gold=[...scope.querySelectorAll('.ui-btn--primary')].filter(visible).map(el=>({text:el.textContent.trim(),...rect(el)}));
    const columns=[...document.querySelectorAll('.workbench-column')].filter(visible).map(el=>{
      const parts=['head','body','foot'].map(kind=>{
        let part=[...el.querySelectorAll(`.workbench-column__${kind}`)].find(node=>node.closest('.workbench-column')===el&&visible(node));
        if(!part&&kind==='body')part=[...el.querySelectorAll('.character-persona')].find(visible);
        if(!part)return null;
        let css=getComputedStyle(part),r=part.getBoundingClientRect(),measured='column-padding';
        // A full-width table is an intentional flush container. Its actual first
        // painted cell owns the content gutter; measuring the zero-padding
        // scroll wrapper would falsely report the approved table as misaligned.
        if(kind==='body'&&parseFloat(css.paddingLeft)===0){const cell=[...part.querySelectorAll('thead th:first-child,tbody tr:first-child td:first-child,.character-directory-group')].find(visible);if(cell){part=cell;css=getComputedStyle(cell);r=cell.getBoundingClientRect();measured=cell.matches('th,td')?'first-visible-table-cell':'first-visible-directory-group';}}
        return{kind,measured,edge:r.left+parseFloat(css.borderLeftWidth)+parseFloat(css.paddingLeft),...rect(part)};
      }).filter(Boolean);
      return{class:el.className,...rect(el),parts,edgeSpread:parts.length?Math.max(...parts.map(p=>p.edge))-Math.min(...parts.map(p=>p.edge)):null};
    });
    const desks=[...document.querySelectorAll('.workbench-desk')].filter(visible).map(rect);
    const pages=[...document.querySelectorAll('.workbench-page')].filter(visible).map(rect),main=document.querySelector('.tpl-main');
    const nativeDetails=[...scope.querySelectorAll('details')].filter(visible).length;
    const bareFile=[...scope.querySelectorAll('input[type="file"]')].filter(visible).map(el=>({id:el.id,...rect(el)}));
    const failedImages=[...document.images].filter(visible).filter(el=>el.complete&&!el.naturalWidth).map(el=>({alt:el.alt,src:new URL(el.src).pathname}));
    const rawStatuses=[...scope.querySelectorAll('.ui-tag,[data-status]')].filter(visible).map(el=>el.textContent.trim()).filter(text=>['needs_review','needs_confirmation','agent_running','committing','queued','regenerating','interrupted','review','saved','failed'].includes(text));
    const nameless=[...scope.querySelectorAll('button,input:not([type="hidden"]):not([type="file"]),textarea,[role="switch"],[role="combobox"]')].filter(visible).filter(el=>!el.getAttribute('aria-label')&&!el.getAttribute('aria-labelledby')&&!el.labels?.length&&!el.textContent.trim()&&!el.closest('label')?.textContent.trim()&&!el.title).map(el=>({tag:el.tagName,id:el.id,class:el.className}));
    return{width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,scrollHeight:document.documentElement.scrollHeight,ornament:document.documentElement.dataset.ornament,gold,columns,desks,pages,main:main?rect(main):null,nativeDetails,bareFile,failedImages,rawStatuses,nameless};
  });
  const layout=async(page,label,ornament)=>{
    await settle(page);const geometry=await pageGeometry(page);measures.push({label,...geometry});
    assert.ok(geometry.scrollWidth<=geometry.width+1,`${label}: document horizontal overflow ${geometry.scrollWidth}/${geometry.width}`);
    assert.ok(geometry.scrollHeight<=geometry.height+1,`${label}: document scrolls instead of the column bodies ${geometry.scrollHeight}/${geometry.height}`);
    assert.equal(geometry.ornament,ornament,'Actual production theme/ornament setting applied');
    assert.ok(geometry.desks.length,'A real production workbench desk is rendered');
    if(geometry.main&&geometry.pages.length)assert.ok(geometry.pages[0].width>=geometry.main.width-2,`${label}: authoring page shrinks inside the available workspace ${geometry.pages[0].width}/${geometry.main.width}`);
    assert.ok(geometry.gold.length<=1,`${label}: competing gold primary actions ${geometry.gold.map(button=>button.text)}`);
    for(const desk of geometry.desks)assert.ok(desk.x>=-1&&desk.right<=geometry.width+1&&desk.y>=-1&&desk.bottom<=geometry.height+1,`${label}: main frame leaves the viewport`);
    for(const column of geometry.columns)if(column.parts.length>=2)assert.ok(column.edgeSpread<=1,`${label}: one column's actual content edges differ ${JSON.stringify(column)}`);
    assert.equal(geometry.nativeDetails,0,'No native disclosure triangle remains in the migrated workspace');
    assert.equal(geometry.bareFile.length,0,'Native file chooser is not painted as a product control');
    assert.deepEqual(geometry.nameless,[],'Visible controls have accessible names');
    assert.deepEqual(geometry.failedImages,[],'Visible image failure has a real fallback');
    assert.deepEqual(geometry.rawStatuses,[],'Task status badges show translated product states');
    await shot(page,label);
  };
  const keyboard=async(page,label)=>{
    const start=page.locator('.workbench-desk button:visible:not(:disabled),.workbench-desk input:visible:not([type="file"]):not(:disabled),.workbench-desk textarea:visible:not(:disabled)').first();
    if(!await start.count())return;await start.focus();const samples=[];
    for(let n=0;n<12;n++){
      await page.keyboard.press('Tab');
      const sample=await page.evaluate(()=>{
        const el=document.activeElement,owners=[el,...(el.closest('label')?.querySelectorAll('.ui-check__box,.ui-switch__track')||[])];for(let node=el.parentElement;node&&node!==document.body;node=node.parentElement)owners.push(node);
        const paints=owners.map(node=>{const r=node.getBoundingClientRect(),css=getComputedStyle(node);return{class:node.className,left:r.left,top:r.top,right:r.right,bottom:r.bottom,width:r.width,height:r.height,opacity:Number(css.opacity),outline:css.outlineStyle,outlineWidth:parseFloat(css.outlineWidth),shadow:css.boxShadow};});
        return{tag:el.tagName,id:el.id,label:el.getAttribute('aria-label')||el.textContent?.trim().slice(0,70)||el.labels?.[0]?.textContent?.trim(),focusVisible:el.matches(':focus-visible'),paints};
      });samples.push(sample);if(sample.tag==='BODY')continue;
      assert.ok(sample.paints.some(paint=>paint.width>0&&paint.height>0&&paint.opacity>0),`${label}: keyboard reaches a hidden control ${JSON.stringify(sample)}`);
      assert.ok(sample.paints.some(paint=>paint.opacity>0&&paint.outlineWidth>0&&paint.outline!=='none'),`${label}: keyboard focus has no painted indicator on its actual control/row ${JSON.stringify(sample)}`);
    }
    results.push({check:`keyboard focus ${label}`,samples});await shot(page,`keyboard-${label}`);
  };
  const routes=[
    {group:'editor',name:'editor',path:'/library/characters/char-demo0',ready:'.character-editor-page'},
    {group:'library',name:'lorebook',path:'/library/lorebooks/book-design',ready:'.lorebook-page'},
    {group:'library',name:'scenarios',path:'/library/scenarios/scenario-round3',ready:'.workbench-desk'},
    {group:'create',name:'workshop-character',path:'/create',ready:'.workshop-tabs-root',tab:'角色工坊'},
    {group:'create',name:'inspiration',path:'/create',ready:'.workshop-tabs-root',tab:'角色卡灵感'},
    {group:'create',name:'workshop-lorebook',path:'/create',ready:'.workshop-tabs-root',tab:'世界书工坊'},
    {group:'create',name:'lorebook-agent',path:'/create',ready:'.workshop-tabs-root',tab:'世界书 Agent'},
    {group:'import',name:'imports',path:'/library/imports/job-round3',ready:'.asset-import-page'},
  ].filter(route=>groups.has(route.group));
  const integrityPage=await fresh();
  await check('isolated server serves the exact externally frozen index',async()=>{
    const expected=await readFile(resolve(process.env.UI_ACCEPTANCE_DIST,'index.html')),response=await integrityPage.request.get(base+'/');
    assert.equal(response.status(),200);const actual=await response.body();assert.deepEqual(actual,expected,'The tested HTML is the frozen candidate, without source/dev substitution');
    results.push({check:'frozen index identity',sha256:createHash('sha256').update(expected).digest('hex')});
  });await integrityPage.context().close();
  if(mode!=='behaviors')for(const width of widths)for(const theme of ['iris-light','iris-night'])for(const ornament of ['rich','subtle','none']){
    Object.assign(appearance,{theme_id:theme,decoration_id:ornament});const page=await fresh(width);
    for(const route of routes)await check(`matrix ${route.name} ${width} ${theme} ${ornament}`,async()=>{
      await open(page,route.path,route.ready);if(route.tab)await selectTab(page,route.tab);await layout(page,`matrix-${route.name}-${width}-${theme}-${ornament}`,ornament);
      if(route.name==='editor'){
        const nav=chapterNavigation(page);
        assert.ok(await nav.count()||await page.getByRole('tablist').count(),'Nine-section navigation is present');
        if(width===390)assert.equal(await page.getByRole('tablist',{name:'角色手稿章节',exact:true}).getByRole('tab').count(),9,'Narrow editor retains preview/trial as its ninth section');
        if(width>=1920){const geometry=await pageGeometry(page);assert.equal(geometry.columns.length,3,'Wide editor keeps its directory, main and read-only side panel');assert.ok(Math.abs(geometry.columns[0].width-232)<=1,'Directory retains the approved 232px allocation');assert.ok(Math.abs(geometry.columns.at(-1).width-340)<=1,'Editor side panel retains its approved 340px allocation');assert.ok(Math.max(...geometry.columns.map(col=>col.y))-Math.min(...geometry.columns.map(col=>col.y))<=1,'All three columns begin on the same top edge');}
      }
      if(theme==='iris-light'&&ornament==='rich'&&[1920,390].includes(width))await keyboard(page,`${route.name}-${width}`);
    });await page.context().close();
  }
  Object.assign(appearance,{theme_id:'iris-light',decoration_id:'rich'});
  if(mode!=='matrix'&&groups.has('editor')){
    const page=await fresh();
    await check('editor eight chapters and focus-only ninth preserve every field',async()=>{
      await open(page,'/library/characters/char-demo0','.character-editor-page');
      const nav=chapterNavigation(page);const links=chapters(page);
      const items=await links.evaluateAll(nodes=>nodes.filter(el=>el.getClientRects().length).map(el=>({id:el.getAttribute('href')?.slice(1)||el.dataset.section||'',name:el.textContent.trim()})));
      assert.equal(items.length,8,'Wide editor has eight editable sections; preview uses the right panel');
      for(let n=0;n<items.length;n++){await links.nth(n).click();await settle(page);const headings=page.locator('.character-editor-columns h2');assert.equal(await headings.count(),1);await layout(page,`editor-chapter-${n+1}-1920`,'rich');}
      assert.equal(await page.getByTestId('card-name').count(),1,'Name has one edit authority');
      const focus=page.getByRole('button',{name:'专注',exact:true});await focus.click();
      assert.equal(await nav.locator('a').count(),9,'Focus mode exposes the preview/trial chapter');
      await nav.locator('a').last().click();await page.getByRole('tab',{name:'人设预览',exact:true}).first().click();
      await page.getByLabel('人设预览文本',{exact:true}).waitFor();assert.ok((await page.getByLabel('人设预览文本',{exact:true}).innerText()).includes(characters[0].card.name));await shot(page,'editor-focused-preview');
      await page.getByRole('tab',{name:'试聊',exact:true}).first().click();await page.getByRole('textbox',{name:'试聊消息',exact:true}).fill('第三轮合成试聊');await page.getByRole('button',{name:'发送',exact:true}).click();await page.getByText('合成试聊：第三轮合成试聊',{exact:true}).waitFor();assert.equal(fixture.state.writes.at(-1).path,'/api/v1/characters/preview-turn');
      await chapters(page).first().click();await page.getByTestId('card-description').fill('已变化的人设使旧试聊过期。');await chapters(page).last().click();await page.getByRole('tab',{name:'试聊',exact:true}).first().click();await page.getByText(/这段试聊基于修改前的人设/).waitFor();assert.equal(await page.getByRole('textbox',{name:'试聊消息',exact:true}).isDisabled(),true);await page.getByRole('button',{name:'按当前人设重新试聊',exact:true}).click();assert.equal(await page.getByRole('textbox',{name:'试聊消息',exact:true}).isDisabled(),false);await shot(page,'editor-trial-stale-reset');
    });
    await check('editor failed write retains full snapshot, retry commits once and reload reads saved card',async()=>{
      resetFailures();await open(page,'/library/characters/char-demo0','.character-editor-page');
      await page.getByTestId('card-name').fill('第三轮保存快照');
      await chapters(page).first().click();
      await page.getByTestId('card-description').fill('保存失败时保留的完整合成人物原稿。');
      const before=structuredClone(characters[0]),writes=designFixture.characterWrites.length;designFixture.failCharacterSave=true;
      await page.getByRole('button',{name:'保存',exact:true}).click();await page.getByText(/保存失败/).first().waitFor();
      assert.equal(await page.getByTestId('card-name').inputValue(),'第三轮保存快照');assert.equal(await page.getByTestId('card-description').inputValue(),'保存失败时保留的完整合成人物原稿。');assert.deepEqual(characters[0],before,'A failed save never mutates the synthetic committed record');await shot(page,'editor-save-failure-snapshot');
      designFixture.failCharacterSave=false;await page.getByRole('button',{name:'保存',exact:true}).click();
      await page.waitForFunction(()=>document.body.innerText.includes('已保存'));
      const attempts=designFixture.characterWrites.slice(writes);assert.equal(attempts.length,2);assert.deepEqual(attempts[0],attempts[1],'Manual retry uses the same complete submitted draft/CAS');
      const committed=await readback(page,'/api/v1/characters/char-demo0');assert.equal(committed.card.name,'第三轮保存快照');assert.equal(committed.card.description,'保存失败时保留的完整合成人物原稿。');assert.equal(committed.revision,before.revision+1);
      for(const key of ['appearance','traits','personality','scenario','first_mes','mes_example','system_prompt','post_history_instructions','creator','character_version','creator_notes','alternate_greetings','extensions'])assert.deepEqual(committed.card[key],before.card[key],`Saving the revised layout preserves ${key}`);
      await page.reload();await page.getByTestId('card-name').waitFor();assert.equal(await page.getByTestId('card-name').inputValue(),'第三轮保存快照');await shot(page,'editor-committed-reloaded');
    });resetFailures();await page.context().close();
  }
  if(mode!=='matrix'&&groups.has('library')){
    const page=await fresh();
    await check('worldbook group, no-result filter, selection and twenty-entry pagination',async()=>{
      await open(page,'/library/lorebooks/book-design','.lorebook-page');
      await page.getByText('未指定世界',{exact:true}).first().waitFor();await page.getByText(fixture.worlds[0].title,{exact:true}).first().waitFor();await page.getByText(fixture.worlds[1].title,{exact:true}).first().waitFor();
      assert.equal(await page.locator('.lorebook-table tbody tr').count(),20);await page.getByRole('button',{name:'下一页',exact:true}).click();assert.equal(await page.locator('.lorebook-table tbody tr').count(),20);await page.getByRole('button',{name:'下一页',exact:true}).click();assert.equal(await page.locator('.lorebook-table tbody tr').count(),3);await page.getByRole('button',{name:'上一页',exact:true}).click();
      await page.getByRole('textbox',{name:'搜索世界书',exact:true}).fill('不存在的合成书');await page.getByText(/没有符合|没有匹配|未找到/).first().waitFor();
      await page.getByRole('textbox',{name:'搜索世界书',exact:true}).fill('');await page.getByRole('button',{name:'多选',exact:true}).click();await page.getByRole('button',{name:'选择当前结果',exact:true}).click();assert.equal(await page.getByRole('checkbox',{checked:true}).count(),fixture.books.length);await page.getByRole('button',{name:'导出所选',exact:true}).click();const exportDialog=page.getByRole('dialog',{name:'导出所选素材',exact:true});await exportDialog.getByText(`共 ${fixture.books.length} 项；头像 0 张。`,{exact:true}).waitFor();await page.keyboard.press('Escape');await exportDialog.waitFor({state:'hidden'});assert.equal(await page.getByRole('checkbox',{checked:true}).count(),fixture.books.length,'Cancelling export preserves selection');await page.getByRole('button',{name:'清空',exact:true}).click();assert.equal(await page.getByRole('checkbox',{checked:true}).count(),0);await page.getByRole('button',{name:'退出',exact:true}).click();
      await page.getByRole('button',{name:/世界书筛选/}).click();await choose(page,'世界书标签筛选','航海');await page.keyboard.press('Escape');assert.ok((await page.getByRole('textbox',{name:'搜索世界书',exact:true}).count())>0);await shot(page,'lorebook-group-filter-selection-pages');
    });
    await check('worldbook twelve editable fields fail safely, persist through reload and preserve UID/extensions',async()=>{
      resetFailures();await open(page,'/library/lorebooks/book-design','.lorebook-page');
      const row=page.locator('.lorebook-table tbody tr').first();await row.focus();await page.keyboard.press('Enter');
      await page.getByRole('button',{name:'编辑条目',exact:true}).click();const dialog=page.getByRole('dialog',{name:/编辑.*条目|编辑词条/});await dialog.waitFor();
      const original=structuredClone(fixture.books[0].entries[0]),writes=fixture.state.writes.length;
      await fill(dialog,'词条名称 / 备注','第三轮全字段保存');await dialog.getByLabel(/触发关键词/).fill('灯塔，航海');await fill(dialog,'辅助关键词','夜晚，涨潮');await fill(dialog,'词条内容','失败后保留且提交后可读回的合成全文。');
      await choose(dialog,'关键词组合','所有辅助关键词匹配');await choose(dialog,'注入位置','指定深度');await dialog.getByLabel('注入深度',{exact:true}).fill('7');await dialog.getByLabel('排序权重',{exact:true}).fill('142');await dialog.getByLabel('触发概率 %',{exact:true}).fill('63');
      await setCheck(dialog,'常驻，每轮使用',true);await setCheck(dialog,'使用辅助关键词',true);await setCheck(dialog,'启用，可用于故事',false);
      fixture.state.failBookSave=true;await dialog.getByRole('button',{name:/保存/}).last().click();await dialog.getByRole('alert').waitFor();assert.equal(await dialog.getByRole('textbox',{name:'词条内容',exact:true}).inputValue(),'失败后保留且提交后可读回的合成全文。');assert.deepEqual(fixture.books[0].entries[0],original);await shot(page,'lorebook-full-fields-save-failed');
      fixture.state.failBookSave=false;await dialog.getByRole('button',{name:/保存/}).last().click();await dialog.waitFor({state:'hidden'});const saved=fixture.books[0].entries[0];
      assert.equal(saved.uid,original.uid);assert.deepEqual(saved.extensions,original.extensions);assert.deepEqual(saved.keys,['灯塔','航海']);assert.deepEqual(saved.secondary_keys,['夜晚','涨潮']);assert.equal(saved.comment,'第三轮全字段保存');assert.equal(saved.content,'失败后保留且提交后可读回的合成全文。');assert.equal(saved.anchor,'at_depth');assert.equal(saved.depth,7);assert.equal(saved.order,142);assert.equal(saved.probability,63);assert.equal(saved.selective_logic,3);assert.equal(saved.enabled,false);assert.equal(saved.constant,true);assert.equal(saved.selective,true);
      assert.equal(fixture.state.writes.slice(writes).filter(write=>write.path==='/api/v1/lorebooks/book-design').length,2);await page.reload();await page.locator('.lorebook-table').waitFor();assert.ok((await page.locator('.lorebook-table').innerText()).includes('第三轮全字段保存'));await shot(page,'lorebook-full-fields-readback');
    });resetFailures();
    await check('world-scoped source comparison preserves manual content and records reviewed revision',async()=>{
      await open(page,'/worlds/world-demo0/lorebooks/book-design','.lorebook-page');const row=page.locator('[data-entry-uid="1"]');await row.click();await page.getByRole('button',{name:'来源有更新 · 比对',exact:true}).click();const dialog=page.getByRole('dialog',{name:'比对档案来源',exact:true});await dialog.waitFor();const old=fixture.books[0].entries[0].content;
      await dialog.getByRole('button',{name:'保留手改，标记已核对',exact:true}).click();await dialog.waitFor({state:'hidden'});assert.equal(fixture.books[0].entries[0].content,old);assert.equal(fixture.books[0].entries[0].extensions['mrp.archive_source'].reviewed_revision,fixture.archive.revision);assert.equal(fixture.books[0].entries[0].extensions['mrp.archive_source'].archive_revision,1);await shot(page,'lorebook-source-manual-content-preserved');
    });
    await check('scenario five steps retain draft, failed save preserves snapshot and write reads back',async()=>{
      resetFailures();await open(page,'/library/scenarios','.workbench-desk');await page.getByRole('button',{name:'制作预设',exact:true}).first().click();
      await page.getByPlaceholder('例如：示例开场',{exact:true}).fill('第三轮五步合成预设');await page.getByPlaceholder('用几句话说明故事背景和体验重点',{exact:true}).fill('保留所有填写步骤的描述。');
      await page.getByRole('button',{name:'下一步',exact:true}).click();await page.getByRole('checkbox',{name:identityPrefix(characters[0].card.name)}).check();
      await page.getByRole('button',{name:'下一步',exact:true}).click();await shot(page,'scenario-step3');await page.getByRole('button',{name:'下一步',exact:true}).click();await page.getByPlaceholder('例如：潮汐书店',{exact:true}).fill('合成灯塔前庭');await fill(page,'开场旁白','保存失败也保留的合成开场旁白。');await page.getByRole('button',{name:'下一步',exact:true}).click();
      fixture.state.failScenarioSave=true;const before=fixture.scenarios.length,writes=fixture.state.writes.length;await page.getByRole('button',{name:'保存场景预设',exact:true}).click();await page.getByRole('alert').waitFor();assert.equal(fixture.scenarios.length,before);await page.getByRole('button',{name:'上一步',exact:true}).click();assert.equal(await page.getByPlaceholder('例如：潮汐书店',{exact:true}).inputValue(),'合成灯塔前庭');await page.getByRole('button',{name:'下一步',exact:true}).click();await shot(page,'scenario-failure-kept-draft');
      fixture.state.failScenarioSave=false;await page.getByRole('button',{name:'保存场景预设',exact:true}).click();await page.getByText('第三轮五步合成预设',{exact:true}).first().waitFor();assert.equal(fixture.scenarios.length,before+1);const writesAfter=fixture.state.writes.slice(writes).filter(write=>write.path==='/api/v1/scenarios');assert.equal(writesAfter.length,2);assert.deepEqual(writesAfter[0].body,writesAfter[1].body);
      const saved=fixture.scenarios.at(-1),reply=await readback(page,`/api/v1/scenarios/${saved.id}`);assert.equal(reply.title,'第三轮五步合成预设');assert.equal(reply.opening.location,'合成灯塔前庭');assert.equal(reply.opening.narration,'保存失败也保留的合成开场旁白。');assert.equal(reply.cast.length,1);await page.reload();await page.getByText('第三轮五步合成预设',{exact:true}).first().waitFor();await shot(page,'scenario-committed-readback');
    });resetFailures();await page.context().close();
  }
  if(mode!=='matrix'&&groups.has('create')){
    const page=await fresh();
    await check('workshop generation failure keeps inputs, retry and real candidate editor save',async()=>{
      resetFailures();await open(page,'/create','.workshop-tabs-root');await selectTab(page,'角色工坊');await fill(page,'一句话需求','合成灯塔守望者');await fill(page,'补充设定','只使用隔离合成输入。');
      fixture.state.failGeneration=true;await page.getByRole('button',{name:'生成候选角色',exact:true}).click();await page.getByRole('alert').waitFor();assert.equal(await page.getByLabel(begins('一句话需求')).inputValue(),'合成灯塔守望者');fixture.state.failGeneration=false;await page.getByRole('button',{name:/重试|生成候选角色/}).first().click();await page.getByRole('button',{name:/合成候选记录者 1/}).click();
      const dialog=page.locator('.character-editor-dialog');await editorDialog(page,dialog,false);await dialog.getByTestId('card-name').fill('第三轮真实候选入库');const before=characters.length;await dialog.getByRole('button',{name:/创建角色|保存到角色库|保存角色/}).last().click();await dialog.waitFor({state:'hidden'});assert.equal(characters.length,before+1);assert.equal(characters.at(-1).card.name,'第三轮真实候选入库');await shot(page,'workshop-real-candidate-committed');
    });resetFailures();
    await check('worldbook workshop generated entries are editable and committed book is read back',async()=>{
      await open(page,'/create','.workshop-tabs-root');await selectTab(page,'世界书工坊');await fill(page,'主题 / 世界观描述','合成星海航线');await page.getByRole('button',{name:'开始生成',exact:true}).click();await page.getByLabel(/^内容/).first().waitFor();await page.getByLabel(/^内容/).first().fill('第三轮工坊编辑过的合成条目。');const before=fixture.books.length;await page.getByRole('button',{name:/入库|保存为世界书/}).last().click();await page.getByText(/已入库|保存成功/).first().waitFor();assert.equal(fixture.books.length,before+1);assert.equal(fixture.books.at(-1).entries[0].content,'第三轮工坊编辑过的合成条目。');const reply=await readback(page,`/api/v1/lorebooks/${fixture.books.at(-1).id}`);assert.equal(reply.entries[0].content,'第三轮工坊编辑过的合成条目。');await shot(page,'workshop-worldbook-committed');
    });
    await check('Agent late poll cannot revive a cancelled task',async()=>{
      resetFailures();const job=fixture.agentJobs[0];job.status='running';job.stage='合成运行阶段';
      try{
        await open(page,'/create','.workshop-tabs-root');await selectTab(page,'世界书 Agent');await page.getByRole('button',{name:'取消任务',exact:true}).waitFor();fixture.state.holdAgentRead=true;
        for(let n=0;n<80&&!fixture.state.pendingAgentReads.length;n++)await page.waitForTimeout(50);assert.equal(fixture.state.pendingAgentReads.length,1,'An actual HTTP poll is suspended after freezing its old response');
        await page.getByRole('button',{name:'取消任务',exact:true}).click();await page.getByRole('button',{name:'恢复任务',exact:true}).waitFor();assert.equal(job.status,'cancelled');fixture.releaseAgentReads();await page.waitForTimeout(500);
        assert.equal(await page.getByRole('button',{name:'取消任务',exact:true}).count(),0,'The delivered old running snapshot cannot reinstall an active task');assert.equal(await page.getByRole('button',{name:'恢复任务',exact:true}).isVisible(),true);await shot(page,'agent-cancel-late-poll-rejected');
      }finally{fixture.releaseAgentReads();fixture.state.holdAgentRead=false;job.status='review';}
    });
    await check('Agent history A to B to A rejects the first A poll response',async()=>{
      resetFailures();const first=fixture.agentJobs[0],second={...structuredClone(first),id:'agent-history-b-round3',world_id:fixture.worlds[1].id,world_title:fixture.worlds[1].title,source_ids:[],status:'review',stage:'B合成审阅'};fixture.agentJobs.push(second);first.status='running';first.stage='A旧运行快照';
      try{
        await open(page,'/create','.workshop-tabs-root');await selectTab(page,'世界书 Agent');await page.getByRole('button',{name:'取消任务',exact:true}).waitFor();fixture.state.holdAgentRead=true;
        for(let n=0;n<80&&!fixture.state.pendingAgentReads.length;n++)await page.waitForTimeout(50);assert.equal(fixture.state.pendingAgentReads.length,1);
        await choose(page,'最近任务',begins(second.world_title));await page.waitForTimeout(200);assert.ok((await page.getByRole('combobox',{name:'最近任务',exact:true}).innerText()).includes(second.world_title));
        first.status='review';first.stage='A新审阅快照';await choose(page,'最近任务',begins(first.world_title));await page.waitForTimeout(200);assert.equal(await page.getByRole('button',{name:'取消任务',exact:true}).count(),0);
        fixture.releaseAgentReads();await page.waitForTimeout(500);assert.equal(await page.getByRole('button',{name:'取消任务',exact:true}).count(),0,'A repeated task ID does not make the earlier A attempt current');assert.ok((await page.getByRole('combobox',{name:'最近任务',exact:true}).innerText()).includes(first.world_title));await shot(page,'agent-history-ABA-old-poll-rejected');
      }finally{fixture.releaseAgentReads();fixture.state.holdAgentRead=false;first.status='review';fixture.agentJobs.splice(fixture.agentJobs.indexOf(second),1);}
    });
    await check('Agent draft saves and simulates before actual synthetic commit',async()=>{
      await open(page,'/create','.workshop-tabs-root');await selectTab(page,'世界书 Agent');await page.getByRole('button',{name:'保存并模拟',exact:true}).waitFor();const before=fixture.books.length;await page.getByRole('button',{name:'保存并模拟',exact:true}).click();await page.getByRole('button',{name:'确认写入世界书',exact:true}).waitFor();await page.getByRole('button',{name:'确认写入世界书',exact:true}).click();await page.getByText(/已写入|已保存/).first().waitFor();assert.equal(fixture.books.length,before+1);assert.equal(fixture.agentJobs[0].drafts[0].status,'committed');assert.ok(fixture.agentJobs[0].drafts[0].simulation?.valid);await shot(page,'workshop-agent-reviewed-committed');
    });await page.context().close();
  }
  if(mode!=='matrix'&&groups.has('import')){
    const page=await fresh();
    await check('inspiration host opens real shared editor, failed draft save retains fields and reload reads revision',async()=>{
      resetFailures();await open(page,'/create','.workshop-tabs-root');await selectTab(page,'角色卡灵感');await page.getByRole('tab',{name:/^灵感任务/}).click();await page.getByRole('button',{name:/合成灵感审阅任务/}).click();await page.getByRole('button',{name:'编辑、试聊并审阅',exact:true}).first().click();const dialog=page.locator('.character-editor-dialog');await editorDialog(page,dialog,true);await dialog.getByTestId('card-name').fill('第三轮灵感草稿修订');fixture.state.failDraftSave=true;await dialog.getByRole('button',{name:'保存草稿',exact:true}).click();await saveFailure(page);assert.equal(await dialog.getByTestId('card-name').inputValue(),'第三轮灵感草稿修订');await page.keyboard.press('Escape');fixture.state.failDraftSave=false;await dialog.getByRole('button',{name:'保存草稿',exact:true}).click();await page.waitForTimeout(400);assert.equal(fixture.inspirationJobs[0].drafts[0].payload.name,'第三轮灵感草稿修订');const before=characters.length;await dialog.getByRole('button',{name:/保存到角色库|入库|创建角色/}).last().click();await dialog.waitFor({state:'hidden'});assert.equal(characters.length,before+1);assert.equal(characters.at(-1).card.name,'第三轮灵感草稿修订');await shot(page,'inspiration-real-editor-committed');
    });resetFailures();
    await check('import regenerated proposal, original draft restore and reapply remain separate',async()=>{
      const job=fixture.importJobs.find(job=>job.id==='job-round3'),prior=job.drafts[0].payload.personality;
      await open(page,'/library/imports/job-round3/drafts/draft-character-round3','.character-editor-dialog');let dialog=page.locator('.character-editor-dialog');
      await dialog.getByRole('button',{name:'重新生成',exact:true}).click();await dialog.waitFor({state:'hidden'});
      assert.equal(job.drafts[0].payload.personality,prior,'Regeneration retains the previously edited draft');assert.equal(job.drafts[0].proposal.payload.personality,'合成重新生成性格');
      await open(page,'/library/imports/job-round3/drafts/draft-character-round3','.character-editor-dialog');dialog=page.locator('.character-editor-dialog');await dialog.getByRole('tab',{name:/性格与关系/}).click();assert.equal(await dialog.getByTestId('card-personality').inputValue(),'合成重新生成性格');
      await dialog.getByRole('button',{name:'恢复旧稿',exact:true}).click();await dialog.getByRole('tab',{name:/性格与关系/}).click();assert.equal(await dialog.getByTestId('card-personality').inputValue(),prior);await dialog.getByRole('button',{name:'重新应用新稿',exact:true}).click();await dialog.getByRole('tab',{name:/性格与关系/}).click();assert.equal(await dialog.getByTestId('card-personality').inputValue(),'合成重新生成性格');
      assert.equal(job.drafts[0].payload.personality,prior,'Local adoption and restore have not committed a character');await shot(page,'import-proposal-original-reapplied');
    });
    await check('import host source and saved character use real editor, remaining drafts remain independent',async()=>{
      await open(page,'/library/imports/job-round3/drafts/draft-character-round3','.character-editor-dialog');const dialog=page.locator('.character-editor-dialog');await editorDialog(page,dialog,true);await dialog.getByTestId('card-name').fill('第三轮导入人物修订');const other=structuredClone(fixture.importJobs.find(job=>job.id==='job-round3').drafts.slice(1));const before=characters.length;fixture.state.failDraftSave=true;await dialog.getByRole('button',{name:'确认并保存人物卡',exact:true}).click();await saveFailure(page);assert.equal(await dialog.getByTestId('card-name').inputValue(),'第三轮导入人物修订');assert.equal(characters.length,before);await page.keyboard.press('Escape');fixture.state.failDraftSave=false;await dialog.getByRole('button',{name:'确认并保存人物卡',exact:true}).click();await page.waitForTimeout(500);assert.equal(characters.length,before+1);const job=fixture.importJobs.find(job=>job.id==='job-round3');assert.equal(job.drafts[0].status,'saved');assert.equal(characters.at(-1).card.name,'第三轮导入人物修订');assert.deepEqual(job.drafts.slice(1),other);await shot(page,'import-real-editor-saved');
      const reply=await readback(page,'/api/v1/asset-import-jobs/job-round3');assert.equal(reply.drafts[0].saved_asset_id,characters.at(-1).id);
    });resetFailures();
    for(const [id,kind]of [['draft-background-round3','世界背景'],['draft-biology-round3','种族']])await check(`import ${kind} review retains source and content on cancel`,async()=>{
      const before=structuredClone(fixture.importJobs.find(job=>job.id==='job-round3').drafts);await open(page,`/library/imports/job-round3/drafts/${id}`,'.asset-import-dialog');await page.getByText(/原稿|来源/).first().waitFor();await page.keyboard.press('Escape');assert.deepEqual(fixture.importJobs.find(job=>job.id==='job-round3').drafts,before);await shot(page,`import-cancel-${id}`);
    });await page.context().close();
  }
  resetFailures();
  const journal={requests:fixture.state.requests,writes:fixture.state.writes};await writeFile(resolve(output,'round3-api-journal.json'),JSON.stringify(journal,null,2));
  const summary={mode,groups:[...groups],widths,checks,failures,measurements:measures.length,limits:['Actual frozen production React with an isolated in-memory API; no private data, live service or model provider is contacted.','Committed UI readback proves the simulated HTTP contract only, not disk persistence, server restart, real model quality or migration.','Screenshots and computed geometry require independent human visual review.','390px is browser emulation, not a physical device.','Synthetic export response exercises the UI boundary only and is not a validated archive.']};
  await writeFile(resolve(output,'round3-summary.json'),JSON.stringify(summary,null,2));
  await writeFile(resolve(output,'round3-fixture-hash.json'),JSON.stringify(Object.fromEntries(await Promise.all(['roundThreeFixture.mjs','roundThreeProductionAcceptance.mjs'].map(async name=>[name,createHash('sha256').update(await readFile(new URL(name,import.meta.url))).digest('hex')]))),null,2));
  assert.deepEqual(failures,[],'Third-round production acceptance failures; inspect unchanged failure evidence and request journals');
}

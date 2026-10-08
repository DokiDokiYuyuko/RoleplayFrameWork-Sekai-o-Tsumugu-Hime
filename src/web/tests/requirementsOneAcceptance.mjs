/** Requirement document 1: isolated real React checks; never a static demo. */
import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

export async function checkRequirementsOneUI(h) {
  const {pageAt,open,shot,bounds,results,appearance,characters,designFixture,output}=h;
  const failures=[]; let current;
  const check=async(name,fn)=>{try{await fn();results.push({check:name,status:'passed'});}catch(error){const file=`failure-${name.replace(/[^a-z0-9]+/gi,'-')}.png`;if(current&&!current.isClosed())await current.screenshot({path:resolve(output,file)}).catch(()=>{});failures.push({name,message:error.message,file});results.push({check:name,status:'failed',message:error.message});}await writeFile(resolve(output,'progress.json'),JSON.stringify({failures,results},null,2));};
  const settled=async(page)=>{await page.evaluate(()=>document.fonts.ready);await page.waitForTimeout(250);};
  const chapter=async(page,id,width)=>{if(width<900&&await page.getByRole('button',{name:'章节目录',exact:true}).getAttribute('aria-expanded')!=='true')await page.getByRole('button',{name:'章节目录',exact:true}).click();const link=page.getByRole('navigation',{name:'角色手稿章节',exact:true}).locator(`a[href="#${id}"]`);await link.click();await settled(page);};
  const shell=page=>page.evaluate(()=>Object.fromEntries(['.character-editor-header','.character-editor-directory','.character-editor-columns','.character-editor-body','.character-editor-related'].map(selector=>{const el=document.querySelector(selector),r=el?.getBoundingClientRect();return[selector,r?{x:r.x,y:r.y,width:r.width,height:r.height}:null];})));
  const onlyReuse=process.env.UI_REQUIREMENTS_1_ONLY_REUSE==='1';
  for(const [width,theme,ornament] of onlyReuse?[]:[[1920,'iris-light','rich'],[2550,'iris-night','rich'],[390,'iris-light','subtle'],[320,'iris-night','none']]) {
    Object.assign(appearance,{theme_id:theme,decoration_id:ornament}); const page=await pageAt(width,width<900?844:1080,width<900);current=page;
    await check(`editor chapters ${width} ${theme} ${ornament}`,async()=>{
      await open(page,'/library/characters/char-demo0','.character-editor-page');await settled(page);
      if(width<900)await page.getByRole('button',{name:'章节目录',exact:true}).click();
      const links=await page.getByRole('navigation',{name:'角色手稿章节',exact:true}).locator('a').evaluateAll(nodes=>nodes.map(el=>({id:el.hash.slice(1),name:el.textContent.trim()})));
      assert.equal(links.length,10);
      for(const item of width===1920&&process.env.UI_REQUIREMENTS_1_QUICK!=='1'?links:links.filter(item=>['character-manuscript','character-profile'].includes(item.id))) {
        await chapter(page,item.id,width);
        const main=page.locator('.character-editor-columns');
        assert.equal(await main.locator('.character-panel-heading h2').count(),1);
        assert.equal((await main.locator('.character-panel-heading h2').innerText()).trim(),item.name);
        const headings=await main.locator('h1,h2,h3').evaluateAll(nodes=>nodes.filter(el=>el.getClientRects().length).map(el=>el.textContent.trim()));
        assert.equal(headings.filter(name=>name===item.name).length,1,'Current chapter has exactly one visible primary heading');
        if(item.id==='character-manuscript'){assert.equal(await main.locator('.mw-writing-surface').count(),0,'Whole-page manuscript removes the duplicate writing card');assert.equal(await main.getByTestId('card-description').isVisible(),true);assert.equal(await main.getByRole('button',{name:'AI 改写',exact:true}).isVisible(),true);}
        await bounds(page,`req1-${item.id}-${width}`);await shot(page,`chapter-${item.id}-${width}-${theme}-${ornament}`);
      }
      const seam=await shell(page);results.push({check:`shell seam ${width}`,rects:seam});
      if(process.env.UI_REQUIREMENTS_1_SEAM==='1'&&width>=900)for(const selector of ['.character-editor-directory','.character-editor-columns','.character-editor-related'])assert.ok(Math.abs(seam[selector].y-seam['.character-editor-header'].y-seam['.character-editor-header'].height)<=1,'Every panel begins exactly at the toolbar seam');
      await page.locator('.character-editor-header').screenshot({path:resolve(output,`seam-header-${width}-${theme}-${ornament}.png`)});
    });
    await check(`library five media states ${width}`,async()=>{
      await open(page,'/library/characters','[data-testid="character-card"]');await settled(page);
      assert.equal(await page.getByTestId('character-card').count(),6);
      const geometry=await page.getByTestId('character-card').evaluateAll(cards=>cards.map(card=>{const r=card.getBoundingClientRect(),body=card.querySelector('.character-dossier-portrait'),avatar=card.querySelector('.character-dossier-avatar');return{name:card.querySelector('h3')?.textContent,y:r.y,body:body?.getBoundingClientRect().toJSON(),avatar:avatar?.getBoundingClientRect().toJSON(),bodyState:body?.getAttribute('data-image-state'),avatarState:avatar?.getAttribute('data-image-state'),bodySrc:body?.querySelector('img[src*="/full-body"]')?.getAttribute('src'),avatarSrc:avatar?.querySelector('img[src*="/avatar"]')?.getAttribute('src')};}));
      assert.ok(Math.max(...geometry.map(a=>geometry.filter(b=>Math.abs(a.y-b.y)<2).length))<=3);
      for(const card of geometry){assert.ok(card.body&&card.avatar);assert.ok(Math.abs(card.body.width/card.body.height-9/16)<.01);assert.ok(card.avatar.x>=card.body.right,'Avatar occupies the separate information area');}
      assert.equal(await page.locator('.character-portrait-caption').count(),0,'No caption strip remains below the main image');
      assert.ok(geometry[0].bodySrc?.includes('/full-body'));assert.ok(geometry[0].avatarSrc?.includes('/avatar'));results.push({check:`media geometry ${width}`,geometry});
      await bounds(page,`req1-library-${width}`);await shot(page,`library-media-states-${width}-${theme}-${ornament}`);
      if(width<900){const title=await page.getByRole('heading',{name:'角色档案',exact:true}).evaluate(el=>({height:el.getBoundingClientRect().height,font:parseFloat(getComputedStyle(el).fontSize)}));assert.ok(title.height<=title.font*2,'Narrow library title remains horizontal');await page.getByTestId('character-card').first().scrollIntoViewIfNeeded();await shot(page,`library-firstcard-visible-${width}-${theme}-${ornament}`);}
      if(width===1920)for(const kind of ['full-body','avatar']){const button=page.getByTestId('character-card').first().locator(`[data-image-kind="${kind}"]`);await button.focus();await page.keyboard.press('Enter');const dialog=page.getByRole('dialog');await dialog.waitFor();const image=dialog.locator('img').first();assert.ok((await image.getAttribute('src')).includes(`/${kind}`),'Keyboard preview binds the correct original media endpoint');await shot(page,`keyboard-preview-${kind}`);await page.keyboard.press('Escape');await dialog.waitFor({state:'hidden'});assert.equal(await button.evaluate(el=>el===document.activeElement),true);}
    });
    await page.context().close();
  }
  if(process.env.UI_REQUIREMENTS_1_QUICK==='1'){await writeFile(resolve(output,'requirements-one-summary.json'),JSON.stringify({failures,scope:'Fast layout review only; all ten chapters and mutation/reuse checks require the full run'},null,2));assert.deepEqual(failures,[]);return;}
  if(!onlyReuse) {
  Object.assign(appearance,{theme_id:'iris-light',decoration_id:'rich'});const page=await pageAt(1920,1080);current=page;
  await check('save states preserve shell geometry and original identity',async()=>{
    await open(page,'/library/characters/char-demo0','.character-editor-page');await chapter(page,'character-profile',1920);const name=page.getByTestId('card-name');await name.fill('接缝保存验收');const before=await shell(page);await shot(page,'save-before-dirty');
    designFixture.failCharacterSave=true;await page.getByRole('button',{name:'保存',exact:true}).click();await page.getByText(/保存失败/).first().waitFor();assert.equal(await name.inputValue(),'接缝保存验收');assert.deepEqual(await shell(page),before);await shot(page,'save-failure-retained');
    designFixture.failCharacterSave=false;await page.getByRole('button',{name:'保存',exact:true}).click();for(let n=0;n<100&&characters[0].card.name!=='接缝保存验收';n++)await page.waitForTimeout(50);assert.equal(characters[0].card.name,'接缝保存验收');assert.deepEqual(await shell(page),before);await shot(page,'save-after-committed');
    await page.getByLabel('更多角色操作',{exact:true}).click();const count=characters.length;await page.getByRole('button',{name:'另存副本',exact:true}).click();for(let n=0;n<100&&characters.length===count;n++)await page.waitForTimeout(50);assert.equal(characters.length,count+1);assert.equal(new URL(page.url()).pathname,'/library/characters/char-demo0');await page.getByText(/副本已保存/).first().waitFor();await shot(page,'save-copy-feedback');
  });designFixture.failCharacterSave=false;await page.context().close();
  }
  const shared=await pageAt(1920,1080);current=shared;
  const verifyShared=async(label)=>{const dialog=shared.locator('.character-editor-dialog');await dialog.waitFor();assert.equal(await dialog.locator('.mw-writing-surface').count(),1,'Shared dialog keeps its necessary writing container');assert.equal(await dialog.getByTestId('card-description').isVisible(),true);assert.ok((await dialog.innerText()).includes('人物原稿'));assert.ok(await dialog.getByRole('button',{name:/保存|创建角色/}).count()>0);await shot(shared,`reuse-${label}`);await shared.keyboard.press('Escape');await dialog.waitFor({state:'hidden'});};
  await check('shared workshop retains dialog headings and operations',async()=>{await open(shared,'/workshop','.character-editor-dialog');await verifyShared('workshop-dialog');});
  await check('shared inspiration draft retains headings and operations',async()=>{await open(shared,'/workshop','.character-editor-dialog');await shared.keyboard.press('Escape');await shared.locator('.character-editor-dialog').waitFor({state:'hidden'});await shared.getByRole('button',{name:'角色卡灵感',exact:true}).click();await shared.getByRole('button',{name:/合成灵感复用验收/}).click();await shared.getByRole('button',{name:'编辑、试聊并审阅',exact:true}).click();await verifyShared('inspiration-draft');});
  await check('shared import review retains headings and operations',async()=>{await open(shared,'/library/imports/job-reuse/drafts/draft-reuse','.character-editor-dialog');await verifyShared('import-review');});
  results.push({check:'CharacterInfoModal edit reachability',status:'not-applicable',reason:'Current production callers provide readOnly; no editable UI entry is fabricated. Dialog reuse is exercised through the real workshop path.'});await shared.context().close();
  await writeFile(resolve(output,'requirements-one-summary.json'),JSON.stringify({failures,limits:['Frozen production React with synthetic media only','Visual seam quality requires independent main review','Shared editor entry checks are tracked separately']},null,2));assert.deepEqual(failures,[]);
}

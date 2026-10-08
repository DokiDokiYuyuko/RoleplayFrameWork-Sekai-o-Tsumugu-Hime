/** Production React acceptance, invoked by UI_ACCEPTANCE_ONLY_PRODUCTION_DESIGN=1.
 * Reuses uiAcceptance's isolated synthetic API and accepts only a frozen dist.
 * Screenshots and DOM measurements are evidence for review, not pixel approval.
 */
import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

export async function checkProductionDesignUI(h) {
  const { pageAt, open, shot, bounds, results, appearance, characters, output, designFixture, testImage } = h;
  const failures = [];
  let activePage;
  const routes = [
    ['characters', '/library/characters', '[data-testid="character-card"]'],
    ['editor', '/library/characters/char-demo0', '.character-editor-page'],
    ['story', '/stories/sess-demo1/branches/sess-demo1', '.v7-message'],
    ['chat', '/simple-chats/chat-demo1', '.sc-bubble'],
    ['worlds', '/worlds', '.worlds-card-grid'],
    ['lorebook', '/library/lorebooks/book-design', '.lorebook-page'],
    ['settings', '/settings/preferences', '.v7-settings'],
  ];
  async function check(name, fn) {
    try { await fn(); results.push({ check: name, status: 'passed' }); }
    catch (error) {
      const filename=`failure-${name.replace(/[^a-z0-9]+/gi,'-').slice(0,110)}.png`;
      if(activePage&&!activePage.isClosed()) await activePage.screenshot({path:resolve(output,filename),animations:'disabled'}).catch(()=>{});
      failures.push({check:name,message:error.message,screenshot:filename}); results.push({check:name,status:'failed',message:error.message,screenshot:filename});
    }
    await writeFile(resolve(output,'progress.json'),JSON.stringify({failures,results},null,2));
  }
  async function settle(page) {
    await page.evaluate(async () => {
      await document.fonts.ready;
      const visible = [...document.images].filter(image => { const r=image.getBoundingClientRect(); return r.width && r.height && r.bottom>0 && r.top<innerHeight; });
      await Promise.race([Promise.all(visible.map(image=>image.decode().catch(()=>{}))),new Promise(done=>setTimeout(done,3000))]);
    });
    await page.waitForTimeout(150);
  }
  async function geometry(page) {
    return page.evaluate(() => {
      const visible = el => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
      const rect = el => { const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height }; };
      const layerStyle=[...document.head.querySelectorAll('style')].find(el=>el.textContent.replace(/\s+/g,'').includes('@layerlegacy,ui.reset,ui.tokens,ui.primitives,ui.layouts,ui.pages;'));
      const firstSheet=document.head.querySelector('link[rel="stylesheet"]');
      return { width: innerWidth, scrollWidth: document.documentElement.scrollWidth, ornament: document.documentElement.dataset.ornament,
        earlyLayers:!!layerStyle&&(!firstSheet||!!(layerStyle.compareDocumentPosition(firstSheet)&Node.DOCUMENT_POSITION_FOLLOWING)),
        controlStyles: [...document.querySelectorAll('button.ui-btn')].filter(visible).map(el=>({text:el.textContent.trim().slice(0,60),display:getComputedStyle(el).display,parentDisplay:getComputedStyle(el.parentElement).display,position:getComputedStyle(el).position,intentionalPosition:el.matches('.sc-group-title,.sc-jump-latest'),skin:el.dataset.skin,tokenHeight:parseFloat(getComputedStyle(el).getPropertyValue('--btn-h')),...rect(el)})),
        navStyles:[...document.querySelectorAll('.ui-navitem')].filter(visible).map(el=>({display:getComputedStyle(el).display,position:getComputedStyle(el).position,height:rect(el).height,tokenHeight:parseFloat(getComputedStyle(el).getPropertyValue('--size-nav-item'))})),
        selectStyles:[...document.querySelectorAll('.ui-select__trigger')].filter(visible).map(el=>({display:getComputedStyle(el).display,height:rect(el).height,tokenHeight:parseFloat(getComputedStyle(el).getPropertyValue('--ctl-h')),borderWidth:parseFloat(getComputedStyle(el).borderTopWidth)})),
        primary: [...document.querySelectorAll('button.ui-btn--primary')].filter(visible).map(el => {
          const body = el.querySelector('.ui-btn__skin-body'), label = el.querySelector('.ui-btn__label');
          const siblings = [...(el.parentElement?.querySelectorAll(':scope > button.ui-btn') ?? [])].filter(visible).filter(other => other !== el);
          return { text: el.textContent.trim(), ...rect(el), disabled: el.disabled, background: getComputedStyle(el).backgroundImage,
            skin: el.dataset.skin, body: body && visible(body) ? { ...rect(body), image: getComputedStyle(body).backgroundImage } : null,
            label: label ? rect(label) : null, siblings: siblings.map(other => ({text:other.textContent.trim(), ...rect(other)})) };
        }),
        portraits: [...document.querySelectorAll('.character-dossier-portrait')].filter(visible).map(rect),
        cards: [...document.querySelectorAll('[data-testid="character-card"]')].map(el => ({ name: el.querySelector('h3')?.textContent, ...rect(el) })),
        failedImages: [...document.images].filter(visible).filter(el => el.complete && !el.naturalWidth).map(el => ({ alt: el.alt, src: new URL(el.src).pathname })),
      };
    });
  }
  // Whole-page coverage at the specified widths; theme/ornament coverage is sampled
  // separately so expensive raster output does not masquerade as extra behaviour.
  const widths = process.env.UI_PRODUCTION_WIDTHS ? process.env.UI_PRODUCTION_WIDTHS.split(',').map(Number) : [1440,1920,2550,390,320];
  const onlyDrawer = process.env.UI_PRODUCTION_ONLY_DRAWER === '1';
  const onlyBehaviors = process.env.UI_PRODUCTION_ONLY_BEHAVIORS === '1' || onlyDrawer;
  assert.ok(widths.every(width=>[1440,1920,2550,390,320].includes(width)), 'Explicit acceptance widths must use the agreed matrix');
  const pages = process.env.UI_PRODUCTION_PAGES ? new Set(process.env.UI_PRODUCTION_PAGES.split(',')) : null;
  for (const width of onlyBehaviors ? [] : widths) {
    const page = await pageAt(width, width < 500 ? 844 : 1080, width < 500);
    activePage=page;
    for (const [name, path, ready] of routes.filter(route=>!pages||pages.has(route[0]))) await check(`matrix ${name} ${width}`, async () => {
      await open(page, path, ready); await settle(page); const info = await geometry(page);
      await shot(page, `matrix-${name}-${width}-light-rich`);
      results.push({ check: `geometry ${name} ${width}`, ...info });
      await bounds(page, `${name}-${width}`);
      assert.equal(info.earlyLayers,true,'HTML declares the complete cascade order before any stylesheet can establish partial layers');
      for(const control of info.controlStyles) {
        // Flex/grid items blockify inline-flex to flex in computed style.
        if(control.skin) assert.ok(control.display==='inline-flex'||(control.display==='flex'&&['flex','inline-flex','grid','inline-grid'].includes(control.parentDisplay)),`${name}: skinned action retains the Button flex formatting primitive`);
        if(!control.intentionalPosition) assert.equal(control.position,'relative',`${name}: production Button primitive retains its positioning layer (${control.text})`);
        if(control.skin&&Number.isFinite(control.tokenHeight)) assert.ok(Math.abs(control.height-control.tokenHeight)<=1,`${name}: fixed skinned action retains its declared control height`);
      }
      for(const nav of info.navStyles) { assert.equal(nav.display,'flex'); assert.equal(nav.position,'relative'); assert.ok(Math.abs(nav.height-nav.tokenHeight)<=1,'Navigation retains the primitive token height'); }
      for(const select of info.selectStyles) { assert.equal(select.display,'flex'); assert.ok(select.borderWidth>=1,'Select retains its operable field outline'); assert.ok(Math.abs(select.height-select.tokenHeight)<=1,'Select retains the input token height'); }
      if (name === 'story') {
        const header=page.locator('.story-workspace-header');
        if(await header.count()) { const box=await header.boundingBox(); results.push({check:`story header ${width}`, ...box}); if(width>=900) assert.ok(box&&Math.abs(box.height-56)<=2,'Story production header uses the approved compact 56px allocation'); }
      }
      for (const button of info.primary) {
        if (button.label) assert.ok(button.label.x >= button.x - 1 && button.label.x + button.label.width <= button.x + button.width + 1, `${name}: primary label stays inside button body allocation`);
        if(button.body) assert.ok(Math.abs(button.body.x+button.body.width/2-button.x-button.width/2)<=3&&Math.abs(button.body.y+button.body.height/2-button.y-button.height/2)<=button.height/2,`${name}: skin body is anchored to its actual button rather than another container`);
        for (const sibling of button.siblings) {
          if (Math.abs(sibling.height-button.height)<=1 && Math.abs(sibling.y-button.y)<Math.min(sibling.height,button.height)/2) assert.ok(Math.abs(sibling.y + sibling.height/2 - button.y - button.height/2)<=1, `${name}: same-size neighbour buttons are vertically centred`);
        }
      }
      if (name === 'characters') {
        assert.equal(info.cards.length, 6, 'Exactly six cards per page');
        assert.ok(Math.max(...info.cards.map(a => info.cards.filter(b => Math.abs(a.y - b.y) < 2).length)) <= 3, 'At most three cards per row');
        for (const portrait of info.portraits) assert.ok(Math.abs(portrait.width / portrait.height - 9 / 16) < .01, 'Actual portrait slot is 9:16');
      }
      if (name === 'editor') {
        if (width < 900) await page.getByRole('button',{name:'章节目录',exact:true}).click();
        await page.getByRole('navigation',{name:'角色手稿章节',exact:true}).getByText('形象与资料',{exact:true}).click();
        await settle(page); await shot(page,`matrix-editor-image-area-${width}-light-rich`);
        await bounds(page,`editor-image-area-${width}`);
        const media=await page.evaluate(()=>{
          const rect=el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};};
          const card=document.querySelector('.character-editor-avatar'); return {avatarCard:rect(card),portrait:rect(document.querySelector('.character-art-preview')),controls:[...card.querySelectorAll(':scope>label,:scope>button')].map(el=>({text:el.textContent.trim(),...rect(el)})),gap:parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--space-2'))};
        }); results.push({check:`editor media actual allocation ${width}`,media});
        if(width>=900&&media.controls.length===2) { assert.ok(media.controls.every(control=>control.width<media.avatarCard.width/2),'Avatar actions stay compact rather than spanning the right column'); assert.ok(Math.abs(media.controls[1].y-media.controls[0].y-media.controls[0].height-media.gap)<=1,'Avatar actions use the designed small vertical gap'); }
      }
      if (name === 'lorebook' && width < 500) {
        const switches=page.locator('.lorebook-table .ui-switch'); assert.equal(await switches.count(),14,'Every synthetic entry retains an accessible switch');
        const reach=[];
        for(let index=0;index<14;index++) {
          const control=switches.nth(index); await control.scrollIntoViewIfNeeded();
          const hit=await control.evaluate(el=>{const r=el.getBoundingClientRect(),target=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return {name:el.querySelector('input')?.getAttribute('aria-label'),left:r.left,right:r.right,top:r.top,bottom:r.bottom,reachable:Boolean(target&&el.contains(target))};});
          reach.push(hit); assert.ok(hit.reachable&&hit.left>=0&&hit.right<=width,`Actual entry switch is not clipped: ${JSON.stringify(hit)}`);
        }
        const more=page.getByRole('button',{name:'条目 14 更多操作',exact:true}); await more.click(); await page.getByRole('menuitem',{name:'查看全文',exact:true}).waitFor(); await page.keyboard.press('Escape');
        assert.equal(await more.evaluate(el=>document.activeElement===el),true,'Entry menu Escape returns its actual focus');
        results.push({check:`lorebook fourteen switch hit targets ${width}`,reach}); await shot(page,`lorebook-last-row-controls-${width}`);
      }
    });
    await page.context().close();
  }
  if(process.env.UI_PRODUCTION_ONLY_MATRIX==='1') {
    await writeFile(resolve(output,'production-design-summary.json'),JSON.stringify({failures,coverage:{onlyMatrix:true,widths,pages:pages?[...pages]:routes.map(r=>r[0])},limits:['Layout-only run excludes theme samples and mutation behaviours.','Screenshots still require independent visual review.']},null,2));
    assert.deepEqual(failures,[],'Production layout-only failures'); return;
  }
  for (const [theme, level, routeIndex, width] of onlyBehaviors ? [] : [['iris-night','rich',2,1920], ['iris-light','subtle',0,1440], ['iris-night','subtle',3,390], ['iris-light','none',1,2550], ['iris-night','none',5,320], ['iris-night','rich',6,1440]]) {
    Object.assign(appearance, { theme_id: theme, decoration_id: level });
    const page = await pageAt(width, width < 500 ? 844 : 1080, width < 500);
    activePage=page;
    await check(`appearance ${theme} ${level} ${routes[routeIndex][0]}`, async () => {
      const [name,path,ready] = routes[routeIndex]; await open(page,path,ready); await settle(page);
      const info = await geometry(page); assert.equal(info.ornament, level); await bounds(page, `sample-${name}-${level}-${width}`);
      await shot(page, `sample-${name}-${width}-${theme}-${level}`); results.push({ check: 'sample geometry', ...info });
    });
    await page.context().close();
  }
  Object.assign(appearance, { theme_id: 'iris-light', decoration_id: 'rich' });
  if(!onlyBehaviors) for(const width of [1440,1920,390]) for(const theme of ['iris-light','iris-night']) {
    Object.assign(appearance,{theme_id:theme});
    const settingPage=await pageAt(width,width<500?844:1080,width<500); activePage=settingPage;
    await check(`settings real card intervals ${width} ${theme}`,async()=>{
      await open(settingPage,'/settings/preferences','.appearance-theme-grid'); await settle(settingPage);
      const detail=await settingPage.evaluate(()=>{
        const box=el=>{const r=el.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height};};
        return {themes:[...document.querySelectorAll('.appearance-theme-option')].map(el=>({outer:box(el),parts:[...el.querySelectorAll('.appearance-theme-sample,.appearance-swatches,.appearance-theme-name,.appearance-help')].map(part=>({kind:part.className,...box(part)}))})),
          avatars:[...document.querySelectorAll('.appearance-avatar-option')].map(el=>({outer:box(el),parts:[el.querySelector('.ui-avatar__frame')||el.querySelector('.ui-avatar'),el.querySelector('strong')].filter(Boolean).map(box)})),
          playground:{outer:box(document.querySelector('.appearance-pointer-playground')),parts:[...document.querySelector('.appearance-pointer-playground .ui-btn__label').children].map(box)}};
      });
      assert.equal(detail.themes.length,11); assert.equal(detail.avatars.length,12);
      const overlap=(a,b)=>Math.min(a.right,b.right)-Math.max(a.left,b.left)>1&&Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)>1;
      for(const [kind,cards] of [['theme',detail.themes],['avatar',detail.avatars],['playground',[detail.playground]]]) {
        for(let index=0;index<cards.length;index++) {
          const card=cards[index];
          for(const part of card.parts) assert.ok(part.left>=card.outer.left-1&&part.right<=card.outer.right+1&&part.top>=card.outer.top-1&&part.bottom<=card.outer.bottom+1,`${kind} inner interval stays in its actual painted card allocation`);
          for(let n=0;n<card.parts.length;n++) for(let m=n+1;m<card.parts.length;m++) assert.ok(!overlap(card.parts[n],card.parts[m]),`${kind} content intervals do not collide`);
          for(let other=index+1;other<cards.length;other++) assert.ok(!overlap(card.outer,cards[other].outer),`${kind} actual outer cards do not overlap`);
        }
      }
      results.push({check:`settings actual intervals ${width} ${theme}`,detail});
      await settingPage.locator('.appearance-theme-grid').screenshot({path:resolve(output,`settings-theme-grid-${width}-${theme}.png`),animations:'disabled'});
      await settingPage.locator('.appearance-avatar-options').screenshot({path:resolve(output,`settings-avatar-grid-${width}-${theme}.png`),animations:'disabled'});
    });
    await settingPage.context().close();
  }
  Object.assign(appearance, { theme_id: 'iris-light', decoration_id: 'rich' });
  if(!onlyDrawer) {
  const page = await pageAt(1920,1080);
  activePage=page;
  await check('pagination retains every synthetic character and page after editor return', async () => {
    await open(page,'/library/characters','[data-testid="character-card"]');
    const names = await page.locator('[data-testid="character-card"] h3').allTextContents();
    await page.getByRole('button',{name:'下一页',exact:true}).click();
    await page.waitForFunction(first => document.querySelector('[data-testid="character-card"] h3')?.textContent !== first, names[0]);
    names.push(...await page.locator('[data-testid="character-card"] h3').allTextContents());
    assert.deepEqual([...new Set(names)].sort(), characters.map(c=>c.card.name).sort(), 'Pagination has no duplicate or missing IDs');
    await page.locator('[data-character-edit]').first().click(); await page.locator('.character-editor-page').waitFor();
    await page.getByRole('button',{name:'返回角色库',exact:true}).click();
    await page.locator('[data-testid="character-card"]').first().waitFor();
    assert.deepEqual(await page.locator('[data-testid="character-card"] h3').allTextContents(),names.slice(6));
    await shot(page,'pagination-editor-return');
  });
  await check('selection retains hidden page IDs',async()=>{
    await open(page,'/library/characters','[data-testid="character-card"]');
    await page.getByLabel('角色库工具',{exact:true}).click(); await page.getByRole('button',{name:'批量选择',exact:true}).click();
    const first=page.locator('[data-testid="character-card"] input[type="checkbox"]').first(); await first.locator('..').click(); assert.equal(await first.isChecked(),true);
    await page.getByRole('button',{name:'下一页',exact:true}).click(); const second=page.locator('[data-testid="character-card"] input[type="checkbox"]').first(); await second.focus(); await page.keyboard.press('Space'); assert.equal(await second.isChecked(),true);
    await page.getByRole('button',{name:'上一页',exact:true}).click(); assert.equal(await page.locator('input[type="checkbox"]:visible').first().isChecked(),true);
    await shot(page,'batch-cross-page-selection');
  });
  await check('failed durable save retains draft and route; successful retry commits once',async()=>{
    await open(page,'/library/characters/char-demo0','.character-editor-page');
    await page.getByRole('navigation',{name:'角色手稿章节',exact:true}).getByText('形象与资料',{exact:true}).click();
    const name=page.getByTestId('card-name'); await name.fill('独立验收未提交草稿');
    const original=characters[0].card.name, revision=characters[0].revision;
    designFixture.failCharacterSave=true; await page.getByRole('button',{name:'保存',exact:true}).click();
    await page.getByText(/保存失败/).first().waitFor(); assert.equal(await name.inputValue(),'独立验收未提交草稿');
    assert.equal(characters[0].card.name,original); assert.equal(characters[0].revision,revision); assert.equal(new URL(page.url()).pathname,'/library/characters/char-demo0');
    await shot(page,'editor-save-failure-draft'); designFixture.failCharacterSave=false;
    await page.getByRole('button',{name:'保存',exact:true}).click();
    for(let n=0;n<100&&characters[0].card.name!=='独立验收未提交草稿';n++) await page.waitForTimeout(50);
    assert.equal(characters[0].card.name,'独立验收未提交草稿'); assert.equal(characters[0].revision,revision+1);
  });
  designFixture.failCharacterSave=false;
  await check('real production crop outputs 720 by 1280 and preserves source fixture',async()=>{
    await open(page,'/library/characters/char-demo0','.character-editor-page');
    await page.getByRole('navigation',{name:'角色手稿章节',exact:true}).getByText('形象与资料',{exact:true}).click();
    const details=page.locator('summary',{hasText:'全身立绘'}); if(await details.count()) await details.first().click();
    const before=testImage.toString('base64'); await page.getByLabel('选择全身立绘文件').setInputFiles({name:'synthetic-source.png',mimeType:'image/png',buffer:testImage});
    const crop=page.getByRole('dialog',{name:'裁剪角色画像',exact:true}); await crop.waitFor();
    const stage=crop.getByRole('group',{name:'图片裁剪选框',exact:true}); await stage.focus(); await page.keyboard.press('+'); await page.keyboard.press('ArrowRight');
    await shot(page,'actual-production-portrait-crop'); await crop.getByRole('button',{name:'保存裁剪',exact:true}).click(); await crop.waitFor({state:'hidden'});
    const upload=designFixture.uploads.at(-1); assert.equal(upload.kind,'full-body'); assert.equal(upload.width,720); assert.equal(upload.height,1280); assert.equal(testImage.toString('base64'),before);
    results.push({check:'independently parsed uploaded PNG IHDR',...upload});
  });
  await check('asset failure keeps usable production controls and stable layout',async()=>{
    await page.route('**/*',route=>{ const u=new URL(route.request().url()); if(/\.(png|webp|svg)$/.test(u.pathname)&&!u.pathname.startsWith('/api/')) return route.abort(); return route.fallback(); });
    await open(page,'/library/characters','[data-testid="character-card"]'); await settle(page);
    assert.equal(await page.getByTestId('character-create').isVisible(),true); assert.equal(await page.getByTestId('character-create').isEnabled(),true);
    await bounds(page,'asset-failure'); await shot(page,'assets-failed-production'); await page.unroute('**/*');
  });
  await check('appearance failed PATCH rolls back then manual retry persists across reload',async()=>{
    await open(page,'/settings/preferences','.v7-settings');
    const original=appearance.reading_width;
    designFixture.failSettingsSave=true;
    await page.locator('#appearance-reading-width').click(); await page.getByRole('option',{name:'窄',exact:true}).click();
    await page.getByText(/外观未保存/).first().waitFor();
    assert.equal(appearance.reading_width,original);
    assert.equal((await page.locator('#appearance-reading-width').innerText()).trim(),'宽');
    await shot(page,'settings-failed-patch-rollback'); designFixture.failSettingsSave=false;
    await page.locator('#appearance-reading-width').click(); await page.getByRole('option',{name:'窄',exact:true}).click();
    for(let n=0;n<100&&appearance.reading_width!=='narrow';n++) await page.waitForTimeout(50);
    assert.equal(appearance.reading_width,'narrow');
    await page.reload(); await page.locator('#appearance-reading-width').waitFor();
    assert.equal((await page.locator('#appearance-reading-width').innerText()).trim(),'窄');
    const writes=designFixture.settingsWrites.filter(write=>write.appearance?.reading_width==='narrow'); assert.equal(writes.length,2);
    await shot(page,'settings-manual-retry-reloaded');
  });
  designFixture.failSettingsSave=false;
  await check('scene artwork cancel, failed save, original command retry and reload',async()=>{
    await open(page,'/stories/sess-demo1/branches/sess-demo1','.v7-message');
    const trigger=page.getByRole('button',{name:'选择场景图',exact:true});
    if(!await trigger.isVisible()) await page.getByRole('button',{name:'故事资料',exact:true}).click();
    const before=designFixture.sceneWrites.length, title=h.snapshot.scenes[0].title, description=h.snapshot.scenes[0].description;
    await trigger.click(); let picker=page.getByRole('dialog',{name:'选择场景图',exact:true});
    await picker.getByRole('button',{name:'旅店大厅',exact:true}).click(); await picker.getByRole('button',{name:'取消',exact:true}).click();
    assert.equal(designFixture.sceneWrites.length,before); assert.equal(h.snapshot.scenes[0].builtin_image_id,null);
    await trigger.click(); picker=page.getByRole('dialog',{name:'选择场景图',exact:true});
    await picker.getByRole('button',{name:'旅店大厅',exact:true}).click(); designFixture.failSceneSave=true;
    await picker.getByRole('button',{name:'保存场景图',exact:true}).click(); await picker.getByRole('alert').waitFor();
    assert.equal(h.snapshot.scenes[0].builtin_image_id,null); assert.equal(await picker.getByRole('button',{name:'旅店大厅',exact:true}).getAttribute('aria-pressed'),'true');
    await shot(page,'scene-image-save-failure-draft'); designFixture.failSceneSave=false;
    await picker.getByRole('button',{name:'重试保存',exact:true}).click(); await picker.waitFor({state:'hidden'});
    assert.equal(designFixture.sceneCommits,1); const writes=designFixture.sceneWrites.slice(before);
    assert.equal(writes.length,2); assert.equal(writes[0].id,writes[1].id); assert.deepEqual(writes[0].body,writes[1].body);
    assert.equal(h.snapshot.scenes[0].title,title); assert.equal(h.snapshot.scenes[0].description,description); assert.equal(h.snapshot.scenes[0].builtin_image_id,'inn-lounge');
    await page.reload(); await page.locator('.v7-message').first().waitFor(); await page.locator('.story-scene-image').waitFor(); await settle(page);
    assert.equal(await page.locator('.story-scene-image').evaluate(el=>el.complete&&el.naturalWidth>0),true); await shot(page,'scene-image-committed-reloaded');
    await page.getByRole('button',{name:'选择场景图',exact:true}).click(); picker=page.getByRole('dialog',{name:'选择场景图',exact:true});
    await picker.getByRole('button',{name:'仅显示文字',exact:true}).click(); await picker.getByRole('button',{name:'保存场景图',exact:true}).click(); await picker.waitFor({state:'hidden'});
    assert.equal(h.snapshot.scenes[0].builtin_image_id,null); assert.equal(designFixture.sceneCommits,2);
  });
  designFixture.failSceneSave=false;
  await page.context().close();
  }
  for(const width of [320,390]) {
    const drawerPage=await pageAt(width,844,true); activePage=drawerPage;
    await check(`simple chat config ${width} real fields, ancestor clipping, save and close`,async()=>{
      await open(drawerPage,'/simple-chats/chat-demo1','.sc-bubble');
      const trigger=drawerPage.getByRole('button',{name:'配置',exact:true}); await trigger.click();
      let dialog=drawerPage.getByRole('dialog',{name:'本次聊天的模型配置',exact:true}); await dialog.waitFor();
      const controls=dialog.locator('input:not([type="hidden"]):not([type="checkbox"]),textarea,button.ui-select__trigger,label.ui-switch,label.ui-check'); const records=[];
      assert.ok(await controls.count()>=10,'Config exercises actual production model and sampling fields');
      for(let n=0;n<await controls.count();n++) {
        const control=controls.nth(n); await control.scrollIntoViewIfNeeded();
        const record=await control.evaluate(el=>{
          const r=el.getBoundingClientRect(),hit=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2),clips=[];
          for(let parent=el.parentElement;parent;parent=parent.parentElement) { const css=getComputedStyle(parent),p=parent.getBoundingClientRect(); const left=p.left+parent.clientLeft,top=p.top+parent.clientTop;
            if(['auto','scroll','hidden','clip'].includes(css.overflowX)&&(r.left<left-1||r.right>left+parent.clientWidth+1)) clips.push({kind:'x',class:parent.className});
            if(['auto','scroll','hidden','clip'].includes(css.overflowY)&&(r.top<top-1||r.bottom>top+parent.clientHeight+1)) clips.push({kind:'y',class:parent.className});
          }
          return {label:el.getAttribute('aria-label')||el.closest('label')?.innerText||el.id,left:r.left,right:r.right,top:r.top,bottom:r.bottom,reachable:!!hit&&(el===hit||el.contains(hit)),clips};
        }); records.push(record);
        assert.ok(record.left>=0&&record.right<=width&&record.reachable&&!record.clips.length,`Actual config field is not clipped: ${JSON.stringify(record)}`);
      }
      results.push({check:`config control hit intervals ${width}`,records});
      const grid=await dialog.locator('.sc-config-grid').evaluate(el=>({width:el.clientWidth,scrollWidth:el.scrollWidth,scrollLeft:el.scrollLeft})); assert.ok(grid.scrollWidth<=grid.width+1); assert.equal(grid.scrollLeft,0);
      const count=designFixture.chatWrites.length; await dialog.getByRole('button',{name:'关闭',exact:true}).click(); await dialog.waitFor({state:'hidden'}); assert.equal(designFixture.chatWrites.length,count);
      await trigger.click(); dialog=drawerPage.getByRole('dialog',{name:'本次聊天的模型配置',exact:true}); await drawerPage.keyboard.press('Escape'); await dialog.waitFor({state:'hidden'}); assert.equal(designFixture.chatWrites.length,count); assert.equal(await trigger.evaluate(el=>el===document.activeElement),true);
      await trigger.click(); dialog=drawerPage.getByRole('dialog',{name:'本次聊天的模型配置',exact:true});
      await dialog.getByRole('textbox',{name:'标题',exact:true}).fill(`隔离配置验收 ${width}`);
      await shot(drawerPage,`chat-config-top-${width}`);
      const save=dialog.getByRole('button',{name:'保存配置',exact:true}); await save.scrollIntoViewIfNeeded(); await shot(drawerPage,`chat-config-save-${width}`); await save.click(); await dialog.waitFor({state:'hidden'});
      assert.equal(designFixture.chatWrites.length,count+1); assert.equal(designFixture.chatWrites.at(-1).title,`隔离配置验收 ${width}`);
      await drawerPage.reload(); await drawerPage.locator('.sc-bubble').first().waitFor(); await drawerPage.getByRole('button',{name:'配置',exact:true}).click();
      assert.equal(await drawerPage.getByRole('dialog',{name:'本次聊天的模型配置',exact:true}).getByRole('textbox',{name:'标题',exact:true}).inputValue(),`隔离配置验收 ${width}`); await drawerPage.keyboard.press('Escape');
    }); await drawerPage.context().close();
  }
  await writeFile(resolve(output,'production-design-summary.json'),JSON.stringify({failures,coverage:{onlyBehaviors,onlyDrawer,widths,pages:pages?[...pages]:routes.map(r=>r[0])},automatedChecks:results.length,limits:['Screenshots require independent human review of actual painted gold body and ornament placement.','Browser viewport emulation is not physical mobile evidence.','No private data, model calls, real server or external network permitted.']},null,2));
  assert.deepEqual(failures,[],'Production design acceptance failures; see evidence JSON and screenshots');
}

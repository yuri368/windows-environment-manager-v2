'use strict';
// Isolated Chrome QA. All fixtures affect only the demo's in-memory state.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {pathToFileURL}=require('node:url');
const {chromium}=require(process.env.V3_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'../..'),checks=[],errors=[];
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.V3_BROWSER_PATH,headless:true});
 const page=await browser.newPage({viewport:{width:1383,height:851},reducedMotion:'reduce'});
 const q=s=>page.locator(s),ev=s=>page.evaluate(s);
 const check=async(name,run)=>{try{await run();checks.push({name,passed:true})}catch(e){checks.push({name,passed:false,error:e.stack})}};
 const shot=name=>page.screenshot({path:path.join(__dirname,name+'.png')});
 const state=()=>ev('JSON.stringify({apps,windows,history,checked:[...checked]})');
 const target="[{app:1,profile:'Python313',varIdx:1}]";
 async function setup(actual='X;A;B;C;Y',desired='A;B;C'){
  await ev('closeModal();reset(true);selected=1;focusedVariable=1;var testV=scheme(1).vars[1];testV.defaultValue='+JSON.stringify(desired)+';testV.baselineValue=testV.defaultValue;testV.mode="-append";windows["User|PATH"]='+JSON.stringify(actual)+';render()');
 }
 async function preview(targets=target,kind='应用'){await ev(`showPreview(${JSON.stringify(kind)},'预览交互验收',${targets})`)}
 async function input(value){await q('#varRows [data-row="1"] .vinput').fill(value)}
 async function historicalEdit(){
  await ev('closeModal();reset(true);windows["User|PATH"]="C:\\Tools\\Base;;C:\\Tools\\SDK;C:\\Tools\\Old;C:\\Tools\\SDK";go("windows")');
  await q('[data-windows-edit="User|PATH"]').click();
  await q('#elementList .element-row').nth(2).locator('.element-input').fill('D:\\Tools\\SDK-Next');
  await q('#elementList .element-row').nth(2).locator('[data-move-up]').click();
  await q('#elementList .element-row').nth(3).locator('[data-delete]').click();
  await q('#addElement').click();await q('#elementList .element-input').last().fill('D:\\Tools\\Extra');
  await q('#applyElements').click();await page.getByRole('button',{name:'历史',exact:true}).click();
  await q('.history-details > summary').first().click();
 }
 page.on('pageerror',e=>errors.push(e.message));
 try{
  await page.goto(process.env.V3_APP_URL||pathToFileURL(path.join(root,'temp.html')).href);
  await check('Normal preview is compact, shows one legend, and still uses the frozen plans',async()=>{
   await page.getByRole('button',{name:'查看Hadoop',exact:true}).click();await q('[data-inspect="1"]').click();
   await page.getByRole('textbox',{name:'Path 当前选定值',exact:true}).fill('%HADOOP_HOME%\\bin;;%HADOOP_HOME%\\bin; H:\\开发工具\\长目录 ;');
   const before=await state();await q('#previewButton').click();assert.equal(await state(),before);
   assert.match(await q('.review-summary').textContent(),/2.*目标.*2.*实际变更.*尚未应用/);
   assert.ok((await q('.review-summary').boundingBox()).height<=68);
   assert.equal(await q('.preview-dialog .link-legend').count(),1);
   assert.equal(await q('.review-row .link-legend').count(),0);
   assert.equal(await ev('modalAction.rows.every(r=>Object.isFrozen(r.plan))'),true);
   await shot('preview-desktop');
  });
  await check('Target navigation, raw expansion and cancel preserve request and draft',async()=>{
   const before=await state(),version=await ev('modalAction.reviewedVersion');
   await q('[data-review-jump]').last().click();
   await q('.review-row .raw-disclosure > summary').last().click();
   assert.equal(await ev('modalAction.reviewedVersion'),version);assert.equal(await state(),before);
   assert.equal(await q('[data-preview-before]').last().textContent(),await ev('modalAction.rows.at(-1).before'));
   await page.keyboard.press('Escape');assert.equal(await state(),before);
  });
  await check('No-operation confirmation does not fabricate history',async()=>{
   await setup();await preview();const before=await ev('history.length');
   assert.equal(await q('.review-summary').getAttribute('data-review-state'),'unchanged');
   assert.match(await q('.review-summary').textContent(),/不会新增.*历史/);
   await q('#confirmDialog').click();assert.equal(await ev('history.length'),before);
  });
  await check('Retained deletion and partial completion keep explanations and original intent',async()=>{
   await setup();await input('A;C;E');
   await ev('scheme(0).vars[1].defaultValue="B"');await preview();
   assert.equal(await q('.review-summary').getAttribute('data-review-state'),'retained');
   assert.match(await q('.review-issues').textContent(),/其他方案仍需使用/);
   await shot('preview-retained');await q('#confirmDialog').click();
   assert.equal(await ev('history[0].action'),'模拟应用（部分完成）');
   assert.equal(await ev('value(scheme(1).vars[1])'),'A;C;E');
  });
  await check('Unsupported ordering and request conflicts still block the full batch',async()=>{
   await setup();await ev('scheme(1).vars[1].mode="append"');await preview();const before=await state();
   assert.equal(await q('.review-summary').getAttribute('data-review-state'),'blocked');
   assert.equal(await q('#confirmDialog').isDisabled(),true);assert.match(await q('.review-limitation').textContent(),/自定义排序/);
   assert.equal(await state(),before);await shot('preview-blocked');
   await setup();await ev('scheme(0).vars[1].mode="replace";scheme(0).vars[1].defaultValue="OTHER"');
   await preview("[{app:1,profile:'Python313',varIdx:1},{app:0,profile:'JDK8',varIdx:1}]");
   assert.equal(await q('#confirmDialog').isDisabled(),true);assert.match(await q('.review-issues').textContent(),/同一目标的请求冲突/);
  });
  await check('Relevant refresh retains raw/focus and requires review then separate confirmation',async()=>{
   await setup();await input('A;C;E');await preview();
   await q('.raw-disclosure > summary').click();await q('.raw-disclosure > summary').focus();
   await ev('windows["User|PATH"]+=";EXT";targetRevisions["User|PATH"]++;refreshPreview()');
   const before=await state();assert.equal(await q('.raw-disclosure').getAttribute('open'),'');
   assert.equal(await ev('document.activeElement.dataset.reviewControl'),'raw');
   assert.match(await q('.review-summary').textContent(),/重新核对/);assert.equal(await ev('modalAction.reviewedVersion'),0);
   await shot('preview-updated');await q('#confirmDialog').click();assert.equal(await state(),before);
   assert.equal(await ev('modalAction.reviewedVersion===modalAction.version'),true);
   await q('#confirmDialog').click();assert.notEqual(await state(),before);
  });
  for(const width of [800,600])await check(`Preview ${width}px preserves controls, body scrolling and stacked values`,async()=>{
   await page.setViewportSize({width,height:851});await setup();await input('A;C;E');await preview();
   assert.ok(await ev('document.documentElement.scrollWidth<=innerWidth'));
   const dialog=await q('.preview-dialog').boundingBox(),foot=await q('.preview-dialog .dialogfoot').boundingBox();
   assert.ok(dialog.x>=0&&dialog.x+dialog.width<=width+1);assert.ok(foot.y+foot.height<=851);
   if(width===600){const panes=await q('.preview-flow > section').evaluateAll(els=>els.map(e=>e.getBoundingClientRect().top));assert.ok(panes[1]>panes[0]);}
   await q('#cancelDialog').focus();await page.keyboard.press('Tab');assert.equal(await ev('document.activeElement.id'),'confirmDialog');
   await shot('preview-'+width);await page.keyboard.press('Escape');
  });
  await check('History detail spans the record, with immutable recorded raw data and correct deletion/addition sides',async()=>{
   await page.setViewportSize({width:1383,height:851});await historicalEdit();
   const body=await q('.history-detail-body').first().boundingBox(),table=await q('.history-page > .history-list').boundingBox();
   assert.ok(body.width>table.width*.90,`${body.width}/${table.width}`);
   assert.equal(await ev('Object.isFrozen(history[0].changes[0])'),true);
   const raw=await ev('history[0].changes[0]');
   const before=q('.history-structure > div').first(),after=q('.history-structure > div').last();
   assert.equal(await before.locator('.is-removed').count(),1);assert.equal(await after.locator('.is-removed').count(),0);
   assert.equal(await before.locator('.is-added').count(),0);assert.equal(await after.locator('.is-added').count(),1);
   await q('.history-change .raw-disclosure > summary').click();
   assert.equal(await q('.history-before').textContent(),raw.before);assert.equal(await q('.history-after').textContent(),raw.after);
   await q('.history-change .raw-disclosure > summary').click();
   await page.waitForTimeout(3000);await shot('history-desktop');
  });
  await check('History keyboard/hover links remain confined to the audited occurrence and closing restores summary focus',async()=>{
   const before=await state();const token=q('.history-structure > div').first().locator('.is-replaced').first();
   const key=await token.getAttribute('data-related-key');await token.focus();
   const linked=q('.history-change').first().locator('.is-related-highlight');assert.equal(await linked.count(),3);
   assert.equal(await q('.history-structure > div').last().locator('.is-related-highlight').getAttribute('data-related-key'),key);
   await q('.history-element-table tbody tr').first().focus();await page.keyboard.press('End');
   assert.equal(await q('.history-element-table tbody tr').last().evaluate(el=>el===document.activeElement),true);
   await ev('var d=document.querySelector(".history-details");d.open=false');
   await page.waitForTimeout(30);assert.equal(await q('.history-details > summary').first().evaluate(el=>el===document.activeElement),true);
   await page.keyboard.press('Enter');assert.equal(await state(),before);
  });
  for(const width of [800,600])await check(`History ${width}px remains full width, wraps and exposes all audit rows`,async()=>{
   await page.setViewportSize({width,height:851});
   assert.ok(await ev('document.documentElement.scrollWidth<=innerWidth'));
   const body=await q('.history-detail-body').first().boundingBox(),table=await q('.history-page > .history-list').boundingBox();
   assert.ok(body.width>table.width*.88);
   await q('.history-element-table tbody tr').last().scrollIntoViewIfNeeded();
   assert.equal(await q('.history-element-table tbody tr').last().isVisible(),true);
   await q('.history-details > summary').first().scrollIntoViewIfNeeded();await shot('history-'+width);
  });
  await check('History state is scoped per record and stable under presentation redraw',async()=>{
   await ev('history.push(history[0]);renderHistory()');
   await q('.history-details > summary').nth(0).click();await q('.history-details > summary').nth(1).click();
   await q('.history-change').first().locator('.is-replaced').first().focus();
   assert.equal(await q('.history-change').nth(1).locator('.is-related-highlight').count(),0);
   const before=await state();await ev('renderHistory()');assert.equal(await state(),before);
  });
  await check('Reduced motion and runtime health',async()=>{
   await q('.history-details > summary').first().click();
   assert.equal(await q('.history-detail-body').first().evaluate(el=>getComputedStyle(el).animationName),'none');
   assert.deepEqual(errors,[]);
  });
 }finally{
  fs.writeFileSync(path.join(__dirname,'browser-report.json'),JSON.stringify({browser:'isolated headless Google Chrome',appUrl:process.env.V3_APP_URL||pathToFileURL(path.join(root,'temp.html')).href,desktopViewport:{width:1383,height:851},narrowWidths:[800,600],passed:checks.filter(c=>c.passed).length,total:checks.length,checks,errors},null,2));
  await browser.close();
 }
 console.log(JSON.stringify({passed:checks.filter(c=>c.passed).length,total:checks.length,failures:checks.filter(c=>!c.passed)},null,2));
 if(checks.some(c=>!c.passed))process.exitCode=1;
})().catch(e=>{console.error(e);process.exitCode=1});

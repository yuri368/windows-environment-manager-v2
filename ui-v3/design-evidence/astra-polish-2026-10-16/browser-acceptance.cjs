'use strict';
// Optional real-browser checks; Playwright is QA tooling, not an application dependency.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {pathToFileURL}=require('node:url');
const {chromium}=require(process.env.V3_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'../..'),out=process.env.V3_EVIDENCE_DIR||__dirname;
const checks=[],errors=[];fs.mkdirSync(out,{recursive:true});
const browserOptions={headless:true,args:['--no-sandbox','--no-zygote','--single-process','--disable-gpu']};
if(process.env.V3_BROWSER_PATH)browserOptions.executablePath=process.env.V3_BROWSER_PATH;
(async()=>{
 const browser=await chromium.launch(browserOptions);
 const page=await browser.newPage({viewport:{width:1360,height:900},reducedMotion:'reduce'});
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(process.env.V3_APP_URL||pathToFileURL(path.join(root,'temp.html')).href);
 if(process.env.V3_FONT_CSS){await page.addStyleTag({content:fs.readFileSync(process.env.V3_FONT_CSS,'utf8')});await page.evaluate(()=>document.fonts.ready);}
 const ev=code=>page.evaluate(code);
 const snapshot=()=>ev('JSON.stringify({windows,history})');
 const q=s=>page.locator(s);
 const setup=async(actual='X;A;B;C;Y')=>ev('closeModal();reset(true);selected=1;focusedVariable=1;const v=scheme(1).vars[1];v.defaultValue="A;B;C";v.baselineValue=v.defaultValue;v.mode="-append";windows["User|PATH"]='+JSON.stringify(actual)+';render();clearTimeout(toastTimer);$("toast").classList.remove("on")');
 const input=async raw=>q('#varRows [data-row="1"] .vinput').fill(raw);
 const preview=async()=>ev("showPreview('应用','当前变量预览',[{app:1,profile:'Python313',varIdx:1}])");
 const check=async(name,run)=>{try{await run();checks.push({name,passed:true});}catch(error){checks.push({name,passed:false,error:error.stack});}};
 await check('Workbench comparison entry works by keyboard and preserves selected input',async()=>{
  await setup();const before=await snapshot();await q('[data-inspect="1"]').focus();await page.keyboard.press('Enter');assert.equal(await ev('document.activeElement.id'),'inspectTitle');assert.equal(await snapshot(),before);await page.screenshot({path:path.join(out,'workbench-desktop.png')});
 });
 await check('Preview target navigation and keyboard focus trap retain the batch scope',async()=>{
  await setup();await input('A;C;E');await q('#previewButton').click();assert.equal(await ev('modalAction.targets.length'),2);assert.equal(await q('[data-review-jump]').count(),2);
  await q('[data-review-jump]').nth(1).focus();await page.keyboard.press('Enter');assert.equal(await ev('document.activeElement.dataset.reviewControl'),'heading');
  await q('#confirmDialog').focus();await page.keyboard.press('Tab');assert.equal(await ev('document.activeElement.id'),'closeDialog');await page.keyboard.press('Shift+Tab');assert.equal(await ev('document.activeElement.id'),'confirmDialog');
  await q('.dialogbody').evaluate(el=>el.scrollTop=0);await page.screenshot({path:path.join(out,'preview-desktop.png')});await page.keyboard.press('Escape');assert.equal(await ev('document.activeElement.id'),'previewButton');
 });
 await check('Checkbox selection and clear keep the new preview label aligned with actual scope',async()=>{
  await setup();await input('A;C;E');await q('[data-check="0"]').click();await q('[data-check="1"]').click();assert.match(await q('#inspectPreview').innerText(),/批量/);await q('#inspectPreview').click();assert.equal(await ev('modalAction.targets.length'),4);await page.keyboard.press('Escape');await q('#selectNone').click();assert.doesNotMatch(await q('#inspectPreview').innerText(),/批量/);await q('#inspectPreview').click();assert.equal(await ev('modalAction.targets.length'),2);await page.keyboard.press('Escape');
 });
 await check('Shared-protected partial apply writes only safe operations and retains intent',async()=>{
  await setup();await input('A;C;E');await ev('scheme(0).vars[1].defaultValue="B"');await preview();assert.match(await q('.review-issues').innerText(),/其他方案仍需使用/);assert.match(await q('#confirmDialog').innerText(),/安全变更/);
  await page.screenshot({path:path.join(out,'preview-retained-desktop.png')});await q('#confirmDialog').click();assert.equal(await ev('windows["User|PATH"]'),'X;A;B;C;Y;E');assert.equal(await ev('history[0].action'),'模拟应用（部分完成）');assert.equal(await ev('scheme(1).vars[1].dirty'),true);assert.ok(await ev('scheme(1).vars[1].matchBaseline!==null'));
 });
 await check('No-operation protected confirmation reports no write and keeps pending intent',async()=>{
  await setup('A;B;B;C');await input('A;C');await preview();const before=await snapshot();assert.equal(await ev('modalAction.rows[0].plan.operations.length'),0);await q('#confirmDialog').click();assert.equal(await snapshot(),before);assert.match(await q('#toast').innerText(),/当前值未改变/);
 });
 await check('Current-value refresh preserves disclosure/focus and requires review then confirm',async()=>{
  await setup();await input('A;C;E');await preview();await q('.raw-disclosure summary').click();await q('.raw-disclosure summary').focus();const before=await snapshot();
  await ev('windows["User|PATH"]+=";EXT";targetRevisions["User|PATH"]++;refreshPreview()');assert.equal(await ev('document.activeElement.dataset.reviewControl'),'raw');assert.equal(await q('.raw-disclosure').evaluate(el=>el.open),true);assert.equal(await ev('modalAction.reviewedVersion'),0);
  const refreshed=await snapshot();assert.notEqual(refreshed,before);await page.screenshot({path:path.join(out,'preview-updated.png')});await q('#confirmDialog').click();assert.equal(await snapshot(),refreshed);assert.equal(await ev('modalAction.reviewedVersion'),await ev('modalAction.version'));await q('#confirmDialog').click();assert.notEqual(await snapshot(),refreshed);
 });
 await check('Cross-version occurrence focus falls back to the same target heading',async()=>{
  await setup();await input('A;C;E');await preview();await q('.is-after [data-related-key]').first().focus();await ev('windows["User|PATH"]="NEW;"+windows["User|PATH"];targetRevisions["User|PATH"]++;refreshPreview()');assert.equal(await ev('document.activeElement.dataset.reviewControl'),'heading');
 });
 await check('No-change selection remains confirmable with no fabricated history',async()=>{
  await setup();await preview();const before=await snapshot();assert.equal(await q('#confirmDialog').isEnabled(),true);await q('#confirmDialog').click();assert.equal(await snapshot(),before);assert.equal(await ev('scheme(1).vars[1].savedValue'),'A;B;C');
 });
 await check('Unsupported append blocks the full batch and visibly reports a new version',async()=>{
  await setup();await ev('scheme(1).vars[1].mode="append"');await preview();assert.equal(await q('#confirmDialog').isDisabled(),true);await ev('windows["User|PATH"]+=";EXT";targetRevisions["User|PATH"]++;refreshPreview()');assert.match(await q('.review-summary').innerText(),/预览已更新至版本 2/);const before=await snapshot();await ev('confirmPreview()');assert.equal(await snapshot(),before);
 });
 await check('Conflicting requests block all targets and display the member modes',async()=>{
  await setup();await ev('scheme(0).vars[1].mode="replace";scheme(0).vars[1].defaultValue="OTHER";showPreview("应用","冲突预览",[{app:1,profile:"Python313",varIdx:1},{app:0,profile:"JDK8",varIdx:1},{app:1,profile:"Python313",varIdx:0}])');assert.equal(await q('#confirmDialog').isDisabled(),true);assert.match(await q('.review-source').first().innerText(),/后置.*替换/);const before=await snapshot();await ev('confirmPreview()');assert.equal(await snapshot(),before);
 });
 await check('600x640 long Path has no horizontal overflow or obscured footer/last target',async()=>{
  await page.setViewportSize({width:600,height:640});await setup('X;A;B;B;C;Y;'+('H:\\中文长路径\\工具链\\').repeat(35));await input('A;C;E');await q('#previewButton').click();await page.screenshot({path:path.join(out,'preview-retained-600.png')});
  const geometry=await ev('(()=>{const d=document.querySelector(".dialog"),b=d.querySelector(".dialogbody"),f=d.querySelector(".dialogfoot");return {doc:document.documentElement.scrollWidth,viewport:innerWidth,width:b.scrollWidth,client:b.clientWidth,footer:f.getBoundingClientRect().bottom,body:b.getBoundingClientRect().bottom,footTop:f.getBoundingClientRect().top}})()');assert.ok(geometry.doc<=geometry.viewport);assert.ok(geometry.width<=geometry.client+1);assert.ok(geometry.footer<=640);assert.ok(geometry.body<=geometry.footTop+1);
  await q('.dialogbody').evaluate(el=>el.scrollTop=el.scrollHeight);const bounds=await ev('(()=>{const b=document.querySelector(".dialogbody"),last=b.querySelector(".review-row:last-child");return {last:last.getBoundingClientRect().bottom,body:b.getBoundingClientRect().bottom}})()');assert.ok(bounds.last<=bounds.body+1);await page.screenshot({path:path.join(out,'preview-bottom-600.png')});
 });
 await check('Narrow return-to-check preserves the draft and focuses its variable',async()=>{
  const before=await snapshot();await q('.review-return').last().click();assert.equal(await ev('screen'),'workbench');assert.equal(await ev('value(scheme(1).vars[1])'),'A;C;E');assert.equal(await ev('document.activeElement.getAttribute("aria-label")'),'Path 当前选定值');assert.equal(await snapshot(),before);await page.screenshot({path:path.join(out,'workbench-600.png')});
 });
 await check('No browser runtime errors',async()=>assert.deepEqual(errors,[]));
 const report={browser:await browser.version(),platform:process.platform,mode:'headless Chromium; browser-memory demo only',viewports:['1360x900','600x640'],font:process.env.V3_FONT_CSS?'QA-only Noto Sans SC injection':'system fonts',passed:checks.filter(c=>c.passed).length,total:checks.length,checks,errors};
 fs.writeFileSync(path.join(out,'browser-report.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));await browser.close();if(checks.some(c=>!c.passed))process.exitCode=1;
})().catch(error=>{console.error(error);process.exitCode=1;});

'use strict';
// QA only: a separate headless installation of Chrome with an ephemeral profile.
// Fixtures operate solely on this browser demo's memory, never Windows or user tabs.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {pathToFileURL}=require('node:url');
const {chromium}=require(process.env.V3_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'../..'),checks=[],errors=[];
const sample='%HADOOP_HOME%\\bin;;%HADOOP_HOME%\\bin; H:\\开发工具\\长目录 ;';
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.V3_BROWSER_PATH,headless:true});
 const page=await browser.newPage({viewport:{width:1383,height:851},reducedMotion:'reduce'});
 const q=s=>page.locator(s),ev=code=>page.evaluate(code);
 page.on('pageerror',error=>errors.push(error.message));
 const check=async(name,run)=>{try{await run();checks.push({name,passed:true});}catch(error){checks.push({name,passed:false,error:error.stack});}};
 const screenShot=name=>page.screenshot({path:path.join(__dirname,name+'.png')});
 const snapshot=()=>ev('JSON.stringify({windows,history,checked:[...checked]})');
 try {
  await page.goto(process.env.V3_APP_URL||pathToFileURL(path.join(root,'temp.html')).href);
  await check('Three sources enter the first desktop screen, with unchanged snapshot and real After',async()=>{
   await page.getByRole('button',{name:'查看Hadoop',exact:true}).click();
   await q('[data-inspect="1"]').click();const before=await snapshot();
   await page.getByRole('textbox',{name:'Path 当前选定值',exact:true}).fill(sample);
   assert.equal(await snapshot(),before);
   assert.equal(await q('#compareSerialized').textContent(),await ev('demoPlan(curProfile().vars[1]).after.raw'));
   for(const id of ['compareDesired','compareActual','compareAfter']){
    const box=await q('#'+id).boundingBox();const bottom=await q('#footer').evaluate(el=>el.getBoundingClientRect().top);
    assert.ok(box.y+24<bottom,`${id} has no first visible line`);
   }
   assert.equal(await q('#overview .eyebrow').count(),0);
   assert.equal(await q('.inspecthead #inspectReview').count(),1);
   await screenShot('workbench-desktop');
  });
  await check('Hover source is distinct from duplicate linkage and input is retained',async()=>{
   const source=q('#compareDesired [data-duplicate-value]').first();
   await source.hover();
   assert.equal(await q('#inspect .is-link-source').count(),1);
   assert.ok(await q('#inspect .is-duplicate-highlight').count()>0);
   await q('#inspectTitle').hover();
   assert.equal(await q('#inspect .is-link-source').count(),0);
   assert.equal(await page.getByRole('textbox',{name:'Path 当前选定值',exact:true}).inputValue(),sample);
  });
  await check('Preview/cancel and keyboard navigation preserve original input and request scope',async()=>{
   const before=await snapshot();await q('#inspectPreview').click();
   assert.equal(await ev('modalAction.targets.length'),2);
   await page.keyboard.press('Escape');assert.equal(await snapshot(),before);
   assert.equal(await page.getByRole('textbox',{name:'Path 当前选定值',exact:true}).inputValue(),sample);
   await q('[data-inspect="0"]').focus();await page.keyboard.press('Enter');
   assert.equal(await ev('document.activeElement.id'),'inspectTitle');
   assert.match(await q('#inspectTitle').textContent(),/HADOOP_HOME/);
   await q('[data-inspect="1"]').click();
  });
  await check('Long textarea content grows instead of creating a clipped internal scroll',async()=>{
   const long=Array.from({length:10},(_,i)=>` H:\\开发工具\\长目录${i}\\工具链 ;`).join('\n');
   const input=page.getByRole('textbox',{name:'Path 当前选定值',exact:true});await input.fill(long);
   const size=await input.evaluate(el=>({client:el.clientHeight,scroll:el.scrollHeight,raw:el.value}));
   assert.equal(size.raw,long);assert.ok(size.client>=size.scroll-1);assert.ok(size.client>112);
   await input.fill(sample);await q('.workscroll').evaluate(el=>el.scrollTop=0);
  });
  await check('Example descriptions remain available without taking a default overview row',async()=>{
   await page.getByRole('button',{name:'查看Node.js',exact:true}).click();
   assert.equal(await q('.overview-description').evaluate(el=>el.open),false);
   await q('.overview-description summary').click();
   assert.match(await q('.demo-description').innerText(),/NODE_OPTIONS/);
   await q('.overview-description summary').click();
  });
  await check('Retained and blocked review messages keep their meanings and no green title icon',async()=>{
   // Narrow explicit fixtures exercise existing review states without changing the engine.
   await ev('reset(true);selected=1;focusedVariable=1;const v=curProfile().vars[1];v.defaultValue="A;B;C";v.baselineValue=v.defaultValue;v.mode="-append";windows["User|PATH"]="X;A;B;C;Y";curProfile().vars[0].defaultValue="B";apps[0].profiles[0].vars[1].defaultValue="B";render()');
   await q('[data-row="1"] .vinput').fill('A;C;E');
   assert.equal(await q('#inspectReview').getAttribute('data-review-state'),'retained');
   assert.match(await q('#inspectReview').innerText(),/删除保留/);
   assert.ok(['none','normal','""'].includes(await q('#inspectReview strong').evaluate(el=>getComputedStyle(el,'::before').content)));
   await screenShot('workbench-retained');
   await ev('curProfile().vars[1].mode="append";renderInspector()');
   assert.equal(await q('#inspectReview').getAttribute('data-review-state'),'blocked');
   assert.match(await q('#inspectReview').innerText(),/暂不支持/);
   assert.equal(await q('#inspectReview strong').evaluate(el=>getComputedStyle(el).color),'rgb(148, 67, 57)');
   await screenShot('workbench-blocked');
  });
  await check('Windows stream retains repeated/empty occurrences and compact single-value rows',async()=>{
   await ev('reset(true);go("windows")');
   const system=q('[data-windows-value="System|PATH"]');
   assert.equal(await system.locator('[data-value-index]').count(),7);
   assert.equal(await system.locator('[data-value-index]').first().getAttribute('data-value-index'),'0');
   const style=await system.evaluate(el=>({border:getComputedStyle(el).borderTopWidth,background:getComputedStyle(el).backgroundColor}));
   assert.equal(style.border,'0px');assert.equal(style.background,'rgba(0, 0, 0, 0)');
   const row=await q('[data-windows-value="System|DOTNET_ROOT"]').evaluate(el=>el.closest('tr').getBoundingClientRect().height);
   assert.ok(row>=42&&row<=48,`Single row ${row}`);
   const special=q('[data-windows-value="User|DEMO_COMMAND"]');
   assert.equal(await special.locator('[data-value-index]').count(),1);
   await screenShot('windows-desktop');
  });
  await check('Windows raw disclosure preserves all raw line endings and whitespace',async()=>{
   const raw='\n C:\\Alpha\r\n;C:\\Alpha\r;; ';
   await page.evaluate(raw=>{windows['User|DEMO_SEARCH_PATH']=raw;renderWindows();},raw);
   const row=q('[data-windows-value="User|DEMO_SEARCH_PATH"]').locator('..');
   await row.locator('..').locator('[data-windows-show-raw]').click();
   assert.equal(await q('[data-windows-raw="User|DEMO_SEARCH_PATH"]').textContent(),raw);
   await row.locator('..').locator('[data-windows-show-raw]').click();
   assert.equal(await q('[data-windows-raw="User|DEMO_SEARCH_PATH"]').isVisible(),false);
  });
  await check('Direct edit targets the keyboard occurrence; delete/restore/cancel leave snapshot intact',async()=>{
   await ev('reset(true);go("windows")');const before=await snapshot();
   const tokens=q('[data-windows-value="System|PATH"] [data-value-index]');
   await tokens.first().focus();await page.keyboard.press('End');
   assert.equal(await ev('document.activeElement.dataset.valueIndex'),'6');
   await page.keyboard.press('Enter');
   assert.equal(await ev('document.activeElement.closest("[data-element-id]")?.dataset.elementId'),'7');
   const row=q('#elementList .element-row').first(),id=await row.getAttribute('data-element-id');
   await row.locator('[data-delete]').click();
   await q(`[data-restore-ghost="${id}"]`).click();
   assert.equal(await q(`#elementList .element-row[data-element-id="${id}"]`).count(),1);
   await q('#cancelDialog').click();assert.equal(await snapshot(),before);
  });
  for(const width of [800,600])await check(`Responsive ${width}px preserves text, visible actions and access to all values`,async()=>{
   await page.setViewportSize({width,height:816});await ev('reset(true);selected=3;focusedVariable=1;render()');
   await q('[data-row="1"] .vinput').fill(sample);
   assert.ok(await ev('document.documentElement.scrollWidth<=innerWidth'));
   await screenShot('workbench-'+width);
   await page.getByRole('button',{name:'Windows 环境',exact:true}).click();
   assert.ok(await ev('document.documentElement.scrollWidth<=innerWidth'));
   const table=await q('.windows-page .outline-table').boundingBox();assert.ok(table.width<=width);
   const system=q('[data-windows-value="System|PATH"]');await system.scrollIntoViewIfNeeded();
   const limits=await system.evaluate(el=>({client:el.clientWidth,scroll:el.scrollWidth}));assert.ok(limits.scroll<=limits.client+1);
   await screenShot('windows-'+width);
   await q('[data-windows-edit="System|PATH"]').click();assert.equal(await q('#elementList .element-row').count(),7);
   await q('#cancelDialog').click();
  });
  await check('Reduced motion removes local stream and comparison reveal animations',async()=>{
   await ev('go("workbench");retrigger($("inspect"),"is-revealing")');
   assert.equal(await q('#inspect').evaluate(el=>getComputedStyle(el).animationName),'none');
   assert.equal(await ev('matchMedia("(prefers-reduced-motion: reduce)").matches'),true);
  });
  await check('No JavaScript runtime errors during all browser workflows',async()=>assert.deepEqual(errors,[]));
 } finally {
  fs.writeFileSync(path.join(__dirname,'browser-report.json'),JSON.stringify({browser:'isolated headless Google Chrome',appUrl:process.env.V3_APP_URL||pathToFileURL(path.join(root,'temp.html')).href,desktopViewport:{width:1383,height:851},narrowWidths:[800,600],passed:checks.filter(c=>c.passed).length,total:checks.length,checks,errors},null,2));
  await browser.close();
 }
 console.log(JSON.stringify({passed:checks.filter(c=>c.passed).length,total:checks.length,failures:checks.filter(c=>!c.passed)},null,2));
 if(checks.some(c=>!c.passed))process.exitCode=1;
})().catch(error=>{console.error(error);process.exitCode=1;});

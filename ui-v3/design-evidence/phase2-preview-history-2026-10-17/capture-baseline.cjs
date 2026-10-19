'use strict';
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const {chromium}=require(process.env.V3_PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.V3_BROWSER_PATH,headless:true});
 const page=await browser.newPage({viewport:{width:1383,height:851},reducedMotion:'reduce'});
 try{
  await page.goto(pathToFileURL(path.resolve(__dirname,'../../temp.html')).href);
  await page.getByRole('button',{name:'查看Hadoop',exact:true}).click();
  await page.locator('[data-inspect="1"]').click();
  await page.getByRole('textbox',{name:'Path 当前选定值',exact:true}).fill('%HADOOP_HOME%\\bin;;%HADOOP_HOME%\\bin; H:\\开发工具\\长目录 ;');
  await page.locator('#previewButton').click();
  await page.screenshot({path:path.join(__dirname,'preview-before-desktop.png')});
  await page.setViewportSize({width:600,height:851});
  await page.screenshot({path:path.join(__dirname,'preview-before-600.png')});
  await page.keyboard.press('Escape');
  await page.setViewportSize({width:1383,height:851});
  await page.evaluate(()=>{windows['User|PATH']='C:\\Tools\\Base;;C:\\Tools\\SDK;C:\\Tools\\Old;C:\\Tools\\SDK';go('windows')});
  await page.locator('[data-windows-edit="User|PATH"]').click();
  const rows=()=>page.locator('#elementList .element-row');
  await rows().nth(2).locator('.element-input').fill('D:\\Tools\\SDK-Next');
  await rows().nth(2).locator('[data-move-up]').click();
  await rows().nth(3).locator('[data-delete]').click();
  await page.locator('#addElement').click();
  await page.locator('#elementList .element-input').last().fill('D:\\Tools\\Extra');
  await page.locator('#applyElements').click();
  await page.getByRole('button',{name:'历史',exact:true}).click();
  await page.locator('.history-details summary').first().click();
  await page.screenshot({path:path.join(__dirname,'history-before-desktop.png')});
  await page.setViewportSize({width:600,height:851});
  await page.screenshot({path:path.join(__dirname,'history-before-600.png')});
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});

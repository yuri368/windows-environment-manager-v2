'use strict';
// Isolated local-development renderer. Does not connect to the user's Chrome profile.
const path=require('node:path'),{pathToFileURL}=require('node:url');
const {chromium}=require(process.env.V3_PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.V3_BROWSER_PATH,headless:true});
 try {
  const page=await browser.newPage({viewport:{width:1383,height:851},reducedMotion:'reduce'});
  await page.goto(pathToFileURL(path.resolve(__dirname,'../../temp.html')).href);
  await page.getByRole('button',{name:'查看Hadoop',exact:true}).click();
  await page.getByRole('button',{name:'查看 Path 的 Windows 对照',exact:true}).click();
  await page.getByRole('textbox',{name:'Path 当前选定值',exact:true}).fill('%HADOOP_HOME%\\bin;;%HADOOP_HOME%\\bin; H:\\开发工具\\长目录 ;');
  await page.screenshot({path:path.join(__dirname,'claude-workbench.png')});
  console.log(await page.evaluate(()=>({width:innerWidth,height:innerHeight,after:document.querySelector('#compareAfter').getBoundingClientRect().toJSON(),input:document.querySelector('[data-row="1"] .vinput').getBoundingClientRect().toJSON(),review:document.querySelector('#inspectReview').innerText})));
  await page.getByRole('button',{name:'Windows 环境',exact:true}).click();
  await page.screenshot({path:path.join(__dirname,'claude-windows.png')});
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});

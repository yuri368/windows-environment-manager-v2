/* Exercise sample UI configurations in JSDOM; no real environment access. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),vm=require('node:vm');
const {JSDOM,VirtualConsole}=require('jsdom');
const root=path.resolve(__dirname,'..'),checks=[],errors=[],vc=new VirtualConsole();
vc.on('jsdomError',e=>errors.push(e.message));
const examples=fs.readFileSync(path.join(root,'demo-configs.js'),'utf8');new vm.Script(examples);
const dom=new JSDOM(fs.readFileSync(path.join(root,'temp.html'),'utf8'),{
  runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,
  beforeParse(w){ for(const configModule of ['config-format.js','config-editor.js'])w.eval(require('node:fs').readFileSync(require('node:path').resolve(__dirname,"..",configModule),'utf8'));
    w.eval(fs.readFileSync(path.join(__dirname,'../demo-plan.js'),'utf8'));w.confirm=()=>true;w.eval(examples);w.eval(fs.readFileSync(path.join(root,'windows-elements.js'),'utf8'));}
});
const w=dom.window,doc=w.document,q=s=>doc.querySelector(s),all=s=>[...doc.querySelectorAll(s)];
const check=(name,fn)=>{fn();checks.push({name,passed:true});};
const change=(el,val)=>{el.value=val;el.dispatchEvent(new w.Event('change',{bubbles:true}));};
const openApp=name=>{const el=all('[data-app]').find(el=>el.querySelector('b').textContent===name);assert.ok(el,name);el.click();};
const choosePlan=name=>{q('#planButton').click();all('[data-plan]').find(el=>el.dataset.plan===name).click();};
const selectVariable=name=>{const row=all('#varRows [data-row]').find(row=>row.querySelector('.vname').textContent===name);assert.ok(row,name);row.click();};
check('Expanded fixtures have 13 apps, 23 schemes and 60 variable definitions; no initial config drafts',()=>{
  assert.equal(all('[data-app]').length,13);
  assert.equal(w.eval('initialApps.reduce((n,a)=>n+a.profiles.length,0)'),23);
  assert.equal(w.eval('initialApps.reduce((n,a)=>n+a.profiles.reduce((k,p)=>k+p.vars.length,0),0)'),60);
  assert.equal(w.eval('configChanges().length'),0);
});
check('Second candidate and saved value select distinct strings without writing the mock snapshot',()=>{
  openApp('Node.js');assert.equal(all('#varRows [data-row]').length,3);assert.match(q('.titleline .tag').textContent,/3\/3/);
  const before=w.eval('JSON.stringify(windows)');
  change(q('#varRows .source'),'候选值 2');assert.equal(q('#varRows .vinput').value,'D:\\便携工具\\Node22');
  change(q('#varRows .source'),'已保存值');assert.equal(q('#varRows .vinput').value,'D:\\Portable Apps\\Node22');
  assert.equal(w.eval('JSON.stringify(windows)'),before);
});
check('Scheme switching keeps each runtime draft in its own scheme',()=>{
  choosePlan('实验版本');assert.equal(q('#varRows .vinput').value,'C:\\Dev Tools\\Node24');
  choosePlan('团队默认');assert.equal(q('#varRows .vinput').value,'D:\\Portable Apps\\Node22');
  selectVariable('NODE_OPTIONS');q('[data-view="windows"]').click();q('[data-windows-edit="User|NODE_OPTIONS"]').click();
  assert.equal(all('.element-input').length,1);assert.ok(q('.element-input').value.includes(';'));
  assert.equal(q('#addElement').hidden,true);q('#cancelDialog').click();q('[data-view="workbench"]').click();
});
check('Seeded whole-string flags stay clean initially and restore to their seeded baseline',()=>{
  q('#configBtn').click();q('[data-editapp="7"]').click();
  const specialRow=all('[data-tableplan][data-tablevar="2"]').find(row=>row.dataset.tableplan===w.eval('editor.profile'));
  assert.ok(specialRow,'The current profile should expose its third variable row');specialRow.click();
  assert.equal(q('#cfgSpecial').checked,true);assert.equal(q('#cancelConfig').disabled,true);
  q('#cfgSpecial').checked=false;q('#cfgSpecial').dispatchEvent(new w.Event('change',{bubbles:true}));
  assert.match(q('#configStatus').textContent,/1 项属性修改/);q('#cancelConfig').click();
  assert.equal(q('#cfgSpecial').checked,true);assert.equal(w.eval('configChanges().length'),0);
});
check('System scope examples expose partial matching and a separate system Path',()=>{
  q('[data-view="workbench"]').click();openApp('CUDA Toolkit');assert.match(q('.titleline .tag').textContent,/部分匹配.*1\/3/);
  selectVariable('Path');q('#editWindowsElements').click();assert.match(q('.dialoghead .eyebrow').textContent,/Windows 环境.*系统/);
  assert.match(q('#dialogTitle').title,/REG_EXPAND_SZ/);
  assert.equal(all('.element-input').filter(el=>el.value==='C:\\Windows\\System32').length,2);
  assert.ok(all('.element-input').some(el=>el.value===''));q('#cancelDialog').click();
});
check('List example retains duplicates and blanks; empty and missing values display differently',()=>{
  openApp('列表与空值');q('#editWindowsElements').click();
  assert.deepEqual(all('.element-input').map(el=>el.value),['C:\\Demo\\Alpha','','C:\\Demo\\Alpha','C:\\Demo\\Gamma','']);
  q('#cancelDialog').click();selectVariable('DEMO_EMPTY_VALUE');assert.equal(q('#compareActual .diff-value').dataset.rawValue,'');assert.equal(q('#compareActual .status-badge').textContent,'空字符串');
  selectVariable('DEMO_UNSET_VALUE');assert.equal(q('#compareActual').textContent,'（不存在）');
  choosePlan('自定义排序预览');q('#previewButton').click();assert.equal(q('#confirmDialog').disabled,true);
  assert.match(q('.dialogbody').textContent,/自定义排序/);q('#cancelDialog').click();
});
check('Shared variable conflict is visible and confirmation is disabled',()=>{
  openApp('Node.js');change(q('#varRows .source'),'默认值');
  for(const name of ['Node.js','共享变量冲突示例']){
    const el=all('[data-app]').find(el=>el.querySelector('b').textContent===name).querySelector('.appcheck');
    el.checked=true;el.dispatchEvent(new w.Event('change',{bubbles:true}));
  }
  const before=w.eval('JSON.stringify(windows)');q('#previewButton').click();
  assert.match(q('.dialogbody').textContent,/目标值存在冲突/);assert.equal(q('#confirmDialog').disabled,true);
  q('#cancelDialog').click();assert.equal(w.eval('JSON.stringify(windows)'),before);
});
check('Long Chinese example exposes six variables with full values and reset restores sample choices',()=>{
  q('#selectNone').click();openApp('中文长路径工具链');assert.equal(all('#varRows [data-row]').length,6);
  assert.ok(q('#varRows .vinput').value.length>65);assert.match(q('.demo-description').textContent,/长路径/);
  w.eval('reset(true)');openApp('Node.js');assert.equal(q('#varRows .source').value,'默认值');
  assert.equal(q('#varRows .vinput').value,'C:\\Dev Tools\\Node22');assert.equal(w.eval('configChanges().length'),0);
});
check('No runtime errors with the expanded configurations',()=>assert.deepEqual(errors,[]));
const report={verification:'JSDOM demo data and UI events only; actual browser visual rendering and system operations not verified',checks};
fs.writeFileSync(path.join(__dirname,'examples-report.json'),JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify(report,null,2));w.close();

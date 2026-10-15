'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
const root=path.resolve(__dirname,'..'),checks=[],errors=[];
const vc=new VirtualConsole();vc.on('jsdomError',error=>errors.push(error.message));
const dom=new JSDOM(fs.readFileSync(path.join(root,'temp.html'),'utf8'),{
 runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,
 beforeParse(w){for(const file of ['config-format.js','config-editor.js','demo-plan.js','demo-configs.js','windows-elements.js'])w.eval(fs.readFileSync(path.join(root,file),'utf8'));w.confirm=()=>true;}
});
const w=dom.window,q=selector=>w.document.querySelector(selector),ev=code=>w.eval(code),all=selector=>[...w.document.querySelectorAll(selector)];
const get=()=>ev('scheme(1).vars[1]'),raw=()=>ev('windows["User|PATH"]'),history=()=>ev('history.length');
const target="[{app:1,profile:'Python313',varIdx:1}]";
function setup(actual='X;A;B;C;Y',desired='A;B;C'){
 ev('closeModal();reset(true)');ev('selected=1;focusedVariable=1;const v=scheme(1).vars[1];v.defaultValue='+JSON.stringify(desired)+';v.baselineValue=v.defaultValue;v.mode="-append";windows["User|PATH"]='+JSON.stringify(actual)+';render()');
}
function input(value){const field=q('#varRows [data-row="1"] .vinput');field.value=value;field.dispatchEvent(new w.Event('input',{bubbles:true}));return field;}
function editorInput(index,value){const field=all('#elementList .element-row .element-input')[index];assert.ok(field);field.value=value;field.dispatchEvent(new w.Event('input',{bubbles:true}));return field;}
function preview(targets=target){ev("showPreview('应用','刷新验收',"+targets+")");}
function check(name,run){try{run();checks.push({name,passed:true});}catch(error){checks.push({name,passed:false,error:error.stack});}}

check('Known Windows edit refreshes workbench and keeps original unique removal available',()=>{
 setup();input('A;C;E');const baseline=get().matchBaseline;
 q('#editWindowsElements').click();editorInput(4,'Z');q('#applyElements').click();
 assert.strictEqual(get().matchBaseline,baseline);assert.equal(raw(),'X;A;B;C;Z');
 const plan=ev('demoPlan(scheme(1).vars[1])');assert.equal(plan.valid,true);assert.equal(plan.afterRaw,'X;A;C;Z;E');assert.equal(plan.operations.find(op=>op.type==='remove').value,'B');
 preview();assert.equal(ev('modalAction.rows[0].plan.afterRaw'),plan.afterRaw);q('#confirmDialog').click();assert.equal(raw(),plan.afterRaw);
});
check('Replacing the original occurrence preserves the draft but blocks deletion revival',()=>{
 setup();input('A;C;E');const baseline=get().matchBaseline;
 q('#editWindowsElements').click();editorInput(2,'REPLACED');q('#applyElements').click();
 assert.strictEqual(get().matchBaseline,baseline);assert.equal(ev('value(scheme(1).vars[1])'),'A;C;E');
 const plan=ev('demoPlan(scheme(1).vars[1])');assert.equal(plan.valid,true);assert.equal(plan.afterRaw,'X;A;REPLACED;C;Y;E');assert.equal(plan.operations.some(op=>op.type==='remove'&&op.value==='B'),false);
 preview();q('#confirmDialog').click();assert.equal(raw(),plan.afterRaw);assert.equal(get().matchBaseline,null);
});
check('Unknown write permits safe addition while unresolved removal and draft stay pending',()=>{
 setup();input('A;C;E');const baseline=get().matchBaseline;
 ev('windows["User|PATH"]="X;A;B;C;Y;EXTERNAL";targetRevisions["User|PATH"]++;refreshVisibleState()');
 const plan=ev('demoPlan(scheme(1).vars[1])');assert.equal(plan.valid,true);assert.equal(plan.operations.some(op=>op.type==='remove'),false);assert.ok(plan.issues.some(issue=>issue.reason==='continuity'));
 preview();q('#confirmDialog').click();assert.equal(raw(),'X;A;B;C;Y;EXTERNAL;E');assert.strictEqual(get().matchBaseline,baseline);assert.equal(get().dirty,true);assert.equal(ev('value(scheme(1).vars[1])'),'A;C;E');
});
check('Preview A to B to C requires fresh review before a single final write',()=>{
 setup();input('A;C;E');preview();const count=history();
 ev('windows["User|PATH"]+=";B_EXT";targetRevisions["User|PATH"]++');q('#confirmDialog').click();
 assert.equal(history(),count);assert.equal(ev('modalAction.version'),2);assert.equal(ev('modalAction.reviewedVersion'),0);assert.ok(q('#modal').classList.contains('open'));
 ev('windows["User|PATH"]+=";C_EXT";targetRevisions["User|PATH"]++');q('#confirmDialog').click();
 assert.equal(history(),count);assert.equal(ev('modalAction.version'),3);assert.equal(ev('modalAction.reviewedVersion'),0);
 q('#confirmDialog').click();assert.equal(history(),count);assert.equal(ev('modalAction.reviewedVersion'),3);
 q('#confirmDialog').click();assert.equal(history(),count+1);assert.match(raw(),/C_EXT;E$/);
});
check('Batch stale first click makes zero writes to all targets',()=>{
 setup();input('A;C;E');ev('scheme(0).vars[0].defaultValue="BATCH_NEW";scheme(0).vars[0].mode="-append"');
 preview("[{app:1,profile:'Python313',varIdx:1},{app:0,profile:'JDK8',varIdx:0}]");
 const count=history();ev('windows["User|PATH"]+=";EXTERNAL";targetRevisions["User|PATH"]++');const before=ev('JSON.stringify(windows)');
 q('#confirmDialog').click();assert.equal(ev('JSON.stringify(windows)'),before);assert.equal(history(),count);assert.ok(q('#modal').classList.contains('open'));assert.equal(ev('modalAction.reviewedVersion'),0);
});
check('Unrelated target changes do not force target preview refresh',()=>{
 setup();input('A;C;E');preview();ev('windows["System|UNRELATED"]="NEW";targetRevisions["System|UNRELATED"]++');
 assert.equal(ev('refreshPreview()'),false);assert.equal(ev('modalAction.version'),1);q('#confirmDialog').click();assert.equal(raw(),'X;A;C;Y;E');
});
check('Visible refresh preserves active textarea node, value and caret',()=>{
 setup();const field=input('A;C;E');field.focus();field.setSelectionRange(2,2);const baseline=get().matchBaseline;
 ev('windows["User|PATH"]+=";EXTERNAL";targetRevisions["User|PATH"]++;refreshVisibleState()');
 assert.strictEqual(q('#varRows [data-row="1"] .vinput'),field);assert.strictEqual(w.document.activeElement,field);assert.equal(field.value,'A;C;E');assert.equal(field.selectionStart,2);assert.strictEqual(get().matchBaseline,baseline);
});
check('Expired Windows editor keeps row IDs and draft after Apply is rejected',()=>{
 setup();q('#editWindowsElements').click();editorInput(0,'DRAFT');const beforeRows=all('#elementList .element-row');const ids=beforeRows.map(row=>row.dataset.elementId),count=history();
 ev('windows["User|PATH"]="EXTERNAL;A;B;C;Y";targetRevisions["User|PATH"]++');q('#applyElements').click();
 assert.equal(history(),count);assert.equal(raw(),'EXTERNAL;A;B;C;Y');assert.ok(q('#modal').classList.contains('open'));
 assert.deepEqual(all('#elementList .element-row').map(row=>row.dataset.elementId),ids);assert.equal(q('#elementList .element-input').value,'DRAFT');
 assert.match(q('#editorLatestSnapshot').textContent,/EXTERNAL;A;B;C;Y/);
});
check('Unrelated configuration changes preserve trusted removal continuity',()=>{
 setup();input('A;C;E');const baseline=get().matchBaseline;
 q('#editWindowsElements').click();editorInput(4,'Z');q('#applyElements').click();
 const state=get().removalState;ev('setConfigProperty(scheme(1).vars[1],"editable",true)');
 assert.strictEqual(get().removalState,state);assert.strictEqual(get().matchBaseline,baseline);
 ev('restoreConfig()');assert.strictEqual(get().removalState,state);assert.strictEqual(get().matchBaseline,baseline);
 const plan=ev('demoPlan(scheme(1).vars[1])');assert.equal(plan.afterRaw,'X;A;C;Z;E');assert.equal(plan.operations.find(op=>op.type==='remove').value,'B');
});
check('No JSDOM runtime errors',()=>assert.deepEqual(errors,[]));
console.log(JSON.stringify({passed:checks.filter(c=>c.passed).length,total:checks.length,checks},null,2));
if(checks.some(c=>!c.passed))process.exitCode=1;
w.close();

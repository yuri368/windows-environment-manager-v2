'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
const root=path.resolve(__dirname,'..'),checks=[],errors=[];
const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
const dom=new JSDOM(fs.readFileSync(path.join(root,'temp.html'),'utf8'),{runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,beforeParse(w){ for(const configModule of ['config-format.js','config-editor.js'])w.eval(require('node:fs').readFileSync(require('node:path').resolve(__dirname,"..",configModule),'utf8'));w.confirm=()=>true;for(const f of ['demo-plan.js','demo-configs.js','windows-elements.js'])w.eval(fs.readFileSync(path.join(root,f),'utf8'));}});
const w=dom.window,q=s=>w.document.querySelector(s),all=s=>[...w.document.querySelectorAll(s)],ev=s=>w.eval(s);
const get=()=>ev('scheme(1).vars[1]'),snapshot=()=>ev('JSON.stringify(windows)'),history=()=>ev('history.length');
const rawPane=id=>all('#'+id+' .diff-value[data-raw-value]').filter(node=>!node.closest('.comparison-token.is-ghost')).map(node=>node.getAttribute('data-raw-value')).join(';');
function input(raw){const field=q('#varRows [data-row="1"] .vinput');field.value=raw;field.dispatchEvent(new w.Event('input',{bubbles:true}));}
function setup(actual='X;A;B;C;Y',desired='A;B;C;D'){
 ev('closeModal();reset(true)');ev("selected=1; focusedVariable=1; const v=scheme(1).vars[1];v.defaultValue="+JSON.stringify(desired)+";v.baselineValue=v.defaultValue;windows['User|PATH']="+JSON.stringify(actual)+";render()");
}
function preview(targets="[{app:1,profile:'Python313',varIdx:1}]"){ev("showPreview('应用','增删验证',"+targets+")");}
function check(name,run){try{run();checks.push({name,passed:true});}catch(e){checks.push({name,passed:false,error:e.stack});}}
try{
check('Baseline is captured before first real input and remains frozen through continuous edits and cancelled preview',()=>{
 setup();input('A;B;E');const baseline=get().matchBaseline;assert.equal(baseline.selectedRaw,'A;B;C;D');assert.ok(Object.isFrozen(baseline));input('A;B;E;F');assert.strictEqual(get().matchBaseline,baseline);const before=snapshot(),n=history();preview();q('#cancelDialog').click();assert.strictEqual(get().matchBaseline,baseline);assert.equal(snapshot(),before);assert.equal(history(),n);
});
check('Safe matched subset deletion and independent additions agree across preview, actual result and immutable history',()=>{
 setup();input('A;B;E;F');const plan=ev('demoPlan(scheme(1).vars[1])');assert.equal(plan.afterRaw,'X;A;B;Y;E;F');assert.deepEqual(Array.from(plan.operations,x=>x.type),['remove','insert','insert']);assert.equal(plan.operations[0].value,'C');assert.match(q('#compareActual').textContent,/C.*待删除/);assert.doesNotMatch(q('#compareDesired').textContent,/待修改|替换/);assert.match(q('#compareDesired').textContent,/E.*待追加/);assert.equal(rawPane('compareActual'),'X;A;B;C;Y');assert.equal(rawPane('compareAfter'),plan.afterRaw);preview();assert.strictEqual(ev('modalAction.rows[0].plan'),plan);q('#confirmDialog').click();assert.equal(ev("windows['User|PATH']"),plan.afterRaw);assert.equal(get().matchBaseline,null);const event=ev('history[0]');assert.strictEqual(event.changes[0].plan,plan);assert.ok(Object.isFrozen(event));q('[data-view="history"]').click();assert.match(q('#screen').textContent,/删除 1 项.*追加 2 项/);
});
check('Previously missing candidate disappears without deleting that value or leaving a stale addition',()=>{
 setup('A;B;C','D');input('E');const plan=ev('demoPlan(scheme(1).vars[1])');assert.equal(plan.operations.length,1);assert.equal(plan.operations[0].value,'E');assert.equal(plan.operations[0].type,'insert');assert.equal(rawPane('compareActual'),'A;B;C');assert.equal(rawPane('compareAfter'),plan.afterRaw);assert.doesNotMatch(q('#compareDesired').textContent,/D/);
});
check('Delete-only keeps new desired matched while recording pending deletion',()=>{
 setup('X;A;B','A;B');input('A');const plan=ev('demoPlan(scheme(1).vars[1])');assert.equal(plan.matchResult.satisfied,true);assert.equal(plan.afterRaw,'X;A');assert.equal(plan.operations[0].type,'remove');assert.match(q('#compareActual').textContent,/待删除/);assert.equal(rawPane('compareAfter'),plan.afterRaw);preview();assert.equal(q('#confirmDialog').disabled,false);q('#confirmDialog').click();assert.equal(ev("windows['User|PATH']"),'X;A');
});
check('Changing back to original selection cancels removals and restore action ends the edit cycle',()=>{
 setup('A;B;C','A;B;C');input('E');const baseline=get().matchBaseline;input('A;B;C');assert.strictEqual(get().matchBaseline,baseline);assert.equal(ev('demoPlan(scheme(1).vars[1]).operations.length'),0);input('E');q('#restoreSelectedEdit').click();assert.equal(ev('value(scheme(1).vars[1])'),'A;B;C');assert.equal(get().matchBaseline,null);
});
check('Duplicate actual occurrences are preserved and never mislabeled as definite removals',()=>{
 setup('A;B;B;C','A;B;C');input('A;C;D');const plan=ev('demoPlan(scheme(1).vars[1])');assert.equal(plan.operations.some(x=>x.type==='remove'),false);assert.equal(plan.afterRaw,'A;B;B;C;D');assert.equal(plan.issues.some(x=>x.reason==='duplicate'),true);assert.doesNotMatch(q('#compareActual').textContent,/待删除/);assert.equal(rawPane('compareActual'),'A;B;B;C');assert.equal(rawPane('compareAfter'),plan.afterRaw);assert.equal(plan.issues.filter(issue=>issue.reason==='duplicate').length,1);
});
check('Other effective selected scheme protects shared target content even when it is not checked',()=>{
 setup('A;B;C','A;B');ev("scheme(0).vars[1].defaultValue='B'");input('A;E');const plan=ev('demoPlan(scheme(1).vars[1])');assert.equal(plan.afterRaw,'A;B;C;E');assert.equal(plan.issues.some(x=>x.reason==='shared'),true);assert.doesNotMatch(q('#compareActual').textContent,/待删除/);assert.equal(rawPane('compareActual'),'A;B;C');assert.equal(rawPane('compareAfter'),plan.afterRaw);
});
check('Two app plans for one target are merged once and duplicate additions are inserted once',()=>{
 setup('A;B;X','A;B');ev("scheme(0).vars[1].defaultValue='B;E';scheme(0).vars[1].mode='-append'");input('A;E');preview("[{app:1,profile:'Python313',varIdx:1},{app:0,profile:'JDK8',varIdx:1}]");assert.equal(ev('modalAction.rows.length'),1);const plan=ev('modalAction.rows[0].plan');assert.equal(plan.afterRaw,'A;B;X;E');assert.equal(plan.operations.filter(x=>x.type==='insert').length,1);q('#confirmDialog').click();assert.equal(ev("windows['User|PATH']"),'A;B;X;E');assert.equal(ev('history[0].changes.length'),1);assert.equal(get().matchBaseline,null);
});
check('Related snapshot changes invalidate edit baseline, preserve current input and require explicit rebase and fresh preview',()=>{
 setup();input('A;B;E');const baseline=get().matchBaseline;ev("windows['User|PATH']+=';EXTERNAL';targetRevisions['User|PATH']=1;render()");const plan=ev('demoPlan(scheme(1).vars[1])');assert.equal(plan.valid,false);assert.equal(plan.operations.length,0);preview();assert.equal(q('#confirmDialog').disabled,true);q('#cancelDialog').click();assert.strictEqual(get().matchBaseline,baseline);q('#rebaseSelectedEdit').click();assert.ok(q('#confirmRebase'));q('#confirmRebase').click();assert.notStrictEqual(get().matchBaseline,baseline);assert.equal(ev('value(scheme(1).vars[1])'),'A;B;E');assert.equal(get().matchBaseline.selectedRaw,'A;B;C;D');preview();q('#confirmDialog').click();assert.equal(ev("windows['User|PATH']"),'X;A;B;Y;EXTERNAL;E');
});
check('Protected dependencies changed after preview invalidate even when the actual target snapshot is unchanged',()=>{
 setup('A;B;C','A;B');input('A;E');preview();ev("scheme(0).vars[1].defaultValue='B'");const before=snapshot(),n=history();q('#confirmDialog').click();assert.equal(snapshot(),before);assert.equal(history(),n);assert.match(q('#toast').textContent,/预览已失效/);
});
check('Unrelated snapshot changes allow safe frozen plan confirmation',()=>{
 setup();input('A;B;E');preview();ev("windows['System|UNRELATED']='NEW';targetRevisions['System|UNRELATED']=5");q('#confirmDialog').click();assert.equal(ev("windows['User|PATH']"),'X;A;B;Y;E');assert.equal(ev("windows['System|UNRELATED']"),'NEW');
});
check('Clearing desired deletes only matched nonblank elements, preserving empty fields and variable existence',()=>{
 setup('A;;B;','A;B');input('');const plan=ev('demoPlan(scheme(1).vars[1])');assert.equal(plan.after.exists,true);assert.equal(plan.afterRaw,';');preview();q('#confirmDialog').click();assert.equal(ev("windows['User|PATH']"),';');
});
check('Element candidate adoption edits live rows only, cancel preserves snapshot and selected baseline',()=>{
 setup('X;A;B','A;B');input('A;E');const before=snapshot(),baseline=get().matchBaseline,n=history();q('#editWindowsElements').click();assert.match(q('#pendingElements').textContent,/方案建议/);assert.ok(all('#pendingElements .element-suggestions-label').some(node=>node.textContent==='建议删除'));assert.ok(all('#pendingElements .element-suggestions-label').some(node=>node.textContent==='建议追加'));assert.equal(q('#pendingElements .pending-remove-token .diff-label').textContent,'建议删除');assert.doesNotMatch(q('#elementList').textContent,/待删除|将替换/);assert.doesNotMatch(q('#elementOriginal').textContent,/待删除|将替换/);assert.doesNotMatch(q('#elementPreview').textContent,/待删除|将替换/);const remove=q('[data-remove-pending]');assert.ok(remove);remove.click();const add=q('[data-add-pending]');assert.ok(add);add.click();assert.equal(snapshot(),before);q('#cancelDialog').click();assert.equal(snapshot(),before);assert.equal(history(),n);assert.strictEqual(get().matchBaseline,baseline);
});
check('Source switch ends prior edit context while actual row editing invalidates outstanding baseline',()=>{
 setup('A;B','A;B');input('A;E');const source=q('#varRows [data-row="1"] .source');source.value='默认值';source.dispatchEvent(new w.Event('change',{bubbles:true}));assert.equal(get().matchBaseline,null);input('A;E');q('#editWindowsElements').click();const row=q('#elementList .element-input');row.value='Z';row.dispatchEvent(new w.Event('input',{bubbles:true}));q('#applyElements').click();assert.equal(ev('demoPlan(scheme(1).vars[1]).valid'),false);assert.equal(ev('value(scheme(1).vars[1])'),'A;E');
});
check('Restoring unrelated configuration changes preserves an existing selected-value edit baseline',()=>{
 setup('A;B','A;B');input('A;E');const baseline=get().matchBaseline;ev("scheme(0).vars[0].defaultValue='UNRELATED';restoreConfig()");assert.strictEqual(get().matchBaseline,baseline);assert.equal(ev('value(scheme(1).vars[1])'),'A;E');assert.equal(ev('demoPlan(scheme(1).vars[1]).afterRaw'),'A;E');
});
check('No JSDOM runtime errors',()=>assert.deepEqual(errors,[]));
}finally{w.close();}
console.log(JSON.stringify({passed:checks.filter(x=>x.passed).length,total:checks.length,checks},null,2));if(checks.some(x=>!x.passed))process.exitCode=1;

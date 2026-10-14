'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
const root=path.resolve(__dirname,'..'),checks=[],errors=[];
const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
const dom=new JSDOM(fs.readFileSync(path.join(root,'temp.html'),'utf8'),{runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,beforeParse(w){w.confirm=()=>true;for(const f of ['demo-plan.js','demo-configs.js','windows-elements.js'])w.eval(fs.readFileSync(path.join(root,f),'utf8'));}});
const w=dom.window,P=w.DemoPlan,q=s=>w.document.querySelector(s),all=s=>[...w.document.querySelectorAll(s)],ev=s=>w.eval(s);
function check(name,run){try{run();checks.push({name,passed:true});}catch(e){checks.push({name,passed:false,error:e.stack});}}
const rows=()=>all('#elementList .element-row'),values=()=>rows().map(n=>n.querySelector('.element-input').value);
function setInput(field,raw){field.value=raw;field.dispatchEvent(new w.Event('input',{bubbles:true}));}
function setup(raw='X;A;C;Y',old='A;C',next='A;E',mode='-append'){
 ev('closeModal();reset(true)');ev('selected=1;focusedVariable=1;const v=scheme(1).vars[1];v.defaultValue='+JSON.stringify(old)+';v.baselineValue=v.defaultValue;v.mode='+JSON.stringify(mode)+";windows['User|PATH']="+JSON.stringify(raw)+';render()');setInput(q('#varRows [data-row="1"] .vinput'),next);q('#editWindowsElements').click();
}
const rawValues=selector=>all(selector+' .diff-value[data-raw-value]').map(n=>n.dataset.rawValue);
const snapshot=()=>ev('JSON.stringify(windows)'),hist=()=>ev('history.length');
const summary=()=>q('#elementStatus').textContent;
const removeButton=()=>q('[data-remove-pending]'),addButton=()=>q('[data-add-pending]');
check('Only suggestion region labels unadopted scheme deletes/adds, never live row or comparison',()=>{
 setup();assert.match(q('#pendingElements').textContent,/方案建议/);assert.match(q('#pendingElements').textContent,/建议删除/);assert.match(q('#pendingElements').textContent,/建议追加/);assert.equal(all('#pendingElements [aria-pressed="true"]').length,0);assert.doesNotMatch(q('#elementList').textContent,/待删除|建议删除|将替换/);assert.doesNotMatch(q('#elementOriginal').textContent+q('#elementPreview').textContent,/待删除|建议删除|将替换/);assert.equal(all('#elementOriginal .is-removed,#elementPreview .is-removed,.element-row.is-pending-remove').length,0);assert.deepEqual(values(),['X','A','C','Y']);assert.deepEqual(rawValues('#elementPreview'),['X','A','C','Y']);assert.match(summary(),/0 项变化/);assert.ok(q('#applyElements').disabled);
});
check('Plus/minus controls explicitly describe adopting into this edit instead of applying Windows',()=>{
 setup();assert.match(removeButton().getAttribute('aria-label'),/采纳/);assert.match(addButton().getAttribute('aria-label'),/采纳/);assert.match(q('#pendingElements').textContent,/仅修改编辑列表/);
});
check('Adopting deletion removes stable row, retires suggestion, creates actual deletion ghosts and summary',()=>{
 setup();const id=rows()[2].dataset.elementId,before=snapshot(),n=hist();removeButton().click();assert.deepEqual(values(),['X','A','Y']);assert.equal(removeButton(),null);assert.equal(q('#elementOriginal [data-element-id="'+id+'"]').classList.contains('is-removed'),true);assert.equal(q('#elementPreview [data-element-id="'+id+'"]').classList.contains('is-removed'),true);assert.match(summary(),/删除 1/);assert.match(summary(),/新增 0/);assert.equal(snapshot(),before);assert.equal(hist(),n);assert.equal(q('#elementSerialized').textContent,'X;A;Y');
});
for(const mode of ['+append','-append','append'])check(mode+' addition changes live rows and exact raw, not Windows before Apply',()=>{
 setup('X;A;C;Y','A;C','A;E',mode);const oldIds=rows().map(n=>n.dataset.elementId),before=snapshot();addButton().click();assert.deepEqual(values(),mode==='+append'?['E','X','A','C','Y']:['X','A','C','Y','E']);assert.ok(addButton());assert.equal(addButton().getAttribute('aria-pressed'),'true');assert.equal(addButton().textContent,'✓');assert.ok(addButton().closest('.editor-suggestion-token').classList.contains('is-adopted'));assert.ok(removeButton());const newRow=rows().find(n=>n.querySelector('input').value==='E');assert.ok(!oldIds.includes(newRow.dataset.elementId));assert.ok(newRow.classList.contains('is-added'));assert.match(summary(),/新增 1/);assert.match(summary(),/删除 0/);assert.equal(snapshot(),before);assert.deepEqual(rawValues('#elementOriginal'),['X','A','C','Y']);assert.ok(rawValues('#elementPreview').includes('E'));
});
check('Manual C to E is explicit REPLACE; unadopted add/delete suggestions retire without fabricated REMOVE/ADD',()=>{
 setup();setInput(rows()[2].querySelector('input'),'E');assert.equal(removeButton(),null);assert.equal(addButton(),null);assert.match(q('#pendingElements').textContent,/没有尚待采纳/);assert.match(summary(),/替换 1/);assert.match(summary(),/新增 0.*删除 0/);q('#applyElements').click();const p=ev('history[0].changes[0].plan');assert.deepEqual(Array.from(p.operations,o=>o.action),['REPLACE']);assert.equal(ev("windows['User|PATH']"),'X;A;E;Y');
});
check('Restore reinstates exact live IDs, suggestions and zero edit; no ghosts remain',()=>{
 setup();const ids=rows().map(n=>n.dataset.elementId),before=snapshot();removeButton().click();addButton().click();q('#restoreElements').click();assert.deepEqual(values(),['X','A','C','Y']);assert.deepEqual(rows().map(n=>n.dataset.elementId),ids);assert.ok(removeButton());assert.ok(addButton());assert.match(summary(),/0 项变化/);assert.ok(q('#applyElements').disabled);assert.equal(all('#elementOriginal .is-removed,#elementPreview .is-removed').length,0);assert.equal(snapshot(),before);
});
check('Cancel discards even adopted changes; reopening recovers current suggestions without history',()=>{
 setup();const before=snapshot(),n=hist();removeButton().click();addButton().click();q('#cancelDialog').click();assert.equal(snapshot(),before);assert.equal(hist(),n);q('#editWindowsElements').click();assert.ok(removeButton());assert.ok(addButton());assert.deepEqual(values(),['X','A','C','Y']);assert.ok(q('#applyElements').disabled);
});
check('Apply deletion only never consumes remaining unadopted E addition',()=>{
 setup();removeButton().click();q('#applyElements').click();assert.equal(ev("windows['User|PATH']"),'X;A;Y');const p=ev('history[0].changes[0].plan');assert.equal(p.operations.length,1);assert.equal(p.operations[0].action,'REMOVE');assert.equal(p.after.raw,'X;A;Y');
});
check('Apply addition only retains unadopted C deletion; history records just one ADD',()=>{
 setup();addButton().click();q('#applyElements').click();assert.equal(ev("windows['User|PATH']"),'X;A;C;Y;E');const p=ev('history[0].changes[0].plan');assert.deepEqual(Array.from(p.operations,o=>o.action),['ADD']);
});
check('Manual delete retires suggestion; detached prior suggestion control cannot mutate session again',()=>{
 setup();const old=removeButton();rows()[2].querySelector('[data-delete]').click();assert.equal(removeButton(),null);const raw=q('#elementSerialized').textContent;old.click();assert.equal(q('#elementSerialized').textContent,raw);assert.match(summary(),/删除 1/);
});
check('Repeated Desired requires only one actual adoption and count follows operations',()=>{
 setup('A;C','C','E;E');addButton().click();assert.equal(all('[data-add-pending]').length,1);assert.equal(addButton().getAttribute('aria-pressed'),'true');assert.equal(values().filter(x=>x==='E').length,1);assert.match(summary(),/新增 1/);
});
check('Existing duplicate actual occurrence never receives automatic deletion suggestion',()=>{
 setup('A;C;C;Y','C','E');assert.equal(removeButton(),null);assert.match(q('#pendingElements').textContent,/重复.*明确选择元素/);assert.equal(all('#elementOriginal .is-removed,#elementPreview .is-removed').length,0);assert.match(summary(),/0 项变化/);
});
check('Shared protected actual remains visible as shared retention, not executable suggestion deletion',()=>{
 setup('A;C;Y','C','E');q('#cancelDialog').click();ev("scheme(0).vars[1].defaultValue='C';render()");q('#editWindowsElements').click();assert.equal(removeButton(),null);assert.match(q('#pendingElements').textContent,/共享保留/);assert.match(summary(),/0 项变化/);
});
check('Invalid suggestion baseline exposes no adoption buttons but permits independent manual editing',()=>{
 setup();q('#cancelDialog').click();ev("windows['User|PATH']+=';EXTERNAL';targetRevisions['User|PATH']=1;render()");q('#editWindowsElements').click();assert.match(q('#pendingElements').textContent,/方案建议已失效.*重新检测/);assert.equal(removeButton(),null);assert.equal(addButton(),null);const f=rows()[0].querySelector('input');setInput(f,'MANUAL');assert.ok(!q('#applyElements').disabled);q('#applyElements').click();assert.equal(ev("windows['User|PATH']"),'MANUAL;A;C;Y;EXTERNAL');
});
check('An actual snapshot change rejects adopted edit at Apply and creates no successful history',()=>{
 setup();const n=hist();addButton().click();ev("windows['User|PATH']='EXTERNAL';targetRevisions['User|PATH']=1");q('#applyElements').click();assert.equal(ev("windows['User|PATH']"),'EXTERNAL');assert.equal(hist(),n);assert.match(q('#toast').textContent,/失效/);
});
check('Whole/replace expectation remains plain until an explicit actual edit',()=>{
 setup('A;B','A;B','C;D','replace');assert.doesNotMatch(q('#elementList').textContent+q('#elementOriginal').textContent+q('#elementPreview').textContent,/将替换/);assert.match(summary(),/0 项变化/);assert.ok(q('#applyElements').disabled);setInput(rows()[0].querySelector('input'),'C');assert.match(summary(),/替换 1/);assert.ok(!q('#applyElements').disabled);
});
check('Result-equal duplicate identity moves produce zero operations and no misleading summary count',()=>{
 setup('A;A','A','A');rows()[1].querySelector('[data-move-up]').click();assert.match(summary(),/0 项变化/);assert.ok(q('#applyElements').disabled);
});
check('Actual replace and move flags count separately from a pending addition suggestion',()=>{
 setup('A;C;Y','C','E');setInput(rows()[1].querySelector('input'),'EDIT');rows()[1].querySelector('[data-move-up]').click();assert.match(summary(),/替换 1/);assert.match(summary(),/移动 2/);assert.match(summary(),/新增 0/);assert.ok(addButton());
});
check('Special whole string has no unadopted replacement label and applies explicit whole edit',()=>{
 setup('A;B','A;B','C;D','replace');q('#cancelDialog').click();ev('scheme(1).vars[1].special=true;render()');q('#editWindowsElements').click();assert.deepEqual(values(),['A;B']);assert.doesNotMatch(q('#elementList').textContent+q('#elementOriginal').textContent+q('#elementPreview').textContent,/将替换/);assert.ok(q('#applyElements').disabled);setInput(q('.element-input'),'C;D');assert.match(summary(),/替换 1/);q('#applyElements').click();assert.equal(ev("windows['User|PATH']"),'C;D');assert.equal(ev('history[0].changes[0].plan.special'),true);
});
check('No JavaScript errors during suggestion/adoption/restore/cancel/apply workflows',()=>assert.deepEqual(errors,[]));
const report={passed:checks.filter(c=>c.passed).length,total:checks.length,checks};console.log(JSON.stringify(report,null,2));if(checks.some(c=>!c.passed))process.exitCode=1;dom.window.close();

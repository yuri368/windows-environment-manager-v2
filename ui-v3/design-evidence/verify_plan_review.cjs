'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
const root=path.resolve(__dirname,'..'),checks=[],errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
const dom=new JSDOM(fs.readFileSync(path.join(root,'temp.html'),'utf8'),{runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,beforeParse(w){w.confirm=()=>true;for(const file of ['demo-plan.js','demo-configs.js','windows-elements.js','config-format.js','config-editor.js'])w.eval(fs.readFileSync(path.join(root,file),'utf8'));}});
const w=dom.window,q=s=>w.document.querySelector(s),all=s=>[...w.document.querySelectorAll(s)],ev=s=>w.eval(s);
const target="[{app:1,profile:'Python313',varIdx:1}]";
const before=()=>ev('JSON.stringify({windows,history})');
function setup(actual='X;A;B;C;Y',desired='A;B;C'){
 ev('closeModal();reset(true);selected=1;focusedVariable=1;const v=scheme(1).vars[1];v.defaultValue='+JSON.stringify(desired)+';v.baselineValue=v.defaultValue;v.mode="-append";windows["User|PATH"]='+JSON.stringify(actual)+';render()');
}
function input(raw){const node=q('#varRows [data-row="1"] .vinput');node.value=raw;node.dispatchEvent(new w.Event('input',{bubbles:true}));return node;}
function preview(targets=target,kind='应用'){ev(`showPreview(${JSON.stringify(kind)},'计划审阅验收',${targets})`);}
function summary(){return q('.review-summary');}
function check(name,run){try{run();checks.push({name,passed:true});}catch(e){checks.push({name,passed:false,error:e.stack});}}
check('Review is read-only and consumes the same frozen plan used by apply',()=>{
 setup();input('A;C;E');const plan=ev('demoPlan(scheme(1).vars[1])'),snapshot=before();preview();assert.strictEqual(ev('modalAction.rows[0].plan'),plan);assert.equal(summary().dataset.reviewState,'changed');assert.ok(Object.isFrozen(plan));ev('renderPreview()');assert.equal(before(),snapshot);assert.strictEqual(ev('modalAction.rows[0].plan'),plan);
 q('#confirmDialog').click();assert.equal(ev('windows["User|PATH"]'),plan.afterRaw);assert.strictEqual(ev('history[0].changes[0].plan'),plan);
});
check('No-write confirmation commits the selected input without fabricating history',()=>{
 setup();preview();const n=ev('history.length');assert.equal(summary().dataset.reviewState,'unchanged');assert.equal(q('#confirmDialog').disabled,false);q('#confirmDialog').click();assert.equal(ev('scheme(1).vars[1].savedValue'),'A;B;C');assert.equal(ev('history.length'),n);assert.match(q('#toast').textContent,/无需改变/);
});
check('Original duplicate protection does not claim current duplicate copies exist',()=>{
 setup('A;B;B;C');input('A;C');ev('windows["User|PATH"]="A;B;C";targetRevisions["User|PATH"]++;refreshVisibleState()');preview();assert.match(q('.review-issues').textContent,/原匹配或当前匹配曾有重复/);assert.equal(ev('modalAction.rows[0].plan.operations.length'),0);assert.equal(summary().dataset.reviewState,'retained');assert.equal(q('#confirmDialog').disabled,false);
 const n=ev('history.length'),baseline=ev('scheme(1).vars[1].matchBaseline');q('#confirmDialog').click();assert.equal(ev('history.length'),n);assert.strictEqual(ev('scheme(1).vars[1].matchBaseline'),baseline);assert.equal(ev('scheme(1).vars[1].dirty'),true);assert.match(q('#toast').textContent,/当前值未改变/);
});
check('Shared explanation identifies protection without invented application ownership',()=>{
 setup();input('A;C;E');ev('scheme(0).vars[1].defaultValue="B"');preview();assert.match(q('.review-issues').textContent,/其他方案仍需使用/);assert.doesNotMatch(q('.review-issues').textContent,/Java|Python/);assert.match(q('#confirmDialog').textContent,/安全变更/);assert.ok(ev('modalAction.rows[0].plan.issues.some(i=>i.reason==="shared")'));
});
check('Continuity-protected partial apply keeps intent and factual history',()=>{
 setup();input('A;C;E');ev('windows["User|PATH"]+=";EXTERNAL";targetRevisions["User|PATH"]++;refreshVisibleState()');const baseline=ev('scheme(1).vars[1].matchBaseline');preview();assert.match(q('.review-issues').textContent,/相同文本重新出现/);assert.match(q('#previewDecisionHint').textContent,/不会标记为全部完成/);q('#confirmDialog').click();assert.match(ev('windows["User|PATH"]'),/B;C;Y;EXTERNAL;E$/);assert.strictEqual(ev('scheme(1).vars[1].matchBaseline'),baseline);assert.equal(ev('history[0].action'),'模拟应用（部分完成）');assert.equal(ev('value(scheme(1).vars[1])'),'A;C;E');
});
check('Conflict blocks all target writes and exposes an actionable explanation',()=>{
 setup();ev('scheme(0).vars[1].mode="replace";scheme(0).vars[1].defaultValue="OTHER"');preview("[{app:1,profile:'Python313',varIdx:1},{app:0,profile:'JDK8',varIdx:1},{app:1,profile:'Python313',varIdx:0}]");const snapshot=before();assert.equal(summary().dataset.reviewState,'blocked');assert.ok(q('#confirmDialog').disabled);assert.match(q('.review-issues').textContent,/同一目标的请求冲突/);q('#confirmDialog').click();assert.equal(before(),snapshot);
});
check('Unsupported append blocks the batch even when its own value needs no operation',()=>{
 setup();ev('scheme(1).vars[1].mode="append"');preview();assert.equal(ev('modalAction.rows[0].plan.operations.length'),0);assert.equal(summary().dataset.reviewState,'blocked');assert.ok(q('#confirmDialog').disabled);assert.match(q('.review-limitation').textContent,/自定义排序/);
});
check('Revoke remains a scope explanation with no executable action',()=>{
 setup();const snapshot=before();preview(target,'撤销');assert.match(summary().textContent,/撤销范围说明/);assert.ok(q('#confirmDialog').disabled);q('#confirmDialog').click();assert.equal(before(),snapshot);
});
function dualScope(){
 setup();ev('scheme(1).vars.push({...scheme(1).vars[1],scope:"System",defaultValue:"SYSTEM",matchBaseline:null,removalState:null});windows["System|PATH"]="SYSTEM"');
 preview("[{app:1,profile:'Python313',varIdx:1},{app:0,profile:'JDK8',varIdx:1},{app:1,profile:'Python313',varIdx:2}]");
}
check('Target index merges the same canonical target and separates User/System Path',()=>{
 dualScope();assert.equal(all('[data-review-jump]').length,2);assert.equal(ev('modalAction.rows.length'),2);assert.notEqual(all('[data-review-key]')[0].dataset.reviewKey,all('[data-review-key]')[1].dataset.reviewKey);assert.equal(ev('modalAction.rows[0].members.length'),2);
});
check('Target navigation changes only reading position and focus',()=>{
 dualScope();const snapshot=before(),version=ev('modalAction.version');all('[data-review-jump]')[1].click();assert.strictEqual(w.document.activeElement,all('[data-review-control="heading"]')[1]);assert.equal(before(),snapshot);assert.equal(ev('modalAction.version'),version);assert.equal(ev('modalAction.reviewedVersion'),version);
});
check('Returning to workbench preserves exact input, baseline, state and batch selection',()=>{
 setup();input('A;C;E');ev('scheme(0).vars[1].defaultValue="B";checked.add(0);checked.add(1)');preview();const baseline=ev('scheme(1).vars[1].matchBaseline'),state=ev('scheme(1).vars[1].removalState'),snapshot=before();q('.review-return').click();assert.equal(ev('screen'),'workbench');assert.equal(ev('selected'),1);assert.equal(ev('focusedVariable'),1);assert.equal(ev('value(scheme(1).vars[1])'),'A;C;E');assert.strictEqual(ev('scheme(1).vars[1].matchBaseline'),baseline);assert.strictEqual(ev('scheme(1).vars[1].removalState'),state);assert.equal(ev('checked.size'),2);assert.equal(before(),snapshot);assert.equal(w.document.activeElement,q('#varRows [data-row="1"] .vinput'));
});
check('Workbench review action preserves the visible batch confirmation scope',()=>{
 setup();q('[data-check="0"]').click();q('[data-check="1"]').click();assert.match(q('#inspectPreview').textContent,/批量预览/);q('#inspectPreview').click();assert.equal(ev('modalAction.targets.length'),4);assert.equal(ev('checked.size'),2);
});
check('Real selection and clear actions keep review scope label current without replacing the draft',()=>{
 setup();const field=input('A;C;E');field.focus();field.setSelectionRange(2,2);q('[data-check="0"]').click();assert.match(q('#inspectPreview').textContent,/批量预览/);assert.strictEqual(q('#varRows [data-row="1"] .vinput'),field);assert.equal(field.selectionStart,2);assert.equal(field.value,'A;C;E');q('#selectNone').click();assert.match(q('#inspectPreview').textContent,/方案预览/);assert.doesNotMatch(q('#inspectPreview').textContent,/批量/);q('#selectAll').click();assert.match(q('#inspectPreview').textContent,/批量/);q('#selectNone').click();assert.doesNotMatch(q('#inspectPreview').textContent,/批量/);
});
check('View comparison is focusable without editing the selected value',()=>{
 setup();ev('compareShown=false;render()');const snapshot=before(),source=ev('scheme(1).vars[1].source');q('[data-inspect="1"]').click();assert.equal(q('#inspect').hidden,false);assert.equal(ev('focusedVariable'),1);assert.equal(ev('scheme(1).vars[1].source'),source);assert.equal(before(),snapshot);assert.equal(w.document.activeElement.id,'inspectTitle');assert.equal(q('#compareToggle').getAttribute('aria-expanded'),'true');
});
check('Returning to a replace context does not open a forbidden element editor',()=>{
 setup();ev('scheme(1).vars[1].mode="append"');preview();q('.review-return').click();ev('focusedVariable=0;renderInspector()');assert.equal(q('#editWindowsElements').hidden,true);q('#compareActual').dispatchEvent(new w.KeyboardEvent('keydown',{key:'Enter',bubbles:true}));assert.equal(q('#modal').classList.contains('open'),false);
});
check('Refresh preserves raw disclosure and focus by canonical target',()=>{
 dualScope();const rows=all('[data-review-key]'),raw=rows[1].querySelector('.raw-disclosure');raw.open=true;raw.querySelector('summary').focus();ev('windows["System|PATH"]="SYSTEM;EXT";targetRevisions["System|PATH"]++;refreshPreview()');const updated=all('[data-review-key]')[1];assert.equal(updated.querySelector('.raw-disclosure').open,true);assert.strictEqual(w.document.activeElement,updated.querySelector('.raw-disclosure summary'));assert.equal(ev('modalAction.reviewedVersion'),0);
});
check('Cross-version token focus falls back to its target, never a guessed occurrence',()=>{
 setup();input('A;C;E');preview();const token=q('.is-after [data-related-key]');token.tabIndex=0;token.focus();ev('windows["User|PATH"]="NEW;"+windows["User|PATH"];targetRevisions["User|PATH"]++;refreshPreview()');assert.equal(w.document.activeElement.dataset.reviewControl,'heading');assert.equal(w.document.activeElement.closest('[data-review-key]').dataset.reviewKey,q('[data-review-key]').dataset.reviewKey);
});
check('Pure presentation redraw preserves the focused token in the correct region',()=>{
 setup();input('A;C;E');preview();const token=q('.is-after [data-related-key]'),key=token.dataset.relatedKey;token.tabIndex=0;token.focus();ev('renderPreview()');assert.equal(w.document.activeElement.dataset.relatedKey,key);assert.ok(w.document.activeElement.closest('.is-after'));
});
check('Updated preview still requires review and separate final confirmation',()=>{
 setup();input('A;C;E');preview();ev('windows["User|PATH"]+=";EXT";targetRevisions["User|PATH"]++');const snapshot=before();q('#confirmDialog').click();assert.equal(before(),snapshot);assert.equal(ev('modalAction.reviewedVersion'),0);q('#confirmDialog').click();assert.equal(before(),snapshot);assert.equal(ev('modalAction.reviewedVersion'),ev('modalAction.version'));q('#confirmDialog').click();assert.notEqual(before(),snapshot);
});
check('Unrelated snapshot changes do not invalidate review',()=>{
 setup();preview();ev('windows["System|UNRELATED"]="X";targetRevisions["System|UNRELATED"]++');assert.equal(ev('refreshPreview()'),false);assert.equal(ev('modalAction.version'),1);
});
check('Closing after a trigger redraw restores focus to its replacement',()=>{
 setup();q('#previewButton').focus();q('#previewButton').click();const old=ev('lastDialogFocus');ev('renderFooter()');assert.equal(old.isConnected,false);q('#cancelDialog').click();assert.equal(w.document.activeElement.id,'previewButton');
});
check('More issue details and raw values are escaped without changing frozen data',()=>{
 setup();const markup=ev('reviewIssueMarkup({issues:[{reason:"continuity",value:"<script>unsafe</script>",actualIndexes:[0]},{reason:"duplicate",value:"A",actualIndexes:[1]},{reason:"shared",value:"B",actualIndexes:[2]}]})');const node=w.document.createElement('div');node.innerHTML=markup;assert.equal(node.querySelector('script'),null);assert.ok(node.querySelector('details'));assert.match(node.textContent,/<script>unsafe<\/script>/);assert.match(node.textContent,/当前位置：1/);
});
check('No runtime errors in review workflows',()=>assert.deepEqual(errors,[]));
console.log(JSON.stringify({passed:checks.filter(c=>c.passed).length,total:checks.length,checks},null,2));if(checks.some(c=>!c.passed))process.exitCode=1;dom.window.close();

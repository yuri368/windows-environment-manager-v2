'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
const root=path.resolve(__dirname,'..'),errors=[],checks=[];
const vc=new VirtualConsole();vc.on('jsdomError',error=>errors.push(error.message));
const dom=new JSDOM(fs.readFileSync(path.join(root,'temp.html'),'utf8'),{
  runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,beforeParse(w){ for(const configModule of ['config-format.js','config-editor.js'])w.eval(require('node:fs').readFileSync(require('node:path').resolve(__dirname,"..",configModule),'utf8'));
    w.confirm=()=>true;
    for(const file of ['demo-plan.js','demo-configs.js','windows-elements.js'])w.eval(fs.readFileSync(path.join(root,file),'utf8'));
  }
});
const w=dom.window,q=s=>w.document.querySelector(s),all=s=>[...w.document.querySelectorAll(s)],ev=s=>w.eval(s);
const A='%MAVEN_HOME%\\bin',B='%MAVEN_HOME%\\bin1';
const live=()=>all('#elementList > .element-row'),ghost=()=>all('#elementList > .element-ghost-row');
const id=node=>Number(node.dataset.elementId),value=node=>node.querySelector('.element-input').value;
const raw=node=>node.querySelector('[data-raw-value]')?.getAttribute('data-raw-value');
const serialized=()=>q('#elementSerialized').textContent;
const remove=()=>q('[data-remove-pending]'),add=()=>q('[data-add-pending]');
const check=(name,fn)=>{try{fn();checks.push({name,passed:true});}catch(error){checks.push({name,passed:false,error:error.stack});}};
function input(node,text){node.value=text;node.dispatchEvent(new w.Event('input',{bubbles:true}));}
const rowFor=elementId=>q('#elementList > .element-row[data-element-id="'+elementId+'"]');
const ghostFor=elementId=>q('#elementList > .element-ghost-row[data-element-id="'+elementId+'"]');
const state=()=>ev('JSON.stringify({windows,history,revision})');
const audit=()=>ev('history[0].changes[0].plan');
function setup(){
  ev('closeModal();reset(true)');
  ev('selected=2;focusedVariable=1;const v=scheme(2).vars[1];v.defaultValue='+JSON.stringify(A)+';v.baselineValue=v.defaultValue;v.mode="+append";windows["User|PATH"]='+JSON.stringify(A)+';render()');
  input(q('#varRows [data-row="1"] .vinput'),B);
  q('#editWindowsElements').click();
  assert.deepEqual(live().map(value),[A]);
  assert.equal(raw(remove().closest('.editor-suggestion-token')),A);
  assert.equal(raw(add().closest('.editor-suggestion-token')),B);
}
check('Manual A to B first resolves both suggestions and records one replacement',()=>{
  setup();const oldId=id(live()[0]),staleRemove=remove(),staleAdd=add(),before=state();
  input(live()[0].querySelector('.element-input'),B);
  assert.equal(id(live()[0]),oldId);assert.deepEqual(live().map(value),[B]);
  assert.equal(remove(),null);assert.equal(add(),null);assert.equal(ghost().length,0);assert.equal(serialized(),B);
  staleRemove.click();staleAdd.click();assert.deepEqual(live().map(value),[B]);assert.equal(ghost().length,0);
  assert.equal(state(),before);
  assert.match(q('#elementStatus').textContent,/替换 1/);
  q('#applyElements').click();
  assert.equal(ev("windows['User|PATH']"),B);
  assert.deepEqual(Array.from(audit().operations,op=>op.action),['REPLACE']);
  assert.equal(audit().before.raw,A);assert.equal(audit().after.raw,B);
});
check('Adopt B then manually replace original A with B retains separate original and adopted identities',()=>{
  setup();const originalId=id(live()[0]);add().click();const adoptedId=id(live().find(node=>id(node)!==originalId));
  input(live().find(node=>id(node)===originalId).querySelector('.element-input'),B);
  assert.notEqual(originalId,adoptedId);assert.deepEqual(live().map(value),[B,B]);
  assert.equal(remove(),null);assert.equal(add().getAttribute('aria-pressed'),'true');
  assert.equal(ghost().length,0);assert.equal(serialized(),B+';'+B);
  assert.equal(live().find(node=>id(node)===originalId).dataset.duplicateCount,'2');
  add().click();assert.deepEqual(live().map(value),[B]);assert.equal(id(live()[0]),originalId);
  assert.equal(add(),null);assert.equal(ghost().length,0);assert.equal(serialized(),B);
});
check('Manual deletion of edited original preserves deletion-time B ghost while adopted B stays live',()=>{
  setup();const originalId=id(live()[0]);add().click();const adoptedId=id(live().find(node=>id(node)!==originalId));
  input(live().find(node=>id(node)===originalId).querySelector('.element-input'),B);
  live().find(node=>id(node)===originalId).querySelector('[data-delete]').click();
  assert.equal(ghost().length,1);assert.equal(id(ghost()[0]),originalId);assert.equal(raw(ghost()[0]),B);
  assert.deepEqual(live().map(value),[B]);assert.equal(id(live()[0]),adoptedId);
  assert.equal(add().getAttribute('aria-pressed'),'true');assert.equal(remove(),null);
  assert.deepEqual(all('#elementOriginal .diff-value[data-raw-value]').map(node=>node.getAttribute('data-raw-value')),[A]);
  assert.deepEqual(all('#elementPreview .diff-value[data-raw-value]').map(node=>node.getAttribute('data-raw-value')),[B,B]);
  assert.equal(serialized(),B);assert.match(q('#elementStatus').textContent,/删除 1/);
});
check('Manual A to B, delete original, then adopt B yields the same distinct ghost and new live B',()=>{
  setup();const originalId=id(live()[0]);input(live()[0].querySelector('.element-input'),B);
  live()[0].querySelector('[data-delete]').click();
  assert.equal(ghost().length,1);assert.equal(raw(ghost()[0]),B);assert.equal(serialized(),'（不存在）');
  assert.equal(remove(),null);assert.ok(add());assert.equal(add().getAttribute('aria-pressed'),'false');
  add().click();assert.deepEqual(live().map(value),[B]);assert.notEqual(id(live()[0]),originalId);
  assert.equal(add().getAttribute('aria-pressed'),'true');assert.equal(raw(ghost()[0]),B);
  assert.equal(serialized(),B);
});
check('Adopt removal then ordinary ghost restore keeps the original ID and clears green provenance',()=>{
  setup();const originalId=id(live()[0]),before=state(),staleRemove=remove();
  staleRemove.click();
  assert.equal(live().length,0);assert.equal(id(ghostFor(originalId)),originalId);
  assert.equal(raw(ghostFor(originalId)),A);assert.equal(remove().getAttribute('aria-pressed'),'true');
  ghostFor(originalId).querySelector('[data-restore-ghost]').click();
  assert.equal(id(rowFor(originalId)),originalId);assert.equal(value(rowFor(originalId)),A);
  assert.equal(ghostFor(originalId),null);assert.equal(remove().getAttribute('aria-pressed'),'false');
  assert.equal(add().getAttribute('aria-pressed'),'false');
  assert.equal(serialized(),A);assert.equal(state(),before);
  staleRemove.click();assert.equal(value(rowFor(originalId)),A);
  assert.equal(q('#applyElements').disabled,true);
});
check('Green suggestion undo restores exact original raw and ID without touching another live row',()=>{
  setup();const originalId=id(live()[0]),before=state();
  add().click();const addedId=id(live().find(node=>id(node)!==originalId));
  remove().click();
  assert.equal(id(ghostFor(originalId)),originalId);
  assert.equal(remove().getAttribute('aria-pressed'),'true');
  remove().click();
  assert.equal(id(rowFor(originalId)),originalId);assert.equal(value(rowFor(originalId)),A);
  assert.equal(id(rowFor(addedId)),addedId);assert.equal(value(rowFor(addedId)),B);
  assert.equal(remove().getAttribute('aria-pressed'),'false');
  assert.equal(add().getAttribute('aria-pressed'),'true');
  assert.equal(ghostFor(originalId),null);assert.equal(state(),before);
});
check('Manual deletion of edited B restores its latest draft with the same original ID',()=>{
  setup();const originalId=id(live()[0]),before=state();
  input(rowFor(originalId).querySelector('.element-input'),B);
  rowFor(originalId).querySelector('[data-delete]').click();
  assert.equal(raw(ghostFor(originalId)),B);
  assert.equal(raw(q('#elementPreview [data-element-id="'+originalId+'"]')),B);
  assert.equal(raw(q('#elementOriginal [data-element-id="'+originalId+'"]')),A);
  assert.equal(remove(),null);assert.ok(add());
  ghostFor(originalId).querySelector('[data-restore-ghost]').click();
  assert.equal(id(rowFor(originalId)),originalId);assert.equal(value(rowFor(originalId)),B);
  assert.equal(ghostFor(originalId),null);assert.equal(remove(),null);assert.equal(add(),null);
  assert.equal(serialized(),B);assert.equal(state(),before);
});
check('Editing adopted B to C breaks its undo provenance and stale button cannot delete C',()=>{
  setup();const originalId=id(live()[0]),before=state(),staleAdd=add();
  staleAdd.click();const adoptedId=id(live().find(node=>id(node)!==originalId));
  input(rowFor(adoptedId).querySelector('.element-input'),'C');
  assert.equal(id(rowFor(adoptedId)),adoptedId);assert.equal(value(rowFor(adoptedId)),'C');
  assert.equal(add().getAttribute('aria-pressed'),'false');
  staleAdd.click();assert.equal(value(rowFor(adoptedId)),'C');
  assert.equal(live().length,2);assert.equal(state(),before);
  add().click();const secondB=live().find(node=>value(node)===B);
  assert.ok(secondB);assert.notEqual(id(secondB),adoptedId);
  add().click();assert.equal(rowFor(id(secondB)),null);
  assert.equal(value(rowFor(adoptedId)),'C');assert.deepEqual(live().map(value),['C',A]);
  assert.equal(state(),before);
});
check('Applying adopted removal records only the factual removal before original, never the pending B addition',()=>{
  setup();const originalId=id(live()[0]),before=state();
  remove().click();
  assert.equal(id(ghostFor(originalId)),originalId);assert.equal(add().getAttribute('aria-pressed'),'false');
  assert.equal(state(),before);assert.equal(q('#applyElements').disabled,false);
  q('#applyElements').click();
  const plan=audit();
  assert.deepEqual(Array.from(plan.operations,op=>op.action),['REMOVE']);
  assert.equal(plan.before.raw,A);assert.equal(plan.after.exists,false);
  assert.equal(ev("windows['User|PATH']"),undefined);
  assert.doesNotMatch(JSON.stringify(ev('history[0]')), /adoptionMap|removalAdoptions|deletedDrafts|displayOrder/);
});
check('No JSDOM runtime errors',()=>assert.deepEqual(errors,[]));
w.close();
const report={verification:'JSDOM-only suggestion and editor routes; native browser visuals and Windows integration not covered',total:checks.length,passed:checks.filter(item=>item.passed).length,failed:checks.filter(item=>!item.passed).length,checks};
process.stdout.write(JSON.stringify(report,null,2)+'\n');
if(report.failed)process.exitCode=1;

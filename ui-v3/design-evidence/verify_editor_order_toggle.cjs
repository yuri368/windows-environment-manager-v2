'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
const root=path.resolve(__dirname,'..'),checks=[],errors=[];
const vc=new VirtualConsole();vc.on('jsdomError',error=>errors.push(error.message));
const dom=new JSDOM(fs.readFileSync(path.join(root,'temp.html'),'utf8'),{runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,beforeParse(w){
  w.confirm=()=>true;
  for(const file of ['demo-plan.js','demo-configs.js','windows-elements.js'])w.eval(fs.readFileSync(path.join(root,file),'utf8'));
}});
const w=dom.window,q=selector=>w.document.querySelector(selector),all=selector=>[...w.document.querySelectorAll(selector)],ev=source=>w.eval(source);
const check=(name,run)=>{try{run();checks.push({name,passed:true});}catch(error){checks.push({name,passed:false,error:error.stack});}};
const visible=()=>all('#elementList > .element-row, #elementList > .element-ghost-row');
const live=()=>all('#elementList > .element-row');
const ghosts=()=>all('#elementList > .element-ghost-row');
const ids=nodes=>nodes.map(node=>Number(node.dataset.elementId));
const values=()=>live().map(node=>node.querySelector('.element-input').value);
const preview=()=>all('#elementPreview [data-element-id]');
const original=()=>all('#elementOriginal [data-element-id]');
const raw=node=>node.querySelector('[data-raw-value]')?.getAttribute('data-raw-value');
const rowValue=node=>node.querySelector('.element-input').value;
const visibleValue=node=>node.classList.contains('element-row')?rowValue(node):raw(node);
const serialized=()=>q('#elementSerialized').textContent;
const historyLength=()=>ev('history.length');
const windowsSnapshot=()=>ev('JSON.stringify(windows)');
function setInput(node,value){node.value=value;node.dispatchEvent(new w.Event('input',{bubbles:true}));}
function setup(raw='A;B;C;D',old=raw,next=old,mode='-append'){
  ev('closeModal();reset(true)');
  ev('selected=1;focusedVariable=1;const v=scheme(1).vars[1];v.defaultValue='+JSON.stringify(old)+';v.baselineValue=v.defaultValue;v.mode='+JSON.stringify(mode)+";windows['User|PATH']="+JSON.stringify(raw)+';render()');
  setInput(q('#varRows [data-row="1"] .vinput'),next);
  q('#editWindowsElements').click();
}
const rowFor=id=>q('#elementList .element-row[data-element-id="'+id+'"]');
const ghostFor=id=>q('#elementList .element-ghost-row[data-element-id="'+id+'"]');
const move=(id,direction)=>{const node=rowFor(id)||ghostFor(id);const button=node.querySelector(direction==='up'?'[data-move-up]':'[data-move-down]');assert.ok(button);assert.equal(button.disabled,false);button.click();};
function dragBefore(fromId,targetId){
  const source=rowFor(fromId),target=q('#elementList [data-element-id="'+targetId+'"]');
  assert.ok(source&&target);
  const transfer={setData(){},effectAllowed:'move'};
  const start=new w.Event('dragstart',{bubbles:true,cancelable:true});
  Object.defineProperty(start,'dataTransfer',{value:transfer});
  source.dispatchEvent(start);
  const drop=new w.Event('drop',{bubbles:true,cancelable:true});
  Object.defineProperty(drop,'dataTransfer',{value:transfer});
  target.dispatchEvent(drop);
}
const addButton=()=>q('[data-add-pending]');
const card=()=>addButton()?.closest('.editor-suggestion-token');
const assertVisibleOrder=expected=>{
  assert.deepEqual(visible().map(visibleValue),expected);
  assert.deepEqual(visible().map(node=>node.querySelector('.element-order').textContent.trim()),expected.map((_,index)=>String(index+1)));
  assert.equal(new Set(ids(visible())).size,visible().length);
  assert.deepEqual(preview().map(raw),expected);
};
check('Ghost buttons move adjacent visible positions without changing plan, raw result, history, or original pane',()=>{
  setup();const originalIds=ids(live()),before=windowsSnapshot(),count=historyLength();
  rowFor(originalIds[1]).querySelector('[data-delete]').click();
  assertVisibleOrder(['A','B','C','D']);
  const ghost=ghostFor(originalIds[1]);assert.ok(ghost);
  assert.match(ghost.querySelector('[data-move-down]').getAttribute('aria-label')+' '+ghost.querySelector('[data-move-down]').getAttribute('title'),/仅调整已移除元素的展示位置/);
  const result=serialized(),status=q('#elementStatus').textContent;
  move(originalIds[1],'down');assertVisibleOrder(['A','C','B','D']);
  move(originalIds[1],'down');assertVisibleOrder(['A','C','D','B']);
  move(originalIds[1],'up');assertVisibleOrder(['A','C','B','D']);
  assert.deepEqual(original().map(raw),['A','B','C','D']);
  assert.equal(serialized(),result);
  assert.equal(q('#elementStatus').textContent.split(' · 展示移动：')[0],status.split(' · 展示移动：')[0]);
  assert.match(q('#elementStatus').textContent,/展示移动：移动 1 · 幽灵移动 1/);
  assert.deepEqual(values(),['A','C','D']);assert.equal(historyLength(),count);assert.equal(windowsSnapshot(),before);
});
check('Two ghosts can pass each other without changing live order or the serialized result',()=>{
  setup('A;B;C;D');const [a,b,c,d]=ids(live());
  rowFor(b).querySelector('[data-delete]').click();rowFor(c).querySelector('[data-delete]').click();
  const result=serialized();assertVisibleOrder(['A','B','C','D']);
  move(b,'down');assertVisibleOrder(['A','C','B','D']);
  assert.deepEqual(ids(visible()),[a,c,b,d]);assert.deepEqual(ids(live()),[a,d]);
  assert.equal(serialized(),result);assert.match(q('#elementStatus').textContent,/移动 0/);
});
check('Live movement crosses a ghost visually; MOVE appears only after live relative order changes',()=>{
  setup('A;B;C');const [a,b,c]=ids(live());
  rowFor(b).querySelector('[data-delete]').click();
  move(a,'down');assertVisibleOrder(['B','A','C']);
  assert.deepEqual(values(),['A','C']);assert.equal(serialized(),'A;C');assert.match(q('#elementStatus').textContent,/展示移动：移动 1 · 幽灵移动 1/);
  move(a,'down');assertVisibleOrder(['B','C','A']);
  assert.deepEqual(values(),['C','A']);assert.equal(serialized(),'C;A');assert.match(q('#elementStatus').textContent,/移动 [1-9]/);
  assert.equal(Number(ghostFor(b).dataset.elementId),b);assert.equal(Number(rowFor(c).dataset.elementId),c);
});
check('Drag and drop inserts a live row before a ghost and matches button-order semantics',()=>{
  setup('A;B;C;D');const [a,b,c,d]=ids(live());
  rowFor(b).querySelector('[data-delete]').click();
  dragBefore(d,b);
  assertVisibleOrder(['A','D','B','C']);assert.deepEqual(values(),['A','D','C']);assert.equal(serialized(),'A;D;C');
  assert.equal(ghostFor(b).getAttribute('draggable'),null);
  assert.deepEqual(ids(live()),[a,d,c]);
});
check('Duplicate originals remain separate stable IDs and inline preview follows ghost movement',()=>{
  setup('A;A;B');const [first,second,third]=ids(live());
  rowFor(second).querySelector('[data-delete]').click();
  move(second,'down');
  assert.deepEqual(ids(visible()),[first,third,second]);
  assert.deepEqual(ids(preview()),[first,third,second]);
  assert.deepEqual(ids(original()),[first,second,third]);
  assert.ok(ghostFor(second));assert.equal(rowFor(first).querySelector('.element-input').value,'A');
  assert.equal(serialized(),'A;B');
});
for(const mode of ['+append','-append'])check(mode+' adoption persists as a green pressed card and toggles its exact new ID',()=>{
  setup('A','A','A;E',mode);
  const prior=ids(live()),before=windowsSnapshot(),count=historyLength();
  assert.equal(addButton().getAttribute('aria-pressed'),'false');
  addButton().click();
  const adopted=live().find(node=>rowValue(node)==='E'),adoptedId=Number(adopted.dataset.elementId);
  assert.ok(!prior.includes(adoptedId));assert.equal(addButton().getAttribute('aria-pressed'),'true');
  assert.equal(addButton().textContent.trim(),'✓');assert.ok(card().classList.contains('is-adopted'));
  assert.match(card().textContent,/已加入本次编辑|已采纳/);assert.doesNotMatch(card().textContent,/建议追加/);
  assert.deepEqual(values(),mode==='+append'?['E','A']:['A','E']);
  q('#addElement').click();setInput(live().at(-1).querySelector('.element-input'),'E');
  const manualId=Number(live().at(-1).dataset.elementId);
  addButton().click();
  assert.equal(rowFor(adoptedId),null);assert.ok(rowFor(manualId));
  assert.equal(values().filter(value=>value==='E').length,1);
  assert.equal(addButton(),null);
  assert.equal(windowsSnapshot(),before);assert.equal(historyLength(),count);
});
check('Moving an adopted row preserves provenance and ✓ still removes that ID',()=>{
  setup('A;B','A','A;E');addButton().click();
  const id=Number(live().find(node=>rowValue(node)==='E').dataset.elementId);
  move(id,'up');assert.equal(addButton().getAttribute('aria-pressed'),'true');
  addButton().click();assert.equal(rowFor(id),null);assert.deepEqual(values(),['A','B']);
  assert.equal(addButton().getAttribute('aria-pressed'),'false');
});
check('Editing an adopted row clears provenance permanently while retaining the edited stable row',()=>{
  setup('A','A','A;E');addButton().click();
  const id=Number(live().find(node=>rowValue(node)==='E').dataset.elementId);
  setInput(rowFor(id).querySelector('.element-input'),'F');
  assert.equal(Number(rowFor(id).dataset.elementId),id);assert.equal(addButton().getAttribute('aria-pressed'),'false');
  setInput(rowFor(id).querySelector('.element-input'),'E');
  assert.equal(addButton(),null);assert.ok(rowFor(id));assert.deepEqual(values(),['A','E']);
  setInput(rowFor(id).querySelector('.element-input'),'F');
  assert.equal(addButton().getAttribute('aria-pressed'),'false');
  addButton().click();
  const second=live().find(node=>rowValue(node)==='E');assert.notEqual(Number(second.dataset.elementId),id);
  addButton().click();assert.ok(rowFor(id));assert.equal(rowFor(Number(second.dataset.elementId)),null);
});
check('Deleting an adopted row clears its card state and leaves no original ghost',()=>{
  setup('A','A','A;E');addButton().click();const id=Number(live().find(node=>rowValue(node)==='E').dataset.elementId);
  rowFor(id).querySelector('[data-delete]').click();
  assert.equal(rowFor(id),null);assert.equal(ghostFor(id),null);assert.equal(addButton().getAttribute('aria-pressed'),'false');
  assert.equal(serialized(),'A');
});
check('A manual row satisfying a suggestion never becomes adopted',()=>{
  setup('A','A','A;E');q('#addElement').click();setInput(live().at(-1).querySelector('.element-input'),'E');
  assert.equal(addButton(),null);assert.equal(all('#pendingElements .is-adopted').length,0);
  assert.deepEqual(values(),['A','E']);
});
check('Restore resets UI order and adoption provenance, and Cancel discards both',()=>{
  setup('A;B;C','A','A;E');const originalIds=ids(live()),before=windowsSnapshot(),count=historyLength();
  rowFor(originalIds[1]).querySelector('[data-delete]').click();move(originalIds[1],'down');addButton().click();
  q('#restoreElements').click();
  assert.deepEqual(ids(visible()),originalIds);assert.deepEqual(values(),['A','B','C']);assert.equal(ghosts().length,0);
  assert.equal(addButton().getAttribute('aria-pressed'),'false');assert.ok(q('#applyElements').disabled);
  rowFor(originalIds[1]).querySelector('[data-delete]').click();move(originalIds[1],'down');addButton().click();
  q('#cancelDialog').click();assert.equal(windowsSnapshot(),before);assert.equal(historyLength(),count);
  q('#editWindowsElements').click();assert.deepEqual(values(),['A','B','C']);assert.equal(ghosts().length,0);
  assert.equal(addButton().getAttribute('aria-pressed'),'false');
});
check('Invalid suggestion and whole or special editors expose no actionable adoption toggle',()=>{
  setup('A','A','A;E');q('#cancelDialog').click();
  ev("windows['User|PATH']='A;EXTERNAL';targetRevisions['User|PATH']=1;render()");
  q('#editWindowsElements').click();assert.equal(addButton(),null);assert.match(q('#pendingElements').textContent,/方案建议已失效/);
  q('#cancelDialog').click();setup('A;B','A;B','C;D','replace');
  assert.equal(addButton(),null);assert.equal(ghosts().length,0);
  q('#cancelDialog').click();ev('scheme(1).vars[1].special=true;render()');q('#editWindowsElements').click();
  assert.equal(addButton(),null);assert.equal(ghosts().length,0);
});
check('Apply and History serialize only live order; UI order and adoption metadata stay out of plan',()=>{
  setup('A;B;C','A','A;E');const [a,b,c]=ids(live());
  const count=historyLength();
  rowFor(b).querySelector('[data-delete]').click();move(b,'down');addButton().click();
  const expected=serialized();assert.equal(expected,'A;C;E');
  q('#applyElements').click();
  assert.equal(ev("windows['User|PATH']"),expected);
  const plan=ev('history[0].changes[0].plan');
  assert.equal(plan.after.raw,expected);
  assert.doesNotMatch(JSON.stringify(ev('history[0]')),/displayOrder|adoptionMap|element-ghost-row|已采纳/);
  assert.equal(historyLength(),count+1);
});
check('Detached adopted control cannot alter a new editor session',()=>{
  setup('A','A','A;E');addButton().click();const detached=addButton();
  q('#cancelDialog').click();q('#editWindowsElements').click();
  const before=serialized();detached.click();assert.equal(serialized(),before);
  assert.equal(addButton().getAttribute('aria-pressed'),'false');
});
check('No JSDOM runtime errors in order and adoption workflows',()=>assert.deepEqual(errors,[]));
const report={passed:checks.filter(result=>result.passed).length,total:checks.length,checks};
console.log(JSON.stringify(report,null,2));if(checks.some(result=>!result.passed))process.exitCode=1;dom.window.close();

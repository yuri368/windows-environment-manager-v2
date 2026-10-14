'use strict';
/* UI-only regression coverage for deletion drafts and raw newline preservation. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
const root=path.resolve(__dirname,'..'),checks=[],errors=[];
const vc=new VirtualConsole();vc.on('jsdomError',error=>errors.push(error.message));
const dom=new JSDOM(fs.readFileSync(path.join(root,'temp.html'),'utf8'),{
  runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,
  beforeParse(w){
    w.confirm=()=>true;
    for(const file of ['demo-plan.js','demo-configs.js','windows-elements.js'])
      w.eval(fs.readFileSync(path.join(root,file),'utf8'));
  }
});
const w=dom.window,doc=w.document,q=s=>doc.querySelector(s),all=s=>[...doc.querySelectorAll(s)];
const check=(name,run)=>{try{run();checks.push({name,passed:true});}catch(error){checks.push({name,passed:false,error:error.stack});}};
const live=()=>all('#elementList > .element-row');
const ghosts=()=>all('#elementList > .element-ghost-row');
const ids=nodes=>nodes.map(node=>Number(node.dataset.elementId));
const values=()=>live().map(node=>node.querySelector('.element-input').value);
const row=id=>q('#elementList > .element-row[data-element-id="'+id+'"]');
const ghost=id=>q('#elementList > .element-ghost-row[data-element-id="'+id+'"]');
const serialized=()=>q('#elementSerialized').textContent;
const edit=(id,value)=>{const field=row(id).querySelector('.element-input');field.value=value;field.dispatchEvent(new w.Event('input',{bubbles:true}));};
const remove=id=>row(id).querySelector('[data-delete]').click();
const undo=id=>ghost(id).querySelector('[data-restore-ghost]').click();
let applied=[];
function direct(raw,whole=false){
  w.WindowsElements.dismiss();q('#modal').classList.remove('open');applied=[];
  w.WindowsElements.open({container:q('#modal'),name:'RAW_REGRESSION',scope:'User',registryType:'REG_SZ',raw,whole,selectedValues:[],
    close:()=>{w.WindowsElements.dismiss();q('#modal').classList.remove('open');},
    onApply:(value,audit,plan)=>applied.push({value,audit,plan})});
  return ids(live());
}
check('Manual delete and undo retain edited value and stable ID, while Apply carries only the result',()=>{
  const [a,b,c]=direct('A;B;C');edit(b,'B1');remove(b);
  assert.ok(ghost(b));assert.equal(row(b),null);assert.deepEqual(ids(live()),[a,c]);
  assert.equal(ghost(b).querySelector('[data-raw-value]').getAttribute('data-raw-value'),'B1');
  undo(b);assert.deepEqual(ids(live()),[a,b,c]);assert.deepEqual(values(),['A','B1','C']);
  assert.equal(serialized(),'A;B1;C');q('#applyElements').click();
  assert.equal(applied.length,1);assert.equal(applied[0].value,'A;B1;C');
  assert.doesNotMatch(JSON.stringify(applied[0]),/deletedDrafts|removalAdoptions|displayOrder/);
});
check('Moved ghost restores latest draft at its current location without reverting other edits',()=>{
  const [a,b,c,d]=direct('A;B;C;D');edit(b,'B1');remove(b);
  ghost(b).querySelector('[data-move-down]').click();edit(a,'A2');
  assert.deepEqual(ids(all('#elementList > [data-element-id]')),[a,c,b,d]);
  undo(b);assert.deepEqual(ids(live()),[a,c,b,d]);
  assert.deepEqual(values(),['A2','C','B1','D']);assert.equal(serialized(),'A2;C;B1;D');
  edit(b,'B2');remove(b);undo(b);assert.deepEqual(values(),['A2','C','B2','D']);
});
check('Duplicate occurrence and original empty element restore only their own draft',()=>{
  const [a,b,empty]=direct('A;A;');edit(b,'A2');remove(b);undo(b);
  assert.deepEqual(ids(live()),[a,b,empty]);assert.deepEqual(values(),['A','A2','']);
  edit(empty,'E');remove(empty);undo(empty);
  assert.deepEqual(values(),['A','A2','E']);assert.equal(serialized(),'A;A2;E');
  const [only]=direct('');assert.deepEqual(values(),['']);remove(only);
  assert.ok(ghost(only));undo(only);assert.deepEqual(values(),['']);
  assert.equal(serialized(),'');assert.equal(q('#applyElements').disabled,true);
  assert.deepEqual(direct(undefined),[]);q('#addElement').click();
  const added=ids(live())[0];edit(added,'new');remove(added);
  assert.deepEqual(live(),[]);assert.deepEqual(ghosts(),[]);assert.equal(serialized(),'（不存在）');
});
check('Restore clears deletion drafts and Cancel discards the entire editing session',()=>{
  const [a,b]=direct('A;B');edit(b,'B1');remove(b);q('#restoreElements').click();
  assert.deepEqual(ids(live()),[a,b]);assert.deepEqual(values(),['A','B']);assert.deepEqual(ghosts(),[]);
  remove(b);undo(b);assert.deepEqual(values(),['A','B']);
  edit(a,'A1');remove(a);q('#cancelDialog').click();
  const reopened=direct('A;B');assert.deepEqual(values(),['A','B']);
  assert.deepEqual(ids(live()),reopened);assert.deepEqual(ghosts(),[]);
});
check('Suggested delete undo still restores original value after manual edit and re-delete',()=>{
  w.eval('closeModal();reset(true)');
  w.eval("selected=1;focusedVariable=1;const v=scheme(1).vars[1];v.defaultValue='A;B';v.baselineValue=v.defaultValue;v.mode='-append';windows['User|PATH']='A;B;C';render()");
  const field=q('#varRows [data-row="1"] .vinput');field.value='A';field.dispatchEvent(new w.Event('input',{bubbles:true}));
  q('#editWindowsElements').click();const originalIds=ids(live()),b=originalIds[1];
  const button=q('[data-remove-pending="1"]');assert.ok(button);button.click();
  const adopted=q('[data-remove-pending="1"]');assert.equal(adopted.getAttribute('aria-pressed'),'true');
  adopted.click();assert.equal(row(b).querySelector('.element-input').value,'B');
  edit(b,'B1');remove(b);undo(b);assert.equal(row(b).querySelector('.element-input').value,'B1');
  q('#cancelDialog').click();
});
function newlineCase(label,raw,newValue,expected,whole=false){
  check(label,()=>{
    const [id]=direct(raw,whole),field=row(id).querySelector('.element-input');
    assert.equal(field.value,(whole?raw:raw.split(';')[0]).replace(/\r\n?|\n/g,'\n'));
    edit(id,newValue);assert.equal(serialized(),expected);
    assert.equal(q('#applyElements').disabled,expected===raw);
    if(expected!==raw){q('#applyElements').click();assert.equal(applied[0].value,expected);}
  });
}
newlineCase('LF stays LF after editing adjacent text','A\nB;C','A\nB!','A\nB!;C');
newlineCase('CRLF survives insertion after newline','A\r\nB;C','A\nBX','A\r\nBX;C');
newlineCase('CR survives insertion before newline','A\rB;C','AX\nB','AX\rB;C');
newlineCase('Mixed line endings survive replacement between boundaries','A\r\nB\rC\nD','A\nB\nCX\nD','A\r\nB\rCX\nD');
newlineCase('Deleting character before CRLF preserves untouched CRLF','AB\r\nCD','A\nCD','A\r\nCD');
newlineCase('Deleting a newline preserves the other original newline','A\r\nB\rC\nD','AB\nC\nD','AB\rC\nD');
newlineCase('Special whole string keeps CRLF CR LF and permits semicolon','A\r\nB;C\rD\nE','A\nBX;C\nD\nE','A\r\nBX;C\rD\nE',true);
check('No-op input, soft wrap and Enter preserve raw until explicit newline is input',()=>{
  const raw='This is a long visual soft-wrapped line\r\nnext';const [id]=direct(raw);
  const field=row(id).querySelector('.element-input');assert.equal(field.getAttribute('wrap'),'soft');
  field.dispatchEvent(new w.Event('input',{bubbles:true}));
  assert.equal(serialized(),raw);assert.equal(q('#applyElements').disabled,true);
  const enter=new w.KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true});
  field.dispatchEvent(enter);assert.equal(enter.defaultPrevented,false);
  edit(id,field.value+'\nadded');assert.equal(serialized(),raw+'\nadded');
});
check('Validation keeps plain semicolon and NUL invalid, while Special accepts semicolon but rejects NUL',()=>{
  let [id]=direct('A');edit(id,'A;B');assert.equal(q('#applyElements').disabled,true);
  assert.match(q('#elementValidation').textContent,/分号/);
  edit(id,'A\0B');assert.equal(q('#applyElements').disabled,true);
  assert.match(q('#elementValidation').textContent,/NUL/);
  [id]=direct('A;B',true);edit(id,'A;B\nC');assert.equal(q('#applyElements').disabled,false);
  edit(id,'A;B\0C');assert.equal(q('#applyElements').disabled,true);
  assert.match(q('#elementValidation').textContent,/NUL/);
});

function nativeEdit(field,start,end,inputType,next){
  field.setSelectionRange(start,end);
  field.dispatchEvent(new w.InputEvent('beforeinput',{bubbles:true,inputType,cancelable:true}));
  field.value=next;
  field.dispatchEvent(new w.InputEvent('input',{bubbles:true,inputType}));
}
for(const whole of [false,true]){
  const mode=whole?'Special':'Ordinary';
  for(const [label,raw,start,end,type,next,expected] of [
    ['Enter after standalone CR preserves two visible newlines','A\rB',2,2,'insertLineBreak','A\n\nB','A\n\nB'],
    ['Removing text between CR and LF preserves two visible newlines','A\rX\nB',2,2,'deleteContentForward','A\n\nB','A\n\nB'],
    ['Delete first adjacent newline','A\r\n\nB',1,1,'deleteContentForward','A\nB','A\nB'],
    ['Backspace second adjacent newline','A\r\n\nB',3,3,'deleteContentBackward','A\nB','A\r\nB'],
    ['Insert LF before CRLF','A\r\nB',1,1,'insertLineBreak','A\n\nB','A\n\r\nB'],
    ['Insert LF after CRLF','A\r\nB',2,2,'insertLineBreak','A\n\nB','A\r\n\nB'],
    ['Replace selected CRLF with LF despite unchanged visible value','A\r\nB',1,2,'insertLineBreak','A\nB','A\nB'],
    ['Backspace supplementary character preserves adjacent CRLF','A😀\r\nB',3,3,'deleteContentBackward','A\nB','A\r\nB']
  ])check(mode+': '+label+' persists exact Apply raw',()=>{
    const [id]=direct(raw,whole),field=row(id).querySelector('.element-input');
    nativeEdit(field,start,end,type,next);assert.equal(serialized(),expected);
    q('#applyElements').click();assert.equal(applied.length,1);
    assert.equal(applied[0].value,expected);assert.equal(applied[0].plan.afterRaw,expected);
  });
}
check('Seam correction is local and saved raw reopens to the exact edited visible text',()=>{
  const raw='Z\r\nA\rX\nB\rC',expected='Z\r\nA\n\nB\rC';
  let [id]=direct(raw),field=row(id).querySelector('.element-input');
  nativeEdit(field,4,4,'deleteContentForward','Z\nA\n\nB\nC');
  assert.equal(serialized(),expected);q('#applyElements').click();
  assert.equal(applied[0].value,expected);
  [id]=direct(expected);assert.equal(row(id).querySelector('.element-input').value,'Z\nA\n\nB\nC');
});
check('Synthetic input fallback also prevents CR and LF merging at a new seam',()=>{
  const [id]=direct('A\rX\nB');edit(id,'A\n\nB');assert.equal(serialized(),'A\n\nB');
});
check('Input range is consumed once and unsupported beforeinput replaces stale metadata',()=>{
  const [id]=direct('A\r\nB'),field=row(id).querySelector('.element-input');
  nativeEdit(field,1,1,'insertLineBreak','A\n\nB');assert.equal(serialized(),'A\n\r\nB');
  edit(id,'A\n\nBX');assert.equal(serialized(),'A\n\r\nBX');
  field.setSelectionRange(1,1);
  field.dispatchEvent(new w.InputEvent('beforeinput',{bubbles:true,inputType:'insertLineBreak'}));
  field.dispatchEvent(new w.InputEvent('beforeinput',{bubbles:true,inputType:'historyUndo'}));
  edit(id,'A\n\nBXY');assert.equal(serialized(),'A\n\r\nBXY');
});
for(const [label,raw,type,next,expected,position=1] of [
  ['Enter at standalone CR seam','A\rB','insertLineBreak','A\n\nB','A\n\nB',2],
  ['Delete text at CR/LF seam','A\rX\nB','deleteContentForward','A\n\nB','A\n\nB',2],
  ['Delete first','A\r\n\nB','deleteContentForward','A\nB','A\nB'],
  ['Insert before','A\r\nB','insertLineBreak','A\n\nB','A\n\r\nB']
])check(label+' adjacent newline persists in workbench Apply and History',()=>{
  w.eval('closeModal();reset(true);selected=1;focusedVariable=1');
  w.eval("windows['User|PATH']="+JSON.stringify(raw)+";render()");
  q('#editWindowsElements').click();
  nativeEdit(live()[0].querySelector('.element-input'),position,position,type,next);
  q('#applyElements').click();
  assert.equal(w.eval("windows['User|PATH']"),expected);
  assert.equal(w.eval('history[0].changes[0].plan.afterRaw'),expected);
});

check('No JSDOM runtime errors',()=>assert.deepEqual(errors,[]));
const failed=checks.filter(item=>!item.passed);
for(const item of checks)console.log((item.passed?'PASS ':'FAIL ')+item.name+(item.passed?'':'\n'+item.error));
if(failed.length)process.exitCode=1;else console.log(checks.length+' Claude repair checks passed.');
w.close();

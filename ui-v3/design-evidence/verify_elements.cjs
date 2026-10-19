/* UI events in JSDOM only. No browser rendering or Windows integration. */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { JSDOM, VirtualConsole } = require('jsdom');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'temp.html'), 'utf8');
const script = fs.readFileSync(path.join(root, 'windows-elements.js'), 'utf8');
const errors = [], checks = [];
const vc = new VirtualConsole(); vc.on('jsdomError', e => errors.push(e.message));
const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true,
  virtualConsole: vc, beforeParse(w){ for(const configModule of ['config-format.js','config-editor.js'])w.eval(require('node:fs').readFileSync(require('node:path').resolve(__dirname,"..",configModule),'utf8'));
    w.eval(fs.readFileSync(path.join(__dirname,'../demo-plan.js'),'utf8')); w.confirm = () => true; w.eval(fs.readFileSync(path.join(root,'demo-configs.js'),'utf8')); w.eval(script); } });
const w = dom.window, doc = w.document;
const q = s => doc.querySelector(s), all = s => [...doc.querySelectorAll(s)];
const check = (name, fn) => { fn(); checks.push({ name, passed: true }); };
const edit = (el, value) => { el.value = value; el.dispatchEvent(new w.Event('input', { bubbles: true })); };
const key = (el, k, opts = {}) => el.dispatchEvent(new w.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, ...opts }));
const dbl = el => { el.focus(); el.dispatchEvent(new w.MouseEvent('dblclick', { bubbles: true })); };
const values = () => all('.element-input').map(el => el.value);
const raw = () => w.eval("windows['User|PATH']");
const cancel = () => q('#cancelDialog').click();
check('Highlight preserves exact raw text, empty and duplicate elements, and escapes markup', () => {
  const sample = ' A ;;<img src=x>;A;B;';
  const el = doc.createElement('div'); el.innerHTML = w.WindowsElements.highlight(sample, ['A']);
  assert.equal(el.textContent, sample); assert.equal(el.querySelectorAll('mark').length, 2);
  assert.equal(el.querySelectorAll('img').length, 0);
});
check('Whole-string highlights only exact matches; blanks are not matched as list elements', () => {
  const el = doc.createElement('div'); el.innerHTML = w.WindowsElements.highlight('A;B', ['A'], true);
  assert.equal(el.querySelectorAll('mark').length, 0);
  el.innerHTML = w.WindowsElements.highlight('A;B', ['A;B'], true);
  assert.equal(el.querySelectorAll('mark').length, 1);
  el.innerHTML = w.WindowsElements.highlight('A;;', ['']);
  assert.equal(el.querySelectorAll('mark').length, 0);
});
check('Workbench highlights just the selected Path element, leaving other elements unmarked', () => {
  q('#varRows [data-row="1"]').click();
  assert.deepEqual(all('#compareActual .diff-token.is-matched .diff-value[data-raw-value]').map(el => el.getAttribute('data-raw-value')), ['%MAVEN_HOME%\\bin']);
  assert.equal(all('#compareActual .diff-value[data-raw-value]').map(el => el.getAttribute('data-raw-value')).join(';'), raw());
});
check('Workbench double click opens shared editor with raw values and demo metadata', () => {
  dbl(q('#compareActual'));
  assert.ok(q('.element-dialog')); assert.match(q('.dialoghead .eyebrow').textContent, /Windows 环境.*用户/);
  assert.match(q('#dialogTitle').title, /REG_EXPAND_SZ/);
  assert.equal(q('#elementTotal').textContent, `有效元素 ${values().length} · 已移除 0`);
  assert.equal(values().join(';'), raw()); assert.equal(q('#applyElements').disabled, true);
});
check('Edit, move, add and delete stay in draft; cancel restores focus and snapshot', () => {
  const before = raw(); const count = values().length;
  edit(q('.element-input'), ' Edited '); q('[data-move-down]').click();
  assert.equal(values()[1], ' Edited ');
  q('#addElement').click(); assert.equal(values().length, count + 1);
  q('[data-delete]').click(); assert.equal(values().length, count);
  assert.equal(raw(), before); cancel(); assert.equal(raw(), before);
  assert.equal(doc.activeElement.id, 'compareActual');
});
check('Explicit workbench edit button provides a discoverable alternative', () => {
  q('#editWindowsElements').click(); assert.ok(q('.element-dialog')); cancel();
});
check('Windows table shows snapshot without scheme highlights and shares the direct editor', () => {
  q('[data-view="windows"]').click(); const el = q('[data-windows-value="User|PATH"]');
  assert.equal(q('[data-windows-raw="User|PATH"]').textContent, raw()); assert.equal(el.querySelectorAll('mark').length, 0);
  dbl(el); assert.ok(q('.element-dialog')); assert.equal(values().join(';'), raw()); cancel();
  q('[data-windows-edit="User|PATH"]').click(); assert.ok(q('.element-dialog')); cancel();
});
check('Keyboard entry and Escape close editor without changing demo data', () => {
  const el = q('[data-windows-value="User|PATH"]'), before = raw(); el.focus(); key(el, 'Enter');
  edit(q('.element-input'), 'discard me'); key(q('.element-input'), 'Escape');
  assert.equal(q('#modal').classList.contains('open'), false); assert.equal(raw(), before);
  assert.equal(doc.activeElement, el);
});
check('Empty segments, repeated values and whitespace survive the editor unchanged', () => {
  w.eval("windows['User|PATH']=' A ;;A;B;' ; renderWindows()");
  dbl(q('[data-windows-value="User|PATH"]'));
  assert.deepEqual(values(), [' A ', '', 'A', 'B', '']);
  assert.equal(all('#elementPreview .diff-value[data-raw-value]').map(node => node.getAttribute('data-raw-value')).join(';'), ' A ;;A;B;');
  assert.equal(q('#applyElements').disabled, true);
});
check('Keyboard ordering retains stable row identity and input focus', () => {
  const el = all('.element-input')[2], id = el.dataset.elementInput;
  el.focus(); key(el, 'ArrowUp', { altKey: true });
  assert.deepEqual(values(), [' A ', 'A', '', 'B', '']);
  assert.equal(doc.activeElement.dataset.elementInput, id);
});
check('Drag/drop event handlers reorder elements without deduplication', () => {
  const rows = all('.element-row'), data = { setData() {}, effectAllowed: '' };
  const drag = new w.Event('dragstart', { bubbles: true, cancelable: true });
  Object.defineProperty(drag, 'dataTransfer', { value: data }); rows[0].dispatchEvent(drag);
  rows[3].dispatchEvent(new w.Event('drop', { bubbles: true, cancelable: true }));
  assert.deepEqual(values(), ['A', '', ' A ', 'B', '']);
});
check('Separator validation prevents ambiguous element input while retaining typed text', () => {
  edit(q('.element-input'), 'X;Y'); assert.equal(q('.element-input').value, 'X;Y');
  assert.equal(q('#applyElements').disabled, true); assert.match(q('#elementValidation').textContent, /分号/);
  edit(q('.element-input'), 'X'); assert.equal(q('#applyElements').disabled, false);
});
check('Apply updates only demo snapshot, keeps raw order, and records a demo history item', () => {
  const configBefore = w.eval('JSON.stringify(apps)'); const expected = values().join(';');
  q('#applyElements').click(); assert.equal(raw(), expected);
  assert.equal(q('#modal').classList.contains('open'), false);
  assert.equal(q('[data-windows-raw="User|PATH"]').textContent, expected);
  assert.equal(w.eval('JSON.stringify(apps)'), configBefore);
  assert.match(w.eval('history[0].detail'), /仅更新页面数据/);
  assert.equal(doc.activeElement.dataset.windowsEdit, 'User|PATH');
});
check('Deleting all elements removes the Windows value rather than persisting an empty string', () => {
  dbl(q('[data-windows-value="User|PATH"]'));
  while (q('[data-delete]')) q('[data-delete]').click();
  assert.equal(values().length, 0); assert.equal(q('#elementSerialized').textContent, '（不存在）');
  q('#applyElements').click(); assert.equal(w.eval("Object.hasOwn(windows,'User|PATH')"), false);
});
check('Special value stays whole with semicolons and hides element sorting controls', () => {
  w.eval("windows['User|WHOLE']=' A;;B; ' ; openWindowsElements('User','WHOLE',[' A;;B; '],true)");
  assert.deepEqual(values(), [' A;;B; ']); assert.equal(q('#addElement').hidden, true);
  assert.equal(q('.element-actions').hidden, true); assert.equal(q('.element-row').draggable, false);
  assert.equal(q('.element-input').tagName, 'TEXTAREA');
  const first=q('#closeDialog'), last=q('#applyElements');
  edit(q('.element-input'), ' X;;Y; '); assert.equal(q('#applyElements').disabled, false);
  first.focus(); key(first, 'Tab', { shiftKey: true }); assert.equal(doc.activeElement, last);
  key(last, 'Tab'); assert.equal(doc.activeElement, first);
  q('#applyElements').click(); assert.equal(w.eval("windows['User|WHOLE']"), ' X;;Y; ');
});
check('All schemes copy is simple and group headings separate name, count and flags', () => {
  q('#configBtn').click(); q('#editorView').value = 'all';
  q('#editorView').dispatchEvent(new w.Event('change', { bubbles: true }));
  assert.equal(q('#editorView option[value="all"]').textContent, '全部方案');
  assert.equal(all('.cfg-scheme-heading').length, 2);
  assert.equal(q('.cfg-scheme-heading h3').textContent, '3.9.11');
  assert.match(q('.cfg-scheme-heading > span').textContent, /2 项变量.*默认方案/);
  assert.equal(q('[data-tableplan="3.9.11"][aria-current="true"]').getAttribute('aria-expanded'),'true');
});
function dragFixture(test,original=' A ;A;;中文\r\n值'){
  const root=q('#modal'),before=w.eval('JSON.stringify({apps,windows,history})');
  const style=doc.createElement('style');style.textContent=fs.readFileSync(path.join(rootPath(),'fx-ui.css'),'utf8');doc.head.append(style);
  w.WindowsElements.open({container:root,name:'Path',scope:'User',raw:original,registryType:'REG_SZ',whole:false,selectedValues:['A'],close:()=>w.WindowsElements.dismiss(),onApply:()=>false});
  const rows=()=>[...root.querySelectorAll('.element-row')],ids=()=>rows().map(row=>row.dataset.elementId);
  root.querySelector('#elementList').getBoundingClientRect=()=>({left:120,top:80,width:724.5,height:529,bottom:609,right:844.5});
  const measureRows=()=>{let top=80;root.querySelectorAll('#elementList > [data-element-id]').forEach(row=>{const rowTop=top,height=row.classList.contains('element-ghost-row')?96:132.25;top+=height;row.getBoundingClientRect=()=>({left:120,top:rowTop,width:724.5,height,bottom:rowTop+height,right:844.5});});};
  const start=(row,x=43,y=31)=>{
    measureRows();
    const rect=row.getBoundingClientRect();
    let image;const data={effectAllowed:'',setData(type,value){this.payload=[type,value];},setDragImage(node,hotX,hotY){image={node,x:hotX,y:hotY};}};
    const event=new w.MouseEvent('dragstart',{bubbles:true,cancelable:true,clientX:rect.left+x,clientY:rect.top+y});Object.defineProperty(event,'dataTransfer',{value:data});row.dispatchEvent(event);
    return {node:doc.querySelector('.element-drag-preview'),nativeImage:image,data,rect};
  };
  const pointer=(row)=>{
    measureRows();const rect=row.getBoundingClientRect(),handle=row.querySelector('.element-order');let captured=false,releases=0;
    handle.setPointerCapture=()=>{captured=true;};handle.hasPointerCapture=()=>captured;handle.releasePointerCapture=()=>{captured=false;releases++;};
    const send=(type,x=rect.left+20,y=rect.top+20,target=doc,extra={})=>{
      const event=new w.MouseEvent(type,{bubbles:true,cancelable:true,button:0,buttons:type==='pointerup'?0:1,clientX:x,clientY:y});
      Object.defineProperties(event,{pointerId:{value:7},isPrimary:{value:true},...Object.fromEntries(Object.entries(extra).map(([key,value])=>[key,{value}]))});
      target.dispatchEvent(event);return event;
    };
    send('pointerdown',rect.left+20,rect.top+20,handle);
    assert(captured,'the order handle captures the mouse pointer');
    return {send,rect,handle,captured:()=>captured,releases:()=>releases};
  };
  try{test({root,rows,ids,start,pointer,original});assert.equal(w.eval('JSON.stringify({apps,windows,history})'),before);}
  finally{w.WindowsElements.dismiss();root.classList.remove('open');root.replaceChildren();style.remove();}
}
function rootPath(){return path.resolve(__dirname,'..');}
function landingAnimationFixture(run){
  const prototype=w.Element.prototype,bounds=prototype.getBoundingClientRect,animate=prototype.animate,calls=[];
  prototype.getBoundingClientRect=function(){
    if(this.parentElement?.id==='elementList'&&this.hasAttribute('data-element-id')){
      const siblings=[...this.parentElement.querySelectorAll(':scope > [data-element-id]')],index=siblings.indexOf(this);
      const height=this.classList.contains('element-ghost-row')?96:132.25;
      const top=80+siblings.slice(0,index).reduce((sum,node)=>sum+(node.classList.contains('element-ghost-row')?96:132.25),0);
      return {left:120,top,width:724.5,height,bottom:top+height,right:844.5};
    }
    return bounds.call(this);
  };
  prototype.animate=function(frames,options){calls.push({element:this,frames,options});return {cancel(){}};};
  try{run(calls);}finally{prototype.getBoundingClientRect=bounds;if(animate)prototype.animate=animate;else delete prototype.animate;}
}
check('Opaque full-row overlay preserves dimensions, live controls and hotspot while native drag bitmap is blank',()=>dragFixture(({root,rows,ids,start,original})=>{
  const source=rows()[3],input=source.querySelector('textarea'),beforeIds=ids(),beforeMarkup=source.innerHTML;
  input.id='live-drag-control';input.name='original-control';input.value=' 当前输入\n A;;A; 中文 ';input.style.height='94px';
  source.classList.add('is-replaced','is-link-source','fx-move');
  const states=source.querySelector('.element-states').textContent,buttons=[...source.querySelectorAll('button')].map(b=>({title:b.title,disabled:b.disabled}));
  const {node,nativeImage,data,rect}=start(source,71,65);
  assert.notStrictEqual(nativeImage.node,node);assert.equal(nativeImage.node.tagName,'CANVAS');assert.equal(nativeImage.node.width,1);assert.equal(nativeImage.node.height,1);assert.equal(nativeImage.x,0);assert.equal(nativeImage.y,0);assert.equal(nativeImage.node.childNodes.length,0);
  assert.deepEqual(data.payload,['text/plain',source.dataset.elementId]);assert.equal(data.effectAllowed,'move');
  assert(node.classList.contains('element-row'));assert(node.classList.contains('element-drag-preview'));assert(node.classList.contains('is-replaced'));assert(!node.matches('.is-dragging,.is-link-source,.fx-move'));
  assert.equal(node.style.width,rect.width+'px');assert.equal(node.style.height,rect.height+'px');assert.equal(node.style.minWidth,node.style.width);assert.equal(node.style.maxWidth,node.style.width);assert.equal(node.style.minHeight,node.style.height);assert.equal(node.style.maxHeight,node.style.height);
  assert.equal(w.getComputedStyle(node).boxSizing,'border-box');assert.match(w.getComputedStyle(node).transform,/translateY/);assert.equal(w.getComputedStyle(node).pointerEvents,'none');assert.equal(w.getComputedStyle(node).position,'fixed');assert.equal(w.getComputedStyle(node).zIndex,'450');assert.equal(w.getComputedStyle(node).opacity,'1');
  assert.equal(node.style.left,rect.left+'px');assert.equal(node.style.top,rect.top+'px');
  doc.dispatchEvent(new w.MouseEvent('dragover',{clientX:400,clientY:300,bubbles:true}));assert.equal(node.style.left,'120px');assert.equal(node.style.getPropertyValue('--drag-y'),'-241.75px');
  doc.dispatchEvent(new w.MouseEvent('dragover',{clientX:500,clientY:360,bubbles:true}));assert.equal(node.style.left,'120px');assert.equal(node.style.getPropertyValue('--drag-y'),'-181.75px');
  source.dispatchEvent(new w.MouseEvent('drag',{clientX:400,clientY:300,bubbles:true}));assert.equal(node.style.getPropertyValue('--drag-y'),'-181.75px');
  source.dispatchEvent(new w.MouseEvent('drag',{clientX:0,clientY:0,bubbles:true}));assert.equal(node.style.left,'120px');assert.equal(node.style.getPropertyValue('--drag-y'),'-181.75px');
  doc.documentElement.dispatchEvent(new w.MouseEvent('dragleave',{bubbles:true,relatedTarget:null}));assert(node.hidden);
  doc.dispatchEvent(new w.MouseEvent('dragover',{clientX:0,clientY:250,bubbles:true}));assert(!node.hidden);assert.equal(node.style.left,'120px');assert.equal(node.style.getPropertyValue('--drag-y'),'-291.75px');
  // Tracking is coordinate-based, independent of decorative/reduced-motion animations.
  const media=w.matchMedia;w.matchMedia=()=>({matches:true});try{doc.dispatchEvent(new w.MouseEvent('dragover',{clientX:600,clientY:400,bubbles:true}));assert.equal(node.style.left,'120px');assert.equal(node.style.getPropertyValue('--drag-y'),'-141.75px');}finally{w.matchMedia=media;}
  assert.equal(node.querySelector('textarea').value,input.value);assert.equal(node.querySelector('textarea').textContent,input.value);assert.equal(node.querySelector('textarea').style.height,'94px');
  assert.equal(node.querySelector('.element-order').textContent,source.querySelector('.element-order').textContent);assert.equal(node.querySelector('.element-states').textContent,states);
  assert.deepEqual([...node.querySelectorAll('button')].map(b=>({title:b.title,disabled:b.disabled})),buttons);
  assert.equal(node.getAttribute('aria-hidden'),'true');assert(node.hasAttribute('inert'));assert.equal(node.draggable,false);
  assert([node,...node.querySelectorAll('*')].every(el=>[...el.attributes].every(a=>a.name!=='id'&&!a.name.startsWith('data-')&&!a.name.startsWith('on'))));
  assert([...node.querySelectorAll('button,textarea')].every(el=>el.tabIndex===-1&&el.onclick===null&&el.oninput===null));assert.equal(doc.querySelectorAll('#live-drag-control').length,1);
  assert.strictEqual(rows()[3],source);assert(source.classList.contains('is-dragging'));assert.equal(source.getBoundingClientRect().width,rect.width);assert.equal(source.getBoundingClientRect().height,rect.height);assert.deepEqual(ids(),beforeIds);
  assert.equal(root.querySelector('#elementSerialized').textContent,original);assert.equal(root.querySelector('#applyElements').disabled,true);
  source.dispatchEvent(new w.Event('dragend',{bubbles:true}));assert(!doc.querySelector('.element-drag-preview'));assert(!nativeImage.node.isConnected);assert(!source.classList.contains('is-dragging'));assert(!root.querySelector('#elementList').classList.contains('is-dragging-list'));
  assert.notEqual(source.innerHTML,beforeMarkup);assert.equal(input.value,' 当前输入\n A;;A; 中文 ');
}));
check('Drag image cleanup covers replacement, normal/no-op row drops, end drops and dismiss without changing occurrence identity',()=>dragFixture(({root,rows,ids,start,original})=>{
  const before=ids(),first=rows()[0],second=rows()[1],old=start(first).node,current=start(second).node;
  assert(!old.isConnected);assert(current.isConnected);assert.equal(doc.querySelectorAll('.element-drag-preview').length,1);assert(!first.classList.contains('is-dragging'));
  second.dispatchEvent(new w.Event('drop',{bubbles:true,cancelable:true}));assert(!current.isConnected);assert(!second.classList.contains('is-dragging'));assert.deepEqual(ids(),before);
  const last=rows().at(-1),endImage=start(last).node;root.querySelector('.element-drop-end').dispatchEvent(new w.Event('drop',{bubbles:true,cancelable:true}));assert(!endImage.isConnected);assert.deepEqual(ids(),before);
  const moved=start(rows()[0]).node;rows()[3].dispatchEvent(new w.Event('drop',{bubbles:true,cancelable:true}));assert(!moved.isConnected);assert.deepEqual(ids(),[before[1],before[2],before[0],before[3]]);
  start(rows().find(row=>row.dataset.elementId===before[0]));rows()[0].dispatchEvent(new w.Event('drop',{bubbles:true,cancelable:true}));assert.deepEqual(ids(),before);assert.equal(root.querySelector('#elementSerialized').textContent,original);
  const ending=start(rows()[0],999,-20);doc.dispatchEvent(new w.MouseEvent('dragover',{clientX:900,clientY:200,bubbles:true}));assert.equal(ending.node.style.left,'120px');assert.equal(ending.node.style.getPropertyValue('--drag-y'),'120px');
  w.WindowsElements.dismiss();assert(!ending.node.isConnected);assert(!root.querySelector('.is-dragging'));assert(!root.querySelector('#elementList').classList.contains('is-dragging-list'));assert(!doc.querySelector('.drag-card'));
}));
check('Vertical rail previews a full slot, moves neighbours and commits only the selected occurrence on drop',()=>dragFixture(({root,rows,ids,start,original})=>{
  const before=ids(),last=rows().at(-1),lift=start(last),input=last.querySelector('textarea');
  const raw=input.value;
  doc.dispatchEvent(new w.MouseEvent('dragover',{clientX:3000,clientY:111,bubbles:true}));
  assert.equal(lift.node.style.left,'120px');assert.equal(lift.node.style.getPropertyValue('--drag-y'),'-396.75px');
  const gap=root.querySelector('.element-drop-gap');assert(!gap.hidden);assert.equal(gap.style.top,'0px');assert.equal(gap.style.height,'132.25px');
  assert(rows().slice(0,3).every(row=>row.style.getPropertyValue('--reorder-y')==='132.25px'));
  assert.deepEqual(ids(),before);assert.equal(root.querySelector('#elementSerialized').textContent,original);assert(root.querySelector('#applyElements').disabled);
  // The moved DOM targets are no longer authoritative: frozen geometry chose
  // the first slot, regardless of which shifted row receives the native drop.
  rows()[2].dispatchEvent(new w.Event('drop',{bubbles:true,cancelable:true}));
  assert.deepEqual(ids(),[before[3],...before.slice(0,3)]);assert.equal(rows()[0].querySelector('textarea').value,raw);
  assert(!doc.querySelector('.element-drag-preview'));assert(!root.querySelector('.element-drop-gap:not([hidden])'));
}));
check('Dragging down opens an end slot and cancellation preserves raw order and row identities',()=>dragFixture(({root,rows,ids,start,original})=>{
  const before=ids(),first=rows()[0],lift=start(first);
  doc.dispatchEvent(new w.MouseEvent('dragover',{clientX:-500,clientY:2000,bubbles:true}));
  assert.equal(lift.node.style.left,'120px');assert.equal(lift.node.style.getPropertyValue('--drag-y'),'396.75px');
  assert(rows().slice(1).every(row=>row.style.getPropertyValue('--reorder-y')==='-132.25px'));
  assert.equal(root.querySelector('.element-drop-gap').style.top,'396.75px');assert.deepEqual(ids(),before);
  first.dispatchEvent(new w.Event('dragend',{bubbles:true}));
  assert.deepEqual(ids(),before);assert.equal(root.querySelector('#elementSerialized').textContent,original);
  assert(rows().every(row=>!row.style.getPropertyValue('--reorder-y')));assert(root.querySelector('.element-drop-gap').hidden);
}));
check('Fast cross-list moves ignore stale source samples and keep a stationary landing slot quiet',()=>dragFixture(({root,rows,ids,start,original})=>{
  const before=ids(),source=rows().at(-1),lift=start(source),list=root.querySelector('#elementList');
  doc.dispatchEvent(new w.MouseEvent('dragover',{clientX:400,clientY:111,bubbles:true}));
  const gap=list.querySelector('.element-drop-gap'),observer=new w.MutationObserver(()=>{});
  observer.observe(list,{attributes:true,subtree:true});
  for(let index=0;index<100;index++){
    source.dispatchEvent(new w.MouseEvent('drag',{clientX:400,clientY:550,bubbles:true}));
    doc.dispatchEvent(new w.MouseEvent('dragover',{clientX:400,clientY:111,bubbles:true}));
  }
  assert.equal(gap.style.top,'0px');assert.equal(lift.node.style.getPropertyValue('--drag-y'),'-396.75px');
  assert.equal(observer.takeRecords().length,0);observer.disconnect();
  assert.deepEqual(ids(),before);assert.equal(root.querySelector('#elementSerialized').textContent,original);
}));
check('A small slot boundary dead band prevents rapid reverse shuffling without blocking a deliberate move',()=>dragFixture(({root,rows,start})=>{
  const source=rows()[0];start(source);
  const move=offset=>doc.dispatchEvent(new w.MouseEvent('dragover',{clientX:1000,clientY:111+offset,bubbles:true}));
  const slot=()=>root.querySelector('.element-drop-gap').style.top;
  move(80);assert.equal(slot(),'132.25px');
  for(const offset of [64,68,65,67,64]){move(offset);assert.equal(slot(),'132.25px');}
  move(60);assert.equal(slot(),'0px');move(68);assert.equal(slot(),'0px');
  move(74);assert.equal(slot(),'132.25px');
}));
check('Scrolled list bounds and landing slots use the same current viewport coordinates',()=>dragFixture(({root,rows,start})=>{
  const source=rows().at(-1),lift=start(source),list=root.querySelector('#elementList');
  list.getBoundingClientRect=()=>({left:120,top:-120,width:724.5,height:529,bottom:409,right:844.5});
  doc.dispatchEvent(new w.MouseEvent('dragover',{clientX:800,clientY:-1000,bubbles:true}));
  assert.equal(lift.node.style.left,'120px');assert.equal(lift.node.style.getPropertyValue('--drag-y'),'-596.75px');assert.equal(list.querySelector('.element-drop-gap').style.top,'0px');
  doc.dispatchEvent(new w.MouseEvent('dragover',{clientX:800,clientY:1000,bubbles:true}));
  assert.equal(lift.node.style.getPropertyValue('--drag-y'),'-200px');assert.equal(list.querySelector('.element-drop-gap').style.top,'396.75px');
}));
check('The empty space opened by neighbours accepts a drop on the list itself',()=>dragFixture(({root,rows,ids,start})=>{
  const before=ids(),list=root.querySelector('#elementList');start(rows()[0]);
  const over=new w.MouseEvent('dragover',{clientX:400,clientY:2000,bubbles:true,cancelable:true});list.dispatchEvent(over);assert(over.defaultPrevented);
  list.dispatchEvent(new w.Event('drop',{bubbles:true,cancelable:true}));
  assert.deepEqual(ids(),[...before.slice(1),before[0]]);assert(!doc.querySelector('.element-drag-preview'));assert(!root.querySelector('.will-shift-up,.will-shift-down'));
}));
check('Overlay document/source tracking detaches on external drop, next drag, render, dragend and dismiss',()=>dragFixture(({root,rows,start})=>{
  const types=['dragover','dragleave','drop','dragend'],hooks=new Map(types.map(type=>[type,new Set()]));
  const add=doc.addEventListener,remove=doc.removeEventListener;
  doc.addEventListener=function(type,handler,options){if(hooks.has(type)&&options===true)hooks.get(type).add(handler);return add.call(this,type,handler,options);};
  doc.removeEventListener=function(type,handler,options){if(hooks.has(type)&&options===true)hooks.get(type).delete(handler);return remove.call(this,type,handler,options);};
  const active=()=>assert(types.every(type=>hooks.get(type).size===1));
  const cleared=()=>{assert(types.every(type=>hooks.get(type).size===0));assert(!doc.querySelector('.element-drag-preview,.element-drag-native'));};
  try{
    const oldSource=rows()[0],first=start(oldSource),second=start(rows()[1]);active();assert(!first.node.isConnected);assert(!first.nativeImage.node.isConnected);
    oldSource.dispatchEvent(new w.MouseEvent('drag',{clientX:800,clientY:600,bubbles:true}));assert.equal(second.node.style.left,'120px');assert.equal(second.node.style.top,second.rect.top+'px');
    doc.body.dispatchEvent(new w.Event('drop',{bubbles:true,cancelable:true}));cleared();
    start(rows()[0]);active();root.querySelector('[data-move-down]').click();cleared();
    const ended=start(rows()[0]);active();rows()[0].dispatchEvent(new w.Event('dragend',{bubbles:true}));cleared();
    const position=ended.node.style.left;doc.dispatchEvent(new w.MouseEvent('dragover',{clientX:700,clientY:700,bubbles:true}));assert.equal(ended.node.style.left,position);
    start(rows()[0]);active();w.WindowsElements.dismiss();cleared();
  }finally{w.WindowsElements.dismiss();doc.addEventListener=add;doc.removeEventListener=remove;}
}));
check('Pointer release outside the list commits the same row, baseline/preview badges and counts as move buttons',()=>dragFixture(({root,rows,ids,pointer,original})=>{
  const before=ids(),sourceId=before.at(-1),pane=selector=>[...root.querySelectorAll(selector+' [data-element-id]')];
  const baseline=pane('#elementOriginal').map(node=>node.querySelector('.diff-value').textContent);
  const feedback=()=>({
    ids:ids(),values:rows().map(row=>row.querySelector('textarea').value),
    rows:rows().map(node=>[node.dataset.elementId,node.classList.contains('is-moved'),node.querySelector('.diff-label.is-moved')?.textContent??null]),
    original:pane('#elementOriginal').map(node=>[node.dataset.elementId,node.querySelector('.diff-value').textContent,node.classList.contains('is-moved')]),
    preview:pane('#elementPreview').map(node=>[node.dataset.elementId,node.querySelector('.diff-value').textContent,node.classList.contains('is-moved')]),
    count:root.querySelector('[data-legend-count="moved"]').textContent,status:root.querySelector('#elementStatus').textContent,
    serialized:root.querySelector('#elementSerialized').textContent,disabled:root.querySelector('#applyElements').disabled
  });
  for(let i=0;i<3;i++)root.querySelector(`[data-element-id="${sourceId}"] [data-move-up]`).click();
  const buttonResult=feedback();assert.equal(buttonResult.count,'4');assert.equal(buttonResult.disabled,false);
  root.querySelector('#restoreElements').click();assert.deepEqual(ids(),before);
  const drag=pointer(rows().at(-1));drag.send('pointermove',3000,100);assert(doc.querySelector('.element-drag-preview'));
  // Native drag/drop events must never take over the pointer gesture.
  const nativeStart=new w.Event('dragstart',{bubbles:true,cancelable:true});drag.handle.dispatchEvent(nativeStart);assert(nativeStart.defaultPrevented);
  assert.deepEqual(ids(),before);assert.equal(root.querySelector('#elementSerialized').textContent,original);
  assert.deepEqual(pane('#elementOriginal').map(node=>node.querySelector('.diff-value').textContent),baseline);
  drag.send('pointerup',3000,100);
  drag.send('lostpointercapture',3000,100,drag.handle);
  assert.deepEqual(feedback(),buttonResult);assert.equal(drag.captured(),false);assert.equal(drag.releases(),1);
  assert.deepEqual(pane('#elementOriginal').map(node=>node.dataset.elementId),before);
  assert.deepEqual(pane('#elementPreview').map(node=>node.dataset.elementId),[sourceId,...before.slice(0,-1)]);
  assert(pane('#elementOriginal').every(node=>node.querySelector('.diff-label.is-moved')));
  assert(pane('#elementPreview').every(node=>node.querySelector('.diff-label.is-moved')));
  assert(!doc.querySelector('.element-drag-preview'));assert(!root.querySelector('.will-shift-up,.will-shift-down'));
  // Detached listeners must not move the list or change the opening snapshot.
  drag.send('pointermove',20,2000);drag.send('pointerup',20,2000);assert.deepEqual(feedback(),buttonResult);
  root.querySelector('#restoreElements').click();assert.deepEqual(ids(),before);assert(!root.querySelector('.is-moved'));
}));
check('Dragging identical occurrences produces movement feedback in both comparison panes even when raw result is unchanged',()=>dragFixture(({root,rows,ids,pointer,original})=>{
  const before=ids(),drag=pointer(rows()[1]);drag.send('pointermove',140,85);drag.send('pointerup',140,85);
  assert.deepEqual(ids(),[before[1],before[0],...before.slice(2)]);
  assert.equal(root.querySelector('#elementSerialized').textContent,original);assert(root.querySelector('#applyElements').disabled);
  assert.equal(root.querySelector('[data-legend-count="moved"]').textContent,'2');
  for(const selector of ['#elementList','#elementOriginal','#elementPreview']){
    for(const id of before.slice(0,2))assert(root.querySelector(`${selector} [data-element-id="${id}"] .diff-label.is-moved`));
  }
  assert.deepEqual([...root.querySelectorAll('#elementOriginal [data-element-id]')].map(node=>node.dataset.elementId),before);
  const back=pointer(rows()[0]);back.send('pointermove',140,180);back.send('pointerup',140,180);
  assert.deepEqual(ids(),before);assert.equal(root.querySelector('[data-legend-count="moved"]').textContent,'0');assert(!root.querySelector('.is-moved'));
},'A;A;;中文; D '));
check('Pointer cancellation and click-only gestures preserve both comparison panes and release capture',()=>dragFixture(({root,rows,ids,pointer,original})=>{
  const before=ids(),panes=()=>['#elementOriginal','#elementPreview'].map(selector=>root.querySelector(selector).innerHTML),baseline=panes();
  let drag=pointer(rows()[0]);drag.send('pointerup');assert.deepEqual(ids(),before);assert.deepEqual(panes(),baseline);assert.equal(drag.releases(),1);
  for(const reason of ['pointercancel','lostpointercapture','Escape','blur']){
    drag=pointer(rows()[0]);drag.send('pointermove',140,2000);assert(doc.querySelector('.element-drag-preview'));
    if(reason==='Escape')doc.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));
    else if(reason==='blur')w.dispatchEvent(new w.Event('blur'));
    else drag.send(reason,140,2000,reason==='lostpointercapture'?drag.handle:doc);
    assert.equal(drag.releases(),1);assert(!doc.querySelector('.element-drag-preview'));assert.deepEqual(ids(),before);assert.deepEqual(panes(),baseline);
    drag.send('pointerup',140,2000);assert.deepEqual(ids(),before);assert.equal(root.querySelector('#elementSerialized').textContent,original);
  }
}));
check('Fast pointer reversals settle on frozen slots without repeated neighbour mutations or stale native samples',()=>dragFixture(({root,rows,ids,pointer,original})=>{
  const before=ids(),source=rows().at(-1),drag=pointer(source),list=root.querySelector('#elementList');
  for(const y of [85,2000,85,2000,85])drag.send('pointermove',1000,y);
  const observer=new w.MutationObserver(()=>{});observer.observe(list,{attributes:true,subtree:true});
  for(let i=0;i<100;i++){
    source.dispatchEvent(new w.MouseEvent('drag',{clientX:400,clientY:550,bubbles:true}));
    drag.send('pointermove',1000,85);
  }
  assert.equal(observer.takeRecords().length,0);observer.disconnect();assert.deepEqual(ids(),before);
  assert.equal(root.querySelector('#elementSerialized').textContent,original);
  drag.send('pointerup',1000,85);assert.deepEqual(ids(),[before.at(-1),...before.slice(0,-1)]);
}));
check('Dropping back into the same slot does not redraw the list or pretend a move occurred',()=>dragFixture(({root,rows,ids,pointer})=>{
  const before=ids(),first=rows()[0],drag=pointer(first);drag.send('pointermove',140,90);drag.send('pointerup',140,90);
  assert.deepEqual(ids(),before);assert.strictEqual(rows()[0],first);assert(!root.querySelector('.fx-move,.is-moved'));
  assert.equal(root.querySelector('[data-legend-count="moved"]').textContent,'0');assert.equal(drag.releases(),1);
}));
check('A ghost can be pointer-dragged with both comparison markers while its deletion draft stays out of the result',()=>dragFixture(({root,rows,ids,pointer})=>{
  const before=ids(),id=before[1],input=rows()[1].querySelector('textarea');input.value=' 删除前的草稿\n中文 ';
  input.dispatchEvent(new w.Event('input',{bubbles:true}));rows()[1].querySelector('[data-delete]').click();
  const ghost=root.querySelector(`.element-ghost-row[data-element-id="${id}"]`);assert(ghost.draggable);
  const beforeRaw=root.querySelector('#elementSerialized').textContent;
  const drag=pointer(ghost);drag.send('pointermove',-400,2000);
  const clone=doc.querySelector('.element-drag-preview');assert(clone.classList.contains('element-ghost-row'));assert.equal(w.getComputedStyle(clone).position,'fixed');assert.equal(clone.style.left,'120px');
  assert.match(clone.textContent,/删除前的草稿/);drag.send('pointerup',-400,2000);
  const displayed=[...root.querySelectorAll('#elementList > [data-element-id]')].map(node=>node.dataset.elementId);
  assert.deepEqual(displayed,[before[0],...before.slice(2),id]);assert.equal(root.querySelector('#elementSerialized').textContent,beforeRaw);
  assert.equal(root.querySelector('[data-legend-count="ghost-moved"]').textContent,'1');
  for(const selector of ['#elementList','#elementOriginal','#elementPreview']){
    const node=root.querySelector(`${selector} [data-element-id="${id}"]`);assert.equal(node.querySelector('.diff-label.is-moved').textContent,'幽灵移动');assert(node.querySelector('.diff-label.is-removed'));
  }
  assert.equal(root.querySelector(`#elementOriginal [data-element-id="${id}"] .diff-value`).textContent,'A');
  root.querySelector(`[data-restore-ghost="${id}"]`).click();assert.deepEqual(ids(),displayed);
  assert.equal(rows().at(-1).querySelector('textarea').value,' 删除前的草稿\n中文 ');
  assert.match(root.querySelector('#elementSerialized').textContent,/删除前的草稿/);assert.equal(root.querySelector('[data-legend-count="ghost-moved"]').textContent,'0');
}));
for(const mechanism of ['pointer','native'])check(`${mechanism} release continues from the visible lifted row instead of replaying its opening position`,()=>dragFixture(({root,rows,ids,pointer,start})=>landingAnimationFixture(calls=>{
  const before=ids(),id=before.at(-1),source=rows().at(-1);let drag;
  if(mechanism==='pointer'){drag=pointer(source);drag.send('pointermove',140,85);}
  else{start(source);doc.dispatchEvent(new w.MouseEvent('dragover',{clientX:140,clientY:85,bubbles:true}));}
  // Simulate a softly-following lift still 32px below its final slot at release.
  const clone=doc.querySelector('.element-drag-preview');clone.getBoundingClientRect=()=>({left:120,top:112,width:724.5,height:132.25,bottom:244.25,right:844.5});
  if(mechanism==='pointer')drag.send('pointerup',140,85);else rows()[0].dispatchEvent(new w.Event('drop',{bubbles:true,cancelable:true}));
  assert.deepEqual(ids(),[id,...before.slice(0,-1)]);
  const landed=rows()[0],motion=calls.find(call=>call.element===landed);assert(motion,'the short remaining distance eases into the slot');
  assert.equal(motion.frames[0].transform,'translate(0px,32px)');assert.equal(motion.frames[1].transform,'translate(0,0)');
  assert(landed.classList.contains('fx-drop'));assert(!landed.classList.contains('fx-move'));assert(!doc.querySelector('.element-drag-preview'));
  assert.equal(root.querySelector('[data-legend-count="moved"]').textContent,'4');
})));
check('An already settled lift stays at the destination on release without a second movement animation',()=>dragFixture(({root,rows,ids,pointer})=>landingAnimationFixture(calls=>{
  const before=ids(),drag=pointer(rows().at(-1));drag.send('pointermove',140,85);
  doc.querySelector('.element-drag-preview').getBoundingClientRect=()=>({left:120,top:80,width:724.5,height:132.25,bottom:212.25,right:844.5});
  drag.send('pointerup',140,85);assert.deepEqual(ids(),[before.at(-1),...before.slice(0,-1)]);
  assert(!calls.some(call=>call.element===rows()[0]));assert(rows()[0].classList.contains('fx-drop'));
})));
check('Ghost release also lands from its current visible position while preserving removal and movement feedback',()=>dragFixture(({root,rows,ids,pointer})=>landingAnimationFixture(calls=>{
  const before=ids(),id=before[1];rows()[1].querySelector('[data-delete]').click();calls.length=0;
  const ghost=root.querySelector(`.element-ghost-row[data-element-id="${id}"]`),drag=pointer(ghost);drag.send('pointermove',140,2000);
  doc.querySelector('.element-drag-preview').getBoundingClientRect=()=>({left:120,top:465.75,width:724.5,height:96,bottom:561.75,right:844.5});
  drag.send('pointerup',140,2000);
  const landed=root.querySelector(`.element-ghost-row[data-element-id="${id}"]`),motion=calls.find(call=>call.element===landed);assert(motion);
  assert.equal(motion.frames[0].transform,'translate(0px,-11px)');assert(landed.classList.contains('fx-drop'));assert(!landed.classList.contains('fx-move'));
  assert.equal(landed.querySelector('.diff-label.is-moved').textContent,'幽灵移动');assert(landed.querySelector('.diff-label.is-removed'));
})));
check('Shared component and additional stylesheet are local; no UI runtime errors', () => {
  assert.deepEqual(errors, []);
  for (const name of ['windows-elements.js', 'elements-ui.css']) assert.ok(fs.existsSync(path.join(root, name)));
  assert.match(html, /src="windows-elements\.js(?:\?v=[a-f0-9]{12})?"/);
  assert.match(html, /href="elements-ui\.css(?:\?v=[a-f0-9]{12})?"/);
});
const report = { verification: 'JSDOM UI event checks only; native dragging, visual rendering and Windows integration not verified', nativeAcceptance: 'Owner reported vertical rail roughly met the intended motion but still jittered; pointer capture, shared movement feedback and ghost dragging await manual verification.', checks };
fs.writeFileSync(path.join(__dirname, 'elements-report.json'), JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify(report, null, 2)); w.close();

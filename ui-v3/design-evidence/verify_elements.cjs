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
function dragFixture(test){
  const root=q('#modal'),before=w.eval('JSON.stringify({apps,windows,history})');
  const style=doc.createElement('style');style.textContent=fs.readFileSync(path.join(rootPath(),'fx-ui.css'),'utf8');doc.head.append(style);
  const original=' A ;A;;中文\r\n值';
  w.WindowsElements.open({container:root,name:'Path',scope:'User',raw:original,registryType:'REG_SZ',whole:false,selectedValues:['A'],close:()=>w.WindowsElements.dismiss(),onApply:()=>false});
  const rows=()=>[...root.querySelectorAll('.element-row')],ids=()=>rows().map(row=>row.dataset.elementId);
  const start=(row,x=43,y=31)=>{
    const rect={left:120,top:80,width:724.5,height:132.25};row.getBoundingClientRect=()=>rect;
    let image;const data={effectAllowed:'',setData(type,value){this.payload=[type,value];},setDragImage(node,hotX,hotY){image={node,x:hotX,y:hotY};}};
    const event=new w.MouseEvent('dragstart',{bubbles:true,cancelable:true,clientX:rect.left+x,clientY:rect.top+y});Object.defineProperty(event,'dataTransfer',{value:data});row.dispatchEvent(event);
    return {...image,data,rect};
  };
  try{test({root,rows,ids,start,original});assert.equal(w.eval('JSON.stringify({apps,windows,history})'),before);}
  finally{w.WindowsElements.dismiss();root.classList.remove('open');root.replaceChildren();style.remove();}
}
function rootPath(){return path.resolve(__dirname,'..');}
check('Native drag image keeps the measured full row, live multiline value, badges, controls and mouse grab point',()=>dragFixture(({root,rows,ids,start,original})=>{
  const source=rows()[3],input=source.querySelector('textarea'),beforeIds=ids(),beforeMarkup=source.innerHTML;
  input.id='live-drag-control';input.name='original-control';input.value=' 当前输入\n A;;A; 中文 ';input.style.height='94px';
  source.classList.add('is-replaced','is-link-source','fx-move');
  const states=source.querySelector('.element-states').textContent,buttons=[...source.querySelectorAll('button')].map(b=>({title:b.title,disabled:b.disabled}));
  const {node,x,y,data,rect}=start(source,71,65);
  assert.equal(x,71);assert.equal(y,65);assert.deepEqual(data.payload,['text/plain',source.dataset.elementId]);assert.equal(data.effectAllowed,'move');
  assert(node.classList.contains('element-row'));assert(node.classList.contains('element-drag-preview'));assert(node.classList.contains('is-replaced'));assert(!node.matches('.is-dragging,.is-link-source,.fx-move'));
  assert.equal(node.style.width,rect.width+'px');assert.equal(node.style.height,rect.height+'px');assert.equal(node.style.minWidth,node.style.width);assert.equal(node.style.maxWidth,node.style.width);assert.equal(node.style.minHeight,node.style.height);assert.equal(node.style.maxHeight,node.style.height);
  assert.equal(w.getComputedStyle(node).boxSizing,'border-box');assert.equal(w.getComputedStyle(node).transform,'none');assert.equal(w.getComputedStyle(node).pointerEvents,'none');
  assert.equal(node.querySelector('textarea').value,input.value);assert.equal(node.querySelector('textarea').textContent,input.value);assert.equal(node.querySelector('textarea').style.height,'94px');
  assert.equal(node.querySelector('.element-order').textContent,source.querySelector('.element-order').textContent);assert.equal(node.querySelector('.element-states').textContent,states);
  assert.deepEqual([...node.querySelectorAll('button')].map(b=>({title:b.title,disabled:b.disabled})),buttons);
  assert.equal(node.getAttribute('aria-hidden'),'true');assert(node.hasAttribute('inert'));assert.equal(node.draggable,false);
  assert([node,...node.querySelectorAll('*')].every(el=>[...el.attributes].every(a=>a.name!=='id'&&!a.name.startsWith('data-')&&!a.name.startsWith('on'))));
  assert([...node.querySelectorAll('button,textarea')].every(el=>el.tabIndex===-1&&el.onclick===null&&el.oninput===null));assert.equal(doc.querySelectorAll('#live-drag-control').length,1);
  assert.strictEqual(rows()[3],source);assert(source.classList.contains('is-dragging'));assert.equal(source.getBoundingClientRect().width,rect.width);assert.equal(source.getBoundingClientRect().height,rect.height);assert.deepEqual(ids(),beforeIds);
  assert.equal(root.querySelector('#elementSerialized').textContent,original);assert.equal(root.querySelector('#applyElements').disabled,true);
  source.dispatchEvent(new w.Event('dragend',{bubbles:true}));assert(!doc.querySelector('.element-drag-preview'));assert(!source.classList.contains('is-dragging'));assert(!root.querySelector('#elementList').classList.contains('is-dragging-list'));
  assert.notEqual(source.innerHTML,beforeMarkup);assert.equal(input.value,' 当前输入\n A;;A; 中文 ');
}));
check('Drag image cleanup covers replacement, normal/no-op row drops, end drops and dismiss without changing occurrence identity',()=>dragFixture(({root,rows,ids,start,original})=>{
  const before=ids(),first=rows()[0],second=rows()[1],old=start(first).node,current=start(second).node;
  assert(!old.isConnected);assert(current.isConnected);assert.equal(doc.querySelectorAll('.element-drag-preview').length,1);assert(!first.classList.contains('is-dragging'));
  second.dispatchEvent(new w.Event('drop',{bubbles:true,cancelable:true}));assert(!current.isConnected);assert(!second.classList.contains('is-dragging'));assert.deepEqual(ids(),before);
  const last=rows().at(-1),endImage=start(last).node;root.querySelector('.element-drop-end').dispatchEvent(new w.Event('drop',{bubbles:true,cancelable:true}));assert(!endImage.isConnected);assert.deepEqual(ids(),before);
  const moved=start(rows()[0]).node;rows()[3].dispatchEvent(new w.Event('drop',{bubbles:true,cancelable:true}));assert(!moved.isConnected);assert.deepEqual(ids(),[before[1],before[2],before[0],before[3]]);
  start(rows().find(row=>row.dataset.elementId===before[0]));rows()[0].dispatchEvent(new w.Event('drop',{bubbles:true,cancelable:true}));assert.deepEqual(ids(),before);assert.equal(root.querySelector('#elementSerialized').textContent,original);
  const ending=start(rows()[0],999,-20);assert.equal(ending.x,ending.rect.width);assert.equal(ending.y,0);
  w.WindowsElements.dismiss();assert(!ending.node.isConnected);assert(!root.querySelector('.is-dragging'));assert(!root.querySelector('#elementList').classList.contains('is-dragging-list'));assert(!doc.querySelector('.drag-card'));
}));
check('Shared component and additional stylesheet are local; no UI runtime errors', () => {
  assert.deepEqual(errors, []);
  for (const name of ['windows-elements.js', 'elements-ui.css']) assert.ok(fs.existsSync(path.join(root, name)));
  assert.match(html, /src="windows-elements\.js(?:\?v=[a-f0-9]{12})?"/);
  assert.match(html, /href="elements-ui\.css(?:\?v=[a-f0-9]{12})?"/);
});
const report = { verification: 'JSDOM UI event checks only; native dragging, visual rendering and Windows integration not verified', checks };
fs.writeFileSync(path.join(__dirname, 'elements-report.json'), JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify(report, null, 2)); w.close();

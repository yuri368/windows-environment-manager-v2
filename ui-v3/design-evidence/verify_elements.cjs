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
  virtualConsole: vc, beforeParse(w) {
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
  assert.ok(q('.element-dialog')); assert.match(q('.elements-meta').textContent, /用户.*REG_EXPAND_SZ（示例）/);
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
check('Windows table highlights prepared schemes and shares the same editor', () => {
  q('[data-view="windows"]').click(); const el = q('[data-windows-value="User|PATH"]');
  assert.equal(el.textContent, raw()); assert.equal(el.querySelectorAll('mark').length, 9);
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
  assert.equal(q('[data-windows-value="User|PATH"]').textContent, expected);
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
  assert.equal(all('.scheme-heading').length, 2);
  assert.equal(q('.scheme-name').textContent, '3.9.11');
  assert.equal(q('.group-meta').textContent, '2 项变量');
  assert.match(q('.scheme-flags').textContent, /默认方案.*当前编辑/);
});
check('Shared component and additional stylesheet are local; no UI runtime errors', () => {
  assert.deepEqual(errors, []);
  for (const name of ['windows-elements.js', 'elements-ui.css']) assert.ok(fs.existsSync(path.join(root, name)));
  assert.match(html, /src="windows-elements\.js"/);
  assert.match(html, /href="elements-ui\.css"/);
});
const report = { verification: 'JSDOM UI event checks only; native dragging, visual rendering and Windows integration not verified', checks };
fs.writeFileSync(path.join(__dirname, 'elements-report.json'), JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify(report, null, 2)); w.close();

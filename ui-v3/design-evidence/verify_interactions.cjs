/* UI event contracts only; no rendered browser or real environment operations. */
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const { JSDOM, VirtualConsole } = require('jsdom');
const root = path.resolve(__dirname, '..');
const errors = [], checks = [], vc = new VirtualConsole();
vc.on('jsdomError', e => errors.push(e.message));
const dom = new JSDOM(fs.readFileSync(path.join(root, 'temp.html'), 'utf8'), {
  runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole: vc,
  beforeParse(w) { w.eval(fs.readFileSync(path.join(root,'demo-configs.js'),'utf8')); w.eval(fs.readFileSync(path.join(root, 'windows-elements.js'), 'utf8')); }
});
const w = dom.window, doc = w.document;
const q = s => doc.querySelector(s), all = s => [...doc.querySelectorAll(s)];
const check = (name, fn) => { fn(); checks.push({ name, passed: true }); };
const key = (el, k, extra = {}) => el.dispatchEvent(new w.KeyboardEvent('keydown', {key:k, bubbles:true, cancelable:true, ...extra}));
const change = (el, val) => { el.value = val; el.dispatchEvent(new w.Event('change',{bubbles:true})); };
const input = (el, val) => { el.value = val; el.dispatchEvent(new w.Event('input',{bubbles:true})); };
const snapshot = () => w.eval('JSON.stringify(windows)');
const configs = () => w.eval('JSON.stringify(apps)');
check('Keyboard application selection preserves focus and current semantics', () => {
  const el=q('[data-app="0"]'); el.focus(); key(el,'Enter');
  assert.equal(doc.activeElement.dataset.appSelect,'0');
  assert.equal(doc.activeElement.getAttribute('aria-pressed'),'true');
  assert.equal(q('.app-title').textContent,'Java');
});
check('Batch checkbox refresh returns focus to that checkbox', () => {
  const el=q('[data-check="0"]'); el.focus();el.checked=true;el.dispatchEvent(new w.Event('change',{bubbles:true}));
  assert.equal(doc.activeElement.dataset.check,'0');assert.equal(doc.activeElement.checked,true);
});
check('Running source selector refresh retains focus and original input draft', () => {
  change(q('#varRows .source'),'自定义值'); assert.equal(doc.activeElement.className,'source');
  input(q('#varRows .vinput'),'  literal runtime draft  ');
});
check('Element restore preserves original whitespace, empty and duplicate rows without applying', () => {
  w.eval("windows['User|PATH']=' A ;;A;B;' ; focusedVariable=1; renderRows()");
  const before=snapshot(), beforeConfig=configs();q('#editWindowsElements').click();
  input(q('.element-input'),'different');q('[data-move-down]').click();q('[data-delete]').click();q('#addElement').click();
  assert.equal(q('#restoreElements').disabled,false);q('#restoreElements').click();
  assert.deepEqual(all('.element-input').map(el=>el.value),[' A ','','A','B','']);
  assert.equal(q('#applyElements').disabled,true);assert.equal(q('#restoreElements').disabled,true);
  assert.equal(snapshot(),before);assert.equal(configs(),beforeConfig);q('#cancelDialog').click();
});
check('Delete last row focuses Add and Tab remains trapped', () => {
  q('#editWindowsElements').click();while(q('[data-delete]'))q('[data-delete]').click();
  assert.equal(doc.activeElement.id,'addElement');
  q('#applyElements').focus();key(doc.activeElement,'Tab');assert.equal(doc.activeElement.id,'closeDialog');
  doc.body.focus(); q('#modal').focus();
  // Simulate focus loss caused by DOM removal, then recover at next Tab.
  const active=doc.activeElement;active.blur();key(doc.body,'Tab');
  assert.equal(doc.activeElement.id,'closeDialog');q('#cancelDialog').click();
});
check('Page search/export shortcuts do not divert focus while an editor is open', () => {
  q('#editWindowsElements').click();q('.element-input').focus();const before=doc.activeElement;
  assert.equal(key(before,'f',{ctrlKey:true}),false);assert.equal(doc.activeElement,before);
  assert.equal(key(before,'s',{ctrlKey:true}),false);assert.equal(doc.activeElement,before);q('#cancelDialog').click();
});
check('Unchanged missing value starts with disabled apply; restore returns to missing baseline', () => {
  w.eval("openWindowsElements('User','ABSENT_UI',[],false)");
  assert.equal(q('#applyElements').disabled,true);q('#addElement').click();input(q('.element-input'),'new');
  q('#restoreElements').click();assert.equal(all('.element-input').length,0);
  assert.equal(doc.activeElement.id,'addElement');assert.equal(q('#applyElements').disabled,true);q('#cancelDialog').click();
});
check('Configuration navigation and All selector preserve their focus and current semantics', () => {
  q('#configBtn').click();q('[data-editapp="2"]').click();assert.equal(doc.activeElement.dataset.editapp,'2');
  assert.equal(doc.activeElement.getAttribute('aria-current'),'true');
  q('[data-editplan="3.3.3"]').click();assert.equal(doc.activeElement.dataset.editplan,'3.3.3');
  change(q('#editorView'),'all');assert.equal(doc.activeElement.id,'editorView');
  const row=q('[data-tableplan="3.9.11"][data-tablevar="1"]');row.focus();key(row,' ');
  assert.equal(doc.activeElement.dataset.tableplan,'3.9.11');assert.equal(doc.activeElement.dataset.tablevar,'1');
  assert.equal(doc.activeElement.getAttribute('aria-current'),'true');
});
check('All four config properties update draft count and retain field focus', () => {
  change(q('#cfgScope'),'System');assert.equal(doc.activeElement.id,'cfgScope');
  change(q('#cfgMode'),'-append');assert.equal(doc.activeElement.id,'cfgMode');
  q('#cfgSpecial').checked=true;q('#cfgSpecial').dispatchEvent(new w.Event('change',{bubbles:true}));
  assert.equal(doc.activeElement.id,'cfgSpecial');assert.equal(q('#cfgMode').value,'replace');
  input(q('#cfgDefault'),'  new config default  ');
  assert.match(q('#configStatus').textContent,/4 项属性修改/);
  assert.equal(q('#configDraftBadge').hidden,false);assert.equal(q('#cancelConfig').disabled,false);
});
check('Diff shows all edited properties in the correct scheme; export reflects them', () => {
  q('[data-edtab="diff"]').click();assert.equal(doc.activeElement.dataset.edtab,'diff');
  const text=q('#sourceText').textContent;
  for(const label of ['默认值','作用域','模式','整体处理'])assert.ok(text.includes('Maven / 3.9.11 / Path · '+label));
  assert.ok(!text.includes('Maven / 3.3.3 / Path'));assert.match(text,/User/);assert.match(text,/System/);
  q('[data-edtab="raw"]').click();assert.match(q('#sourceText').textContent,/special = true/);
});
check('Restore config clears all four properties while preserving runtime draft and Windows snapshot', () => {
  const before=snapshot();q('#cancelConfig').click();
  assert.equal(snapshot(),before);assert.equal(q('#cancelConfig').disabled,true);
  assert.equal(q('#configDraftBadge').hidden,true);assert.equal(w.eval('configChanges().length'),0);
  assert.equal(w.eval("apps[0].profiles[0].vars[0].custom"),'  literal runtime draft  ');
  assert.equal(doc.activeElement.id,'downloadToml');
  q('[data-edtab="visual"]').click();assert.equal(q('#cfgScope').value,'User');
  assert.equal(q('#cfgMode').value,'+append');assert.equal(q('#cfgSpecial').checked,false);
});
check('Restoring one changed value manually clears its draft count', () => {
  const baseline=q('#cfgDefault').value;input(q('#cfgDefault'),'change once');
  assert.match(q('#configStatus').textContent,/1 项属性修改/);input(q('#cfgDefault'),baseline);
  assert.equal(q('#cancelConfig').disabled,true);assert.equal(q('#configDraftBadge').hidden,true);
});
check('No runtime errors in the revised UI event workflows', () => assert.deepEqual(errors,[]));
const report={verification:'JSDOM event contracts only; browser visual rendering, native dragging and real Windows operations not tested',checks};
fs.writeFileSync(path.join(__dirname,'interaction-report.json'),JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify(report,null,2));w.close();

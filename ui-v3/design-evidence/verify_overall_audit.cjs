/* Additional JSDOM contracts for workbench scope, focus and preview semantics. */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { JSDOM, VirtualConsole } = require('jsdom');

const root = path.resolve(__dirname, '..');
const errors = [], checks = [], virtualConsole = new VirtualConsole();
virtualConsole.on('jsdomError', error => errors.push(error.message));
const dom = new JSDOM(fs.readFileSync(path.join(root, 'temp.html'), 'utf8'), {
  runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole,
  beforeParse(window) {
    window.eval(fs.readFileSync(path.join(__dirname,'../demo-plan.js'),'utf8'));
    window.confirm = () => true;
    window.eval(fs.readFileSync(path.join(root, 'demo-configs.js'), 'utf8'));
    window.eval(fs.readFileSync(path.join(root, 'windows-elements.js'), 'utf8'));
  }
});
const w = dom.window, doc = w.document;
const q = selector => doc.querySelector(selector);
const all = selector => [...doc.querySelectorAll(selector)];
const reset = () => { w.WindowsElements.dismiss(); q('#modal').classList.remove('open'); w.eval('reset(true)'); };
const input = (element, value) => { element.value = value; element.dispatchEvent(new w.Event('input', { bubbles: true })); };
const change = (element, value) => { element.value = value; element.dispatchEvent(new w.Event('change', { bubbles: true })); };
const key = (element, name, extra = {}) => element.dispatchEvent(new w.KeyboardEvent('keydown', { key: name, bubbles: true, cancelable: true, ...extra }));
const appIndex = name => w.eval(`apps.findIndex(a => a.name === ${JSON.stringify(name)})`);
const selectApp = name => { q(`[data-app="${appIndex(name)}"]`).click(); };
const previewRow = name => all('.diffrow').find(row => row.querySelector('.k').textContent.includes(name));
const pairText = (row, kind) => row.querySelector(`.pair.${kind} span:last-child`).textContent;
const check = (name, verify) => {
  try { reset(); verify(); checks.push({ name, passed: true }); }
  catch (error) { checks.push({ name, passed: false, error: error.message }); }
};

try {
  check('Search-scoped Select All chooses only visible applications', () => {
    input(q('#appSearch'), 'Java');
    assert.equal(all('[data-app]').length, 1);
    assert.match(q('#selectAll').textContent, /全选可见/);
    q('#selectAll').click();
    assert.deepEqual([...w.eval('checked')], [appIndex('Java')]);
    q('#previewButton').click();
    assert.match(q('#dialogTitle').textContent, /批量/);
    assert.equal(all('.diffrow').length, 2);
    assert.ok(all('.diffrow').every(row => row.querySelector('.k').textContent.includes('Java')));
  });

  check('Filtered Select All preserves earlier hidden selection and discloses hidden count', () => {
    const node = appIndex('Node.js');
    q(`[data-check="${node}"]`).click();
    input(q('#appSearch'), 'Java');
    q('#selectAll').click();
    assert.deepEqual(new Set(w.eval('checked')), new Set([node, appIndex('Java')]));
    assert.match(q('#selectionCounter').textContent, /1 个在当前筛选中隐藏/);
    q('#previewButton').click();
    assert.ok(all('.diffrow').some(row => row.querySelector('.k').textContent.includes('Java')));
    assert.ok(all('.diffrow').some(row => row.querySelector('.k').textContent.includes('Node.js')));
  });

  check('Status filter selects only visible unmatched applications and exposes pressed state', () => {
    q('[data-filter="unmatched"]').click();
    assert.equal(q('[data-filter="unmatched"]').getAttribute('aria-pressed'), 'true');
    assert.equal(q('[data-filter="all"]').getAttribute('aria-pressed'), 'false');
    const visible = all('[data-app]').map(row => +row.dataset.app);
    assert.ok(visible.length > 0 && visible.length < w.eval('apps.length'));
    q('#selectAll').click();
    assert.deepEqual([...w.eval('checked')].sort((a,b)=>a-b), visible.sort((a,b)=>a-b));
  });

  check('Only-mismatch retains an active input during typing, then filters it on committed change', () => {
    w.eval('selected=2;profileChoice[2]="3.3.3";renderWorkbench()');
    q('#onlyMismatch').click();
    const field = q('#varRows [data-row="0"] .vinput');
    field.focus();
    input(field, w.eval("windows['User|MAVEN_HOME']"));
    assert.match(q('#varRows [data-row="0"] .result').textContent, /匹配/);
    assert.equal(doc.activeElement, field);
    assert.ok(q('#varRows [data-row="0"]'));
    field.dispatchEvent(new w.Event('change', { bubbles: true }));
    assert.equal(q('#varRows [data-row="0"]'), null);
    assert.equal(doc.activeElement, q('#onlyMismatch'));
  });

  check('Source change that resolves mismatch filters the row and restores focus to filter control', () => {
    selectApp('Node.js');
    change(q('#varRows [data-row="0"] .source'), '候选值 2');
    q('#onlyMismatch').click();
    const source = q('#varRows [data-row="0"] .source');
    assert.ok(source);
    change(source, '默认值');
    assert.equal(q('#varRows [data-row="0"]'), null);
    assert.equal(doc.activeElement, q('#onlyMismatch'));
  });

  check('Focusing a source selector aligns Windows comparison with that variable', () => {
    const second = q('#varRows [data-row="1"] .source');
    second.focus();
    assert.equal(doc.activeElement, second);
    assert.match(q('#inspectTitle').textContent, /Path/);
  });

  check('Plan and revoke menus support arrows, endpoints and Escape focus restoration', () => {
    const plan = q('#planButton'); plan.click();
    assert.equal(plan.getAttribute('aria-expanded'), 'true');
    assert.equal(doc.activeElement, all('#planMenu [data-plan]')[0]);
    key(doc.activeElement, 'End');
    assert.equal(doc.activeElement, all('#planMenu [data-plan]').at(-1));
    key(doc.activeElement, 'Home');
    assert.equal(doc.activeElement, all('#planMenu [data-plan]')[0]);
    key(doc.activeElement, 'Escape');
    assert.equal(doc.activeElement, plan);
    assert.equal(plan.getAttribute('aria-expanded'), 'false');

    const revoke = q('#revokeButton'); revoke.click();
    assert.equal(revoke.getAttribute('aria-expanded'), 'true');
    assert.equal(doc.activeElement, all('#revokeMenu [data-revoke]')[0]);
    key(doc.activeElement, 'ArrowDown');
    assert.equal(doc.activeElement, all('#revokeMenu [data-revoke]')[1]);
    key(doc.activeElement, 'End');
    assert.equal(doc.activeElement, all('#revokeMenu [data-revoke]').at(-1));
    key(doc.activeElement, 'Escape');
    assert.equal(doc.activeElement, revoke);
    assert.equal(revoke.getAttribute('aria-expanded'), 'false');
  });

  check('Closing an open revoke menu by outside click returns focus to its trigger', () => {
    const revoke = q('#revokeButton'); revoke.click();
    assert.equal(doc.activeElement, all('#revokeMenu [data-revoke]')[0]);
    doc.body.click();
    assert.equal(q('#revokeMenu').classList.contains('show'), false);
    assert.equal(revoke.getAttribute('aria-expanded'), 'false');
    assert.equal(doc.activeElement, revoke);
  });

  check('Navigation keeps focus and current-page semantics through view changes', () => {
    const windowsTab = q('[data-view="windows"]'); windowsTab.click();
    assert.equal(doc.activeElement, windowsTab);
    assert.equal(windowsTab.getAttribute('aria-current'), 'page');
    q('#toWork').click();
    const workTab = q('[data-view="workbench"]');
    assert.equal(doc.activeElement, workTab);
    assert.equal(workTab.getAttribute('aria-current'), 'page');
    q('#configBtn').click();
    assert.equal(doc.activeElement, q('#configBtn'));
    assert.equal(q('#configBtn').getAttribute('aria-current'), 'page');
  });

  check('Configuration inspector labels all disabled fields and explains default-source linkage', () => {
    q('#configBtn').click();
    for (const field of all('#editorInspector input:disabled')) {
      assert.ok(field.id, 'Disabled field has an ID');
      assert.ok(q(`#editorInspector label[for="${field.id}"]`), `Disabled field ${field.id} has a label`);
    }
    const guidance = q('#editorInspector').textContent;
    assert.match(guidance, /工作台/);
    assert.match(guidance, /默认值/);
    assert.match(guidance, /其他来源/);
  });

  check('Configuration default edit follows default source while saved source remains selected', () => {
    selectApp('Node.js');
    change(q('#varRows [data-row="0"] .source'), '已保存值');
    const saved = q('#varRows [data-row="0"] .vinput').value;
    q('#configBtn').click();
    q(`[data-editapp="${appIndex('Node.js')}"]`).click();
    input(q('#cfgDefault'), 'C:\\New Demo Node');
    q('[data-view="workbench"]').click();
    assert.equal(q('#varRows [data-row="0"] .source').value, '已保存值');
    assert.equal(q('#varRows [data-row="0"] .vinput').value, saved);
    change(q('#varRows [data-row="0"] .source'), '默认值');
    assert.equal(q('#varRows [data-row="0"] .vinput').value, 'C:\\New Demo Node');
  });

  check('An intentionally empty custom value survives switching to default source and back', () => {
    const row = '#varRows [data-row="1"]';
    input(q(`${row} .vinput`), '');
    assert.equal(w.eval('curProfile().vars[1].custom'), '');
    assert.equal(q(`${row} .source`).value, '自定义值');
    change(q(`${row} .source`), '默认值');
    assert.equal(q(`${row} .vinput`).value, w.eval('curProfile().vars[1].defaultValue'));
    change(q(`${row} .source`), '自定义值');
    assert.equal(q(`${row} .vinput`).value, '');
    assert.equal(w.eval('curProfile().vars[1].custom'), '');
  });

  check('Row revoke action is explicitly identified as a preview', () => {
    assert.match(q('#varRows [data-row="0"] [data-undo]').textContent, /撤销预览/);
    assert.match(q('#revokeButton').getAttribute('title') || q('#revokeMenu').textContent, /日志|历史|预览/);
  });

  check('Matched raw list element with whitespace remains byte-for-byte unchanged on preview and confirm', () => {
    const raw = ' %MAVEN_HOME%\\bin ';
    w.eval(`windows['User|PATH']=${JSON.stringify(raw)};renderWorkbench()`);
    assert.match(q('#varRows [data-row="1"] .result').textContent, /✓ 匹配/);
    q('#previewButton').click();
    const row = previewRow('Path');
    assert.equal(pairText(row, 'del'), raw);
    assert.equal(pairText(row, 'add'), raw);
    const historyBefore = w.eval('history.length');
    q('#confirmDialog').click();
    assert.equal(w.eval("windows['User|PATH']"), raw);
    assert.equal(w.eval('history.length'), historyBefore);
  });

  check('Adding missing front elements preserves old blanks, duplicates and order in preview, snapshot and history', () => {
    const raw = 'X;;X;';
    w.eval(`windows['User|PATH']=${JSON.stringify(raw)};curProfile().vars[1].source='自定义值';curProfile().vars[1].custom='A;B';renderWorkbench()`);
    q('#previewButton').click();
    const row = previewRow('Path'), expected = 'A;B;X;;X;';
    assert.equal(pairText(row, 'del'), raw);
    assert.equal(pairText(row, 'add'), expected);
    q('#confirmDialog').click();
    assert.equal(w.eval("windows['User|PATH']"), expected);
    const change = w.eval('history[0].changes.find(c=>c.name==="Path")');
    assert.equal(change.before, raw);
    assert.equal(change.after, expected);
  });

  check('Adding a missing tail element preserves matched whitespace and raw empty slices', () => {
    selectApp('Python');
    const raw = ' %PYTHON_HOME% ;X;;';
    w.eval(`windows['User|PATH']=${JSON.stringify(raw)};renderWorkbench()`);
    q('#previewButton').click();
    const row = previewRow('Path');
    // The original trailing ;; represents two retained empty elements.
    assert.equal(pairText(row, 'add'), raw + ';%PYTHON_HOME%\\Scripts');
    q('#confirmDialog').click();
    assert.equal(w.eval("windows['User|PATH']"), raw + ';%PYTHON_HOME%\\Scripts');
  });

  check('Undefined and present-empty actual lists remain distinct in append preview', () => {
    w.eval("curProfile().vars[1].source='自定义值';curProfile().vars[1].custom='B';delete windows['User|PATH'];renderWorkbench()");
    q('#previewButton').click();
    let row = previewRow('Path');
    assert.equal(pairText(row, 'del'), '（不存在）');
    assert.equal(pairText(row, 'add'), 'B');
    q('#cancelDialog').click();
    assert.equal(w.eval("windows['User|PATH']"), undefined);

    w.eval("windows['User|PATH']='';renderWorkbench()");
    q('#previewButton').click();
    row = previewRow('Path');
    assert.equal(pairText(row, 'del'), '');
    assert.equal(pairText(row, 'add'), 'B;');
    q('#confirmDialog').click();
    assert.equal(w.eval("windows['User|PATH']"), 'B;');
  });

  check('Repeated desired slices add each missing normalized element once and preserve first raw spelling', () => {
    const raw = 'X;;X;', desired = 'A;A; B ;B';
    w.eval(`windows['User|PATH']=${JSON.stringify(raw)};curProfile().vars[1].source='自定义值';curProfile().vars[1].custom=${JSON.stringify(desired)};renderWorkbench()`);
    q('#previewButton').click();
    const row = previewRow('Path');
    assert.equal(pairText(row, 'add'), 'A; B ;X;;X;');
    q('#confirmDialog').click();
    assert.equal(w.eval("windows['User|PATH']"), 'A; B ;X;;X;');
  });

  check('Native app selection button and batch checkbox are sibling controls with independent actions', () => {
    const row = q('[data-app="0"]'), select = row.querySelector('[data-app-select]'), checkbox = row.querySelector('[data-check]');
    assert.equal(row.getAttribute('role'), 'group');
    assert.equal(row.getAttribute('tabindex'), '-1');
    assert.equal(select.tagName, 'BUTTON');
    assert.equal(checkbox.tagName, 'INPUT');
    assert.equal(select.parentElement, checkbox.parentElement);
    checkbox.click();
    assert.equal(w.eval('selected'), 2);
    assert.equal(w.eval('checked.has(0)'), true);
    q('[data-app-select="0"]').click();
    assert.equal(w.eval('selected'), 0);
    assert.equal(w.eval('checked.has(0)'), true);
    assert.equal(doc.activeElement, q('[data-app-select="0"]'));
    assert.equal(doc.activeElement.getAttribute('aria-pressed'), 'true');
  });

  check('Cancelling a prepared preview leaves drafts, Windows snapshot and history exact', () => {
    input(q('#varRows [data-row="0"] .vinput'), 'C:\\Preview Only');
    const before = w.eval('JSON.stringify({apps,windows,history,revision})');
    q('#previewButton').focus();
    q('#previewButton').click();
    assert.equal(q('#modal').classList.contains('open'), true);
    q('#cancelDialog').click();
    assert.equal(q('#modal').classList.contains('open'), false);
    assert.equal(w.eval('JSON.stringify({apps,windows,history,revision})'), before);
    assert.equal(doc.activeElement, q('#previewButton'));
  });

  check('Append preview leaves matched System PATH exact and does not mutate User PATH', () => {
    selectApp('CUDA Toolkit');
    const systemBefore = w.eval("windows['System|PATH']"), userBefore = w.eval("windows['User|PATH']");
    assert.match(q('#varRows [data-row="1"] .result').textContent, /✓ 匹配/);
    q('#previewButton').click();
    const row = previewRow('Path');
    assert.equal(pairText(row, 'add'), systemBefore);
    q('#confirmDialog').click();
    assert.equal(w.eval("windows['System|PATH']"), systemBefore);
    assert.equal(w.eval("windows['User|PATH']"), userBefore);
  });

  check('Replace and whole values still produce their selected raw strings', () => {
    selectApp('Node.js');
    change(q('#varRows [data-row="0"] .source'), '候选值 2');
    q('#previewButton').click();
    const home = previewRow('NODE_HOME'), whole = previewRow('NODE_OPTIONS');
    assert.equal(pairText(home, 'add'), w.eval('value(curProfile().vars[0])'));
    assert.equal(pairText(whole, 'add'), w.eval('value(curProfile().vars[2])'));
  });

  check('Custom-order append remains blocked and Escape closes preview without mutating data', () => {
    selectApp('列表与空值');
    q('#planButton').click();
    all('#planMenu [data-plan]').find(button => button.dataset.plan === '自定义排序预览').click();
    const before = w.eval('JSON.stringify({windows,history})');
    q('#previewButton').click();
    assert.equal(q('#confirmDialog').disabled, true);
    assert.match(q('.banner.warn').textContent, /排序/);
    key(q('#closeDialog'), 'Escape');
    assert.equal(q('#modal').classList.contains('open'), false);
    assert.equal(w.eval('JSON.stringify({windows,history})'), before);
  });

  check('Demo reset clears selection, filters and configuration draft', () => {
    input(q('#appSearch'), 'Java'); q('#selectAll').click();
    q('[data-filter="unmatched"]').click();
    q('#configBtn').click(); input(q('#cfgDefault'), 'Changed config');
    q('#resetBtn').click();
    assert.equal(w.eval('checked.size'), 0);
    assert.equal(q('#appSearch').value, '');
    assert.equal(q('[data-filter="all"]').getAttribute('aria-pressed'), 'true');
    assert.equal(w.eval('configChanges().length'), 0);
  });

  check('Overall audit interactions emit no JSDOM runtime errors', () => assert.deepEqual(errors, []));

  const report = {
    verification: 'JSDOM interaction and data checks only; browser layout and real Windows operations not verified',
    total: checks.length,
    passed: checks.filter(item => item.passed).length,
    failed: checks.filter(item => !item.passed).length,
    checks
  };
  fs.writeFileSync(path.join(__dirname, 'overall-audit-report.json'), JSON.stringify(report, null, 2) + '\n');
  process.stdout.write(JSON.stringify(report, null, 2) + '\n');
  if (report.failed) process.exitCode = 1;
} finally { w.close(); }

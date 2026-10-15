/* V3.1 offline workflow acceptance: JSDOM only, no browser or Windows writes.
 * Run with NODE_PATH pointing to the existing ui-v3/node_modules.
 * This check prints its evidence and does not create or update report files.
 */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { JSDOM, VirtualConsole } = require('jsdom');
const root = path.resolve(__dirname, '..');
const checks = [], errors = [];
const virtualConsole = new VirtualConsole();
virtualConsole.on('jsdomError', error => errors.push(error.message));
const dom = new JSDOM(fs.readFileSync(path.join(root, 'temp.html'), 'utf8'), {
  runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole,
  beforeParse(window){ for(const configModule of ['config-format.js','config-editor.js'])window.eval(require('node:fs').readFileSync(require('node:path').resolve(__dirname,"..",configModule),'utf8'));
    window.confirm = () => true;
    for (const file of ['demo-plan.js', 'demo-configs.js', 'windows-elements.js'])
      window.eval(fs.readFileSync(path.join(root, file), 'utf8'));
  }
});
const w = dom.window, q = selector => w.document.querySelector(selector);
const all = selector => [...w.document.querySelectorAll(selector)];
const evaluate = code => w.eval(code);
const actual = () => evaluate('JSON.stringify(windows)');
const afterPane = () => all('#compareAfter .diff-value[data-raw-value]').filter(node => !node.closest('.comparison-token.is-ghost')).map(node => node.getAttribute('data-raw-value')).join(';');
const historySize = () => evaluate('history.length');
const pathRaw = () => evaluate("windows['User|PATH']");
const oldSelection = '%PYTHON_HOME%;%PYTHON_HOME%\\Scripts';
const newSelection = '%PYTHON_HOME%;%PYTHON_HOME%\\Scripts1';
const pathTarget = "[{app:1,profile:'Python313',varIdx:1}]";
function input(element, raw) {
  assert.ok(element, 'Expected an editable field');
  element.value = raw;
  element.dispatchEvent(new w.Event('input', { bubbles: true }));
}
function python() {
  evaluate('closeModal(); reset(true)');
  q('[data-app-select="1"]').click();
  q('#varRows [data-row="1"] .vinput').focus();
}
function editPython() {
  python();
  input(q('#varRows [data-row="1"] .vinput'), newSelection);
}
function previewPath() {
  evaluate(`showPreview('应用','Python Path 验证',${pathTarget})`);
  assert.equal(q('#confirmDialog').disabled, false);
}
function frozenTree(value) {
  if (!value || typeof value !== 'object') return;
  assert.equal(Object.isFrozen(value), true, 'Every nested plan/history object must be frozen');
  for (const child of Object.values(value)) frozenTree(child);
}
function planFor({ raw, desired, mode = '-append', special = false, scope = 'User' }) {
  return w.DemoPlan.build({ target: { scope, name: 'Path' }, mode, special,
    snapshot: { exists: raw !== undefined, raw: raw === undefined ? null : raw,
      type: 'REG_EXPAND_SZ', revision: 7 }, selectedRaw: desired });
}
function check(name, verify) {
  try { verify(); checks.push({ name, passed: true }); }
  catch (error) { checks.push({ name, passed: false, error: error.stack }); }
}
try {
  check('Python Path starts matched; ordinary Scripts1 input generates independent removal and append, never inferred replacement', () => {
    python();
    assert.match(q('#varRows [data-row="1"] .result').textContent, /匹配 2\/2/);
    const before = actual(), count = historySize();
    input(q('#varRows [data-row="1"] .vinput'), newSelection);
    assert.equal(actual(), before);
    assert.equal(historySize(), count);
    assert.match(q('#compareLabel').textContent, /选定值已修改/);
    assert.ok(q('#compareActual').textContent.includes('待删除'));
    assert.equal(all('#compareDesired .diff-value[data-raw-value]').map(node => node.getAttribute('data-raw-value')).join(';'), newSelection);
    assert.match(q('#varRows [data-row="1"] .result').textContent, /部分匹配 1\/2/);
    assert.match(q('#compareDesired').textContent, /Scripts1.*待追加/);
    assert.doesNotMatch(q('#compareActual').textContent, /待修改|将替换|替换/);
    assert.equal(q('#compareActual .pending-replacement'), null);
    assert.equal(afterPane(), evaluate('demoPlan(scheme(1).vars[1]).after.raw'));
    assert.equal(q('#compareSerialized').textContent, evaluate('demoPlan(scheme(1).vars[1]).after.exists ? demoPlan(scheme(1).vars[1]).after.raw : \"（不存在）\"'));
    assert.equal(q('#compareDesired .pending-add-token .diff-value').getAttribute('data-raw-value'), '%PYTHON_HOME%\\Scripts1');
  });
  check('Workbench cached plan is the exact object used by the first preview step; preview cancel preserves actual, saved state and history', () => {
    editPython();
    const plan = evaluate('demoPlan(scheme(1).vars[1])');
    frozenTree(plan);
    const before = actual(), count = historySize(), saved = evaluate('scheme(1).vars[1].savedValue');
    previewPath();
    assert.strictEqual(evaluate('modalAction.rows[0].plan'), plan);
    assert.strictEqual(evaluate('demoPlan(scheme(1).vars[1])'), plan);
    assert.equal(evaluate('modalAction.rows[0].after'), `${pathRaw().split(';').filter(x=>x!=='%PYTHON_HOME%\\Scripts').join(';')};%PYTHON_HOME%\\Scripts1`);
    q('#cancelDialog').click();
    assert.equal(actual(), before);
    assert.equal(historySize(), count);
    assert.equal(evaluate('scheme(1).vars[1].savedValue'), saved);
  });
  check('Confirm removes the matched Scripts, appends Scripts1, preserves unrelated segments and freezes history', () => {
    editPython();
    const before = pathRaw(), count = historySize();
    previewPath();
    const plan = evaluate('modalAction.rows[0].plan');
    assert.equal(plan.operations.length, 2);
    assert.equal(plan.operations[0].type, 'remove');
    assert.equal(plan.operations[0].value, '%PYTHON_HOME%\\Scripts');
    assert.equal(plan.operations[1].type, 'insert');
    assert.equal(plan.operations[1].value, '%PYTHON_HOME%\\Scripts1');
    assert.equal(plan.operations[1].position, 'tail');
    q('#confirmDialog').click();
    assert.equal(pathRaw(), `${before.split(';').filter(x=>x!=='%PYTHON_HOME%\\Scripts').join(';')};%PYTHON_HOME%\\Scripts1`);
    assert.equal(pathRaw().split(';').includes('%PYTHON_HOME%\\Scripts'), false);
    assert.equal(historySize(), count + 1);
    const event = evaluate('history[0]'), change = event.changes[0];
    frozenTree(event);
    assert.strictEqual(change.plan, plan);
    assert.strictEqual(change.operations, plan.operations);
    assert.equal(change.before, before);
    assert.equal(change.after, pathRaw());
    assert.equal(evaluate('scheme(1).vars[1].savedValue'), newSelection);
    assert.throws(() => { change.plan.after.raw = 'forged'; }, TypeError);
    assert.throws(() => { change.operations.push({ type: 'replace' }); }, { name: 'TypeError' });
    const persisted = JSON.stringify(event);
    input(q('#varRows [data-row="1"] .vinput'), '%PYTHON_HOME%\\Scripts2');
    q('#editWindowsElements').click();
    input(q('#elementList .element-input'), 'EDITED_LATER');
    q('#applyElements').click();
    assert.equal(JSON.stringify(event), persisted, 'Later selected-value or Windows-row edits must not rewrite history');
    q('[data-view="history"]').click();
    assert.match(q('#screen').textContent, /历史/);
  });
  check('An explicit duplicate Windows row replacement records its stable ID and frozen audit, without replacing its equal-valued neighbor', () => {
    python();
    evaluate("windows['User|ID_TEST']='A;A;B'; openWindowsElements('User','ID_TEST')");
    const rows = all('#elementList .element-row'), id = Number(rows[1].dataset.elementId);
    const originalId = Number(rows[0].dataset.elementId), count = historySize();
    input(rows[1].querySelector('.element-input'), 'C');
    assert.equal(evaluate("windows['User|ID_TEST']"), 'A;A;B');
    assert.ok(q(`#elementOriginal [data-element-id="${id}"]`).classList.contains('is-replaced'));
    assert.equal(q(`#elementOriginal [data-element-id="${originalId}"]`).classList.contains('is-replaced'), false);
    q('#applyElements').click();
    assert.equal(evaluate("windows['User|ID_TEST']"), 'A;C;B');
    assert.equal(historySize(), count + 1);
    const change = evaluate('history[0].changes[0]');
    frozenTree(change);
    assert.equal(change.plan.source, 'windows-element-edit');
    assert.equal(change.operations.length, 1);
    assert.equal(change.operations[0].elementId, id);
    assert.equal(change.operations[0].type, 'replace');
    assert.equal(change.operations[0].before, 'A');
    assert.equal(change.operations[0].after, 'C');
    assert.equal(change.elements.find(item => item.elementId === id).replaced, true);
    assert.strictEqual(change.elements, change.plan.audit.elements);
    assert.strictEqual(change.operations, change.plan.operations);
    const persisted = JSON.stringify(change);
    evaluate("openWindowsElements('User','ID_TEST')");
    input(q('#elementList .element-input'), 'Z');
    q('#applyElements').click();
    assert.equal(JSON.stringify(change), persisted);
  });
  check('Cancel after explicit element editing produces no mutation or history; no-change confirm produces no history', () => {
    python();
    const before = actual(), count = historySize();
    q('#editWindowsElements').click();
    assert.equal(q('#applyElements').disabled, true);
    input(q('#elementList .element-input'), 'cancelled');
    q('#cancelDialog').click();
    assert.equal(actual(), before);
    assert.equal(historySize(), count);
    previewPath();
    assert.equal(evaluate('modalAction.rows[0].plan.operations.length'), 0);
    q('#confirmDialog').click();
    assert.equal(actual(), before);
    assert.equal(historySize(), count);
  });
  const staleCases = [
    ['same-target raw changes', "windows['User|PATH'] += ';EXTERNAL'"],
    ['same-target existence changes', "delete windows['User|PATH']"],
    ['same-target registry type changes', "targetTypes['User|PATH']='REG_SZ'"],
    ['same-target ABA raw with revision changes', "const rawBeforeABA=windows['User|PATH']; windows['User|PATH']='TEMP'; windows['User|PATH']=rawBeforeABA; targetRevisions['User|PATH']=(targetRevisions['User|PATH']||0)+2"],
    ['selected raw changes', "setSelectedValue(scheme(1).vars[1],'CHANGED_AFTER_PREVIEW')"],
    ['selected mode changes', "scheme(1).vars[1].mode='replace'"],
    ['selected scope changes', "scheme(1).vars[1].scope='System'"],
    ['selected special flag changes', "scheme(1).vars[1].special=true"]
  ];
  for (const [name, mutation] of staleCases) check(`Stale preview rejects ${name}, preserving intervening state and history`, () => {
    editPython(); previewPath();
    const count = historySize();
    evaluate(mutation);
    const afterExternalChange = actual();
    q('#confirmDialog').click();
    assert.equal(actual(), afterExternalChange);
    assert.equal(historySize(), count);
    assert.match(q('#toast').textContent, /预览已失效/);
    assert.equal(q('#modal').classList.contains('open'), false);
  });
  check('Unrelated target/global revision changes do not invalidate a target-specific preview', () => {
    editPython(); const before = pathRaw(), count = historySize(); previewPath();
    evaluate("windows['System|UNRELATED']='external'; targetRevisions['System|UNRELATED']=91; revision+=10");
    q('#confirmDialog').click();
    assert.equal(pathRaw(), `${before.split(';').filter(x=>x!=='%PYTHON_HOME%\\Scripts').join(';')};%PYTHON_HOME%\\Scripts1`);
    assert.equal(evaluate("windows['System|UNRELATED']"), 'external');
    assert.equal(historySize(), count + 1);
  });
  check('An old explicit element plan also rejects ABA revision changes without history', () => {
    python(); q('#editWindowsElements').click();
    input(q('#elementList .element-input'), 'EDIT');
    evaluate("targetRevisions['User|PATH']=(targetRevisions['User|PATH']||0)+2");
    const before = actual(), count = historySize();
    q('#applyElements').click();
    assert.equal(actual(), before);
    assert.equal(historySize(), count);
    assert.match(q('#toast').textContent, /本次编辑已失效/);
  });
  check('Empty append is a no-op for both missing and existing-empty values, preserving their distinct existence states through confirmation', () => {
    for (const raw of [undefined, '']) {
      python();
      if (raw === undefined) evaluate("delete windows['User|PATH']");
      else evaluate("windows['User|PATH']=''");
      input(q('#varRows [data-row="1"] .vinput'), ' ;  ;');
      const before = actual(), count = historySize(); previewPath();
      const plan = evaluate('modalAction.rows[0].plan');
      assert.equal(plan.before.exists, raw !== undefined);
      assert.equal(plan.after.exists, raw !== undefined);
      assert.equal(plan.before.raw, raw === undefined ? null : '');
      assert.equal(plan.after.raw, plan.before.raw);
      assert.equal(plan.operations.length, 0);
      q('#confirmDialog').click();
      assert.equal(actual(), before);
      assert.equal(historySize(), count);
    }
  });
  check('Missing element editor cancel keeps the variable missing; explicitly adding an empty row creates an existing empty value', () => {
    python();
    const count = historySize();
    evaluate("openWindowsElements('User','MISSING_EMPTY_TEST')");
    assert.equal(all('#elementList .element-row').length, 0);
    assert.equal(q('#applyElements').disabled, true);
    q('#cancelDialog').click();
    assert.equal(evaluate("Object.hasOwn(windows,'User|MISSING_EMPTY_TEST')"), false);
    assert.equal(historySize(), count);
    evaluate("openWindowsElements('User','MISSING_EMPTY_TEST')");
    q('#addElement').click();
    assert.equal(q('#elementList .element-input').value, '');
    assert.equal(q('#applyElements').disabled, false);
    q('#applyElements').click();
    assert.equal(evaluate("Object.hasOwn(windows,'User|MISSING_EMPTY_TEST')"), true);
    assert.equal(evaluate("windows['User|MISSING_EMPTY_TEST']"), '');
    assert.equal(historySize(), count + 1);
    const change = evaluate('history[0].changes[0]');
    assert.equal(change.plan.before.exists, false);
    assert.equal(change.plan.before.raw, null);
    assert.equal(change.plan.after.exists, true);
    assert.equal(change.plan.after.raw, '');
    assert.equal(change.operations[0].type, 'add');
    frozenTree(change);
  });
  check('Append preserves original whitespace, empty fields, duplicate actuals and order; normalizes only membership and inserts each missing value once', () => {
    const raw = ' A ;A;;B; ', desired = 'A; A ; C ;C;c;;';
    const plan = planFor({ raw, desired });
    assert.equal(plan.after.raw, `${raw}; C ;c`);
    assert.equal(plan.matchResult.totalCount, 5);
    assert.equal(plan.matchResult.matchedCount, 2);
    assert.equal(plan.operations.length, 2);
    assert.equal(plan.operations[0].value, ' C ');
    assert.equal(plan.operations[1].value, 'c');
    assert.equal(planFor({ raw, desired: 'A;A;B' }).after.raw, raw);
    assert.equal(planFor({ raw, desired: 'A;A;B' }).operations.length, 0);
    assert.equal(planFor({ raw, desired, mode: '+append' }).after.raw, ` C ;c;${raw}`);
  });
  check('Repeated missing selection counts 0/3 but projects one actual append operation and one label in each workbench representation', () => {
    python();
    evaluate("windows['User|PATH']='A'");
    input(q('#varRows [data-row="1"] .vinput'), 'B; B ;B');
    const plan = evaluate('demoPlan(scheme(1).vars[1])');
    assert.equal(plan.operations.length, 1);
    assert.equal(plan.operations[0].type, 'insert');
    assert.equal(plan.operations[0].value, 'B');
    assert.equal(plan.operations[0].selectedIndex, 0);
    assert.equal(plan.matchResult.totalCount, 3);
    assert.equal(plan.matchResult.matchedCount, 0);
    assert.match(q('#varRows [data-row="1"] .result').textContent, /0\/3/);
    assert.equal(all('#compareDesired .pending-add-token').length, 1);
    assert.equal(all('#compareDesired .is-pending-add').length, 1);
    assert.equal(all('#compareDesired .diff-value[data-raw-value]').map(node => node.getAttribute('data-raw-value')).join(';'), 'B; B ;B');
    assert.equal(plan.after.raw, 'A;B');
    assert.equal(pathRaw(), 'A');
  });
  check('User/System scopes are isolated, names preserve display case and lookup uses canonical name', () => {
    python();
    evaluate("windows['System|PATH']='SYSTEM_ONLY'");
    input(q('#varRows [data-row="1"] .vinput'), newSelection);
    previewPath(); q('#confirmDialog').click();
    assert.equal(evaluate("windows['System|PATH']"), 'SYSTEM_ONLY');
    const plan = evaluate('history[0].changes[0].plan');
    assert.equal(plan.targetKey.scope, 'User');
    assert.equal(plan.targetKey.name, 'Path');
    assert.equal(plan.targetKey.canonicalName, 'PATH');
    const systemPlan = planFor({ raw: 'SYSTEM_ONLY', desired: 'NEXT', scope: 'System' });
    assert.equal(systemPlan.targetKey.scope, 'System');
    assert.equal(systemPlan.after.raw, 'SYSTEM_ONLY;NEXT');
  });
  check('Special strings keep semicolons as one exact value, and whole replacements do not invent element identities', () => {
    const plan = planFor({ raw: 'A;B;; ', desired: ' A;B;; ', special: true });
    assert.equal(plan.matchResult.totalCount, 1);
    assert.equal(plan.matchResult.satisfied, false);
    assert.equal(plan.after.raw, ' A;B;; ');
    assert.equal(plan.operations.length, 1);
    assert.equal(plan.operations[0].type, 'replace');
    assert.equal(plan.operations[0].whole, true);
    assert.equal(plan.operations[0].elementId, undefined);
    assert.equal(planFor({ raw: '', desired: '', special: true }).operations.length, 0);
    assert.equal(planFor({ raw: undefined, desired: '', mode: 'replace' }).after.exists, true);
    python();
    evaluate("windows['User|SPECIAL_TEST']='A;A;;'; openWindowsElements('User','SPECIAL_TEST',[],true)");
    assert.equal(all('#elementList .element-input').length, 1);
    input(q('#elementList .element-input'), 'B;B;;');
    q('#applyElements').click();
    assert.equal(evaluate("windows['User|SPECIAL_TEST']"), 'B;B;;');
    assert.equal(evaluate('history[0].changes[0].plan.special'), true);
  });
  check('Custom-order append remains blocked and closing its preview adds no history', () => {
    editPython(); evaluate("scheme(1).vars[1].mode='append'");
    const before = actual(), count = historySize();
    evaluate(`showPreview('应用','排序确认边界',${pathTarget})`);
    assert.equal(q('#confirmDialog').disabled, true);
    evaluate('confirmPreview()');
    assert.equal(actual(), before);
    assert.equal(historySize(), count);
    q('#closeDialog').click();
    assert.equal(historySize(), count);
  });
  check('No JSDOM runtime errors occurred', () => assert.deepEqual(errors, []));
} finally { w.close(); }
const report = { verification: 'JSDOM offline workflow contracts; no visual rendering or Windows Registry execution',
  passed: checks.filter(check => check.passed).length, total: checks.length, checks };
console.log(JSON.stringify(report, null, 2));
if (checks.some(check => !check.passed)) process.exitCode = 1;

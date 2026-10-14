/* JSDOM event and semantic DOM contracts only. No browser or Windows operations. */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { JSDOM, VirtualConsole } = require('jsdom');
const root = path.resolve(__dirname, '..');
const errors = [], checks = [];
const console = new VirtualConsole();
console.on('jsdomError', error => errors.push(error.message));
const dom = new JSDOM(fs.readFileSync(path.join(root, 'temp.html'), 'utf8'), {
  runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole: console,
  beforeParse(window) {
    window.eval(fs.readFileSync(path.join(__dirname,'../demo-plan.js'),'utf8'));
    window.confirm = () => true;
    window.eval(fs.readFileSync(path.join(root, 'demo-configs.js'), 'utf8'));
    window.eval(fs.readFileSync(path.join(root, 'windows-elements.js'), 'utf8'));
  }
});
const w = dom.window, doc = w.document;
const q = selector => doc.querySelector(selector);
const all = selector => Array.from(doc.querySelectorAll(selector));
const check = (name, verify) => { verify(); checks.push({ name, passed: true }); };
const input = (element, value) => {
  assert.ok(element, 'Input must exist');
  element.value = value;
  element.dispatchEvent(new w.Event('input', { bubbles: true }));
};
const change = (element, value) => {
  element.value = value;
  element.dispatchEvent(new w.Event('change', { bubbles: true }));
};
const reset = () => w.eval('reset(true)');
const runningRow = () => q('#varRows [data-row="0"]');
const runningInput = () => runningRow().querySelector('.vinput');
const runningSource = () => runningRow().querySelector('.source');
const selectedValue = () => w.eval('value(curProfile().vars[0])');
const pending = () => w.eval('dirty(curProfile())');
const elementRows = () => all('#elementList .element-row');
const rowId = row => row.dataset.elementId;
const row = id => q(`#elementList [data-element-id="${id}"]`);
const rowInput = id => row(id).querySelector('.element-input');
const values = () => elementRows().map(element => element.querySelector('.element-input').value);
const tokens = side => all(`#element${side} .diff-token`);
const token = (side, id) => q(`#element${side} .diff-token[data-element-id="${id}"]`);
const tokenValue = element => {
  assert.ok(element, 'Expected identity-preserving diff token');
  const value = element.querySelector('.diff-value');
  assert.ok(value, 'Diff token must separate value from annotation');
  return value.hasAttribute('data-raw-value') ? value.getAttribute('data-raw-value') : value.textContent;
};
const serialized = () => q('#elementSerialized').textContent;
const open = (raw, selected = [], whole = false) => {
  reset();
  w.__regressionRaw = raw;
  w.__regressionSelected = selected;
  w.__regressionWhole = whole;
  w.eval("windows['User|REGRESSION'] = __regressionRaw; openWindowsElements('User', 'REGRESSION', __regressionSelected, __regressionWhole)");
};
const snapshot = () => w.eval('JSON.stringify(windows)');
const cancel = () => q('#cancelDialog').click();

try {
  check('Runtime edit and default restore count exact string differences, including whitespace', () => {
    reset();
    const initial = selectedValue();
    assert.equal(pending(), 0);
    input(runningInput(), initial + ' ');
    assert.equal(pending(), 1);
    assert.ok(runningInput().classList.contains('modified'));
    change(runningSource(), '默认值');
    assert.equal(selectedValue(), initial);
    assert.equal(pending(), 0);
    assert.equal(runningInput().classList.contains('modified'), false);
  });
  check('Choosing a custom, saved or candidate source with the same value creates no pending change', () => {
    reset();
    const initial = selectedValue();
    change(runningSource(), '自定义值');
    assert.equal(selectedValue(), initial);
    assert.equal(pending(), 0);
    input(runningInput(), initial);
    assert.equal(pending(), 0);
    w.eval('curProfile().vars[0].savedValue = value(curProfile().vars[0]); curProfile().vars[0].choices = [value(curProfile().vars[0])]; renderRows()');
    change(runningSource(), '已保存值');
    assert.equal(pending(), 0);
    change(runningSource(), '候选值 1');
    assert.equal(selectedValue(), initial);
    assert.equal(pending(), 0);
  });
  check('Typing the original runtime value back removes pending styling and count', () => {
    reset();
    const initial = selectedValue();
    input(runningInput(), 'temporary runtime value');
    assert.equal(pending(), 1);
    input(runningInput(), initial);
    assert.equal(pending(), 0);
    assert.equal(runningInput().classList.contains('modified'), false);
  });
  check('Matching result stays independent from pending edits in both matching and mismatching states', () => {
    reset();
    input(runningInput(), 'different from demo Windows');
    assert.equal(pending(), 1);
    assert.match(runningRow().querySelector('.result').textContent, /不匹配/);
    w.eval('windows[key(curProfile().vars[0])] = value(curProfile().vars[0]); renderRows()');
    assert.equal(pending(), 1);
    const result = runningRow().querySelector('.result');
    assert.match(result.textContent, /匹配/);
    assert.doesNotMatch(result.textContent, /不匹配|草稿|待应用/);
    assert.equal(result.classList.contains('warn'), false);
  });
  check('Simulated application resets the runtime baseline to the applied string', () => {
    reset();
    const initial = selectedValue(), applied = initial + ' runtime changed';
    input(runningInput(), applied);
    assert.equal(pending(), 1);
    q('#previewButton').click();
    assert.equal(q('#confirmDialog').disabled, false);
    q('#confirmDialog').click();
    assert.equal(pending(), 0);
    assert.equal(w.eval('curProfile().vars[0].baselineValue'), applied);
    input(runningInput(), applied + ' ');
    assert.equal(pending(), 1);
    input(runningInput(), applied);
    assert.equal(pending(), 0);
    change(runningSource(), '默认值');
    assert.equal(selectedValue(), initial);
    assert.equal(pending(), 1);
  });
  check('Runtime pending copy uses pending-application language without draft wording', () => {
    reset();
    input(runningInput(), 'pending runtime replacement');
    const visibleCopy = ['#applist', '#overview', '#varTotal', '#compareResult', '#footer']
      .map(selector => q(selector).textContent).join(' ');
    assert.match(visibleCopy, /待应用/);
    assert.doesNotMatch(visibleCopy, /草稿/);
    assert.doesNotMatch(runningRow().querySelector('.result').textContent, /待应用/);
  });
  check('Matched elements carry the semantic match class; edits refresh that class independently', () => {
    open(' A ;B;C', ['A', 'C']);
    assert.deepEqual(elementRows().map(element => element.classList.contains('is-matched')), [true, false, true]);
    const id = rowId(elementRows()[1]);
    input(rowInput(id), 'C');
    assert.ok(row(id).classList.contains('is-matched'));
    assert.ok(row(id).classList.contains('is-replaced'));
    assert.match(row(id).querySelector('.element-change-state').textContent, /替换/);
    input(rowInput(id), 'B');
    assert.equal(row(id).classList.contains('is-matched'), false);
    assert.equal(row(id).classList.contains('is-replaced'), false);
    cancel();
  });
  check('Editing the second duplicate marks only its stable identity in both original and edited diff', () => {
    open('A;A;B', ['A']);
    const ids = elementRows().map(rowId);
    assert.equal(new Set(ids).size, 3);
    input(rowInput(ids[1]), 'A changed');
    for (const side of ['Original', 'Preview']) {
      assert.deepEqual(tokens(side).filter(element => element.classList.contains('is-replaced')).map(element => element.dataset.elementId), [ids[1]]);
      assert.equal(token(side, ids[0]).classList.contains('is-replaced'), false);
      assert.match(token(side, ids[1]).querySelector('.diff-label').textContent, /替换/);
    }
    assert.equal(tokenValue(token('Original', ids[1])), 'A');
    assert.equal(tokenValue(token('Preview', ids[1])), 'A changed');
    assert.equal(serialized(), 'A;A changed;B');
    cancel();
  });
  check('Changing an element back clears replacement annotations without changing its identity', () => {
    open(' A ;B');
    const id = rowId(elementRows()[0]);
    input(rowInput(id), 'changed');
    input(rowInput(id), ' A ');
    assert.equal(rowId(elementRows()[0]), id);
    assert.equal(row(id).classList.contains('is-replaced'), false);
    assert.equal(all('#elementOriginal .is-replaced, #elementPreview .is-replaced').length, 0);
    row(id).querySelector('[data-move-down]').click();
    assert.ok(row(id).classList.contains('is-moved'));
    row(id).querySelector('[data-move-up]').click();
    assert.equal(all('.element-row.is-moved, #elementOriginal .is-moved, #elementPreview .is-moved').length, 0);
    assert.equal(serialized(), ' A ;B');
    assert.equal(q('#applyElements').disabled, true);
    cancel();
  });
  check('Mixed deletion, addition and movement preserve old identities and serialize only the edited values', () => {
    open('A;B;C', ['A']);
    const before = snapshot(), ids = elementRows().map(rowId);
    row(ids[1]).querySelector('[data-delete]').click();
    assert.equal(all('.element-row.is-moved').length, 0, 'Deletion-induced position shifts are not movement');
    q('#addElement').click();
    const added = rowId(elementRows().at(-1));
    input(rowInput(added), 'new');
    row(ids[2]).querySelector('[data-move-up]').click();
    assert.deepEqual(values(), ['C', 'A', 'new']);
    assert.equal(serialized(), 'C;A;new');
    assert.equal(snapshot(), before);
    assert.ok(token('Original', ids[1]).classList.contains('is-removed'));
    assert.equal(tokenValue(token('Original', ids[1])), 'B');
    assert.ok(token('Preview', ids[1]).classList.contains('is-removed'));
    assert.equal(tokenValue(token('Preview', ids[1])), 'B');
    assert.ok(token('Preview', added).classList.contains('is-added'));
    assert.equal(token('Original', added), null);
    assert.ok(row(added).classList.contains('is-added'));
    assert.match(row(added).querySelector('.element-change-state').textContent, /新增/);
    assert.ok(row(ids[2]).classList.contains('is-moved'));
    assert.match(row(ids[2]).querySelector('.element-change-state').textContent, /移动/);
    for (const side of ['Original', 'Preview']) assert.ok(token(side, ids[2]).classList.contains('is-moved'));
    q('#applyElements').click();
    assert.equal(w.eval("windows['User|REGRESSION']"), 'C;A;new');
  });
  check('Restore reinstates exact initial strings, identities, matching and clean change labels', () => {
    open(' A ;;A;B;', ['A']);
    const ids = elementRows().map(rowId), before = snapshot();
    input(rowInput(ids[0]), 'modified');
    row(ids[2]).querySelector('[data-move-up]').click();
    row(ids[3]).querySelector('[data-delete]').click();
    q('#addElement').click();
    q('#restoreElements').click();
    assert.deepEqual(values(), [' A ', '', 'A', 'B', '']);
    assert.deepEqual(elementRows().map(rowId), ids);
    assert.deepEqual(elementRows().map(element => element.classList.contains('is-matched')), [true, false, true, false, false]);
    assert.equal(all('.element-row.is-replaced, .element-row.is-added, .element-row.is-moved, .diff-token.is-removed, .diff-token.is-replaced, .diff-token.is-added, .diff-token.is-moved').length, 0);
    assert.equal(q('#applyElements').disabled, true);
    assert.equal(q('#restoreElements').disabled, true);
    assert.equal(serialized(), ' A ;;A;B;');
    assert.equal(snapshot(), before);
    cancel();
  });
  check('Empty duplicate positions stay distinct when only the second empty segment is removed', () => {
    open('A;;A;');
    const ids = elementRows().map(rowId);
    assert.deepEqual(values(), ['A', '', 'A', '']);
    row(ids[3]).querySelector('[data-delete]').click();
    assert.deepEqual(elementRows().map(rowId), ids.slice(0, 3));
    assert.equal(tokens('Original').filter(element => element.classList.contains('is-removed')).length, 1);
    assert.ok(token('Original', ids[3]).classList.contains('is-removed'));
    assert.ok(token('Preview', ids[3]).classList.contains('is-removed'));
    assert.equal(token('Original', ids[1]).classList.contains('is-removed'), false);
    assert.equal(all('.element-row.is-moved').length, 0);
    assert.equal(tokenValue(token('Original', ids[1])), '');
    assert.equal(tokenValue(token('Preview', ids[1])), '');
    assert.equal(serialized(), 'A;;A');
    while (q('#elementList [data-delete]')) q('#elementList [data-delete]').click();
    assert.equal(elementRows().length, 0);
    assert.equal(serialized(), '（不存在）');
    assert.equal(tokens('Original').filter(element => element.classList.contains('is-removed')).length, 4);
    assert.equal(tokens('Preview').filter(element => element.classList.contains('is-removed')).length, 4);
    assert.equal(q('#applyElements').disabled, false);
    q('#applyElements').click();
    assert.equal(w.eval("Object.hasOwn(windows,'User|REGRESSION')"), false);
  });
  check('Diff values escape markup and annotations never contaminate serialized or applied data', () => {
    const original = ' <img src=x> ;& "quoted";tail';
    open(original);
    const id = rowId(elementRows()[0]), replacement = '<script>alert(1)</script> & "text"';
    input(rowInput(id), replacement);
    assert.equal(all('#elementOriginal img, #elementOriginal script, #elementPreview img, #elementPreview script').length, 0);
    assert.equal(tokenValue(token('Original', id)), ' <img src=x> ');
    assert.equal(tokenValue(token('Preview', id)), replacement);
    const expected = replacement + ';& "quoted";tail';
    assert.equal(serialized(), expected);
    q('#applyElements').click();
    assert.equal(w.eval("windows['User|REGRESSION']"), expected);
  });
  check('Whole strings retain semicolons as one replacement and keep actual-value marks separate', () => {
    open(' A;;B; ', [' A;;B; '], true);
    const id = rowId(elementRows()[0]);
    assert.equal(elementRows().length, 1);
    assert.equal(rowInput(id).tagName, 'TEXTAREA');
    assert.equal(row(id).draggable, false);
    assert.equal(q('#addElement').hidden, true);
    input(rowInput(id), ' X;;Y; ');
    assert.equal(tokens('Original').length, 1);
    assert.equal(tokens('Preview').length, 1);
    assert.ok(token('Original', id).classList.contains('is-replaced'));
    assert.ok(token('Preview', id).classList.contains('is-replaced'));
    assert.equal(serialized(), ' X;;Y; ');
    q('#applyElements').click();
    assert.equal(w.eval("windows['User|REGRESSION']"), ' X;;Y; ');
    reset();
    w.eval("{ const v=curProfile().vars[0]; v.mode='replace'; v.special=false; v.defaultValue='A;B'; v.source='默认值'; windows[key(v)]=' B;A '; renderRows(); }");
    assert.equal(q('#compareAfter .diff-label').textContent, '整体待替换');
    q('#editWindowsElements').click();
    assert.equal(serialized(), ' B;A ');
    assert.equal(all('.element-row.is-replaced').length, 0);
    assert.match(q('#elementStatus').textContent, /^本次编辑：0 项变化/); assert.equal(all('.element-change-state .diff-label').length, 0); assert.equal(q('#applyElements').disabled, true);
    assert.equal(all('#elementOriginal .is-replaced, #elementPreview .is-replaced').length, 0);
    cancel();
    w.eval('windows[key(curProfile().vars[0])] = value(curProfile().vars[0]); renderRows()');
    assert.equal(q('#compareAfter .diff-label'), null);
    w.eval("{ const v=curProfile().vars[0]; v.special=true; windows[key(v)]='A;B;'; renderRows(); }");
    assert.equal(q('#compareAfter .diff-label').textContent, '整体待替换');
    w.eval("{ const v=curProfile().vars[0]; v.special=false; v.mode='+append'; renderRows(); }");
    assert.equal(q('#compareAfter .diff-label'), null, 'Append intent is not whole-value replacement');
  });
  check('Semantic change-state workflows emit no JSDOM runtime errors', () => assert.deepEqual(errors, []));
  const report = {
    verification: 'JSDOM event and semantic DOM checks only; browser rendering, native drag and Windows integration not verified', checks
  };
  fs.writeFileSync(path.join(__dirname, 'change-states-report.json'), JSON.stringify(report, null, 2) + '\n');
  process.stdout.write(JSON.stringify(report, null, 2) + '\n');
} finally {
  w.close();
}

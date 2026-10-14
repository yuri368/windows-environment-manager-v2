/* JSDOM interaction and semantic DOM checks only. No visual or Windows integration. */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { JSDOM, VirtualConsole } = require('jsdom');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'temp.html'), 'utf8');
const errors = [], checks = [];
const virtualConsole = new VirtualConsole();
virtualConsole.on('jsdomError', error => errors.push(error.message));
const dom = new JSDOM(html, {
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
const all = selector => Array.from(doc.querySelectorAll(selector));
const check = (name, fn) => {
  try { fn(); checks.push({ name, passed: true }); }
  catch (error) { checks.push({ name, passed: false, error: error.stack }); }
};
const reset = () => { w.WindowsElements.dismiss(); w.eval('reset(true)'); };
const persisted = () => w.eval('JSON.stringify({apps,windows,history,revision})');
const history = () => JSON.parse(w.eval('JSON.stringify(history)'));
const entry = () => history()[0];
const renderHistory = () => q('[data-view="history"]').click();
const input = (element, value) => {
  assert.ok(element, 'Expected editable field');
  element.value = value;
  element.dispatchEvent(new w.Event('input', { bubbles: true }));
};
const fixture = ({ before, after, whole = false, name = 'HISTORY_TEST' }) => {
  reset();
  w.__historyFixture = { before, after, whole, name };
  w.eval(`{
    const f = __historyFixture;
    selected = 2; focusedVariable = 0;
    curProfile().vars = [mkVar([f.name, 'User', 'replace', f.after, { special: f.whole }], 0)];
    if (f.before === undefined) delete windows['User|' + f.name];
    else windows['User|' + f.name] = f.before;
    render();
  }`);
};
const confirmPreview = () => {
  q('#previewButton').click();
  assert.equal(q('#confirmDialog').disabled, false, 'Fixture should be confirmable');
  q('#confirmDialog').click();
};
const openFixtureEditor = () => {
  q('[data-view="windows"]').click();
  q('[data-windows-edit="User|HISTORY_TEST"]').click();
};

try {
  check('Cancelled preview and cancelled element edit leave history and page state exact', () => {
    fixture({ before: 'old', after: 'new' });
    const before = persisted();
    q('#previewButton').click(); q('#cancelDialog').click();
    assert.equal(persisted(), before);
    openFixtureEditor();
    input(q('.element-input'), 'draft only');
    q('#cancelDialog').click();
    assert.equal(persisted(), before);
  });

  check('Confirmed single change stores exact raw before and after values independent of later snapshot edits', () => {
    const rawBefore = ' A ;;A; ';
    const rawAfter = ' B ;;A; ';
    fixture({ before: rawBefore, after: rawAfter });
    const count = history().length;
    confirmPreview();
    const saved = entry();
    assert.equal(history().length, count + 1);
    assert.ok(Array.isArray(saved.changes));
    assert.equal(saved.changes.length, 1);
    assert.equal(saved.changes[0].scope, 'User');
    assert.equal(saved.changes[0].name, 'HISTORY_TEST');
    assert.equal(saved.changes[0].before, rawBefore);
    assert.equal(saved.changes[0].after, rawAfter);
    assert.equal(saved.changes[0].whole, false);
    assert.equal(saved.changes[0].counts, undefined, 'Preview has no stable editor IDs to invent an element audit');
    w.eval("windows['User|HISTORY_TEST'] = 'later'; render()");
    assert.deepEqual(entry(), saved);
    renderHistory();
    assert.ok(q('#screen').textContent.includes(rawBefore));
    assert.ok(q('#screen').textContent.includes(rawAfter));
  });

  check('Whole value editor retains semicolons as one audited replacement', () => {
    fixture({ before: ' A;;B; ', after: ' X;;Y; ', whole: true });
    openFixtureEditor();
    assert.equal(all('#elementList .element-row').length, 1);
    input(q('.element-input'), ' X;;Y; ');
    q('#applyElements').click();
    const change = entry().changes[0];
    assert.equal(change.whole, true);
    assert.equal(change.before, ' A;;B; ');
    assert.equal(change.after, ' X;;Y; ');
    assert.equal(change.elements.length, 1);
    assert.deepEqual(change.counts, { added: 0, removed: 0, replaced: 1, moved: 0 });
  });

  check('Confirming an unchanged preview creates no operation history entry', () => {
    fixture({ before: 'same raw', after: 'same raw' });
    const before = persisted();
    q('#previewButton').click();
    const confirm = q('#confirmDialog');
    if (!confirm.disabled) confirm.click();
    else q('#cancelDialog').click();
    assert.equal(history().length, JSON.parse(before).history.length);
    assert.equal(w.eval("windows['User|HISTORY_TEST']"), 'same raw');
  });

  check('Batch confirmation records each target with its own exact before and after', () => {
    reset();
    w.eval(`{
      scheme(0).vars = [mkVar(['BATCH_A', 'User', 'replace', 'next A'], 0)];
      scheme(1).vars = [mkVar(['BATCH_B', 'System', 'replace', 'next B'], 0)];
      windows['User|BATCH_A'] = 'old A'; windows['System|BATCH_B'] = 'old B';
      checked = new Set([0, 1]); render();
    }`);
    const prior = history().length;
    confirmPreview();
    assert.equal(history().length, prior + 1);
    const changes = entry().changes;
    assert.equal(changes.length, 2);
    assert.deepEqual(changes.map(({ scope, name, before, after }) => ({ scope, name, before, after })), [
      { scope: 'User', name: 'BATCH_A', before: 'old A', after: 'next A' },
      { scope: 'System', name: 'BATCH_B', before: 'old B', after: 'next B' }
    ]);
    renderHistory();
    for (const raw of ['old A', 'next A', 'old B', 'next B']) assert.ok(q('#screen').textContent.includes(raw));
  });

  check('Batch changes to one target store one snapshot-bound result with both original requests', () => {
    reset();
    w.eval(`{
      scheme(0).vars = [mkVar(['SHARED_BATCH', 'User', '+append', 'front'], 0)];
      scheme(1).vars = [mkVar(['SHARED_BATCH', 'User', '-append', 'tail'], 0)];
      windows['User|SHARED_BATCH'] = 'core';
      checked = new Set([0, 1]); render();
    }`);
    confirmPreview();
    const changes = entry().changes;
    assert.equal(changes.length, 1);
    assert.deepEqual(changes.map(({before,after})=>({before,after})),[{before:'core',after:'front;core;tail'}]);
    assert.deepEqual(Array.from(changes[0].plan.requests,r=>r.mode),['+append','-append']);
    for (const change of changes) {
      assert.equal(change.counts, undefined);
      assert.equal(change.elements, undefined);
    }
  });

  check('All-unchanged batch confirmation adds no history entry', () => {
    reset();
    w.eval(`{
      scheme(0).vars = [mkVar(['BATCH_NOOP_A', 'User', 'replace', 'same A'], 0)];
      scheme(1).vars = [mkVar(['BATCH_NOOP_B', 'System', 'replace', 'same B'], 0)];
      windows['User|BATCH_NOOP_A'] = 'same A'; windows['System|BATCH_NOOP_B'] = 'same B';
      checked = new Set([0, 1]); render();
    }`);
    const prior = history().length;
    confirmPreview();
    assert.equal(history().length, prior);
  });

  check('Missing original and present empty original remain distinct in stored history', () => {
    fixture({ before: undefined, after: 'new' });
    confirmPreview();
    assert.equal(entry().changes[0].before, undefined);
    renderHistory();
    assert.equal(q('pre.history-before').textContent, '（不存在）');
    fixture({ before: '', after: 'new' });
    confirmPreview();
    assert.equal(entry().changes[0].before, '');
    renderHistory();
    assert.equal(q('pre.history-before').textContent, '（空字符串）');
  });

  check('Element editor apply records exact raw strings after duplicate, empty and markup edits', () => {
    const before = ' A ;;A;B;';
    fixture({ before, after: 'ignored selected value' });
    const oldCount = history().length;
    openFixtureEditor();
    const rows = () => all('#elementList .element-row');
    assert.deepEqual(rows().map(row => row.querySelector('.element-input').value), [' A ', '', 'A', 'B', '']);
    input(rows()[2].querySelector('.element-input'), '<img src=x>');
    rows()[1].querySelector('[data-delete]').click();
    q('#addElement').click();
    input(rows().at(-1).querySelector('.element-input'), ' tail ');
    const after = q('#elementSerialized').textContent;
    assert.equal(after, ' A ;<img src=x>;B;; tail ');
    q('#applyElements').click();
    assert.equal(history().length, oldCount + 1);
    assert.equal(w.eval("windows['User|HISTORY_TEST']"), after);
    assert.equal(entry().changes[0].before, before);
    assert.equal(entry().changes[0].after, after);
    assert.equal(entry().changes[0].whole, false);
    assert.deepEqual(entry().changes[0].counts, { added: 1, removed: 1, replaced: 1, moved: 0 });
    assert.equal(entry().changes[0].elements.filter(item => item.added).length, 1);
    assert.equal(entry().changes[0].elements.filter(item => item.removed).length, 1);
    assert.equal(entry().changes[0].elements.filter(item => item.replaced).length, 1);
    renderHistory();
    const disclosure = q('details.history-details');
    assert.ok(disclosure.querySelector('section.history-change'));
    assert.equal(disclosure.querySelector('pre.history-before').textContent, before);
    assert.equal(disclosure.querySelector('pre.history-after').textContent, after);
    assert.ok(disclosure.querySelector('table'));
    assert.equal(q('#screen img'), null);
    assert.ok(q('#screen').textContent.includes('<img src=x>'));
  });

  check('History disclosure shows escaped raw strings and cannot change operational state', () => {
    fixture({ before: '<img src=x>; A ', after: '<script>alert(1)</script>; B ' });
    confirmPreview();
    renderHistory();
    const before = persisted();
    const disclosure = q('#screen details.history-details');
    assert.ok(disclosure, 'A native disclosure should expose history details');
    assert.ok(disclosure.querySelector('summary'));
    disclosure.open = true;
    disclosure.dispatchEvent(new w.Event('toggle'));
    assert.equal(q('#screen img, #screen script'), null);
    assert.ok(disclosure.textContent.includes('<img src=x>; A '));
    assert.ok(disclosure.textContent.includes('<script>alert(1)</script>; B '));
    assert.equal(disclosure.querySelector('pre.history-before').textContent, '<img src=x>; A ');
    assert.equal(disclosure.querySelector('pre.history-after').textContent, '<script>alert(1)</script>; B ');
    assert.equal(persisted(), before);
    assert.equal(disclosure.querySelector('button, input, select, textarea'), null);
  });

  check('Editor sidebar has separate app and profile groups with independent list containers', () => {
    reset(); q('#configBtn').click();
    const groups = all('.editor-left .editor-nav-group');
    assert.equal(groups.length, 2);
    assert.deepEqual(groups.map(group => group.querySelector('.editor-nav-list')?.dataset.editorList), ['apps', 'profiles']);
    assert.deepEqual(groups.map(group => group.querySelector('.editor-nav-list')?.querySelectorAll('button').length), [13, 2]);
    assert.deepEqual(groups.map(group => group.querySelector('.editor-nav-list')?.contains(group.querySelector('.editor-section'))), [false, false]);
    const lists = groups.map(group => group.querySelector('.editor-nav-list'));
    assert.equal(new Set(lists).size, 2);
    assert.ok(groups[0].textContent.includes('应用'));
    assert.ok(groups[1].textContent.includes('方案'));
  });

  check('Sidebar CSS assigns two constrained groups and overflow to each list', () => {
    const css = fs.readFileSync(path.join(root, 'ui-polish.css'), 'utf8');
    const rule = selector => {
      const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      return css.match(new RegExp(`${escaped}\\s*\\{([^}]+)\\}`))?.[1] || '';
    };
    assert.match(rule('.editor-left'), /display:\s*grid/);
    assert.match(rule('.editor-left'), /grid-template-rows:\s*minmax\(0,\s*2fr\)\s+minmax\(0,\s*1fr\)/);
    assert.match(rule('.editor-nav-group'), /min-height:\s*0/);
    assert.match(rule('.editor-nav-list'), /overflow-y:\s*auto/);
    assert.match(rule('.editor-nav-list'), /flex:\s*1/);
  });

  check('Sidebar app and profile buttons select correctly while table rows select variables by mouse and keyboard', () => {
    reset(); q('#configBtn').click();
    q('[data-editapp="0"]').click();
    assert.equal(w.eval('editor.app'), 0);
    assert.equal(q('[data-editapp="0"]').getAttribute('aria-current'), 'true');
    const profile = all('[data-editplan]')[1];
    const profileName = profile.dataset.editplan;
    profile.click();
    assert.equal(w.eval('editor.profile'), profileName);

    const rows = () => all('tr[data-tableplan][data-tablevar]');
    const variableRow = index => rows().find(row => row.dataset.tableplan === w.eval('editor.profile') && +row.dataset.tablevar === index);
    let target = variableRow(1);
    assert.ok(target, 'The selected profile should expose its second variable row');
    assert.equal(target.tagName, 'TR');
    assert.equal(target.getAttribute('tabindex'), '0');
    target.click();
    assert.equal(w.eval('editor.varIdx'), 1);
    assert.equal(variableRow(1).getAttribute('aria-current'), 'true');
    assert.equal(doc.activeElement, variableRow(1));

    target = variableRow(0);
    target.focus();
    target.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    assert.equal(w.eval('editor.varIdx'), 1);
    assert.equal(variableRow(1).getAttribute('aria-current'), 'true');
    assert.equal(doc.activeElement, variableRow(1));

    variableRow(1).dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Home', bubbles: true }));
    assert.equal(w.eval('editor.varIdx'), 0);
    assert.equal(variableRow(0).getAttribute('aria-current'), 'true');
    assert.equal(doc.activeElement, variableRow(0));

    variableRow(0).dispatchEvent(new w.KeyboardEvent('keydown', { key: 'End', bubbles: true }));
    const lastRow = rows().filter(row => row.dataset.tableplan === w.eval('editor.profile')).at(-1);
    assert.equal(w.eval('editor.varIdx'), +lastRow.dataset.tablevar);
    assert.equal(lastRow.getAttribute('aria-current'), 'true');
    assert.equal(doc.activeElement, lastRow);

    lastRow.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    assert.equal(w.eval('editor.varIdx'), +lastRow.dataset.tablevar);
    assert.equal(variableRow(+lastRow.dataset.tablevar).getAttribute('aria-current'), 'true');
    assert.equal(doc.activeElement, variableRow(+lastRow.dataset.tablevar));
    variableRow(+lastRow.dataset.tablevar).dispatchEvent(new w.KeyboardEvent('keydown', { key: ' ', bubbles: true }));
    assert.equal(w.eval('editor.varIdx'), +lastRow.dataset.tablevar);
    assert.equal(doc.activeElement, variableRow(+lastRow.dataset.tablevar));
  });

  check('History and sidebar workflows produce no JSDOM runtime errors', () => assert.deepEqual(errors, []));
  const report = {
    verification: 'JSDOM data and interaction checks only; visual layout, native browser scrolling and Windows integration not verified',
    total: checks.length,
    passed: checks.filter(item => item.passed).length,
    failed: checks.filter(item => !item.passed).length,
    checks
  };
  fs.writeFileSync(path.join(__dirname, 'history-sidebar-report.json'), JSON.stringify(report, null, 2) + '\n');
  process.stdout.write(JSON.stringify(report, null, 2) + '\n');
  if (report.failed) process.exitCode = 1;
} finally { w.close(); }

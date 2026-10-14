/* DOM-only append guidance contracts. No browser or Windows operations. */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { JSDOM, VirtualConsole } = require('jsdom');
const root = path.resolve(__dirname, '..');
const errors = [], checks = [];
const virtualConsole = new VirtualConsole();
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
const all = selector => Array.from(doc.querySelectorAll(selector));
const plain = value => JSON.parse(JSON.stringify(value));
const check = (name, verify) => {
  try { verify(); checks.push({ name, passed: true }); }
  catch (error) { checks.push({ name, passed: false, error: error.stack }); }
};
const reset = () => { w.WindowsElements.dismiss(); w.eval('reset(true)'); q('#modal').classList.remove('open'); };
const input = (element, value) => {
  assert.ok(element, 'Expected editable input');
  element.value = value;
  element.dispatchEvent(new w.Event('input', { bubbles: true }));
};
const change = (element, value) => {
  assert.ok(element, 'Expected source selector');
  element.value = value;
  element.dispatchEvent(new w.Event('change', { bubbles: true }));
};
const rows = () => all('#elementList .element-row');
const values = () => rows().map(row => row.querySelector('.element-input').value);
const serialized = () => q('#elementSerialized').textContent;
const paneRaw = selector => Array.from(q(selector).querySelectorAll('.diff-value[data-raw-value]')).filter(node => !node.closest('.comparison-token.is-ghost')).map(node => node.getAttribute('data-raw-value')).join(';');
const snapshot = () => w.eval('JSON.stringify(windows)');
const state = () => w.eval('JSON.stringify({apps,windows,history,revision})');
const cancel = () => q('#cancelDialog').click();
const rawValues = container => container ? [...(container.matches('[data-raw-value]') ? [container] : []), ...container.querySelectorAll('[data-raw-value]')].map(node => node.getAttribute('data-raw-value')) : [];
const candidates = () => all('#pendingElements .element-suggestions-group .pending-add-token').flatMap(rawValues);
const inspectorCandidates = () => all('#compareDesired .pending-add-token').flatMap(token => rawValues(token));
const actualText = () => {
  return paneRaw('#compareActual');
};
const selection = (raw, selected, whole = false) => plain(w.WindowsElements.selectionInfo(raw, selected, whole));
const fixture = ({ actual = 'A', desired = 'A;B', mode = '+append', whole = false, scope = 'User', name = 'APPEND_TEST' } = {}) => {
  reset();
  w.__appendFixture = { actual, desired, mode, whole, scope, name };
  w.eval(`{
    const f=__appendFixture;
    selected=2; focusedVariable=0;
    curProfile().vars=[mkVar([f.name,f.scope,f.mode,f.desired,{special:f.whole,savedValue:'A;C',choices:['A;D','A']}],0)];
    windows[f.scope+'|'+f.name.toUpperCase()]=f.actual;
    render();
  }`);
};
const openFixture = () => { q('#editWindowsElements').click(); return rows().map(row => row.dataset.elementId); };
const editorOpen = (raw, selected = ['A;B'], mode = '+append', whole = false) => {
  reset(); w.__appendRaw = raw; w.__appendSelected = selected; w.__appendMode = mode; w.__appendWhole = whole;
  w.eval("windows['User|APPEND_TEST']=__appendRaw; openWindowsElements('User','APPEND_TEST',__appendSelected,__appendWhole,__appendMode)");
  return rows().map(row => row.dataset.elementId);
};
const liveRow = id => q(`#elementList [data-element-id="${id}"]`);
const edit = (id, value) => input(liveRow(id).querySelector('.element-input'), value);
const ratio = (matched, total) => {
  for (const selector of ['#varRows tr.sel .result', '#compareResult']) assert.match(q(selector).textContent, new RegExp(`${matched}\\s*/\\s*${total}`), selector);
};

try {
  check('selectionInfo reports matched and missing selected occurrences with preserved raw slices', () => {
    assert.deepEqual(selection(' A ;C', [' A ; B ']), {
      total: 2, matched: 1, missing: [{ value: ' B ', index: 1 }],
      parts: [{ value: ' A ', index: 0, matched: true }, { value: ' B ', index: 1, matched: false }]
    });
  });
  check('Duplicate selected occurrences contribute individually without consuming actual occurrences', () => {
    const info = selection('A', ['A;A;B']);
    assert.equal(info.total, 3); assert.equal(info.matched, 2);
    assert.deepEqual(info.missing, [{ value: 'B', index: 2 }]);
    fixture({ actual: 'A', desired: 'A;A;B' }); ratio(2, 3);
  });
  check('Multiple selected values flatten to occurrence indexes and preserve duplicate missing candidates', () => {
    const info = selection('A', ['A;', ' B ;B']);
    assert.equal(info.total, 3); assert.equal(info.matched, 1);
    assert.deepEqual(info.parts, [
      {value:'A',index:0,matched:true}, {value:'',index:1,matched:false},
      {value:' B ',index:2,matched:false}, {value:'B',index:3,matched:false}
    ]);
    assert.deepEqual(info.missing, [{value:' B ',index:2}, {value:'B',index:3}]);
  });
  check('Blank selected slices retain their raw text and indexes but are excluded from denominator', () => {
    const info = selection('A', [' A ;; B ; ']);
    assert.equal(info.total, 2); assert.equal(info.matched, 1);
    assert.deepEqual(info.parts.map(part => part.value), [' A ', '', ' B ', ' ']);
    assert.deepEqual(info.missing, [{ value: ' B ', index: 2 }]);
    fixture({ actual: 'A', desired: 'A;;B' }); ratio(1, 2);
  });
  check('Matching trims only whitespace and preserves case, slash and unexpanded reference distinctions', () => {
    const info = selection(' A ;C:\\Bin;%HOME%\\bin', ['A;a;C:/Bin;C:\\bin;C:\\Bin\\;C:\\Home\\bin;%HOME%\\bin']);
    assert.equal(info.total, 7); assert.equal(info.matched, 2);
    assert.deepEqual(info.missing.map(part => part.value), ['a', 'C:/Bin', 'C:\\bin', 'C:\\Bin\\', 'C:\\Home\\bin']);
  });
  check('Undefined actual values expose all valid selected elements as missing', () => {
    assert.deepEqual(selection(undefined, [' A ;B']), {
      total: 2, matched: 0, missing: [{ value: ' A ', index: 0 }, { value: 'B', index: 1 }],
      parts: [{ value: ' A ', index: 0, matched: false }, { value: 'B', index: 1, matched: false }]
    });
  });
  check('Whole values use independent exact matching and never split semicolons', () => {
    const match = selection(' A;;B; ', [' A;;B; '], true);
    assert.equal(match.total, 1); assert.equal(match.matched, 1); assert.equal(match.parts.length, 1);
    assert.equal(match.parts[0].value, ' A;;B; ');
    const miss = selection('A;;B;', [' A;;B; '], true);
    assert.equal(miss.matched, 0); assert.equal(miss.missing.length, 1);
  });
  check('Maven Path custom source marks missing desired value and leaves existing actual text intact', () => {
    reset(); q('#varRows [data-row="1"] .vinput').focus();
    const raw = w.eval("windows['User|PATH']");
    input(q('#varRows [data-row="1"] .vinput'), ' Pathcustom ');
    assert.deepEqual(rawValues(q('#compareDesired')), [' Pathcustom ']);
    assert.deepEqual(rawValues(q('#compareDesired .pending-add-token')), [' Pathcustom ']);
    assert.match(q('#compareLabel').textContent, /选定值已修改/);
    assert.deepEqual(inspectorCandidates(), [' Pathcustom ']);
    assert.equal(actualText(), raw); ratio(0, 1);
    openFixture(); assert.deepEqual(candidates(), [' Pathcustom ']); assert.equal(serialized(), raw);
    cancel(); assert.equal(w.eval("windows['User|PATH']"), raw);
  });
  check('Python Path two selected values report partial 1/2 and expose only the missing value', () => {
    reset();
    w.eval("selected=1;focusedVariable=1;windows['User|PATH']='%PYTHON_HOME%';render()");
    ratio(1, 2);
    assert.deepEqual(inspectorCandidates(), ['%PYTHON_HOME%\\Scripts']);
    assert.match(q('#varRows [data-row="1"] .result').textContent, /部分/);
    openFixture(); assert.deepEqual(candidates(), ['%PYTHON_HOME%\\Scripts']); cancel();
  });
  for (const mode of ['+append', '-append', 'append']) {
    check(`${mode} marks pending desired slices and displays separate readonly actual candidates`, () => {
      fixture({ actual: 'A;X', desired: 'A; B ', mode }); ratio(1, 2);
      assert.deepEqual(rawValues(q('#compareDesired')), ['A', ' B ']);
      assert.deepEqual(rawValues(q('#compareDesired .pending-add-token')), [' B ']);
      assert.deepEqual(inspectorCandidates(), [' B ']); assert.equal(actualText(), 'A;X');
      const expectedAfter = mode === '+append' ? ' B ;A;X' : 'A;X; B ';
      assert.equal(paneRaw('#compareAfter'), expectedAfter);
      assert.equal(q('#compareSerialized').textContent, expectedAfter);
      assert.deepEqual(rawValues(q('#compareActual')), ['A', 'X']);
      assert.equal(q('#compareDesired .pending-add-token button, #compareDesired .pending-add-token input'), null);
      openFixture(); assert.deepEqual(candidates(), [' B ']); cancel();
    });
  }
  check('Partial, full and zero append matches distinguish 1/2, 2/2 and 0/2', () => {
    fixture({ actual: 'A' }); ratio(1, 2); assert.match(q('#compareResult').textContent, /部分/);
    fixture({ actual: 'A;B;X' }); ratio(2, 2); assert.match(q('#compareResult').textContent, /匹配/);
    assert.equal(q('#compareDesired .pending-add-token'), null); assert.deepEqual(inspectorCandidates(), []);
    fixture({ actual: 'X' }); ratio(0, 2); assert.match(q('#compareResult').textContent, /不匹配|未匹配/);
  });
  check('Replace retains whole item mismatch with extra actual elements despite desired ratio 2/2', () => {
    fixture({ actual: 'A;B;X', mode: 'replace' }); ratio(2, 2);
    assert.equal(w.eval('itemMatched(curProfile().vars[0])'), false);
    assert.match(q('#compareResult').textContent, /不匹配|未匹配|额外差异/);
    assert.ok(q('#varRows tr.sel .result').classList.contains('warn'));
    assert.doesNotMatch(q('#compareResult').textContent, /全匹配|完全匹配/);
    assert.equal(q('#compareDesired .pending-add-token'), null); assert.deepEqual(inspectorCandidates(), []);
    assert.match(q('#compareAfter').textContent, /整体待替换/);
    openFixture(); assert.deepEqual(candidates(), []); assert.match(q('#elementStatus').textContent, /0 项变化/); assert.doesNotMatch(q('#elementPreview').textContent, /待删除|将替换/); assert.equal(all('#elementList .element-change-state .diff-label').length, 0); assert.equal(q('#applyElements').disabled, true); cancel();
  });
  check('Whole editor preserves semicolon string with no pending candidate or misleading 0/0', () => {
    fixture({ actual: 'A;B', desired: 'A;C', mode: '+append', whole: true });
    assert.equal(q('#compareDesired .pending-add-token'), null); assert.deepEqual(inspectorCandidates(), []);
    assert.doesNotMatch(q('#compareResult').textContent, /0\s*\/\s*0/);
    openFixture(); assert.deepEqual(values(), ['A;B']); assert.deepEqual(candidates(), []);
    assert.equal(rows()[0].querySelector('.element-input').tagName, 'TEXTAREA'); cancel();
  });
  check('Empty selected values preserve raw blank slices without 0/0 detection or append candidates', () => {
    for (const desired of ['', ' ; ; ']) {
      fixture({ desired });
      assert.equal(q('#compareDesired .pending-add-token'), null); assert.deepEqual(inspectorCandidates(), []);
      assert.doesNotMatch(q('#varRows .result').textContent + q('#compareResult').textContent, /0\s*\/\s*0/);
      openFixture(); assert.deepEqual(candidates(), []); assert.equal(serialized(), 'A'); cancel();
    }
  });
  check('Pending candidates retain raw whitespace and escape markup without creating active DOM', () => {
    const candidate = ' <img src=x onerror="window.__injected=1"> & "quoted" ';
    fixture({ actual: ' A ;;X; ', desired: `A;${candidate}` });
    assert.equal(actualText(), ' A ;;X; '); assert.deepEqual(inspectorCandidates(), [candidate]);
    assert.deepEqual(rawValues(q('#compareDesired .pending-add-token')), [candidate]);
    assert.equal(q('#inspect img'), null); assert.equal(w.__injected, undefined);
    openFixture(); assert.deepEqual(candidates(), [candidate]); assert.equal(q('#pendingElements img'), null);
    assert.equal(serialized(), ' A ;;X; '); cancel();
  });
  check('Dialog candidates are separate from live rows, stable IDs, duplicates and serialized result', () => {
    editorOpen('A;A;;', ['A;B;B']);
    assert.deepEqual(values(), ['A', 'A', '', '']); assert.deepEqual(candidates(), ['B']);
    assert.equal(serialized(), 'A;A;;');
    assert.ok(q('#pendingElements')); assert.match(q('#pendingElements').textContent, /方案建议/);
    assert.match(q('#pendingElements').textContent, /建议追加/); assert.match(q('#pendingElements').textContent, /仅作用于本次编辑，确认应用前不会写入 Windows/);
    assert.equal(q('#pendingElements').querySelector('[data-element-id],input,textarea,[data-move-up],[data-move-down],[data-delete],.is-duplicate,.element-row'), null);
    for (const button of q('#pendingElements').querySelectorAll('button')) {
      assert.ok(button.matches('.pending-add-button[data-add-pending]'));
      assert.match(button.textContent, /＋/);
    }
    assert.equal(q('#applyElements').disabled, true); assert.equal(q('#restoreElements').disabled, true); cancel();
  });
  check('Readonly guidance cannot change dirty state, apply state or persisted demo values', () => {
    fixture(); const before = state(); openFixture();
    const status = q('#elementStatus').textContent;
    assert.equal(state(), before); assert.equal(q('#applyElements').disabled, true);
    q('#pendingElements').dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
    q('#pendingElements').dispatchEvent(new w.MouseEvent('pointerover', { bubbles: true }));
    assert.equal(q('#elementStatus').textContent, status); assert.equal(state(), before);
    assert.equal(q('#applyElements').disabled, true); assert.equal(serialized(), 'A'); cancel();
    assert.equal(state(), before);
  });
  check('Manually adding a missing element removes guidance and apply serializes only actual live rows', () => {
    const ids = editorOpen('A', ['A; B ;C']);
    q('#addElement').click(); const added = rows().at(-1).dataset.elementId;
    edit(added, ' B '); assert.deepEqual(candidates(), ['C']);
    assert.equal(serialized(), 'A; B '); assert.deepEqual(values(), ['A', ' B ']);
    assert.equal(liveRow(ids[0]).querySelector('.element-match-state').textContent.includes('匹配'), true);
    q('#applyElements').click(); assert.equal(w.eval("windows['User|APPEND_TEST']"), 'A; B ');
    assert.equal(q('#modal').classList.contains('open'), false);
  });
  check('Editing a live row to missing value updates guidance and restoring recomputes from original rows', () => {
    const ids = editorOpen('A;X'); edit(ids[1], 'B');
    assert.deepEqual(candidates(), []); assert.equal(serialized(), 'A;B');
    edit(ids[0], 'Y'); assert.deepEqual(candidates(), ['A']);
    q('#restoreElements').click(); assert.deepEqual(candidates(), ['B']);
    assert.equal(serialized(), 'A;X'); assert.deepEqual(rows().map(row => row.dataset.elementId), ids);
    assert.equal(q('#applyElements').disabled, true); cancel();
  });
  check('Deleting a matched element makes it a candidate while deletion ghosts remain excluded', () => {
    const ids = editorOpen('A;B'); assert.deepEqual(candidates(), []);
    liveRow(ids[0]).querySelector('[data-delete]').click();
    assert.deepEqual(candidates(), ['A']); assert.equal(serialized(), 'B');
    assert.ok(q('#elementPreview .is-removed')); assert.deepEqual(values(), ['B']);
    q('#restoreElements').click(); assert.deepEqual(candidates(), []); assert.equal(serialized(), 'A;B'); cancel();
  });
  check('Cancel discards live edits and reopening has no latent insertion of pending candidates', () => {
    const ids = editorOpen('A'); const before = snapshot(); edit(ids[0], 'X'); cancel();
    assert.equal(snapshot(), before);
    w.eval("openWindowsElements('User','APPEND_TEST',['A;B'],false,'+append')");
    assert.deepEqual(values(), ['A']); assert.deepEqual(candidates(), ['B']); assert.equal(serialized(), 'A'); cancel();
  });
  check('Applying an unrelated live edit never implicitly appends pending candidates', () => {
    const ids = editorOpen('A', ['A;B']); edit(ids[0], 'X');
    assert.deepEqual(candidates(), ['A', 'B']); q('#applyElements').click();
    assert.equal(w.eval("windows['User|APPEND_TEST']"), 'X');
    w.eval("openWindowsElements('User','APPEND_TEST',['A;B'],false,'+append')");
    assert.deepEqual(values(), ['X']); assert.deepEqual(candidates(), ['A', 'B']); cancel();
  });
  check('Undefined actual editor starts with no live rows and cannot apply readonly candidates', () => {
    editorOpen(undefined, [' A ;B']); assert.deepEqual(values(), []); assert.deepEqual(candidates(), [' A ', 'B']);
    assert.equal(serialized(), '（不存在）'); assert.equal(q('#applyElements').disabled, true);
    assert.match(q('#elementOriginal').textContent, /不存在/); cancel();
    assert.equal(w.eval("windows['User|APPEND_TEST']"), undefined);
  });
  check('Matching preserves raw live rows; definite append candidates contain only the first normalized occurrence', () => {
    const ids = editorOpen(' A ', ['A;A;B']); assert.deepEqual(candidates(), ['B']);
    edit(ids[0], 'a'); assert.deepEqual(candidates(), ['A', 'B']);
    edit(ids[0], ' A '); assert.deepEqual(candidates(), ['B']); assert.equal(serialized(), ' A '); cancel();
  });
  check('Same target name keeps User and System candidates isolated', () => {
    fixture({ name: 'PATH', scope: 'User', actual: 'A', desired: 'A;B' });
    w.eval("windows['System|PATH']='B'"); openFixture(); assert.deepEqual(candidates(), ['B']); cancel();
    w.eval("curProfile().vars[0].scope='System';render()");
    assert.deepEqual(inspectorCandidates(), ['A']); openFixture(); assert.deepEqual(candidates(), ['A']);
    edit(rows()[0].dataset.elementId, 'C'); q('#applyElements').click();
    assert.equal(w.eval("windows['User|PATH']"), 'A'); assert.equal(w.eval("windows['System|PATH']"), 'C');
  });
  check('Windows target conflicts suppress executable candidates while isolated System append remains available', () => {
    fixture({name:'APPEND_TEST',actual:'A',desired:'A;B'});
    w.eval(`{
      curProfile().vars.push(mkVar(['APPEND_TEST','User','replace','A;C'],1));
      curProfile().vars.push(mkVar(['APPEND_TEST','System','+append','A;D'],2));
      windows['System|APPEND_TEST']='D';go('windows');
    }`);
    const user = q('[data-windows-value="User|APPEND_TEST"]');
    const system = q('[data-windows-value="System|APPEND_TEST"]');
    assert.equal(user.textContent, 'A'); assert.equal(system.textContent, 'D');
    assert.equal(user.querySelector('.pending-additions'), null);
    assert.deepEqual(rawValues(user.parentElement.querySelector('.pending-additions')), []);assert.match(user.parentElement.textContent,/目标计划冲突/);
    assert.deepEqual(rawValues(system.parentElement.querySelector('.pending-additions')), ['A']);
    q('[data-windows-edit="User|APPEND_TEST"]').click();
    assert.deepEqual(candidates(), []); assert.equal(serialized(), 'A'); cancel();
    q('[data-windows-edit="System|APPEND_TEST"]').click();
    assert.deepEqual(candidates(), ['A']); assert.equal(serialized(), 'D'); cancel();
  });
  check('Source switching refreshes desired guidance and newly opened dialog selection', () => {
    fixture(); assert.deepEqual(inspectorCandidates(), ['B']);
    change(q('#varRows .source'), '已保存值'); assert.deepEqual(inspectorCandidates(), ['C']);
    openFixture(); assert.deepEqual(candidates(), ['C']); cancel();
    change(q('#varRows .source'), '候选值 1'); assert.deepEqual(inspectorCandidates(), ['D']);
    input(q('#varRows .vinput'), 'A;E'); assert.deepEqual(inspectorCandidates(), ['E']);
    openFixture(); assert.deepEqual(candidates(), ['E']); cancel();
    change(q('#varRows .source'), '候选值 2'); assert.deepEqual(inspectorCandidates(), []); ratio(1, 1);
  });
  check('options.appendValues supports readonly guidance independent of highlight selectedValues', () => {
    reset(); let applied;
    const close = () => { w.WindowsElements.dismiss(); q('#modal').classList.remove('open'); };
    w.WindowsElements.open({ container:q('#modal'), name:'DIRECT', scope:'User', registryType:'REG_SZ', raw:'A',
      selectedValues:['A;B'], appendValues:['A;C'], whole:false, close, onApply:raw => { applied=raw; } });
    assert.deepEqual(candidates(), ['C']); assert.equal(serialized(), 'A'); assert.equal(q('#applyElements').disabled, true);
    edit(rows()[0].dataset.elementId, 'X'); q('#applyElements').click(); assert.equal(applied, 'X');
  });
  check('Explicit appendValues in a whole-string session still cannot create split candidates', () => {
    reset();
    const close = () => { w.WindowsElements.dismiss(); q('#modal').classList.remove('open'); };
    w.WindowsElements.open({container:q('#modal'),name:'WHOLE',scope:'User',registryType:'REG_SZ',raw:'A;B',
      selectedValues:['A;C'],appendValues:['A;C'],whole:true,close,onApply:()=>{}});
    assert.deepEqual(values(), ['A;B']); assert.deepEqual(candidates(), []); assert.equal(serialized(), 'A;B'); close();
  });
  check('Missing append context and replace/whole contexts do not generate editor candidates', () => {
    for (const [mode, whole] of [[null,false], ['replace',false], ['+append',true]]) {
      editorOpen('A', ['A;B'], mode, whole); assert.deepEqual(candidates(), []); cancel();
    }
  });
  check('Append feedback workflows emit no JSDOM runtime errors', () => assert.deepEqual(errors, []));
  const report = {
    verification: 'DOM-only: JSDOM events, selected-occurrence ratios, readonly candidate semantics, raw text and serialization; browser appearance and Windows integration not verified',
    total: checks.length, passed: checks.filter(item => item.passed).length,
    failed: checks.filter(item => !item.passed).length, checks
  };
  fs.writeFileSync(path.join(__dirname, 'append-feedback-report.json'), JSON.stringify(report, null, 2) + '\n');
  process.stdout.write(JSON.stringify(report, null, 2) + '\n');
  if (report.failed) process.exitCode = 1;
} finally { w.close(); }

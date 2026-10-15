/* DOM-only contracts for explicit adoption of a single pending append slice. */
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
const check = (name, verify) => {
  try { verify(); checks.push({ name, passed: true }); }
  catch (error) { checks.push({ name, passed: false, error: error.stack }); }
};
const reset = () => { w.WindowsElements.dismiss(); w.eval('reset(true)'); q('#modal').classList.remove('open'); };
const rows = () => all('#elementList .element-row');
const ids = () => rows().map(row => row.dataset.elementId);
const values = () => rows().map(row => row.querySelector('.element-input').value);
const serialized = () => q('#elementSerialized').textContent;
const pending = () => all('#pendingElements .pending-add-token:not(.is-adopted) [data-raw-value]').map(node => node.getAttribute('data-raw-value'));
const buttons = () => all('#pendingElements .pending-add-button[data-add-pending]');
const button = index => q(`#pendingElements .pending-add-button[data-add-pending="${index}"]`);
const liveRow = id => q(`#elementList [data-element-id="${id}"]`);
const edit = (id, value) => {
  const input = liveRow(id).querySelector('.element-input');
  input.value = value; input.dispatchEvent(new w.Event('input', { bubbles: true }));
};
const cancel = () => q('#cancelDialog').click();
const persisted = () => w.eval('JSON.stringify({apps,windows,history,revision})');
const adopted = originalIds => rows().filter(row => !originalIds.includes(row.dataset.elementId));
const entries = (value, mode = '+append') => [{ value, mode }];
let applied = [], closed = 0;
function direct(raw, appendEntries = entries('B'), { whole = false, selectedValues, appendValues } = {}) {
  reset(); applied = []; closed = 0;
  const close = () => { closed++; w.WindowsElements.dismiss(); q('#modal').classList.remove('open'); };
  const options = { container:q('#modal'), name:'ADOPT_TEST', scope:'User', registryType:'REG_SZ', raw,
    selectedValues:selectedValues || (appendEntries || []).map(entry => entry.value), whole, close,
    onApply:value => applied.push(value) };
  if (appendEntries != null) options.appendEntries = appendEntries;
  if (appendValues !== undefined) options.appendValues = appendValues;
  w.WindowsElements.open(options);
  return ids();
}
function fixture({ actual = 'A;X', desired = 'A; B ;C', mode = '+append', whole = false, scope = 'User', name = 'ADOPT_TEST' } = {}) {
  reset(); w.__adoptFixture = { actual, desired, mode, whole, scope, name };
  w.eval(`{
    const f=__adoptFixture; selected=2;focusedVariable=0;
    curProfile().vars=[mkVar([f.name,f.scope,f.mode,f.desired,{special:f.whole}],0)];
    windows[f.scope+'|'+f.name.toUpperCase()]=f.actual;render();
  }`);
}
const openFixture = () => { q('#editWindowsElements').click(); return ids(); };
function assertFocusedAdded(originalIds, value, candidateIndex) {
  const fresh = adopted(originalIds);
  assert.equal(fresh.length, 1, 'One new live row');
  assert.ok(fresh[0].classList.contains('is-added'));
  assert.equal(fresh[0].querySelector('.element-input').value, value);
  assert.equal(doc.activeElement, button(candidateIndex), 'Focus stays on the re-created suggestion button');
  assert.equal(doc.activeElement.getAttribute('aria-pressed'), 'true');
  assert.ok(q(`#elementPreview .is-added[data-element-id="${fresh[0].dataset.elementId}"]`));
  return fresh[0].dataset.elementId;
}

try {
  check('Every actionable pending occurrence has an explicit native plus button and flattened selected index', () => {
    direct('A;A;;', [{value:'A; B ;',mode:'+append'},{value:'C; D ',mode:'-append'}]);
    assert.deepEqual(pending(), [' B ', 'C', ' D ']);
    assert.deepEqual(buttons().map(node => node.dataset.addPending), ['1','3','4']);
    for (const node of buttons()) {
      assert.equal(node.tagName, 'BUTTON'); assert.equal(node.type, 'button'); assert.equal(node.disabled, false);
      assert.match(node.textContent, /＋/);
      assert.ok(node.getAttribute('aria-label') || node.getAttribute('title'), 'Plus action has an accessible name');
      node.focus(); assert.equal(doc.activeElement, node, 'Native button accepts focus');
    }
    assert.equal(q('#pendingElements input, #pendingElements textarea, #pendingElements [data-element-id], #pendingElements [data-delete], #pendingElements [data-move-up], #pendingElements [data-move-down]'), null);
    assert.deepEqual(values(), ['A','A','','']); assert.equal(serialized(), 'A;A;;');
    assert.equal(q('#applyElements').disabled, true); assert.equal(q('#restoreElements').disabled, true);
    assert.equal(q('#pendingElements .is-duplicate'), null);
    cancel();
  });
  for (const mode of ['+append','-append','append']) {
    check(`${mode} adopts exactly one raw slice in the expected position with stable existing rows and focused suggestion`, () => {
      const originalIds = direct(' A ;;A;X; ', entries('A; B ;C',mode));
      const before = persisted(), originalMarkup = q('#elementOriginal').innerHTML;
      button(1).focus(); button(1).click();
      const newId = assertFocusedAdded(originalIds, ' B ', 1);
      assert.deepEqual(values(), mode === '+append' ? [' B ',' A ','','A','X',' '] : [' A ','','A','X',' ',' B ']);
      assert.deepEqual(ids().filter(id => id !== newId), originalIds, 'Existing stable IDs keep their order');
      assert.deepEqual(pending(), ['C']); assert.equal(q('#applyElements').disabled, false);
      assert.equal(q('#elementOriginal').innerHTML, originalMarkup);
      assert.equal(persisted(), before); assert.deepEqual(applied, []);
      const output = serialized(); q('#applyElements').click();
      assert.deepEqual(applied, [output]); assert.equal(closed, 1);
    });
    check(`Workbench ${mode} passes current mode into editor without writing page state before apply`, () => {
      fixture({mode}); const before = persisted(); const originalIds = openFixture();
      button(1).click(); assertFocusedAdded(originalIds, ' B ', 1);
      const expected = mode === '+append' ? ' B ;A;X' : 'A;X; B ';
      assert.equal(serialized(), expected); assert.equal(persisted(), before);
      assert.deepEqual(pending(), ['C']); q('#applyElements').click();
      assert.equal(w.eval("windows['User|ADOPT_TEST']"), expected);
      assert.equal(w.eval('history.length'), JSON.parse(before).history.length + 1);
      assert.equal(w.eval('revision'), JSON.parse(before).revision + 1);
      assert.equal(w.eval('curProfile().vars[0].defaultValue'), JSON.parse(before).apps[2].profiles.find(p=>p.name===w.eval('curProfile().name')).vars[0].defaultValue);
    });
  }
  check('Duplicate missing targets project one definite insertion; stale button clicks cannot duplicate it', () => {
    const originalIds = direct('A;A;;', entries(' B ;B;C','-append'));
    const oldFirst = button(0);
    assert.equal(button(1),null);
    assert.deepEqual(pending(), [' B ','C']);
    oldFirst.click(); oldFirst.click();
    // A stale queued control must remain harmless even if still under the delegated host.
    q('#pendingElements').appendChild(oldFirst); oldFirst.click(); oldFirst.remove();
    assert.deepEqual(values(), ['A','A','','',' B ']); assert.equal(adopted(originalIds).length,1);
    assert.deepEqual(pending(), ['C']); assert.equal(serialized(),'A;A;;; B '); cancel();
  });
  check('Clicking a retained button after a manual live edit rechecks whether its slice is still missing', () => {
    const originalIds = direct('A;X', entries('B','+append')), old = button(0);
    edit(originalIds[1], ' B '); assert.deepEqual(pending(), []); old.click();
    assert.deepEqual(values(), ['A',' B ']); assert.deepEqual(ids(), originalIds); cancel();
  });
  check('Deleting an adopted slice makes its original flattened candidate actionable again with a fresh live ID', () => {
    const originalIds = direct('A;X', entries('A;B;C','+append'));
    button(1).click(); const firstId = adopted(originalIds)[0].dataset.elementId;
    liveRow(firstId).querySelector('[data-delete]').click();
    assert.deepEqual(values(), ['A','X']); assert.deepEqual(pending(), ['B','C']);
    button(1).click(); const secondId = assertFocusedAdded(originalIds,'B',1);
    assert.notEqual(secondId, firstId); assert.deepEqual(values(), ['B','A','X']); cancel();
  });
  check('Repeated front adoptions insert at the current head in click order while original duplicate and empty rows retain order', () => {
    const originalIds = direct('X;;X;', entries('A;B;C','+append'));
    button(0).click(); button(1).click();
    assert.deepEqual(values(), ['B','A','X','','X','']);
    assert.deepEqual(ids().slice(2), originalIds); assert.deepEqual(pending(), ['C']); cancel();
  });
  check('Mixed source modes preserve each candidate direction instead of appending all selected specs to the tail', () => {
    const originalIds = direct('X;;Y', [{value:'F1;F2',mode:'+append'},{value:'T1',mode:'-append'},{value:'T2',mode:'append'}]);
    button(2).click(); button(0).click(); button(3).click(); button(1).click();
    assert.deepEqual(values(), ['F2','F1','X','','Y','T1','T2']);
    assert.deepEqual(ids().slice(2,5), originalIds); assert.deepEqual(pending(), []); cancel();
  });
  check('Adopted rows enter existing exact-raw duplicate feedback only after a later live edit creates a duplicate', () => {
    const originalIds = direct('A;A;;', entries('B','-append'));
    assert.equal(q('#pendingElements .is-duplicate'), null); button(0).click();
    const addedId = adopted(originalIds)[0].dataset.elementId;
    assert.equal(liveRow(addedId).classList.contains('is-duplicate'),false);
    edit(addedId,'A');
    assert.equal(liveRow(addedId).classList.contains('is-added'),true);
    assert.equal(liveRow(addedId).classList.contains('is-duplicate'),true);
    assert.equal(liveRow(addedId).dataset.duplicateCount,'3');
    assert.equal(serialized(),'A;A;;;A'); assert.deepEqual(pending(),['B']); cancel();
  });
  check('Restore exactly recovers original raw whitespace, duplicates, empty slices and IDs after adoption plus live edits', () => {
    const raw = ' A ;;A; ;', originalIds = direct(raw, entries('B; C ','+append'));
    button(0).click(); edit(originalIds[0],'changed'); liveRow(originalIds[2]).querySelector('[data-delete]').click();
    q('#restoreElements').click();
    assert.deepEqual(values(), [' A ','','A',' ','']); assert.deepEqual(ids(),originalIds);
    assert.equal(serialized(),raw); assert.deepEqual(pending(),['B',' C ']);
    assert.equal(q('#applyElements').disabled,true); assert.equal(q('#restoreElements').disabled,true);
    assert.equal(q('#elementList .is-added'),null); assert.deepEqual(applied,[]); cancel();
  });
  check('Cancel after adoption leaves snapshot, configuration, history and revision exact and reopening has original raw rows', () => {
    fixture({actual:' A ;;A; ;',desired:'A; B ;C'}); const before = persisted(); openFixture();
    button(1).click(); cancel(); assert.equal(persisted(),before);
    openFixture(); assert.deepEqual(values(),[' A ','','A',' ','']);
    assert.equal(serialized(),' A ;;A; ;'); assert.deepEqual(pending(),[' B ','C']);
    assert.equal(q('#applyElements').disabled,true); cancel();
  });
  check('Apply stores only current live rows including adopted and manually edited values while unclicked candidates stay excluded', () => {
    fixture({mode:'-append'}); const before = persisted(), originalIds = openFixture();
    button(1).click(); edit(originalIds[1],'Y');
    assert.deepEqual(pending(),['C']); assert.equal(persisted(),before); q('#applyElements').click();
    assert.equal(w.eval("windows['User|ADOPT_TEST']"),'A;Y; B ');
    assert.equal(q('#modal').classList.contains('open'),false);
    openFixture(); assert.deepEqual(values(),['A','Y',' B ']); assert.deepEqual(pending(),['C']); cancel();
  });
  for (const raw of [undefined,'']) {
    check(`Adoption with ${raw===undefined?'undefined':'empty-string'} original distinguishes no rows from one preserved empty row`, () => {
      const originalIds = direct(raw,entries(' A ;B','-append'));
      assert.deepEqual(values(),raw===undefined?[]:['']); button(0).click();
      assertFocusedAdded(originalIds,' A ',0);
      assert.deepEqual(values(),raw===undefined?[' A ']:['',' A ']);
      assert.equal(serialized(),raw===undefined?' A ':'; A ');
      q('#restoreElements').click(); assert.deepEqual(ids(),originalIds);
      assert.deepEqual(values(),raw===undefined?[]:['']); assert.equal(q('#applyElements').disabled,true);
      assert.deepEqual(pending(),[' A ','B']); cancel();
    });
  }
  check('Appending a NUL-containing candidate participates in existing validation and cannot apply', () => {
    const originalIds = direct('A',entries('B\0C','-append'));
    const adoptButton = button(0); assert.ok(adoptButton); adoptButton.click();
    assert.equal(adopted(originalIds).length,1); assert.equal(serialized(),'A;B\0C');
    assert.match(q('#elementValidation').textContent,/NUL/); assert.equal(q('#applyElements').disabled,true);
    q('#applyElements').click(); assert.deepEqual(applied,[]);
    q('#restoreElements').click(); assert.deepEqual(values(),['A']); cancel();
  });
  check('Unknown and replace entry modes cannot offer an adoption action', () => {
    direct('A',[{value:'unknown',mode:'future-mode'},{value:'replace',mode:'replace'},{value:'missing',mode:undefined}]);
    assert.deepEqual(buttons(),[]); assert.deepEqual(values(),['A']); assert.equal(q('#applyElements').disabled,true); cancel();
  });
  check('Legacy appendValues without mode stays readonly even with missing values', () => {
    direct('A',null,{appendValues:[' B ;C'],selectedValues:['A; B ;C']});
    assert.deepEqual(pending(),[' B ','C']); assert.deepEqual(buttons(),[]);
    assert.deepEqual(values(),['A']); assert.equal(q('#applyElements').disabled,true); cancel();
  });
  check('Whole-string and replace workbench contexts do not create candidates or plus actions', () => {
    direct('A;X',entries('B;C','+append'),{whole:true});
    assert.deepEqual(values(),['A;X']); assert.deepEqual(pending(),[]); assert.deepEqual(buttons(),[]); cancel();
    for (const options of [{mode:'replace'},{mode:'+append',whole:true}]) {
      fixture(options); openFixture(); assert.deepEqual(pending(),[]); assert.deepEqual(buttons(),[]); cancel();
    }
  });
  check('Empty raw selected slices remain excluded from adoption while flattened nonempty indexes retain their gaps', () => {
    direct('X',entries(' ; ; A ;; B ;','+append'));
    assert.deepEqual(pending(),[' A ',' B ']); assert.deepEqual(buttons().map(node=>node.dataset.addPending),['2','4']);
    button(4).click(); assert.deepEqual(values(),[' B ','X']); assert.deepEqual(pending(),[' A ']); cancel();
  });
  check('Standalone pendingMarkup remains readonly outside editor, and omitted mode metadata remains readonly within editing markup', () => {
    for (const [editing,modes] of [[false,['+append']], [true,[]]]) {
      const host = doc.createElement('div'); host.innerHTML=w.WindowsElements.pendingMarkup('A',['A;B'],false,editing,modes);
      assert.ok(host.querySelector('[data-raw-value="B"]'));
      assert.equal(host.querySelector('button,input,textarea,[data-element-id],[data-delete],[data-move-up],[data-move-down]'),null);
    }
  });
  check('Windows direct editing ignores mixed scheme directions and preserves scope isolation', () => {
    fixture({actual:'U;;U',desired:' FRONT ',mode:'+append',name:'ADOPT_TEST'});
    w.eval(`{
      curProfile().vars.push(mkVar(['ADOPT_TEST','User','-append',' BACK '],1));
      curProfile().vars.push(mkVar(['ADOPT_TEST','User','append','CUSTOM'],2));
      curProfile().vars.push(mkVar(['ADOPT_OTHER','User','replace','REPLACEMENT'],3));
      curProfile().vars.push(mkVar(['ADOPT_TEST','System','+append','SYS_FRONT'],4));
      curProfile().vars.push(mkVar(['ADOPT_TEST','System','-append','SYS_BACK'],5));
      windows['System|ADOPT_TEST']='S';go('windows');
    }`);
    for (const target of ['User|ADOPT_TEST','System|ADOPT_TEST']) {
      const host = q(`[data-windows-value="${target}"]`).parentElement;
      assert.equal(host.querySelector('.pending-additions'),null);
    }
    const before = persisted(); q('[data-windows-edit="User|ADOPT_TEST"]').click();
    assert.deepEqual(pending(),[]);assert.deepEqual(values(),['U','','U']);
    assert.equal(q('#pendingElements').hidden,true);assert.equal(persisted(),before);
    assert.equal(q('#applyElements').disabled,true);cancel();
    assert.equal(w.eval("windows['System|ADOPT_TEST']"),'S');
    q('[data-windows-edit="System|ADOPT_TEST"]').click();
    assert.deepEqual(pending(),[]);assert.deepEqual(values(),['S']);
    assert.equal(q('#pendingElements').hidden,true);cancel();
    assert.equal(w.eval("windows['System|ADOPT_TEST']"),'S');
  });
  check('Adoption workflows emit no JSDOM runtime errors', () => assert.deepEqual(errors,[]));
  const report = {
    verification:'DOM-only: JSDOM native button focus/click, adoption direction, raw slices, stable live-row IDs, revalidation, restore/cancel/apply and page state; native browser keyboard activation, visual layout and Windows integration not verified',
    total:checks.length, passed:checks.filter(item=>item.passed).length, failed:checks.filter(item=>!item.passed).length, checks
  };
  fs.writeFileSync(path.join(__dirname,'adopt-pending-report.json'),JSON.stringify(report,null,2)+'\n');
  process.stdout.write(JSON.stringify(report,null,2)+'\n');
  if (report.failed) process.exitCode=1;
} finally { w.close(); }

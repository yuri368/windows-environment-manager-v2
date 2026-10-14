/* DOM-only workflow contracts. Does not run a browser or write Windows values. */
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
const rows = () => all('#elementList .element-row');
const idOf = element => element.dataset.elementId;
const row = id => q(`#elementList .element-row[data-element-id="${id}"]`);
const inputFor = id => row(id).querySelector('.element-input');
const tokens = side => all(`#element${side} .diff-token`);
const token = (side, id) => q(`#element${side} .diff-token[data-element-id="${id}"]`);
const serialized = () => q('#elementSerialized').textContent;
const snapshot = () => w.eval('JSON.stringify(windows)');
const cancel = () => q('#cancelDialog').click();
const open = (raw, whole = false) => {
  w.eval('reset(true)');
  w.__feedbackRaw = raw;
  w.__feedbackWhole = whole;
  w.eval("windows['User|FEEDBACK_REGRESSION'] = __feedbackRaw; openWindowsElements('User', 'FEEDBACK_REGRESSION', [], __feedbackWhole)");
  q('.element-raw').open = true;
  return rows().map(idOf);
};
const edit = (id, value) => {
  const element = inputFor(id);
  element.value = value;
  element.dispatchEvent(new w.Event('input', { bubbles: true }));
};
const pointer = (element, type, relatedTarget = null) => {
  assert.ok(element, `Missing ${type} target`);
  element.dispatchEvent(new w.MouseEvent(type, { bubbles: type !== 'pointerleave', relatedTarget }));
};
const duplicate = (element, count) => {
  assert.ok(element, 'Expected duplicate feedback member');
  assert.equal(element.classList.contains('is-duplicate'), count > 1);
  if (count > 1) {
    assert.equal(element.getAttribute('data-duplicate-count'), String(count));
    assert.match(element.textContent, new RegExp(`重复\\s*×\\s*${count}`));
  } else {
    assert.ok([null, '1'].includes(element.getAttribute('data-duplicate-count')));
    assert.doesNotMatch(element.textContent, /重复\s*×\s*\d/);
  }
};
const groupCounts = (side, ids, counts) => ids.forEach((id, index) => duplicate(token(side, id), counts[index]));
const rowCounts = (ids, counts) => ids.forEach((id, index) => duplicate(row(id), counts[index]));
const highlight = id => {
  const actual = all('#modal .is-related-highlight').map(element => `${element.classList.contains('element-row') ? 'row' : element.closest('#elementOriginal') ? 'original' : 'preview'}:${idOf(element)}`).sort();
  const expected = id === null ? [] : [row(id) ? `row:${id}` : null, token('Original', id) ? `original:${id}` : null, token('Preview', id) ? `preview:${id}` : null].filter(Boolean).sort();
  assert.deepEqual(actual, expected, 'Highlight must follow one stable ID across existing representations');
};
const check = (name, verify) => {
  try { verify(); checks.push({ name, passed: true }); }
  catch (error) { checks.push({ name, passed: false, error: error.stack }); }
};

try {
  check('Initial duplicates annotate every member independently in original, preview and editing rows', () => {
    const ids = open('A;A;B');
    for (const side of ['Original', 'Preview']) groupCounts(side, ids, [2, 2, 1]);
    rowCounts(ids, [2, 2, 1]);
    assert.equal(serialized(), 'A;A;B');
    assert.equal(q('#applyElements').disabled, true);
  });
  check('Editing second A to B keeps original A x2 and marks both live B members x2', () => {
    const ids = open('A;A;B'), before = snapshot();
    edit(ids[1], 'B');
    groupCounts('Original', ids, [2, 2, 1]);
    groupCounts('Preview', ids, [1, 2, 2]);
    rowCounts(ids, [1, 2, 2]);
    assert.ok(token('Original', ids[1]).classList.contains('is-replaced'));
    assert.ok(token('Preview', ids[1]).classList.contains('is-replaced'));
    assert.equal(serialized(), 'A;B;B');
    assert.equal(snapshot(), before);
    assert.equal(q('#applyElements').disabled, false);
    q('#applyElements').click();
    assert.equal(w.eval("windows['User|FEEDBACK_REGRESSION']"), 'A;B;B');
  });
  check('Deleting one A preserves original duplicates but excludes preview deletion ghost from live counts', () => {
    const ids = open('A;A;B');
    row(ids[1]).querySelector('[data-delete]').click();
    groupCounts('Original', ids, [2, 2, 1]);
    groupCounts('Preview', ids, [1, 1, 1]);
    rowCounts([ids[0], ids[2]], [1, 1]);
    assert.ok(token('Preview', ids[1]).classList.contains('is-removed'));
    assert.equal(row(ids[1]), null);
    assert.equal(serialized(), 'A;B');
  });
  check('Deleting changed duplicate does not count the ghost old value or the removed new value', () => {
    const ids = open('A;A;B');
    edit(ids[1], 'B');
    row(ids[1]).querySelector('[data-delete]').click();
    groupCounts('Original', ids, [2, 2, 1]);
    groupCounts('Preview', ids, [1, 1, 1]);
    assert.equal(token('Preview', ids[1]).querySelector('.diff-value').getAttribute('data-raw-value'), 'A');
    assert.equal(serialized(), 'A;B');
  });
  check('Adding a duplicate marks existing and new members, then changing it back removes stale marks', () => {
    const ids = open('A;B');
    q('#addElement').click();
    const added = idOf(rows().at(-1));
    edit(added, 'A');
    groupCounts('Original', ids, [1, 1]);
    groupCounts('Preview', [...ids, added], [2, 1, 2]);
    rowCounts([...ids, added], [2, 1, 2]);
    assert.ok(token('Preview', added).classList.contains('is-added'));
    assert.equal(token('Original', added), null);
    assert.equal(serialized(), 'A;B;A');
    edit(added, 'C');
    groupCounts('Preview', [...ids, added], [1, 1, 1]);
    rowCounts([...ids, added], [1, 1, 1]);
  });
  check('Raw equality keeps A, a, leading/trailing whitespace, slash and case variants distinct', () => {
    const raw = 'A;a; A;A ;C:\\Bin;c:\\Bin;C:/Bin;C:\\Bin\\';
    const ids = open(raw);
    for (const side of ['Original', 'Preview']) groupCounts(side, ids, ids.map(() => 1));
    rowCounts(ids, ids.map(() => 1));
    assert.equal(serialized(), raw);
    edit(ids[1], 'A');
    groupCounts('Preview', ids, [2, 2, 1, 1, 1, 1, 1, 1]);
    groupCounts('Original', ids, ids.map(() => 1));
  });
  check('Repeated empty segments are real duplicate members and trailing empty values survive application', () => {
    const ids = open('A;;B;');
    assert.equal(ids.length, 4);
    for (const side of ['Original', 'Preview']) groupCounts(side, ids, [1, 2, 1, 2]);
    rowCounts(ids, [1, 2, 1, 2]);
    edit(ids[2], 'C');
    assert.equal(serialized(), 'A;;C;');
    q('#applyElements').click();
    assert.equal(w.eval("windows['User|FEEDBACK_REGRESSION']"), 'A;;C;');
  });
  check('Whole special strings retain semicolons without internal duplicate warnings', () => {
    const ids = open('A;A;;', true);
    assert.equal(ids.length, 1);
    assert.equal(inputFor(ids[0]).tagName, 'TEXTAREA');
    for (const side of ['Original', 'Preview']) groupCounts(side, ids, [1]);
    rowCounts(ids, [1]);
    edit(ids[0], 'B;B;;');
    for (const side of ['Original', 'Preview']) groupCounts(side, ids, [1]);
    rowCounts(ids, [1]);
    assert.equal(serialized(), 'B;B;;');
    q('#applyElements').click();
    assert.equal(w.eval("windows['User|FEEDBACK_REGRESSION']"), 'B;B;;');
  });
  check('Moving, replacing, adding and removing retain all four semantic inline token states', () => {
    const ids = open('A;B;C');
    edit(ids[0], 'Z');
    row(ids[2]).querySelector('[data-move-up]').click();
    row(ids[1]).querySelector('[data-delete]').click();
    q('#addElement').click();
    const added = idOf(rows().at(-1));
    edit(added, 'N');
    for (const side of ['Original', 'Preview']) {
      assert.ok(token(side, ids[0]).classList.contains('is-replaced'));
      assert.ok(token(side, ids[1]).classList.contains('is-removed'));
    }
    // With only A/C remaining, move is judged against remaining original identities.
    row(ids[2]).querySelector('[data-move-up]').click();
    for (const side of ['Original', 'Preview']) assert.ok(token(side, ids[2]).classList.contains('is-moved'));
    assert.ok(token('Preview', added).classList.contains('is-added'));
    assert.equal(serialized(), 'C;Z;N');
    const css = fs.readFileSync(path.join(root, 'elements-ui.css'), 'utf8');
    assert.match(css, /\.diff-token\b[^{]*\{[^}]*display:\s*inline(?:-flex)?(?:\s|;)/);
    for (const state of ['moved', 'replaced', 'added', 'removed']) assert.match(css, new RegExp(`\\.diff-token\\.is-${state}\\b[^{]*\\{`));
  });
  check('Original pointer hover distinguishes equal-valued elements by stable ID', () => {
    const ids = open('A;A;B');
    pointer(token('Original', ids[1]), 'pointerover');
    highlight(ids[1]);
    pointer(token('Original', ids[1]), 'pointerout', q('#elementStatus'));
    highlight(null);
  });
  check('Preview pointer hover is symmetric with original hover and row hover', () => {
    const ids = open('A;A;B');
    pointer(token('Preview', ids[0]), 'pointerover');
    highlight(ids[0]);
    pointer(token('Preview', ids[0]), 'pointerout', row(ids[2]));
    pointer(row(ids[2]), 'pointerover');
    highlight(ids[2]);
    pointer(q('#modal'), 'pointerleave');
    highlight(null);
  });
  check('Token child-to-child pointer transitions preserve hover without highlight flicker', () => {
    const ids = open('A;A;B');
    edit(ids[1], 'B');
    const element = token('Preview', ids[1]);
    const from = element.querySelector('.diff-value'), to = element.querySelector('.diff-label');
    assert.ok(from && to, 'Fixture needs separate token child nodes');
    pointer(from, 'pointerover');
    highlight(ids[1]);
    const watched = all('#modal [data-element-id]');
    const observer = new w.MutationObserver(() => {});
    watched.forEach(node => observer.observe(node, { attributes: true, attributeFilter: ['class'] }));
    pointer(from, 'pointerout', to);
    highlight(ids[1]);
    pointer(to, 'pointerover', from);
    highlight(ids[1]);
    const classMutations = observer.takeRecords();
    observer.disconnect();
    assert.equal(classMutations.length, 0, 'Moving within one token must not toggle highlight classes');
    pointer(to, 'pointerout', q('#elementStatus'));
    highlight(null);
  });
  check('Input focus links all representations; hover overrides focus and leaving restores focused ID', () => {
    const ids = open('A;A;B');
    inputFor(ids[0]).focus();
    highlight(ids[0]);
    pointer(token('Preview', ids[1]), 'pointerover');
    highlight(ids[1]);
    pointer(token('Preview', ids[1]), 'pointerout', q('#elementStatus'));
    highlight(ids[0]);
    pointer(token('Original', ids[2]), 'pointerover');
    inputFor(ids[1]).focus();
    highlight(ids[2]);
    pointer(q('#modal'), 'pointerleave');
    highlight(ids[1]);
    q('#addElement').focus();
    highlight(null);
  });
  check('Programmatic token focus links both sides without adding tokens to normal Tab order', () => {
    const ids = open('A;A;B');
    for (const side of ['Original', 'Preview']) for (const element of tokens(side)) {
      assert.equal(element.getAttribute('tabindex'), '-1');
      assert.equal(element.tabIndex, -1);
    }
    token('Original', ids[1]).focus();
    highlight(ids[1]);
    token('Preview', ids[2]).focus();
    highlight(ids[2]);
    q('#closeDialog').focus();
    highlight(null);
  });
  check('Typing and row redraw preserve focused identity highlighting', () => {
    const ids = open('A;A;B');
    inputFor(ids[1]).focus();
    edit(ids[1], 'B');
    highlight(ids[1]);
    assert.equal(doc.activeElement, inputFor(ids[1]));
    row(ids[1]).querySelector('[data-move-down]').click();
    assert.equal(idOf(doc.activeElement.closest('.element-row')), ids[1]);
    highlight(ids[1]);
    assert.equal(serialized(), 'A;B;B');
  });
  check('Hover changes classes only: node identity, comparison content and serialization remain untouched', () => {
    const ids = open('A;A;B');
    const originalNodes = all('#elementOriginal *, #elementPreview *, #elementList *');
    const result = q('#elementSerialized'), before = serialized();
    const descriptor = Object.getOwnPropertyDescriptor(w.Node.prototype, 'textContent');
    let serializationWrites = 0;
    Object.defineProperty(result, 'textContent', { configurable: true, get() { return descriptor.get.call(this); }, set(value) { serializationWrites++; descriptor.set.call(this, value); } });
    const observer = new w.MutationObserver(() => {});
    for (const selector of ['#elementOriginal', '#elementPreview', '#elementList', '#elementSerialized']) observer.observe(q(selector), { childList: true, subtree: true, characterData: true });
    pointer(token('Original', ids[0]), 'pointerover');
    highlight(ids[0]);
    pointer(token('Original', ids[0]), 'pointerout', token('Preview', ids[2]));
    pointer(token('Preview', ids[2]), 'pointerover');
    highlight(ids[2]);
    pointer(token('Preview', ids[2]), 'pointerout', q('#elementStatus'));
    const mutations = observer.takeRecords();
    observer.disconnect();
    delete result.textContent;
    assert.equal(mutations.length, 0, 'Hover must not rewrite content or comparison DOM');
    assert.equal(serializationWrites, 0, 'Hover must not reserialize');
    assert.equal(serialized(), before);
    assert.deepEqual(all('#elementOriginal *, #elementPreview *, #elementList *'), originalNodes);
  });
  check('Deletion clears deleted hover identity, focuses remaining row and does not leave ghost highlight', () => {
    const ids = open('A;A;B');
    inputFor(ids[0]).focus();
    pointer(token('Preview', ids[1]), 'pointerover');
    row(ids[1]).querySelector('[data-delete]').click();
    assert.equal(row(ids[1]), null);
    assert.equal(token('Preview', ids[1]).classList.contains('is-related-highlight'), false);
    assert.equal(token('Original', ids[1]).classList.contains('is-related-highlight'), false);
    highlight(idOf(doc.activeElement.closest('.element-row')));
  });
  check('Restoring resets duplicate annotations and clears hover of removed added identities', () => {
    const ids = open('A;A;B');
    edit(ids[1], 'B');
    q('#addElement').click();
    const added = idOf(rows().at(-1));
    edit(added, 'B');
    pointer(token('Preview', added), 'pointerover');
    highlight(added);
    q('#restoreElements').click();
    assert.deepEqual(rows().map(idOf), ids);
    assert.equal(token('Preview', added), null);
    assert.equal(serialized(), 'A;A;B');
    for (const side of ['Original', 'Preview']) groupCounts(side, ids, [2, 2, 1]);
    rowCounts(ids, [2, 2, 1]);
    highlight(idOf(doc.activeElement.closest('.element-row')));
    assert.equal(q('#applyElements').disabled, true);
  });
  check('Close and cancel dismiss highlights; reopening starts without previous hover or focused IDs', () => {
    for (const closeSelector of ['#cancelDialog', '#closeDialog']) {
      const ids = open('A;A;B');
      inputFor(ids[0]).focus();
      pointer(token('Original', ids[1]), 'pointerover');
      highlight(ids[1]);
      const stale = token('Original', ids[1]);
      q(closeSelector).click();
      assert.equal(q('#modal').classList.contains('open'), false);
      highlight(null);
      pointer(stale, 'pointerout', q('#footer'));
      highlight(null);
      const nextIds = open('A;A;B');
      highlight(null);
      assert.equal(nextIds.some(id => ids.includes(id)), false);
      pointer(token('Preview', nextIds[2]), 'pointerover');
      highlight(nextIds[2]);
      cancel();
      highlight(null);
    }
  });
  check('Applying duplicate edits dismisses all highlight state while preserving raw output', () => {
    const ids = open('A;A;B');
    inputFor(ids[1]).focus();
    edit(ids[1], 'B');
    pointer(token('Original', ids[0]), 'pointerover');
    q('#applyElements').click();
    assert.equal(w.eval("windows['User|FEEDBACK_REGRESSION']"), 'A;B;B');
    highlight(null);
  });
  check('Element feedback workflows emit no JSDOM runtime errors', () => assert.deepEqual(errors, []));
  const report = {
    verification: 'DOM-only: JSDOM event, semantic class, raw serialization and node identity checks; real browser visual rendering, native pointer behavior and Windows integration not verified',
    total: checks.length, passed: checks.filter(item => item.passed).length,
    failed: checks.filter(item => !item.passed).length, checks
  };
  fs.writeFileSync(path.join(__dirname, 'element-feedback-report.json'), JSON.stringify(report, null, 2) + '\n');
  process.stdout.write(JSON.stringify(report, null, 2) + '\n');
  if (report.failed) process.exitCode = 1;
} finally {
  w.close();
}

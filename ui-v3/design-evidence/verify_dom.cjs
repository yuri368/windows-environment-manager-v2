/* DOM interaction checks only: no browser rendering, registry or backend. */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { JSDOM, VirtualConsole } = require('jsdom');
const html = fs.readFileSync(path.join(__dirname, '../temp.html'), 'utf8');
const elementScript = fs.readFileSync(path.join(__dirname, '../windows-elements.js'), 'utf8');
const vm = require('node:vm');
new vm.Script(elementScript);
new vm.Script(html.match(/<script>([\s\S]*?)<\/script>/)[1]);
const errors = [], results = [];
const vc = new VirtualConsole();
vc.on('jsdomError', e => errors.push(e.message));
const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true,
  virtualConsole: vc, beforeParse(w) { w.confirm = () => true; w.eval(elementScript); } });
const w = dom.window, doc = w.document;
const q = s => doc.querySelector(s);
const all = s => [...doc.querySelectorAll(s)];
const change = (el, value) => { el.value = value; el.dispatchEvent(new w.Event('change', { bubbles: true })); };
const input = (el, value) => { el.value = value; el.dispatchEvent(new w.Event('input', { bubbles: true })); };
const keyboard = (el, key, shiftKey = false) => el.dispatchEvent(new w.KeyboardEvent('keydown', {key, shiftKey, bubbles: true, cancelable: true}));
function check(name, fn) { fn(); results.push({name, passed: true}); }

check('Startup: seven applications and Maven current scheme', () => {
  assert.equal(all('[data-app]').length, 7);
  assert.equal(q('.app-title').textContent, 'Maven');
  assert.equal(all('#varRows [data-row]').length, 2);
});
check('Scheme menu: arrows, Escape, focus and exact new scheme', () => {
  const trigger = q('#planButton'); trigger.focus(); keyboard(trigger, 'ArrowDown');
  assert.equal(trigger.getAttribute('aria-expanded'), 'true');
  keyboard(doc.activeElement, 'End'); assert.equal(doc.activeElement.dataset.plan, '3.3.3');
  keyboard(doc.activeElement, 'Escape'); assert.equal(trigger.getAttribute('aria-expanded'), 'false');
  assert.equal(doc.activeElement, trigger);
  trigger.click(); q('[data-plan="3.3.3"]').click();
  assert.match(q('#planButton').textContent, /3\.3\.3/);
  assert.equal(doc.activeElement.id, 'planButton');
});
check('Search and keyboard application selection', () => {
  input(q('#appSearch'), 'Python'); assert.equal(all('[data-app]').length, 1);
  keyboard(q('[data-app]'), ' '); assert.equal(q('.app-title').textContent, 'Python');
  input(q('#appSearch'), '');
});
check('Exact raw custom string retained in running draft', () => {
  const exact = '  C:\\Tools\\A;C:\\Tools\\B  ';
  input(q('#varRows .vinput'), exact);
  assert.equal(q('#compareDesired').textContent, exact);
  assert.equal(q('#varRows .source').value, '自定义值');
  assert.match(q('.footer-summary').textContent, /输入草稿/);
});
check('Preview cancel and keyboard trap preserve mock Windows value', () => {
  const before = q('#compareActual').textContent;
  const trigger = q('#previewButton'); trigger.focus(); trigger.click();
  assert.equal(q('#modal').classList.contains('open'), true);
  const cancel = q('#cancelDialog'), first = q('#closeDialog'), last = q('#confirmDialog');
  last.focus(); keyboard(last, 'Tab'); assert.equal(doc.activeElement, first);
  keyboard(first, 'Tab', true); assert.equal(doc.activeElement, last);
  cancel.click(); assert.equal(q('#modal').classList.contains('open'), false);
  assert.equal(q('#compareActual').textContent, before);
  assert.equal(doc.activeElement.id, 'previewButton');
});
check('Batch preview contains selected app names', () => {
  q('#selectAll').click(); assert.equal(all('.appcheck:checked').length, 7);
  q('#previewButton').click(); const text = q('.dialogbody').textContent;
  for (const app of ['Java','Python','Maven','Hadoop','Scala','Git','VSCode']) assert.ok(text.includes(app));
  q('#cancelDialog').click(); q('#selectNone').click();
});
check('Editor defaults to one scheme and All view has explicit groups', () => {
  q('#configBtn').click(); assert.equal(q('#editorView').value, 'current');
  assert.equal(all('.scheme-group').length, 1); assert.equal(all('[data-tableplan]').length, 2);
  change(q('#editorView'), 'all'); assert.equal(all('.scheme-group').length, 2);
  assert.equal(all('[data-tableplan]').length, 4);
  q('[data-tableplan="3.3.3"]').click();
  assert.match(q('#editorInspector').textContent, /Maven \/ 3\.3\.3/);
});
check('Configuration exact string only changes selected scheme', () => {
  const exact = '  C:\\New\\Maven;Literal\\Value  ';
  input(q('#cfgDefault'), exact);
  assert.equal(q('[data-tableplan="3.3.3"] td:last-child').textContent, exact);
  assert.notEqual(q('[data-tableplan="3.9.11"] td:last-child').textContent, exact);
});
check('Raw TOML and differences remain available and labelled as demo', () => {
  q('[data-edtab="diff"]').click(); assert.match(q('#sourceText').textContent, /Literal/);
  q('[data-edtab="raw"]').click(); assert.match(q('#sourceText').textContent, /default_value/);
  assert.match(q('.pageh').textContent, /示例 TOML/);
});
check('Windows and History views preserved', () => {
  q('[data-view="windows"]').click(); assert.ok(q('#screen').textContent.includes('Windows'));
  q('[data-view="history"]').click(); assert.ok(q('#screen').textContent.includes('历史'));
});
check('No runtime errors; local polish stylesheet exists', () => {
  assert.deepEqual(errors, []);
  assert.ok(fs.existsSync(path.join(__dirname, '../ui-polish.css')));
  assert.match(html, /未连接系统/);
});
const report = { verification: 'JSDOM interactions; visual rendering and real backend not verified', checks: results };
fs.writeFileSync(path.join(__dirname, 'dom-report.json'), JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify(report, null, 2));
w.close();

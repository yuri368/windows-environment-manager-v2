'use strict';
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {JSDOM, VirtualConsole} = require('jsdom');

const root = path.resolve(__dirname, '..');
const checks = [], runtimeErrors = [], vc = new VirtualConsole();
vc.on('jsdomError', error => runtimeErrors.push(error.message));
const dom = new JSDOM(fs.readFileSync(path.join(root, 'temp.html'), 'utf8'), {
  runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole: vc,
  beforeParse(window) {
    window.confirm = () => true;
    for (const file of ['demo-plan.js', 'demo-configs.js', 'windows-elements.js', 'config-format.js', 'config-editor.js'])
      window.eval(fs.readFileSync(path.join(root, file), 'utf8'));
  }
});
const w = dom.window, q = selector => w.document.querySelector(selector);
const ev = source => w.eval(source);
const json = source => JSON.parse(ev(`JSON.stringify(${source})`));
const check = (name, run) => {
  try { run(); checks.push({name, passed: true}); }
  catch (error) { checks.push({name, passed: false, error: error.stack}); }
};
const input = (selector, value) => {
  const node = q(selector); assert.ok(node, `Missing ${selector}`);
  node.value = value; node.dispatchEvent(new w.Event('input', {bubbles: true}));
};
const change = (selector, value) => {
  const node = q(selector); assert.ok(node, `Missing ${selector}`);
  node.value = value; node.dispatchEvent(new w.Event('change', {bubbles: true}));
};
const click = selector => { const node = q(selector); assert.ok(node, `Missing ${selector}`); node.click(); };
const setup = (app = 7, profile = '团队默认') => {
  ev('closeModal();reset(true)');
  ev(`editor={app:${app},profile:${JSON.stringify(profile)},varIdx:0,tab:'visual'};go('config')`);
};
const create = (kind, fields, context) => w.createConfigEntry(kind, fields, context);
const unchanged = () => ({apps: ev('JSON.stringify(apps)'), environment: ev('JSON.stringify({windows,history})')});
const assertRejectedWithoutMutation = (kind, fields, context) => {
  const before = unchanged(), result = create(kind, fields, context);
  assert.equal(result.ok, false, JSON.stringify(result));
  assert.ok(result.error, JSON.stringify(result));
  assert.deepEqual(unchanged(), before);
};

check('Add buttons open one inline form and cancellation leaves all data untouched', () => {
  setup(); const before = unchanged();
  for (const button of ['#cfgAddApp', '#cfgAddProfile', '#cfgAddVariable']) {
    click(button);
    assert.ok(q('#cfgCreateForm'));
    for (const id of ['#cfgCreateName', '#cfgCreateScope', '#cfgCreateMode', '#cfgCreateSubmit', '#cfgCreateCancel']) assert.ok(q(id));
    if (button === '#cfgAddApp') { assert.ok(q('#cfgCreateAppName')); assert.ok(q('#cfgCreateProfileName')); }
    if (button === '#cfgAddProfile') assert.ok(q('#cfgCreateProfileName'));
    click('#cfgCreateCancel');
    assert.equal(q('#cfgCreateForm'), null);
    assert.deepEqual(unchanged(), before);
  }
});

check('App button creates one app, its default profile and first empty variable', () => {
  setup(); const before = ev('apps.length'), environment = unchanged().environment;
  click('#cfgAddApp'); input('#cfgCreateAppName', '新建工具'); input('#cfgCreateProfileName', '默认方案');
  input('#cfgCreateName', 'TOOL_HOME'); click('#cfgCreateSubmit');
  const created = json('apps[apps.length-1]');
  assert.equal(ev('apps.length'), before + 1);
  assert.equal(created.name, '新建工具'); assert.equal(created.default, '默认方案');
  assert.equal(created.profiles.length, 1); assert.equal(created.profiles[0].name, '默认方案');
  assert.equal(created.profiles[0].vars.length, 1);
  assert.equal(created.profiles[0].vars[0].defaultValue, '');
  assert.equal(created.profiles[0].vars[0].name, 'TOOL_HOME');
  assert.equal(created.profiles[0].vars[0].scope, 'User');
  assert.equal(created.profiles[0].vars[0].mode, 'replace');
  assert.deepEqual(json('({app:editor.app,profile:editor.profile,varIdx:editor.varIdx})'), {app:before,profile:'默认方案',varIdx:0});
  assert.equal(q('#cfgName')?.textContent, 'TOOL_HOME');
  assert.equal(unchanged().environment, environment);
});

check('Profile button creates an appended profile with one selected variable', () => {
  setup(); const before = ev('apps[7].profiles.length');
  click('#cfgAddProfile'); input('#cfgCreateProfileName', '新方案'); input('#cfgCreateName', 'DEMO_HOME');
  change('#cfgCreateScope', 'System'); change('#cfgCreateMode', '+append'); click('#cfgCreateSubmit');
  const profile = json('apps[7].profiles[apps[7].profiles.length-1]');
  assert.equal(ev('apps[7].profiles.length'), before + 1);
  assert.equal(profile.name, '新方案'); assert.equal(profile.vars.length, 1);
  assert.equal(profile.vars[0].name, 'DEMO_HOME'); assert.equal(profile.vars[0].scope, 'System');
  assert.equal(profile.vars[0].mode, '+append'); assert.equal(profile.vars[0].defaultValue, '');
  assert.equal(ev('apps[7].default'), '团队默认'); assert.equal(ev('editor.profile'), '新方案');
  assert.equal(q('#cfgName')?.textContent, 'DEMO_HOME');
});

check('Variable button appends a mkVar-shaped variable and selects it', () => {
  setup(); const before = ev('configProfile().vars.length');
  click('#cfgAddVariable'); input('#cfgCreateName', 'EXTRA_HOME'); click('#cfgCreateSubmit');
  const v = json('configProfile().vars[configProfile().vars.length-1]');
  assert.equal(ev('configProfile().vars.length'), before + 1);
  assert.equal(v.id, before); assert.equal(v.name, 'EXTRA_HOME');
  assert.equal(v.defaultValue, ''); assert.equal(v.baselineValue, '');
  assert.equal(v.source, '默认值'); assert.equal(v.savedValue, null); assert.deepEqual(v.choices, []);
  assert.equal(v.editable, true); assert.equal(v.special, false);
  assert.equal(ev('editor.varIdx'), before); assert.equal(q('#cfgName')?.textContent, 'EXTRA_HOME');
});

check('Blank names, reserved profile name, invalid scope and invalid mode reject atomically', () => {
  setup();
  assertRejectedWithoutMutation('app', {appName:' ',profileName:'默认',name:'X'});
  assertRejectedWithoutMutation('app', {appName:'新应用',profileName:' ',name:'X'});
  assertRejectedWithoutMutation('profile', {profileName:'default_option',name:'X'});
  assertRejectedWithoutMutation('profile', {profileName:'新方案',name:' '});
  assertRejectedWithoutMutation('variable', {name:''});
  assertRejectedWithoutMutation('variable', {name:'X',scope:'Machine'});
  assertRejectedWithoutMutation('variable', {name:'X',mode:'prepend'});
});

check('Duplicate app/profile and same-scope variable names reject without partial children', () => {
  setup();
  assertRejectedWithoutMutation('app', {appName:'Node.js',profileName:'新方案',name:'UNIQUE'});
  assertRejectedWithoutMutation('profile', {profileName:'团队默认',name:'UNIQUE'});
  assertRejectedWithoutMutation('variable', {name:'node_home',scope:'User'});
});

check('Matching names may coexist in User and System and export as a V2 two-record array', () => {
  setup(); const result = create('variable', {name:'NODE_HOME',scope:'System',mode:'replace'});
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(ev('configProfile().vars.filter(v=>v.name.toUpperCase()==="NODE_HOME").length'), 2);
  assert.equal(ev('ConfigFormat.errors(apps).length'), 0);
  const text = ev('generateToml()');
  assert.match(text, /"NODE_HOME"\s*=\s*\[\s*\{[^\n]*scope = "User"[^\n]*\},\s*\{[^\n]*scope = "System"/);
});

check('Existing Special target passes special=true and replace into a new profile', () => {
  setup();
  const result = create('profile', {profileName:'继承整体处理',name:'NODE_OPTIONS',scope:'User',mode:'+append'});
  assert.equal(result.ok, true, JSON.stringify(result));
  const v = json('configProfile().vars[0]');
  assert.equal(v.name, 'NODE_OPTIONS'); assert.equal(v.special, true); assert.equal(v.mode, 'replace');
  assert.equal(ev('ConfigFormat.errors(apps).length'), 0);
});

check('Diff records app and profile subtree additions once and variable addition once', () => {
  setup(); assert.equal(create('app', {appName:'新增应用',profileName:'基础',name:'APP_ROOT'}).ok, true);
  let changes = json('configChanges()');
  assert.equal(changes.length, 1); assert.equal(changes[0].field, 'addition');
  assert.equal(changes[0].app, '新增应用');
  assert.match(ev('generateDiff()'), /新增应用/);
  setup(); assert.equal(create('profile', {profileName:'新增方案',name:'PROFILE_ROOT'}).ok, true);
  changes = json('configChanges()');
  assert.equal(changes.length, 1); assert.equal(changes[0].field, 'addition');
  assert.equal(changes[0].profile, '新增方案');
  setup(); assert.equal(create('variable', {name:'ADDED_ROOT'}).ok, true);
  changes = json('configChanges()');
  assert.equal(changes.length, 1); assert.equal(changes[0].field, 'addition');
  assert.equal(changes[0].name, 'ADDED_ROOT');
  click('#configTabDiff'); assert.match(q('#sourceText').textContent, /ADDED_ROOT/);
});

check('Restore removes all added levels, preserves original object/runtime identity and environment', () => {
  setup();
  ev('window.originalApp=apps[7];window.originalProfile=apps[7].profiles[0];window.originalVar=apps[7].profiles[0].vars[0];originalVar.source="自定义值";originalVar.custom="草稿值";originalVar.dirty=true');
  const environment = unchanged().environment;
  assert.equal(create('variable', {name:'TEMP_VAR'}).ok, true);
  assert.equal(create('profile', {profileName:'临时方案',name:'PROFILE_VAR'}).ok, true);
  assert.equal(create('app', {appName:'临时应用',profileName:'初始',name:'APP_VAR'}).ok, true);
  assert.ok(ev('configChanges().length') >= 3);
  ev('restoreConfig()');
  assert.equal(ev('apps.length'), ev('initialApps.length'));
  assert.equal(ev('apps[7].profiles.length'), ev('initialApps[7].profiles.length'));
  assert.equal(ev('apps[7].profiles[0].vars.length'), ev('initialApps[7].profiles[0].vars.length'));
  assert.equal(ev('apps[7]===originalApp && apps[7].profiles[0]===originalProfile && apps[7].profiles[0].vars[0]===originalVar'), true);
  assert.equal(ev('originalVar.source'), '自定义值'); assert.equal(ev('originalVar.custom'), '草稿值');
  assert.equal(ev('configChanges().length'), 0);
  assert.equal(unchanged().environment, environment);
  assert.ok(q('#cfgName')); assert.ok(ev('editor.app>=0 && editor.app<apps.length'));
});

check('New app is reachable in workbench and preview without writing simulated Windows/history', () => {
  setup(); const environment = unchanged().environment;
  assert.equal(create('app', {appName:'预览应用',profileName:'默认',name:'PREVIEW_ROOT'}).ok, true);
  ev('configVar().defaultValue="C:\\\\Demo\\\\New";selected=editor.app;go("workbench")');
  assert.equal(ev('appNow().name'), '预览应用');
  assert.equal(ev('curProfile().vars[0].name'), 'PREVIEW_ROOT');
  assert.match(q('#screen').textContent, /PREVIEW_ROOT/);
  click('#previewButton');
  assert.equal(q('#modal').classList.contains('open'), true);
  assert.match(q('#previewMeta').textContent, /1 条变量记录/);
  assert.equal(unchanged().environment, environment);
});

check('Restoring a selected new app leaves config and workbench navigation usable', () => {
  setup(); assert.equal(create('app', {appName:'待还原应用',profileName:'初始',name:'TMP_ROOT'}).ok, true);
  ev('restoreConfig()');
  assert.ok(ev('editor.app>=0 && editor.app<apps.length'));
  assert.ok(ev('configProfile().vars.length>0')); assert.ok(q('#editorbody'));
  ev('selected=editor.app;go("workbench")');
  assert.ok(q('#previewButton')); assert.ok(ev('selected>=0 && selected<apps.length'));
});

check('Existing invalid choice blocks any new entry without mutating the draft', () => {
  setup(); click('#cfgAddChoice');
  assert.ok(ev('ConfigFormat.errors(apps).length>0'));
  const before = unchanged(), initialCount = ev('configChanges().length');
  const result = create('variable', {name:'BLOCKED_ROOT'});
  assert.equal(result.ok, false); assert.ok(result.error);
  assert.deepEqual(unchanged(), before);
  assert.equal(ev('configChanges().length'), initialCount);
  click('#cfgAddVariable'); input('#cfgCreateName', 'BLOCKED_ROOT'); click('#cfgCreateSubmit');
  assert.match(q('#cfgCreateError').textContent, /候选|配置/);
  assert.deepEqual(unchanged(), before);
});

check('A new same-target selection invalidates and refreshes the open shared-target preview', () => {
  setup(); ev('selected=7;go("workbench")');
  ev('showPreview("应用","共享目标预览",[{app:7,profile:"团队默认",varIdx:0}])');
  assert.equal(q('#modal').classList.contains('open'), true);
  const before = json('modalAction.rows[0].protectionSignature');
  assert.equal(ev('modalAction.version'), 1);
  const result = create('app', {appName:'共享目标应用',profileName:'默认',name:'NODE_HOME',scope:'User'});
  assert.equal(result.ok, true, JSON.stringify(result));
  ev('apps[editor.app].profiles[0].vars[0].defaultValue="C:\\\\Shared\\\\Node"');
  assert.equal(ev('previewIsCurrent(modalAction)'), false);
  assert.equal(ev('refreshPreview()'), true);
  assert.equal(ev('modalAction.version'), 2);
  assert.notEqual(json('modalAction.rows[0].protectionSignature'), before);
  assert.match(json('modalAction.rows[0].protectionSignature'), /Shared/);
  assert.equal(ev('previewIsCurrent(modalAction)'), true);
  assert.match(q('#previewVersionStatus').textContent, /版本 2/);
});

check('Config creation and rendering produce no JSDOM runtime errors', () => assert.deepEqual(runtimeErrors, []));
console.log(JSON.stringify({passed: checks.filter(item => item.passed).length, total: checks.length, checks}, null, 2));
if (checks.some(item => !item.passed)) process.exitCode = 1;
dom.window.close();

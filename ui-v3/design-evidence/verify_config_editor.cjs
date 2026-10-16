'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
const root=path.resolve(__dirname,'..'),checks=[],errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
const dom=new JSDOM(fs.readFileSync(path.join(root,'temp.html'),'utf8'),{runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,beforeParse(w){w.confirm=()=>true;for(const file of ['demo-plan.js','demo-configs.js','windows-elements.js','config-format.js','config-editor.js'])w.eval(fs.readFileSync(path.join(root,file),'utf8'));}});
const w=dom.window,q=s=>w.document.querySelector(s),all=s=>[...w.document.querySelectorAll(s)],ev=s=>w.eval(s);
const check=(name,run)=>{try{run();checks.push({name,passed:true});}catch(error){checks.push({name,passed:false,error:error.stack});}};
const input=(node,raw)=>{node.value=raw;node.dispatchEvent(new w.Event('input',{bubbles:true}));};
const change=(node,value)=>{node.value=value;node.dispatchEvent(new w.Event('change',{bubbles:true}));};
const setup=()=>ev('closeModal();reset(true);editor={app:7,profile:apps[7].default,varIdx:0,tab:"visual"};go("config")');
const choices=()=>JSON.parse(ev('JSON.stringify(ConfigFormat.choices(configVar()))'));
const environment=()=>ev('JSON.stringify({windows,history})');
check('Variable attributes and choices are directly editable inside one center canvas',()=>{
 setup();assert.equal(q('.editor-right'),null);assert.ok(q('#editorInspector').closest('.editor-mid'));assert.ok(q('#editorInspector').closest('.cfg-variable'));assert.equal(all('#editorInspector').length,1);assert.equal(all('[data-choice-label]').length,2);assert.equal(q('#cfgDefault').value,ev('configVar().defaultValue'));
});
check('Editing choice label/value updates one configuration field and workbench selection',()=>{
 setup();const before=environment();input(q('#cfgChoiceLabel0'),'Node 22 portable');input(q('#cfgChoiceValue0'),'  D:\\Portable\\Node;tools  ');assert.deepEqual(choices()[0],{label:'Node 22 portable',value:'  D:\\Portable\\Node;tools  '});assert.equal(ev('configChanges().filter(c=>c.field==="choices").length'),1);assert.equal(environment(),before);
 ev('selected=7;go("workbench")');change(q('#varRows .source'),'候选值 1');assert.equal(q('#varRows .vinput').value,'  D:\\Portable\\Node;tools  ');assert.match(q('#varRows .source').textContent,/Node 22 portable/);
});
check('Choice add/remove/reorder preserve a selected candidate by explicit occurrence action',()=>{
 setup();ev('configVar().source="候选值 1"');const original=ev('value(configVar())');q('[data-choice-down="0"]').click();assert.equal(ev('configVar().source'),'候选值 2');assert.equal(ev('value(configVar())'),original);q('[data-choice-remove="1"]').click();assert.equal(ev('configVar().source'),'默认值');assert.equal(ev('value(configVar())'),ev('configVar().defaultValue'));
 q('#cfgAddChoice').click();assert.equal(w.document.activeElement.id,'cfgChoiceDraftLabel');assert.ok(q('#cfgChoiceDraft'));assert.equal(choices().length,1);assert.ok(!q('#downloadToml').disabled);input(q('#cfgChoiceDraftLabel'),'Empty candidate');q('#cfgChoiceDraftAdd').click();assert.equal(choices()[1].value,'');assert.equal(choices()[1].label,'Empty candidate');q('[data-choice-remove="1"]').click();assert.equal(choices().length,1);
});
check('V2 exact label/value uniqueness is validated without trimming values or changing case',()=>{
 setup();input(q('#cfgChoiceLabel1'),choices()[0].label);assert.ok(q('#downloadToml').disabled);assert.match(q('#cfgValidation').textContent,/名称重复/);input(q('#cfgChoiceLabel1'),'候选值 1 '.trim().toUpperCase()+' alternate');input(q('#cfgChoiceValue1'),choices()[0].value);assert.ok(q('#downloadToml').disabled);input(q('#cfgChoiceValue1'),choices()[0].value+' ');assert.ok(!q('#downloadToml').disabled);
});
check('Default and choice text editing preserve untouched CRLF/CR and leading newlines',()=>{
 setup();ev('setConfigProperty(configVar(),"defaultValue","A\\r\\nB\\rC\\nD");setConfigProperty(configVar(),"choices",[{label:"one",value:"\\nA\\r\\nB\\rC"}]);renderEditorBody()');
 assert.equal(q('#cfgChoiceValue0').value,'\nA\nB\nC');input(q('#cfgDefault'),'X\nB\nC\nD');assert.equal(ev('configVar().defaultValue'),'X\r\nB\rC\nD');input(q('#cfgChoiceValue0'),'\nX\nB\nC');assert.equal(choices()[0].value,'\nX\r\nB\rC');
});
check('editable gates runtime free input while default, saved and configured choices stay available',()=>{
 setup();q('#cfgEditable').checked=false;q('#cfgEditable').dispatchEvent(new w.Event('change',{bubbles:true}));assert.equal(ev('configVar().editable'),false);ev('selected=7;go("workbench")');const node=q('#varRows .vinput'),source=q('#varRows .source');assert.equal(node.readOnly,true);assert.ok(!Array.from(source.options).some(o=>o.value==='自定义值'));assert.ok(Array.from(source.options).some(o=>o.value==='已保存值'));change(source,'候选值 2');assert.equal(q('#varRows .vinput').value,ev('ConfigFormat.choices(scheme(7).vars[0])[1].value'));
});
check('Config reset covers choices and editable, leaves Windows/history unchanged',()=>{
 setup();const before=environment(),initial=choices();input(q('#cfgChoiceLabel0'),'changed');q('#cfgEditable').checked=false;q('#cfgEditable').dispatchEvent(new w.Event('change',{bubbles:true}));q('#cancelConfig').click();assert.deepEqual(choices(),initial);assert.equal(ev('configVar().editable'),true);assert.equal(ev('configChanges().length'),0);assert.equal(environment(),before);
});
check('V2 export uses app/profile tables and required fields without runtime metadata',()=>{
 setup();const text=ev('generateToml()');assert.match(text,/\["Node\.js"\."团队默认"\]/);assert.match(text,/default_option = "团队默认"/);assert.match(text,/editable = true/);assert.match(text,/choices = \[\{ label = /);assert.doesNotMatch(text,/\[\[application|savedValue|baselineValue|matchBaseline|custom =|source =/);
});
check('Uncommitted choice stays outside raw preview and export until confirmed',()=>{
 setup();const before=choices();q('#cfgAddChoice').click();input(q('#cfgChoiceDraftLabel'),'New empty');q('#configTabRaw').click();assert.deepEqual(choices(),before);assert.doesNotMatch(q('#sourceText').textContent,/New empty/);assert.ok(!q('#downloadToml').disabled);q('#configTabVisual').click();assert.equal(q('#cfgChoiceDraft'),null);assert.match(q('#cfgAddChoice').textContent,/继续新增/);q('#cfgAddChoice').click();assert.ok(q('#cfgChoiceDraft'));q('#cfgChoiceDraftAdd').click();assert.equal(choices()[2].label,'New empty');assert.equal(choices()[2].value,'');assert.ok(!q('#downloadToml').disabled);
});
check('V2 dual-scope variables serialize as one two-record array and Special consistency is enforced',()=>{
 const app={name:'Quoted app',default:'1.2',profiles:[{name:'1.2',vars:[{name:'Path',scope:'User',mode:'+append',editable:false,defaultValue:'A',choices:[]},{name:'PATH',scope:'System',mode:'-append',editable:true,defaultValue:'B',choices:[]}]}]};
 const text=w.ConfigFormat.serialize([app]);assert.match(text,/"Path" = \[ \{.*scope = "User".*scope = "System"/);app.profiles[0].vars.push({...app.profiles[0].vars[0],mode:'replace',special:true});assert.ok(w.ConfigFormat.errors([app]).some(e=>e.field==='special'));assert.throws(()=>w.ConfigFormat.serialize([app]));
});
check('Restoring reordered choices keeps the same candidate and falls back when that choice was edited',()=>{
 setup();ev('configVar().source="候选值 2"');const selected=ev('value(configVar())');q('[data-choice-up="1"]').click();assert.equal(ev('configVar().source'),'候选值 1');q('#cancelConfig').click();assert.equal(ev('value(configVar())'),selected);assert.equal(ev('configVar().source'),'候选值 2');input(q('#cfgChoiceValue1'),'changed candidate');q('#cancelConfig').click();assert.equal(ev('configVar().source'),'默认值');
});
check('Workbench textarea edits preserve untouched raw line endings and leading newline',()=>{
 setup();ev('setConfigProperty(configVar(),"defaultValue","\\nA\\r\\nB\\rC");selected=7;go("workbench")');const node=q('#varRows .vinput');assert.equal(node.tagName,'TEXTAREA');assert.equal(node.value,'\nA\nB\nC');input(node,'\nAX\nB\nC');assert.equal(ev('value(scheme(7).vars[0])'),'\nAX\r\nB\rC');assert.equal(ev('scheme(7).vars[0].source'),'自定义值');
});
check('TOML export escapes DEL while retaining the actual raw configuration value',()=>{
 setup();input(q('#cfgDefault'),'A\x7fB');const text=ev('generateToml()');assert.ok(text.includes('A\\u007fB'));assert.ok(!text.includes('\x7f'));assert.equal(ev('configVar().defaultValue'),'A\x7fB');
});
check('Config rendering and editing produce no JSDOM runtime errors',()=>assert.deepEqual(errors,[]));
console.log(JSON.stringify({passed:checks.filter(c=>c.passed).length,total:checks.length,checks},null,2));if(checks.some(c=>!c.passed))process.exitCode=1;dom.window.close();

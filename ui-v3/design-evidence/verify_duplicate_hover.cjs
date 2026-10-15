'use strict';
// DOM event/state coverage only; native pointer and visual acceptance are separate.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
const root=path.resolve(__dirname,'..'),checks=[],errors=[],vc=new VirtualConsole();
vc.on('jsdomError',e=>errors.push(e.message));
const dom=new JSDOM(fs.readFileSync(path.join(root,'temp.html'),'utf8'),{runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,beforeParse(w){ for(const configModule of ['config-format.js','config-editor.js'])w.eval(require('node:fs').readFileSync(require('node:path').resolve(__dirname,"..",configModule),'utf8'));w.confirm=()=>true;for(const f of ['demo-plan.js','demo-configs.js','windows-elements.js'])w.eval(fs.readFileSync(path.join(root,f),'utf8'));}});
const w=dom.window,doc=w.document,q=s=>doc.querySelector(s),all=(s,host=doc)=>[...host.querySelectorAll(s)],ev=s=>w.eval(s);
const check=(name,run)=>{try{run();checks.push({name,passed:true});}catch(e){checks.push({name,passed:false,error:e.stack});}};
const pointer=(node,type='pointerover',relatedTarget=null)=>node.dispatchEvent(new w.MouseEvent(type,{bubbles:type!=='pointerleave',relatedTarget}));
const dup=node=>node.classList.contains('is-duplicate-highlight');
const related=node=>node.classList.contains('is-related-highlight');
const rows=()=>all('#elementList .element-row');
const token=(side,id)=>q(`#element${side} [data-element-id="${id}"]`);
const row=id=>q(`#elementList [data-element-id="${id}"]`);
function open(raw,whole=false){
 ev('closeModal();reset(true);go("windows")');w.__duplicateRaw=raw;w.__duplicateWhole=whole;
 ev('windows["User|DUPLICATE_HOVER"]=__duplicateRaw;openWindowsElements("User","DUPLICATE_HOVER",[],__duplicateWhole)');
 q('.element-raw').open=true;return rows().map(node=>node.dataset.elementId);
}
function edit(id,value){const input=row(id).querySelector('.element-input');input.value=value;input.dispatchEvent(new w.Event('input',{bubbles:true}));}
const model=()=>ev('JSON.stringify({apps,windows,history})');
function fixture(markup){const host=doc.createElement('section');host.className='value-comparison';host.innerHTML=markup;doc.body.append(host);w.WindowsElements.bindComparison(host);return host;}
const stream=(markup,className='')=>`<div class="value-stream ${className}">${markup}</div>`;

check('Hovering a repeated Original value highlights every equal raw occurrence across Original, Preview and rows',()=>{
 const ids=open('A;A;B');pointer(token('Original',ids[0]));
 for(const id of ids.slice(0,2)){assert.ok(dup(token('Original',id)));assert.ok(dup(token('Preview',id)));assert.ok(dup(row(id)));}
 for(const node of [token('Original',ids[2]),token('Preview',ids[2]),row(ids[2])])assert.ok(!dup(node));
 assert.deepEqual(all('#modal .is-related-highlight').map(n=>n.dataset.elementId),[ids[0],ids[0],ids[0]]);
});
check('Original and Preview with the same ID but changed raw text select their own duplicate groups',()=>{
 const ids=open('A;A;B');edit(ids[1],'B');pointer(token('Original',ids[1]));
 assert.ok(dup(token('Original',ids[0])));assert.ok(dup(token('Preview',ids[0])));assert.ok(related(token('Preview',ids[1])));assert.ok(!dup(token('Preview',ids[1])));
 pointer(token('Original',ids[1]),'pointerout',token('Preview',ids[1]));
 assert.ok(!dup(token('Original',ids[0])));assert.ok(dup(token('Original',ids[2])));assert.ok(dup(token('Preview',ids[1])));assert.ok(dup(token('Preview',ids[2])));assert.ok(dup(row(ids[2])));
 assert.ok(related(token('Original',ids[1])));assert.ok(!related(token('Original',ids[2])));
});
check('Pointer transitions among value, duplicate badge and icon keep the duplicate group highlighted',()=>{
 const ids=open('A;A;B'),source=token('Preview',ids[0]),badge=source.querySelector('.is-duplicate'),icon=badge.querySelector('.ic');
 pointer(source.querySelector('.diff-value'));pointer(source.querySelector('.diff-value'),'pointerout',badge);pointer(badge);pointer(badge,'pointerout',icon);pointer(icon);
 assert.ok(dup(token('Original',ids[1])));assert.ok(dup(row(ids[1])));
});
check('Pointerleave clears duplicate highlighting and restores focused occurrence identity only',()=>{
 const ids=open('A;A;B');row(ids[2]).querySelector('.element-input').focus();pointer(token('Original',ids[0]));pointer(q('#modal'),'pointerleave');
 assert.equal(all('#modal .is-duplicate-highlight').length,0);
 assert.deepEqual(all('#modal .is-related-highlight').map(n=>n.dataset.elementId),[ids[2],ids[2],ids[2]]);
 row(ids[0]).querySelector('.element-input').focus();assert.equal(all('#modal .is-duplicate-highlight').length,0);
});
check('Repeated empty slices link as empty raw values without including whitespace',()=>{
 const ids=open('; ;A;');pointer(token('Preview',ids[0]));
 for(const id of [ids[0],ids[3]])assert.ok(dup(token('Original',id))&&dup(row(id)));
 assert.ok(!dup(token('Original',ids[1])));assert.equal(q('#elementSerialized').textContent,'; ;A;');
});
check('Editing a hovered row refreshes duplicate metadata without changing Original snapshots',()=>{
 const ids=open('A;A;B');pointer(row(ids[1]));edit(ids[1],'B');
 assert.ok(!dup(token('Original',ids[0])));assert.ok(dup(token('Original',ids[2])));assert.ok(dup(row(ids[2])));
 assert.equal(token('Original',ids[1]).dataset.duplicateValue,'A');assert.equal(token('Preview',ids[1]).dataset.duplicateValue,'B');
});
check('Ghosts can receive the raw duplicate highlight while retaining deletion-time text and live duplicate counts',()=>{
 const ids=open('A;A;B');edit(ids[1],'B');row(ids[1]).querySelector('[data-delete]').click();pointer(token('Original',ids[0]));
 assert.ok(dup(token('Original',ids[1])));assert.ok(!dup(token('Preview',ids[1])));assert.ok(!dup(row(ids[1])));
 assert.equal(row(ids[1]).dataset.duplicateValue,'B');assert.ok(row(ids[1]).classList.contains('element-ghost-row'));
 assert.equal(token('Preview',ids[2]).dataset.duplicateCount,'1');assert.equal(q('#elementSerialized').textContent,'A;B');
});
check('An unchanged deleted duplicate remains a highlighted ghost but does not gain a duplicate badge',()=>{
 const ids=open('A;A;B');row(ids[1]).querySelector('[data-delete]').click();pointer(token('Original',ids[0]));
 assert.ok(dup(token('Preview',ids[1]))&&dup(row(ids[1])));assert.ok(!token('Preview',ids[1]).classList.contains('is-duplicate'));
 assert.doesNotMatch(row(ids[1]).textContent,/重复/);
});
check('Hover is display-only: model, plan result, node identities and rendered values remain unchanged',()=>{
 const ids=open('A;A;B'),before=model(),serialized=q('#elementSerialized').textContent,nodes=all('#modal [data-element-id]'),values=nodes.map(n=>n.textContent);
 pointer(token('Original',ids[0]));pointer(token('Preview',ids[1]));pointer(q('#modal'),'pointerleave');
 assert.equal(model(),before);assert.equal(q('#elementSerialized').textContent,serialized);assert.deepEqual(all('#modal [data-element-id]'),nodes);assert.deepEqual(nodes.map(n=>n.textContent),values);
 q('#cancelDialog').click();assert.equal(all('#modal .is-duplicate-highlight').length,0);
});
check('Read-only streams preserve exact raw equality, case, whitespace, CRLF and escaped markup',()=>{
 const raw='A;A;a; A;A ;<b>\r\nX;<b>\r\nX',host=fixture(stream(w.WindowsElements.staticMarkup(raw,{comparison:true})));
 const nodes=all('.diff-token',host);assert.deepEqual(nodes.map(n=>n.dataset.duplicateValue),raw.split(';'));assert.equal(host.querySelector('b:not(.ic)'),null);
 pointer(nodes[0]);assert.deepEqual(nodes.filter(dup),nodes.slice(0,2));pointer(nodes[5]);assert.deepEqual(nodes.filter(dup),nodes.slice(5));host.remove();
});
check('Read-only comparison linkage is symmetric, independently scoped and restores keyboard focus',()=>{
 const html=stream(w.WindowsElements.staticMarkup('A;A;B',{comparison:true}));const host=fixture(html+html),other=fixture(html);
 const nodes=all('.diff-token',host);nodes[2].focus();pointer(nodes[4]);assert.equal(nodes.filter(dup).length,4);assert.equal(all('.is-duplicate-highlight',other).length,0);
 pointer(host,'pointerleave');assert.equal(nodes.filter(dup).length,0);assert.deepEqual(nodes.filter(related),[nodes[2],nodes[5]]);host.remove();other.remove();
});
check('History without recorded element audit links duplicate text without inventing occurrence correspondence',()=>{
 const host=fixture(stream(w.WindowsElements.historyMarkup('A;A',{side:'before'}))+stream(w.WindowsElements.historyMarkup('A;B',{side:'after'})));
 const nodes=all('.diff-token',host);pointer(nodes[0]);assert.deepEqual(nodes.filter(dup),nodes.slice(0,3));assert.deepEqual(nodes.filter(related),[nodes[0]]);host.remove();
});
check('Workbench and application preview markup link duplicates without mutating their frozen plan',()=>{
 const plan=w.DemoPlan.build({mode:'-append',actualRaw:'A;A;B',selectedRaw:'A;C'}),before=JSON.stringify(plan),view=w.WindowsElements.comparisonMarkup(plan);
 const host=fixture(stream(view.actual)+stream(view.after)+stream(view.selected)),nodes=all('.diff-token',host);pointer(nodes[0]);
 assert.equal(nodes.filter(dup).length,5);assert.equal(JSON.stringify(plan),before);assert.equal(plan.after.raw,'A;A;B;C');host.remove();
});
check('Static display tokens can link their duplicate group and whole strings never infer internal duplicates',()=>{
 let host=fixture(stream(w.WindowsElements.staticMarkup('A;A;B'))),nodes=all('.diff-token',host);pointer(nodes[0]);assert.deepEqual(nodes.filter(dup),nodes.slice(0,2));host.remove();
 host=fixture(stream(w.WindowsElements.staticMarkup('A;A',{whole:true,comparison:true})));nodes=all('.diff-token',host);pointer(nodes[0]);assert.equal(nodes.filter(dup).length,0);host.remove();
 const ids=open('A;A',true);pointer(token('Original',ids[0]));assert.equal(all('#modal .is-duplicate-highlight').length,0);
});
check('Actual Windows inventory streams reuse duplicate hover and remain isolated by variable',()=>{
 ev('closeModal();reset(true);windows["User|DUP_ONE"]="A;A;B";windows["User|DUP_TWO"]="A;A";go("windows")');
 const first=q('[data-windows-value="User|DUP_ONE"]'),second=q('[data-windows-value="User|DUP_TWO"]'),before=model();pointer(first.querySelector('.is-duplicate'));
 assert.equal(all('.is-duplicate-highlight',first).length,2);assert.equal(all('.is-duplicate-highlight',second).length,0);assert.equal(model(),before);
});
check('Duplicate hover workflows produce no JSDOM runtime errors',()=>assert.deepEqual(errors,[]));
console.log(JSON.stringify({verification:'DOM-only events, classes, stable occurrence identity and data immutability; native pointer and visual layout not verified',passed:checks.filter(c=>c.passed).length,total:checks.length,checks},null,2));
if(checks.some(c=>!c.passed))process.exitCode=1;dom.window.close();

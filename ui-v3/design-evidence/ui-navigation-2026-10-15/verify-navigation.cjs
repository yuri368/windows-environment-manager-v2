'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
const root=path.resolve(__dirname,'../..'),checks=[],errors=[];
const vc=new VirtualConsole();vc.on('jsdomError',error=>errors.push(error.message));
const dom=new JSDOM(fs.readFileSync(path.join(root,'temp.html'),'utf8'),{runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,beforeParse(w){ for(const configModule of ['config-format.js','config-editor.js'])w.eval(require('node:fs').readFileSync(require('node:path').resolve(__dirname,"../..",configModule),'utf8'));
  w.confirm=()=>true;
  for(const file of ['demo-plan.js','demo-configs.js','windows-elements.js'])w.eval(fs.readFileSync(path.join(root,file),'utf8'));
}});
const w=dom.window,q=selector=>w.document.querySelector(selector),all=selector=>[...w.document.querySelectorAll(selector)],ev=code=>w.eval(code);
const input=(node,value)=>{node.value=value;node.dispatchEvent(new w.Event('input',{bubbles:true}));};
const key=(node,value,options={})=>node.dispatchEvent(new w.KeyboardEvent('keydown',{key:value,bubbles:true,cancelable:true,...options}));
const contentState=()=>ev('JSON.stringify({apps,windows,history,checked:[...checked]})');
async function check(name,run){try{await run();checks.push({name,passed:true});}catch(error){checks.push({name,passed:false,error:error.stack});}}
(async()=>{
  await check('Empty-search recovery preserves current application, checked batch and all content',()=>{
    q('[data-check="0"]').click();const before=contentState(),selected=ev('selected');
    input(q('#appSearch'),'NO_APPLICATION_MATCHES');assert.ok(q('#resetAppFilters'));assert.ok(q('#selectAll').disabled);
    q('#resetAppFilters').click();assert.equal(all('[data-app-select]').length,13);assert.equal(q('#appSearch').value,'');assert.equal(ev('selected'),selected);assert.equal(contentState(),before);assert.equal(w.document.activeElement,q('#appSearch'));
  });
  await check('Clearing text preserves matching filter and batch selection',()=>{
    q('[data-filter="unmatched"]').click();const count=all('[data-app-select]').length,before=contentState();
    input(q('#appSearch'),'NO_MATCH');q('#clearAppSearch').click();assert.equal(ev('filter'),'unmatched');assert.equal(all('[data-app-select]').length,count);assert.equal(contentState(),before);
  });
  await check('Escape clears search but composing Escape does not discard IME input',()=>{
    input(q('#appSearch'),'中文输入');key(q('#appSearch'),'Escape',{isComposing:true});assert.equal(q('#appSearch').value,'中文输入');
    key(q('#appSearch'),'Escape');assert.equal(q('#appSearch').value,'');assert.equal(w.document.activeElement,q('#appSearch'));
  });
  await check('Locating filtered-out current app resets only view filters and focuses its button',()=>{
    input(q('#appSearch'),'NO_MATCH');const before=contentState(),selected=ev('selected');assert.ok(q('#locateCurrentApp'));
    q('#locateCurrentApp').click();assert.equal(ev('filter'),'all');assert.equal(w.document.activeElement,q(`[data-app-select="${selected}"]`));assert.equal(contentState(),before);assert.ok(q('.appitem.is-located'));
  });
  await check('Application list keys move focus without changing selection or content',()=>{
    const before=contentState(),selected=ev('selected');q('[data-app-select="0"]').focus();key(w.document.activeElement,'ArrowDown');assert.equal(w.document.activeElement.dataset.appSelect,'1');
    key(w.document.activeElement,'End');assert.equal(w.document.activeElement.dataset.appSelect,'12');key(w.document.activeElement,'Home');assert.equal(w.document.activeElement.dataset.appSelect,'0');
    key(w.document.activeElement,'ArrowDown',{ctrlKey:true});assert.equal(w.document.activeElement.dataset.appSelect,'0');assert.equal(ev('selected'),selected);assert.equal(contentState(),before);
  });
  await check('Application rerender and checkbox rerender preserve list scroll',()=>{
    q('#applist').scrollTop=173;q('[data-app-select="10"]').click();assert.equal(q('#applist').scrollTop,173);
    q('#applist').scrollTop=201;q('[data-check="10"]').click();assert.equal(q('#applist').scrollTop,201);assert.equal(w.document.activeElement.dataset.check,'10');
  });
  await check('Zero mismatches offers a reversible return to all variables',()=>{
    q('[data-app-select="2"]').click();const before=contentState();q('#onlyMismatch').click();assert.equal(all('#varRows [data-row]').length,0);assert.ok(q('#showAllVariables'));
    q('#showAllVariables').click();assert.equal(q('#onlyMismatch').checked,false);assert.equal(all('#varRows [data-row]').length,2);assert.equal(w.document.activeElement,q('#onlyMismatch'));assert.equal(contentState(),before);
  });
  await check('Compare disclosure keeps aria-expanded, controls and visibility consistent',()=>{
    const button=q('#compareToggle');assert.equal(button.getAttribute('aria-controls'),'inspect');
    button.click();assert.equal(button.getAttribute('aria-expanded'),'false');assert.equal(q('#inspect').hidden,true);
    button.click();assert.equal(button.getAttribute('aria-expanded'),'true');assert.equal(q('#inspect').hidden,false);assert.ok(q('#inspect').classList.contains('is-revealing'));
  });
  await check('Config tabs have one active tab stop and a named panel',()=>{
    ev('go("config")');assert.equal(q('[role="tablist"]').getAttribute('aria-label'),'配置视图');assert.equal(all('[role="tab"][tabindex="0"]').length,1);
    assert.equal(q('#editorbody').getAttribute('aria-labelledby'),'configTabVisual');assert.equal(q('#configTabVisual').getAttribute('aria-selected'),'true');
  });
  await check('Config arrow and Home/End navigation preserve content and focus the new tab',()=>{
    const before=contentState();q('#configTabVisual').focus();key(w.document.activeElement,'ArrowRight');assert.equal(ev('editor.tab'),'raw');assert.equal(w.document.activeElement.id,'configTabRaw');assert.match(q('#sourceText').textContent,/default_value/);
    key(w.document.activeElement,'End');assert.equal(ev('editor.tab'),'diff');assert.equal(q('#editorbody').getAttribute('aria-labelledby'),'configTabDiff');
    key(w.document.activeElement,'Home',{altKey:true});assert.equal(ev('editor.tab'),'diff');key(w.document.activeElement,'Home');assert.equal(ev('editor.tab'),'visual');assert.equal(contentState(),before);
  });
  await check('Config app and scheme lists support focus-only keys',()=>{
    const before=contentState(),app=ev('editor.app');q('[data-editapp="0"]').focus();key(w.document.activeElement,'End');assert.equal(w.document.activeElement.dataset.editapp,'12');assert.equal(ev('editor.app'),app);
    key(w.document.activeElement,'Home');assert.equal(w.document.activeElement.dataset.editapp,'0');assert.equal(contentState(),before);
    const schemes=all('[data-editplan]');schemes[0].focus();key(w.document.activeElement,'End');assert.equal(w.document.activeElement,schemes.at(-1));
  });
  await check('Config same-variable refresh retains the unified canvas and sidebar scroll',()=>{
    q('[data-editor-list="apps"]').scrollTop=98;q('.editor-mid').scrollTop=31;
    q('#cfgScope').dispatchEvent(new w.Event('change',{bubbles:true}));assert.equal(q('[data-editor-list="apps"]').scrollTop,98);assert.equal(q('.editor-right'),null);assert.equal(q('.editor-mid').scrollTop,31);assert.equal(w.document.activeElement.id,'cfgScope');
  });
  await check('Config variable switch retains navigation and expands exactly one inline form',()=>{
    q('[data-editor-list="apps"]').scrollTop=110;q('.editor-mid').scrollTop=100;q('[data-tablevar="1"]').click();
    assert.equal(q('[data-editor-list="apps"]').scrollTop,110);assert.equal(all('#editorInspector').length,1);assert.ok(q('#editorInspector').closest('.cfg-variable'));assert.equal(q('.editor-right'),null);assert.equal(w.document.activeElement.dataset.tablevar,'1');
    const content=contentState();key(w.document.activeElement,'ArrowLeft');assert.equal(w.document.activeElement.dataset.tablevar,'0');key(w.document.activeElement,'ArrowRight');assert.equal(w.document.activeElement.dataset.tablevar,'1');assert.equal(contentState(),content);
  });
  await check('New config app or profile starts at top without resetting application list scroll',()=>{
    q('.editor-mid').scrollTop=250;q('.editor-shell').scrollTop=320;q('[data-editor-list="apps"]').scrollTop=90;
    q('[data-editapp="0"]').click();assert.equal(q('.editor-mid').scrollTop,0);assert.equal(q('.editor-shell').scrollTop,0);assert.equal(q('[data-editor-list="apps"]').scrollTop,90);
    q('.editor-mid').scrollTop=140;q('.editor-shell').scrollTop=180;all('[data-editplan]').at(-1).click();assert.equal(q('.editor-mid').scrollTop,0);assert.equal(q('.editor-shell').scrollTop,0);
  });
  await check('Copy reports success only after Clipboard write resolves',async()=>{
    q('#configTabRaw').click();let written=null;Object.defineProperty(w.navigator,'clipboard',{configurable:true,value:{writeText:async text=>{written=text;}}});
    q('#copyText').click();await new Promise(resolve=>setImmediate(resolve));assert.ok(written.includes('default_value'));assert.equal(q('#copyText').dataset.copyState,'copied');
  });
  await check('Clipboard rejection keeps copy button available and displays recovery message',async()=>{
    ev('renderEditorBody()');Object.defineProperty(w.navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw new Error('blocked')}}});q('#copyText').click();await new Promise(resolve=>setImmediate(resolve));
    assert.equal(q('#copyText').dataset.copyState,undefined);assert.match(q('#toast').textContent,/手动选中复制/);
  });
  await check('Pending copy keeps its original text and never marks a replacement view copied',async()=>{
    let resolveWrite,written;Object.defineProperty(w.navigator,'clipboard',{configurable:true,value:{writeText:text=>{written=text;return new Promise(resolve=>{resolveWrite=resolve;});}}});
    const source=q('#sourceText').textContent;q('#copyText').click();q('#configTabDiff').click();const replacement=q('#copyText'),toast=q('#toast').textContent;
    resolveWrite();await new Promise(resolve=>setImmediate(resolve));assert.equal(written,source);assert.equal(replacement.dataset.copyState,undefined);assert.equal(replacement.textContent,'复制内容');assert.equal(q('#toast').textContent,toast);
  });
  await check('Leaving config while copy is pending produces no false copy failure',async()=>{
    let resolveWrite;Object.defineProperty(w.navigator,'clipboard',{configurable:true,value:{writeText:()=>new Promise(resolve=>{resolveWrite=resolve;})}});
    q('#copyText').click();ev('go("workbench")');const toast=q('#toast').textContent;resolveWrite();await new Promise(resolve=>setImmediate(resolve));assert.equal(q('#copyText'),null);assert.equal(q('#toast').textContent,toast);
  });
  await check('No runtime errors in navigation/recovery workflows',()=>assert.deepEqual(errors,[]));
  const result={passed:checks.filter(check=>check.passed).length,total:checks.length,scope:'JSDOM state and focus checks; actual scroll geometry requires browser verification',checks};
  fs.writeFileSync(path.join(__dirname,'navigation-checks.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));dom.window.close();if(result.passed!==result.total)process.exitCode=1;
})();

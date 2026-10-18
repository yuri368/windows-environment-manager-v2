'use strict';
// Contract fixtures exercise the real production scripts. These are not a backend acceptance test.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
const root=path.resolve(__dirname,'..'),source=name=>fs.readFileSync(path.join(root,name),'utf8');
const tick=()=>new Promise(resolve=>setTimeout(resolve,5));
const id=n=>'00000000-0000-4000-8000-'+String(n).padStart(12,'0');
const clone=x=>JSON.parse(JSON.stringify(x));
function initial(empty=false){
  const apps=empty?[]:[{name:'工具',recordId:id(2),default:'稳定',profiles:[{name:'稳定',recordId:id(3),vars:[{id:0,recordId:id(4),name:'Path',scope:'User',mode:'-append',defaultValue:'A;B',source:'默认值',custom:null,savedValue:null,baselineValue:'A;B',selectedRaw:'A;B',matchBaseline:null,removalState:null,dirty:false,choices:[],editable:true,special:false}]}]}];
  const profileChoices={[id(2)]:id(3)};
  return {workspace:{id:id(1),revision:1,validRevision:1,draftRevision:1,errors:[],fileRevision:'disk-1',configPath:'managed/env.toml'},apps,workingApps:clone(apps),configurationBaseline:clone(apps),profileChoices,draftProfileChoices:clone(profileChoices),snapshots:[{scope:'User',name:'Path',exists:true,raw:'X;A',type:'REG_EXPAND_SZ',revision:'env-1',supported:true}],history:[],mode:'isolated',capabilities:{execute:true},recovery:[],uiDrafts:{}};
}
async function harness(empty=false,savedState=null){
  let state=savedState?clone(savedState):initial(empty),w,seq=100,reviewCount=0,executeCount=0,loseExecution=false,rejectReview=false,updateReview=false,failEdit=false;
  const calls=[],errors=[],plans=new Map(),edits=new Map(),orders=new Map(),operations=new Map();
  const variable=()=>state.workingApps[0].profiles.find(p=>p.recordId===state.profileChoices[state.workingApps[0].recordId]).vars[0];
  const runtimeFields=['source','custom','selectedRaw','savedValue','baselineValue','matchBaseline','removalState','dirty'];
  function syncRuntime(){
    for(const a of state.apps)for(const p of a.profiles)for(const v of p.vars){
      const current=state.workingApps.flatMap(a=>a.profiles.flatMap(p=>p.vars)).find(w=>w.recordId===v.recordId);
      if(current)for(const field of runtimeFields)v[field]=clone(current[field]??null);
    }
  }
  function bump(){state.workspace.revision++;state.workspace.draftRevision=state.workspace.revision;}
  function draft(body){
    if(body.apps){
      state.apps=clone(body.apps);
      state.apps.forEach(a=>{a.recordId||=id(++seq);a.profiles.forEach(p=>{p.recordId||=id(++seq);p.vars.forEach(v=>v.recordId||=id(++seq));});});
      state.workspace.errors=w.ConfigFormat.errors(state.apps).map(({field,message})=>({field,message}));
      if(!state.workspace.errors.length){
        const old=state.workingApps.flatMap(a=>a.profiles.flatMap(p=>p.vars));
        state.workingApps=clone(state.apps);
        for(const a of state.workingApps){for(const p of a.profiles)for(const v of p.vars){const before=old.find(o=>o.recordId===v.recordId);if(before){for(const field of runtimeFields)v[field]=clone(before[field]??null);if(v.source==='默认值')v.selectedRaw=v.defaultValue;}}
          if(!a.profiles.some(p=>p.recordId===state.profileChoices[a.recordId]))state.profileChoices[a.recordId]=(a.profiles.find(p=>p.name===a.default)||a.profiles[0]).recordId;
        }
        state.workspace.validRevision=state.workspace.revision+1;
      }
    }
    if(body.profileChoices)for(const [appId,profileId]of Object.entries(body.profileChoices)){
      const working=state.workingApps.find(a=>a.recordId===appId);
      if(working?.profiles.some(p=>p.recordId===profileId))state.profileChoices[appId]=profileId;
      else state.draftProfileChoices[appId]=profileId;
    }
    if(!state.workspace.errors.length)state.draftProfileChoices=clone(state.profileChoices);
    if(body.uiDrafts)state.uiDrafts=clone(body.uiDrafts);
    bump();syncRuntime();return state;
  }
  function receipt(body){
    if(body.orderSessionIds?.length)body={...body,orderSessionId:body.orderSessionIds.at(-1)};
    const snapshot=clone(state.snapshots[0]);let plan,members=[id(4)];
    if(body.kind==='manual'){
      const e=edits.get(body.editSessionId),raw=e.rows.map(r=>r.value).join(';');members=[];
      const elements=e.rows.map((r,i)=>{const b=e.originalRows.findIndex(o=>o.id===r.id);return {elementId:r.id,beforeIndex:b<0?null:b,afterIndex:i,before:b<0?undefined:e.originalRows[b].value,after:r.value,added:b<0,removed:false,replaced:b>=0&&e.originalRows[b].value!==r.value,moved:b>=0&&b!==i};});
      for(const [i,r]of e.originalRows.entries())if(!e.rows.some(n=>n.id===r.id))elements.push({elementId:r.id,beforeIndex:i,afterIndex:null,before:r.value,added:false,removed:true,replaced:false,moved:false});
      plan=w.DemoPlan.buildElementEdit({target:{scope:'User',name:'Path'},snapshot,afterRaw:raw,audit:{before:snapshot.raw,after:raw,whole:false,elements,counts:Object.fromEntries(['added','removed','replaced','moved'].map(k=>[k,elements.filter(e=>e[k]).length]))}});
    }else plan=w.DemoPlan.build({target:{scope:'User',name:'Path'},snapshot,mode:variable().mode,selectedRaw:variable().selectedRaw,whole:false});
    plan=clone(plan);
    if(variable()?.mode==='append'&&!body.orderSessionId)plan.needsOrdering=true;
    if(body.orderSessionId){const o=orders.get(body.orderSessionId);plan.after.raw=plan.afterRaw=o.rows.map(r=>r.value).join(';');plan.needsOrdering=false;}
    const result={planId:id(++seq),version:1,digest:'digest-'+seq,status:plan.needsOrdering?'ordering_required':'ready',targets:[{scope:'User',name:'Path',beforeSnapshot:snapshot,plan,members}],issues:[],intent:body};plans.set(result.planId,result);return clone(result);
  }
  async function fetch(url,options={}){
    const route=url.replace('/api/v1',''),method=options.method||'GET',body=options.body?JSON.parse(options.body):null;calls.push({route,method,body,headers:options.headers,credentials:options.credentials});
    const ok=value=>({ok:true,json:async()=>clone(value),text:async()=>typeof value==='string'?value:JSON.stringify(value)});
    if(route==='/session/exchange')return ok({csrfToken:'test-csrf'});
    if(route==='/bootstrap'||route==='/environment/refresh')return ok(state);
    if(route.endsWith('/draft'))return ok(draft(body));
    if(route.startsWith('/selections/')){const v=variable();if(route.endsWith('/restore')){v.source='自定义值';v.selectedRaw=v.custom=v.baselineValue;}else{v.source=body.source;v.custom=body.raw;v.selectedRaw=body.raw;v.dirty=true;}bump();syncRuntime();return ok(state);}
    if(route.endsWith('/save')){assert.equal(body.expectedDiskHash,state.workspace.fileRevision);assert.equal(state.workspace.errors.length,0);state.workspace.fileRevision='disk-'+(++seq);state.configurationBaseline=clone(state.workingApps);return ok(state);}
    if(route.endsWith('/restore')){state.apps=clone(state.configurationBaseline);state.workingApps=clone(state.configurationBaseline);state.workspace.errors=[];bump();state.workspace.validRevision=state.workspace.revision;return ok(state);}
    if(route.endsWith('/export'))return ok('TOML fixture');
    if(route==='/plans')return ok(receipt(body));
    if(route.endsWith('/review')){reviewCount++;if(rejectReview)return ok({status:'rejected',message:'审阅拒绝 fixture'});if(updateReview){updateReview=false;const p=clone(plans.get(route.split('/')[2]));p.version++;p.digest+='new';plans.set(p.planId,p);return ok({status:'preview_updated',plan:p});}return ok({reviewToken:'review-'+reviewCount});}
    if(route.endsWith('/execute')){executeCount++;assert(body.reviewToken);const p=plans.get(body.planId);state.snapshots[0]={...state.snapshots[0],...p.targets[0].plan.after,revision:'env-'+seq};state.workspace.revision++;const result={status:'committed',state:clone(state)};operations.set(body.idempotencyKey,result);if(loseExecution){loseExecution=false;throw new Error('lost acknowledgement');}return ok(result);}
    if(route.startsWith('/operations/by-key/'))return ok(operations.get(route.split('/').pop())||{status:'not_found'});
    if(route==='/edit-sessions'){const s=state.snapshots[0],rows=s.raw.split(';').map(value=>({id:id(++seq),value}));const e={sessionId:id(++seq),scope:'User',name:'Path',snapshot:clone(s),revision:0,rows,originalRows:clone(rows),whole:false,type:s.type};edits.set(e.sessionId,e);return ok(e);}
    if(route.startsWith('/edit-sessions/')){if(failEdit)throw new Error('STALE_EDIT fixture');const e=edits.get(route.split('/').pop());assert.equal(body.expectedRevision,e.revision);e.rows=body.rows.map(r=>({...r,id:r.id||id(++seq)}));e.revision++;return ok(e);}
    if(route==='/order-sessions'){const p=plans.get(body.planId),e={sessionId:id(++seq),revision:0,rows:p.targets[0].plan.after.raw.split(';').map(value=>({id:id(++seq),value}))};orders.set(e.sessionId,e);return ok(e);}
    if(route.startsWith('/order-sessions/')){const e=orders.get(route.split('/').pop());e.rows=body.order.map(id=>e.rows.find(r=>r.id===id));e.revision++;return ok(e);}
    throw new Error('Unhandled fixture '+method+' '+route);
  }
  const vc=new VirtualConsole();vc.on('jsdomError',error=>errors.push(error));
  const html=source('index.html').replace(/<script[^>]*src="([^"]+)"[^>]*><\/script>/g,(_,name)=>'<script>'+source(name).replace(/<\/script/gi,'<\\/script')+'</script>');
  const dom=new JSDOM(html,{url:'http://127.0.0.1:8788/#ticket=test-ticket',runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,beforeParse(window){w=window;w.fetch=fetch;w.scrollTo=()=>{};w.confirm=()=>true;}});
  await dom.window.productReady;
  return {w,dom,calls,errors,state:()=>state,set:(values)=>{({loseExecution=loseExecution,rejectReview=rejectReview,updateReview=updateReview,failEdit=failEdit}=values);},counts:()=>({reviewCount,executeCount}),q:s=>w.document.querySelector(s),run:s=>w.eval(s),close:()=>dom.window.close()};
}
(async()=>{
  let count=0;const check=async(name,fn)=>{await fn();count++;console.log('PASS '+name);};
  const h=await harness(),{w,q,run}=h;
  try{
    await check('bootstrap exchanges ticket, removes fragment and carries CSRF/cookie credentials',async()=>{assert.equal(w.location.hash,'');assert.equal(h.calls[0].route,'/session/exchange');assert(q('#varRows'));await run('refreshEnvironment()');const call=h.calls.find(c=>c.route==='/environment/refresh');assert.equal(call.credentials,'same-origin');assert.equal(call.headers['X-CSRF-Token'],'test-csrf');});
    await check('rapid selection input is ordered and preserves textarea/caret during acknowledgement',async()=>{const input=q('.vinput');input.focus();input.value='B';input.dispatchEvent(new w.Event('input',{bubbles:true}));input.value='B;C';input.dispatchEvent(new w.Event('input',{bubbles:true}));input.setSelectionRange(2,2);await run('flushInputs()');assert.strictEqual(q('.vinput'),input);assert.equal(input.selectionStart,2);assert.equal(h.state().apps[0].profiles[0].vars[0].selectedRaw,'B;C');assert.deepEqual(h.calls.filter(c=>c.route.startsWith('/selections/')).map(c=>c.body.raw),['B','B;C']);});
    await check('preview renders authoritative receipt; review and execute require separate clicks',async()=>{await run("showPreview('应用','验收',[{app:0,profile:'稳定',varIdx:0}])");assert(q('[data-preview-index]'));await run('confirmPreview()');assert.equal(h.counts().executeCount,0);assert.equal(q('#confirmDialog').textContent,'执行已审阅计划');await run('confirmPreview()');assert.equal(h.counts().executeCount,1);assert(!q('#modal').classList.contains('open'));});
    await check('review replacement plan never executes in same click',async()=>{await run("showPreview('应用','更新验收',[{app:0,profile:'稳定',varIdx:0}])");h.set({updateReview:true});const before=h.counts().executeCount;await run('confirmPreview()');assert.equal(h.counts().executeCount,before);assert(q('#previewVersionStatus').textContent.includes('更新'));assert.equal(q('#confirmDialog').textContent,'审阅此计划');run('closeModal()');});
    await check('HTTP 200 rejected review remains visible and does not execute',async()=>{await run("showPreview('应用','拒绝验收',[{app:0,profile:'稳定',varIdx:0}])");h.set({rejectReview:true});await run('confirmPreview()');assert(q('#previewDecisionHint').textContent.includes('拒绝'));h.set({rejectReview:false});run('closeModal()');});
    await check('lost execute acknowledgement reconciles by original key without resend',async()=>{await run("showPreview('应用','网络验收',[{app:0,profile:'稳定',varIdx:0}])");await run('confirmPreview()');const before=h.counts().executeCount;h.set({loseExecution:true});await run('confirmPreview()');assert.equal(h.counts().executeCount,before+1);assert(h.calls.some(c=>c.route.startsWith('/operations/by-key/')));assert.equal(w.ProductRuntime.pendingKey(),null);});
    await check('Ctrl+S uses managed conditional save',async()=>{w.document.dispatchEvent(new w.KeyboardEvent('keydown',{key:'s',ctrlKey:true,bubbles:true}));await tick();await run('API.flush()');assert(h.calls.some(c=>c.route.endsWith('/save')&&c.body.expectedRevision));});
    await check('manual edit cancellation restores original DOM, ghost rows and draft text',async()=>{await run("openWindowsElements('User','Path')");const input=q('.element-input');input.value='manual draft';input.dispatchEvent(new w.Event('input',{bubbles:true}));const editor=q('.element-dialog');q('#applyElements').click();await tick();assert(q('.preview-dialog'));run('closeModal()');await tick();assert.strictEqual(q('.element-dialog'),editor);assert.equal(q('.element-input').value,'manual draft');run('closeModal()');});
    await check('async edit-session failure retains full editor and draft',async()=>{await run("openWindowsElements('User','Path')");const input=q('.element-input');input.value='preserved';input.dispatchEvent(new w.Event('input',{bubbles:true}));h.set({failEdit:true});q('#applyElements').click();await tick();assert(q('.element-dialog'));assert.equal(q('.element-input').value,'preserved');assert(q('#elementValidation').textContent.includes('STALE'));h.set({failEdit:false});run('closeModal()');});
    await check('manual preview follows frozen occurrence audit for duplicates, empty rows, ADD/REMOVE/REPLACE/MOVE',async()=>{
      const elements=[
        {elementId:id(700),beforeIndex:0,afterIndex:0,before:'A',after:'A',added:false,removed:false,replaced:false,moved:false},
        {elementId:id(701),beforeIndex:1,afterIndex:1,before:'A',after:'B',added:false,removed:false,replaced:true,moved:false},
        {elementId:id(702),beforeIndex:2,afterIndex:3,before:'',after:'',added:false,removed:false,replaced:false,moved:true},
        {elementId:id(703),beforeIndex:3,afterIndex:null,before:' X ',added:false,removed:true,replaced:false,moved:false},
        {elementId:id(704),beforeIndex:null,afterIndex:2,after:'A',added:true,removed:false,replaced:false,moved:false}
      ];
      const beforeRaw='A;A;; X ',afterRaw='A;B;A;';
      const plan=w.DemoPlan.buildElementEdit({target:{scope:'User',name:'Path'},snapshot:{exists:true,raw:beforeRaw,type:'REG_SZ',revision:'audit-fixture'},afterRaw,audit:{before:beforeRaw,after:afterRaw,whole:false,elements,counts:{added:1,removed:1,replaced:1,moved:1}}});
      const frozen=JSON.stringify(plan);
      const node=w.document.createElement('div');
      node.innerHTML=w.previewRowMarkup({v:{name:'Path',mode:'element-edit'},scope:'User',name:'Path',members:[],plan,before:beforeRaw,after:afterRaw,whole:false},'应用',0);
      const tokens=side=>[...node.querySelectorAll('.is-'+side+' .diff-token')];
      assert.deepEqual(tokens('before').map(t=>t.querySelector('[data-raw-value]').dataset.rawValue),['A','A','',' X ']);
      assert.deepEqual(tokens('after').map(t=>t.querySelector('[data-raw-value]').dataset.rawValue),['A','B','A','']);
      const old=tokens('before'),next=tokens('after');
      assert.equal(old[0].dataset.relatedKey,next[0].dataset.relatedKey);
      assert.equal(old[1].dataset.relatedKey,next[1].dataset.relatedKey);
      assert.notEqual(next[0].dataset.relatedKey,next[2].dataset.relatedKey);
      assert.equal(old[2].dataset.relatedKey,next[3].dataset.relatedKey);
      assert(next[1].classList.contains('is-replaced'));assert(next[2].classList.contains('is-added'));
      assert(next[3].classList.contains('is-moved'));assert(old[3].classList.contains('is-removed'));
      assert(!next[0].classList.contains('is-replaced'));assert(!node.textContent.includes('整体待替换'));
      assert.equal(JSON.stringify(plan),frozen);
    });
    await check('manual Special stays whole and scheme replace still uses whole-replacement preview',async()=>{
      const beforeRaw='A;A',afterRaw='A;B',snapshot={exists:true,raw:beforeRaw,type:'REG_SZ',revision:'whole-fixture'};
      const plans=[w.DemoPlan.buildElementEdit({target:{scope:'User',name:'Path'},snapshot,afterRaw,audit:{before:beforeRaw,after:afterRaw,whole:true,elements:[{elementId:id(710),beforeIndex:0,afterIndex:0,before:beforeRaw,after:afterRaw,added:false,removed:false,replaced:true,moved:false}],counts:{added:0,removed:0,replaced:1,moved:0}}}),w.DemoPlan.build({target:{scope:'User',name:'Path'},snapshot,mode:'replace',selectedRaw:afterRaw,whole:false})];
      for(const [index,plan]of plans.entries()){
        const node=w.document.createElement('div');node.innerHTML=w.previewRowMarkup({v:{name:'Path',mode:plan.mode},scope:'User',name:'Path',members:[],plan,before:beforeRaw,after:afterRaw,whole:plan.special},'应用',0);
        assert.equal(node.querySelectorAll('.is-before .diff-token').length,1);assert.equal(node.querySelectorAll('.is-after .diff-token').length,1);
        assert.equal(node.querySelector('.is-after [data-raw-value]').dataset.rawValue,afterRaw);
        if(index===1)assert(node.textContent.includes('整体待替换'));
      }
    });
    await check('manual summary describes frozen operations including MOVE and type-only without whole replacement',async()=>{
      const cases=[
        [[{type:'replace',action:'REPLACE',elementId:id(720)}],'按审阅结果 · 替换 1 个元素。'],
        [[{type:'move',action:'MOVE',elementId:id(720)}],'按审阅结果 · 移动 1 个元素。'],
        [[{type:'type-change',action:'TYPE_CHANGE'}],'按审阅结果 · 更改注册表类型。'],
        [[{type:'replace',elementId:id(720)},{type:'move',elementId:id(720)},{type:'type-change'}],'按审阅结果 · 替换 1 个元素 · 移动 1 个元素 · 更改注册表类型。'],
        [[{type:'add',elementId:id(721)},{type:'remove',elementId:id(722)}],'按审阅结果 · 新增 1 个元素 · 删除 1 个元素。'],
        [[{type:'remove',action:'REMOVE_VARIABLE'}],'按审阅结果 · 删除变量。'],
        [[],'']
      ];
      for(const [operations,expected]of cases){
        const plan=Object.freeze({kind:'element-edit',valid:true,issues:[],operations:Object.freeze(operations.map(Object.freeze))}),before=JSON.stringify(plan);
        assert.equal(w.planSummary(plan),expected);assert.equal(JSON.stringify(plan),before);
      }
      assert.equal(w.planSummary({kind:'whole',special:true,operations:[{type:'replace'}]}),'应用时整体替换当前值。');
      const ordered=Object.freeze({kind:'append',valid:true,issues:[],operations:Object.freeze([Object.freeze({type:'insert',position:'tail'}),Object.freeze({type:'move',action:'MOVE',order:['b','a','new']})])});
      const original=JSON.stringify(ordered);
      assert.equal(w.planSummary(ordered),'按已确认顺序排列元素 · 新增 1 项');
      assert.equal(JSON.stringify(ordered),original);
    });
    await check('append ordering uses UUID permutation then rebuilds a fresh receipt',async()=>{run("apps[0].profiles[0].vars[0].mode='append';scheduleDraft()");await run('flushInputs()');await run("showPreview('应用','排序验收',[{app:0,profile:'稳定',varIdx:0}])");assert(q('#confirmDialog').disabled);await run('openOrdering(modalAction,modalAction.rows[0])');q('#saveOrder').click();await tick();assert(q('.preview-dialog'));assert(!q('#confirmDialog').disabled);assert(h.calls.some(c=>c.route.startsWith('/order-sessions/')&&c.method==='PATCH'&&c.body.order.every(s=>/^[0-9a-f-]{36}$/.test(s))));run('closeModal()');});
    await check('undo sends actual variable UUIDs and enables review',async()=>{run("apps[0].profiles[0].vars[0].mode='-append';scheduleDraft()");await run('flushInputs()');await run("showPreview('撤销','撤销验收',[{app:0,profile:'稳定',varIdx:0}])");const call=h.calls.filter(c=>c.route==='/plans').at(-1);assert.equal(call.body.kind,'undo');assert.deepEqual(call.body.recordIds,[id(4)]);assert(!q('#confirmDialog').disabled);run('closeModal()');});
    await check('Unicode cache identity follows ConfigFormat canonicalization',async()=>{assert.notEqual(run("key({scope:'User',name:'ß'})"),run("key({scope:'User',name:'SS'})"));});
    assert.deepEqual(h.errors,[]);
  }finally{h.close();}
  const isolated=await harness();let invalidSaved;
  try{
    const invalidRaw='bad\r\n原文\0',originalDefault='A;B';
    await check('invalid configuration draft persists exact raw without changing working tree or valid revision',async()=>{
      isolated.run("go('config')");const validRevision=isolated.state().workspace.validRevision;
      isolated.run(`setConfigProperty(configVar(),'defaultValue',${JSON.stringify(invalidRaw)})`);await isolated.run('flushInputs()');
      assert.equal(isolated.state().apps[0].profiles[0].vars[0].defaultValue,invalidRaw);
      assert.equal(isolated.state().workingApps[0].profiles[0].vars[0].defaultValue,originalDefault);
      assert.equal(isolated.state().workspace.validRevision,validRevision);
      assert(isolated.state().workspace.draftRevision>validRevision);
      assert(isolated.q('#saveConfig').disabled);assert(isolated.q('#downloadToml').disabled);
      isolated.run("go('workbench')");assert.equal(isolated.q('.vinput').value,originalDefault);
      assert(isolated.q('#configurationNotice').textContent.includes('工作台继续使用有效版本 '+validRevision));
      assert.notStrictEqual(isolated.run('apps[0]'),isolated.run('workingApps[0]'));
    });
    await check('acknowledged draft uses server errors once while distinct fields retain distinct diagnostics',async()=>{
      const savedErrors=clone(isolated.state().workspace.errors);
      isolated.state().workspace.errors=[{field:'defaultValue',message:'不能包含 NUL 字符',location:['apps',0,'profiles',0,'vars',0,'defaultValue']}];
      await isolated.run('refreshEnvironment()');
      assert.equal(isolated.run('configurationErrors().length'),1);
      assert.equal((isolated.q('#configurationNotice').textContent.match(/NUL/g)||[]).length,1);
      assert(isolated.q('#configurationNotice').textContent.includes('1 项错误'));
      isolated.run("go('config')");assert(isolated.q('#saveConfig').disabled);assert(isolated.q('#downloadToml').disabled);
      isolated.state().workspace.errors.push({field:'choices',message:'不能包含 NUL 字符',location:['apps',0,'profiles',0,'vars',0,'choices',0,'value']});
      await isolated.run('refreshEnvironment()');assert.equal(isolated.run('configurationErrors().length'),2);
      isolated.state().workspace.errors=savedErrors;await isolated.run('refreshEnvironment()');isolated.run("go('workbench')");
    });
    await check('invalid Save/Export guard does not poison runtime mutation queue or working Apply',async()=>{
      const saveCount=isolated.calls.filter(c=>c.route.endsWith('/save')).length;
      await assert.rejects(isolated.run('saveConfiguration()'),/配置草稿无效/);
      assert.throws(()=>isolated.run('requireValidConfiguration()'),/配置草稿无效/);
      assert.equal(isolated.calls.filter(c=>c.route.endsWith('/save')).length,saveCount);
      const input=isolated.q('.vinput');input.focus();input.value='Run; 自定义 ';input.dispatchEvent(new isolated.w.Event('input',{bubbles:true}));await isolated.run('flushInputs()');
      assert.equal(isolated.state().workingApps[0].profiles[0].vars[0].selectedRaw,'Run; 自定义 ');
      assert.equal(isolated.state().apps[0].profiles[0].vars[0].defaultValue,invalidRaw);
      await isolated.run("showPreview('应用','有效工作区计划',[{app:0,profile:'稳定',varIdx:0}])");
      const call=isolated.calls.filter(c=>c.route==='/plans').at(-1);assert.deepEqual(call.body.recordIds,[id(4)]);
      await isolated.run('confirmPreview()');await isolated.run('confirmPreview()');
      assert.equal(isolated.counts().executeCount,1);assert(!isolated.q('#modal').classList.contains('open'));
      assert.equal(isolated.run('apps[0].profiles[0].vars[0].defaultValue'),invalidRaw);
    });
    await check('refresh keeps invalid configuration, runtime source/raw, textarea identity and caret',async()=>{
      const input=isolated.q('.vinput');input.focus();input.setSelectionRange(2,2);
      await isolated.run('refreshEnvironment()');assert.strictEqual(isolated.q('.vinput'),input);assert.equal(input.selectionStart,2);
      assert.equal(isolated.run('apps[0].profiles[0].vars[0].defaultValue'),invalidRaw);
      assert.equal(isolated.run('scheme().vars[0].custom'),'Run; 自定义 ');
      assert.equal(isolated.run('apps[0].profiles[0].vars[0].custom'),'Run; 自定义 ');
      isolated.run("go('config')");
      const diff=isolated.run("configChanges().find(c=>c.field==='defaultValue')");assert.equal(diff.before,originalDefault);assert.equal(diff.after,invalidRaw);
      invalidSaved=clone(isolated.state());
    });
    await check('valid draft correction promotes by UUID while retaining latest runtime custom value and saved baseline',async()=>{
      isolated.run("setConfigProperty(configVar(),'defaultValue','Corrected; 默认值')");await isolated.run('flushInputs()');
      assert.equal(isolated.state().workspace.errors.length,0);
      assert.equal(isolated.state().workingApps[0].profiles[0].vars[0].defaultValue,'Corrected; 默认值');
      assert.equal(isolated.state().workingApps[0].profiles[0].vars[0].recordId,id(4));
      assert.equal(isolated.state().workingApps[0].profiles[0].vars[0].custom,'Run; 自定义 ');
      assert.equal(isolated.run("configChanges().find(c=>c.field==='defaultValue').before"),originalDefault);
      assert(!isolated.q('#saveConfig').disabled);
      await isolated.run('saveConfiguration()');assert.equal(isolated.run('configChanges().length'),0);
      assert.equal(isolated.state().configurationBaseline[0].profiles[0].vars[0].defaultValue,'Corrected; 默认值');
    });
    assert.deepEqual(isolated.errors,[]);
  }finally{isolated.close();}
  const restarted=await harness(false,invalidSaved);
  try{
    await check('restart restores invalid raw and diff against persisted configurationBaseline rather than draft',async()=>{
      assert.equal(restarted.run('scheme().vars[0].defaultValue'),'A;B');restarted.run("go('config')");
      const diff=restarted.run("configChanges().find(c=>c.field==='defaultValue')");assert.equal(diff.before,'A;B');assert.equal(diff.after,'bad\r\n原文\0');
      assert(!restarted.q('#cancelConfig').disabled);assert(restarted.q('#saveConfig').disabled);
      await restarted.run('refreshEnvironment()');assert.equal(restarted.run("configChanges().find(c=>c.field==='defaultValue').before"),'A;B');
    });
    await check('restore after restart removes invalid draft and returns to saved baseline only',async()=>{
      const environmentBefore=JSON.stringify(restarted.state().snapshots);await restarted.run('restoreConfig()');
      assert.equal(restarted.run('configChanges().length'),0);assert.equal(restarted.run('configVar().defaultValue'),'A;B');assert.equal(restarted.state().workspace.errors.length,0);
      assert.equal(JSON.stringify(restarted.state().snapshots),environmentBefore);
    });
    assert.deepEqual(restarted.errors,[]);
  }finally{restarted.close();}
  const preparedState=initial(),alternate=clone(preparedState.apps[0].profiles[0]);alternate.name='备用';alternate.recordId=id(53);alternate.vars[0].recordId=id(54);
  for(const tree of [preparedState.apps,preparedState.workingApps,preparedState.configurationBaseline])tree[0].profiles.push(clone(alternate));
  const prepared=await harness(false,preparedState);
  try{
    await check('prepared choice PATCH never sends invalid apps or changes draft-only parents',async()=>{
      prepared.run("apps[0].profiles[0].vars[0].mode='invalid';apps.push({name:'草稿父级',default:'草稿方案',profiles:[{name:'草稿方案',vars:[{name:'DraftOnly',scope:'User',mode:'invalid',defaultValue:'',editable:true,special:false,choices:[]}]}]});scheduleDraft()");await prepared.run('flushInputs()');
      const draftId=prepared.state().apps[1].recordId;prepared.run("profileChoice[0]='备用';queuePreparedChoice(0)");await prepared.run('flushInputs()');
      const mutation=prepared.calls.filter(c=>c.route.endsWith('/draft')&&c.body.profileChoices?.[id(2)]===id(53)).at(-1);assert(mutation);assert(!('apps' in mutation.body));
      assert.equal(prepared.run('scheme().name'),'备用');assert.equal(prepared.run('workingApps.length'),1);assert.equal(prepared.run('apps.length'),2);
      assert.equal(prepared.run('apps[1].recordId'),draftId);assert.equal(prepared.state().profileChoices[id(2)],id(53));
      prepared.run("apps[0].profiles[0].vars[0].defaultValue='another draft';scheduleDraft()");await prepared.run('flushInputs()');
      assert.equal(prepared.state().profileChoices[id(2)],id(53));assert.equal(prepared.run('scheme().name'),'备用');
    });
    await check('draft-only new nodes remain additions and are excluded from prepared plan IDs',async()=>{
      const additions=prepared.run("configChanges().filter(c=>c.field==='addition').map(c=>c.app)");assert(additions.includes('草稿父级'));
      const ids=prepared.run("targetRecordIds([{app:0,profile:'备用',varIdx:0}])");assert.deepEqual([...ids],[id(54)]);
      assert.equal(prepared.state().configurationBaseline.length,1);assert.equal(prepared.state().workingApps.length,1);
    });
    assert.deepEqual(prepared.errors,[]);
  }finally{prepared.close();}
  const legalSaved=initial();legalSaved.apps[0].profiles[0].vars[0].defaultValue='Unsaved legal';legalSaved.workingApps=clone(legalSaved.apps);legalSaved.workspace.revision=3;legalSaved.workspace.validRevision=3;legalSaved.workspace.draftRevision=3;
  const legalRestart=await harness(false,legalSaved);
  try{await check('restart also retains legal unsaved diff against saved baseline',async()=>{legalRestart.run("go('config')");const diff=legalRestart.run("configChanges().find(c=>c.field==='defaultValue')");assert.equal(diff.before,'A;B');assert.equal(diff.after,'Unsaved legal');assert(!legalRestart.q('#cancelConfig').disabled);await legalRestart.run('refreshEnvironment()');assert.equal(legalRestart.run("configChanges().find(c=>c.field==='defaultValue').before"),'A;B');});assert.deepEqual(legalRestart.errors,[]);}finally{legalRestart.close();}
  const reorderedState=initial(),savedOther=clone(reorderedState.configurationBaseline[0].profiles[0].vars[0]);
  Object.assign(savedOther,{recordId:id(740),id:1,name:'Other',defaultValue:'Saved other',selectedRaw:'Saved other',baselineValue:'Saved other'});
  reorderedState.configurationBaseline[0].profiles[0].vars.push(savedOther);
  const added=clone(savedOther);Object.assign(added,{recordId:id(741),name:'UnsavedNew',defaultValue:'New value'});
  const edited=clone(savedOther);edited.defaultValue='Unsaved other';
  reorderedState.apps[0].profiles[0].vars=[edited,reorderedState.apps[0].profiles[0].vars[0],added];reorderedState.workingApps=clone(reorderedState.apps);
  const reordered=await harness(false,reorderedState);
  try{
    await check('saved baselines bind by UUID across reordered projections and new nodes remain additions after restart',async()=>{
      reordered.run("go('config')");let changes=JSON.parse(reordered.run('JSON.stringify(configChanges())'));
      const edit=changes.find(c=>c.name==='Other'&&c.field==='defaultValue');assert.equal(edit.before,'Saved other');assert.equal(edit.after,'Unsaved other');
      assert.equal(changes.filter(c=>c.name==='UnsavedNew'&&c.field==='addition').length,1);
      assert(!changes.some(c=>c.name==='Path'&&c.field==='defaultValue'));
      const original=reordered.run('apps[0].profiles[0].vars[0]');
      await reordered.run('refreshEnvironment()');assert.strictEqual(reordered.run('apps[0].profiles[0].vars[0]'),original);
      changes=JSON.parse(reordered.run('JSON.stringify(configChanges())'));assert.equal(changes.find(c=>c.name==='Other'&&c.field==='defaultValue').before,'Saved other');
      assert.equal(changes.filter(c=>c.name==='UnsavedNew'&&c.field==='addition').length,1);
    });
    assert.deepEqual(reordered.errors,[]);
  }finally{reordered.close();}
  const nativeState=initial();nativeState.snapshots=[{scope:'User',name:'Path',exists:true,raw:null,type:'REG_DWORD',revision:'native-1',supported:false,display:'42',native_type:4},{scope:'System',name:'NativeBinary',exists:true,raw:null,type:'REG_BINARY',revision:'native-2',supported:false,display:'00 FF',native_type:3},{scope:'User',name:'RawString',exists:true,raw:' A;A;;中文\r\n ',type:'REG_EXPAND_SZ',revision:'string-1',supported:true}];
  const native=await harness(false,nativeState);
  try{
    await check('unsupported actual bootstrap renders workbench as native readonly without string-rule planning',async()=>{
      assert(native.q('#varRows'));assert(native.q('#compareActual').textContent.includes('REG_DWORD'));assert.equal(native.q('#compareActual [data-native-display]').textContent,'42');
      assert(!native.q('#compareActual').textContent.includes('null'));assert(native.q('#compareAfter').textContent.includes('不能应用'));
      assert.equal(native.run("Object.hasOwn(windows,'User|PATH')"),false);assert.equal(native.run('demoSnapshot(scheme().vars[0]).raw'),null);
      assert(native.q('.vinput').readOnly);assert(native.q('.source').disabled);assert(native.q('#previewButton').disabled);assert(native.q('#inspectPreview').disabled);assert(native.q('#editWindowsElements').hidden);
      assert.throws(()=>native.run('demoPlan(scheme().vars[0])'),/不支持的类型/);
      await new Promise(resolve=>setTimeout(resolve,280));assert.equal(native.calls.filter(c=>c.route==='/plans').length,0);
    });
    await check('Windows native rows show native type/display and expose no edit, token or delete route',async()=>{
      native.run("go('windows')");const rows=[...native.w.document.querySelectorAll('[data-unsupported-target]')];assert.equal(rows.length,2);
      assert.deepEqual(rows.map(r=>r.querySelector('[data-native-display]').textContent),['42','00 FF']);
      for(const row of rows){assert(!row.textContent.includes('null'));assert([...row.querySelectorAll('button')].every(b=>b.disabled));assert.equal(row.querySelectorAll('[data-value-index],[data-windows-edit],[data-delete-windows]').length,0);}
      assert.equal(native.q('[data-windows-raw="User|RAWSTRING"]').textContent,' A;A;;中文\r\n ');
      const before=native.calls.length;await native.run("openWindowsElements('User','Path')");assert.equal(native.calls.length,before);
      await assert.rejects(native.run('deleteWindowsVariable(productState.snapshots[0])'),/不支持的类型/);
      assert.equal(native.calls.filter(c=>c.route==='/edit-sessions'||c.route.endsWith('/execute')).length,0);
    });
    await check('refresh from unsupported native type to supported string restores ordinary editor controls',async()=>{
      native.state().snapshots[0]={scope:'User',name:'Path',exists:true,raw:' A;A;;中文\r\n ',type:'REG_EXPAND_SZ',revision:'native-to-string',supported:true};
      await native.run('refreshEnvironment()');native.run("go('workbench')");assert(!native.q('.vinput').readOnly);assert(!native.q('.source').disabled);assert(!native.q('#previewButton').disabled);
      assert.equal(native.run("windows['User|PATH']"),' A;A;;中文\r\n ');
      native.state().snapshots[0]={scope:'User',name:'Path',exists:true,raw:null,type:'REG_DWORD',revision:'string-to-native',supported:false,display:'42',native_type:4};
      const input=native.q('.vinput');await native.run('refreshEnvironment()');assert.strictEqual(native.q('.vinput'),input);assert(input.readOnly);assert(native.q('.source').disabled);assert(native.q('#previewButton').disabled);assert.equal(native.q('#compareActual [data-native-display]').textContent,'42');
    });
    assert.deepEqual(native.errors,[]);
  }finally{native.close();}
  const empty=await harness(true);
  try{await check('empty workspace starts with only application name and persists naming draft',async()=>{assert.equal(empty.q('#emptyCreate').querySelectorAll('input').length,1);assert.equal(empty.q('#emptyCreate input').name,'appName');const input=empty.q('#emptyCreate input');input.value='新应用';input.dispatchEvent(new empty.w.Event('input',{bubbles:true}));empty.q('#emptyCreate').dispatchEvent(new empty.w.Event('submit',{bubbles:true,cancelable:true}));await tick();assert.equal(empty.q('#emptyCreate input').name,'profileName');assert.equal(empty.state().uiDrafts.emptyCreation.appName,'新应用');assert.equal(empty.state().apps.length,0);});assert.deepEqual(empty.errors,[]);}finally{empty.close();}
  await check('production shell preserves CSS order and contains no fixtures/inline scripts',async()=>{const html=source('index.html');assert(!html.includes('demo-configs'));assert(!/<script>/.test(html));const styles=s=>[...s.matchAll(/<link rel="stylesheet"[^>]+>/g)].map(m=>m[0]);assert.deepEqual(styles(html),styles(source('temp.html')));assert(!source('product-app.js').includes('function applyDemoPlan'));assert(!source('product-app.js').includes('reset(true)'));});
  console.log(`${count} product contract checks passed (JSDOM fixtures; real backend/browser acceptance is separate).`);
})().catch(error=>{console.error(error);process.exitCode=1;});

'use strict';
const clone=x=>JSON.parse(JSON.stringify(x));
const MODE={'replace':'替换','+append':'前置','-append':'后置','append':'自定义排序'};
const $=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
let apps=[],workingApps=[],draftProfileChoice={},windows={},checked=new Set(),selected=0,profileChoice={},filter='all',search='',onlyMismatch=false,compareShown=true,focusedVariable=0,screen='workbench',modalAction=null,revision=0,history=[],editor={app:0,profile:'',varIdx:0,tab:'visual'},toastTimer=null;
let lastDialogFocus = null, targetRevisions = {}, targetTypes = {}, planCache = new WeakMap();
let configOriginals = new WeakMap();
let configurationBaselineById=new Map();
function appNow(i=selected){return workingApps[i]}
function scheme(i=selected){return workingApps[i]?.profiles.find(p=>p.name===profileChoice[i])||workingApps[i]?.profiles[0]}
function choiceValue(choice,fallback){return choice===undefined?fallback:typeof choice==='string'?choice:choice.value}
function value(v){if(v.source.startsWith('候选值 '))return choiceValue(v.choices[Number(v.source.slice(4))-1],v.defaultValue);return v.source==='自定义值'?v.custom??'':v.source==='已保存值'?v.savedValue??v.defaultValue:v.source==='候选值'?choiceValue(v.choices[0],v.defaultValue):v.defaultValue}
function key(v){return v.scope+'|'+ConfigFormat.matchName(v.name)}
function demoSnapshot(v,raw){
 const current=arguments.length>1?raw:windows[key(v)];
 return {exists:current!==undefined,raw:current===undefined?null:current,type:targetTypes[key(v)]||(ConfigFormat.matchName(v.name)==='PATH'?'REG_EXPAND_SZ':'REG_SZ'),revision:targetRevisions[key(v)]||0};
}
function variableOwner(v){
 for(let app=0;app<workingApps.length;app++)for(const p of workingApps[app].profiles){const varIdx=p.vars.indexOf(v);if(varIdx>=0)return {app,profile:p.name,varIdx};}
 return null;
}
function protectedSelections(vars){
 const excluded=new Set(vars),target=key(vars[0]);
 return workingApps.flatMap((a,i)=>scheme(i).vars).filter(v=>!excluded.has(v)&&key(v)===target).map(value);
}
function currentRemovalState(v,snapshot=demoSnapshot(v)){
 if(!v.matchBaseline){v.removalState=null;return null;}
 v.removalState=DemoPlan.syncRemovalState({baseline:v.matchBaseline,state:v.removalState,snapshot});
 return v.removalState;
}
function planContextSignature(vars){return JSON.stringify(vars.map(v=>[key(v),v.mode,v.special,value(v),v.matchBaseline,currentRemovalState(v)]));}
function selectionEdit(v){return DemoPlan.selectionChange({target:{scope:v.scope,name:v.name},beforeRaw:v.matchBaseline?.selectedRaw??v.baselineValue,afterRaw:value(v),whole:v.special})}
function setSelectedValue(v,raw){if(unsupportedTarget(v))return;beginSelectedEdit(v,raw);v.custom=raw;v.source='自定义值';v.dirty=hasPendingValue(v);queueSelection(v)}
function sizeSelectedValueInput(input){
 input.style.height='auto';
 input.style.height=Math.max(input.scrollHeight+input.offsetHeight-input.clientHeight,32)+'px';
}
window.addEventListener('resize',()=>{if(screen==='workbench')document.querySelectorAll('#varRows .vinput').forEach(sizeSelectedValueInput)});
function planSummary(plan){
 if(plan.valid===false)return '编辑上下文或目标存在冲突，请检查作用域和模式。';
 const pending=plan.issues?.filter(issue=>['duplicate','shared','continuity'].includes(issue.reason)).length||0;
 const pendingText=pending?'仍有 '+pending+' 条删除保留提示，未解决的删除继续保留。':'';
 if(!plan.operations.length)return pendingText;
 if(plan.kind==='element-edit'){
  const changes=[['add','ADD','新增'],['remove','REMOVE','删除'],['replace','REPLACE','替换'],['move','MOVE','移动']].map(([type,action,label])=>{
   const count=new Set(plan.operations.filter(op=>(op.type===type||op.action===action)&&op.action!=='REMOVE_VARIABLE').map(op=>op.elementId??op)).size;
   return count?`${label} ${count} 个元素`:'';
  });
  if(plan.operations.some(op=>op.action==='REMOVE_VARIABLE'))changes.push('删除变量');
  if(plan.operations.some(op=>op.type==='type-change'||op.action==='TYPE_CHANGE'))changes.push('更改注册表类型');
  return ['按审阅结果',...changes,pendingText].filter(Boolean).join(' · ')+'。';
 }
 if(plan.kind!=='append')return '应用时整体替换当前值。';
 const removes=plan.operations.filter(op=>op.type==='remove'),inserts=plan.operations.filter(op=>op.type==='insert');
 if(plan.operations.some(op=>op.type==='move'||op.action==='MOVE'))return ['按已确认顺序排列元素',inserts.length?`新增 ${inserts.length} 项`:'',removes.length?`删除 ${removes.length} 项`:'',pendingText].filter(Boolean).join(' · ');
 return [removes.length?`删除 ${removes.length} 项`:'',inserts.length?`在${inserts.every(op=>op.position==='head')?'开头':inserts.every(op=>op.position==='tail')?'末尾':'各方案指定位置'}追加 ${inserts.length} 项`:'','保留其他元素',pendingText].filter(Boolean).join(' · ');
}
// Presentation only: these classifications never change a frozen plan or its eligibility.
function reviewTargetKey(r){return JSON.stringify([r.scope,r.plan?.targetKey.canonicalName||ConfigFormat.matchName(r.name)])}
function reviewState(plan,{ordering=false,unsupported=false}={}){
 const retained=(plan?.issues||[]).filter(issue=>['duplicate','shared','continuity'].includes(issue.reason));
 const blocked=unsupported||ordering||plan?.valid===false;
 const changed=!!plan?.operations.length;
 return {retained,changed,blocked,kind:blocked?'blocked':retained.length?'retained':changed?'changed':'unchanged',
  label:blocked?'暂不能应用':retained.length?'删除保留':changed?'有实际变更':'值不变'};
}
function reviewRowState(r,kind='应用'){
 return reviewState(r.plan,{ordering:r.needsOrdering});
}
function reviewIssueMarkup(plan){
 const reasons={
  duplicate:['存在重复，未自动删除','原匹配或当前匹配曾有重复，不能安全确定自动删除对象。当前内容保留；需要删除时，请在元素编辑器核对具体行。'],
  shared:['其他方案仍需使用，已保留','本次不会删除这项内容。请先核对其他方案的选定值，再决定如何处理。'],
  continuity:['原元素无法确认，已保留','当前值已经变化，原删除依据不再可靠。相同文本重新出现，也不会恢复自动删除资格。'],
  stale:['编辑上下文已变化','请返回工作台核对作用域、模式与当前方案。当前输入继续保留。'],
  conflict:['同一目标的请求冲突','请返回工作台核对本批次方案的模式和选定值；本批次尚未应用。']
 };
 const items=(plan?.issues||[]).map(issue=>{
  const [title,hint]=reasons[issue.reason]||['计划需要检查','请返回工作台核对当前方案；此提示不会自动修改任何值。'];
  const raw=Array.isArray(issue.value)?issue.value.join(' / '):String(issue.value??'');
  const fields=issue.reason==='stale'?(issue.fields||[]).map(field=>({target:'目标或作用域',mode:'模式',special:'整体处理类型'}[field])).filter(Boolean):[];
  return `<li><div><strong>${title}</strong><code>${esc(raw||'（空字符串）')}</code></div><p>${hint}${fields.length?' 需核对：'+fields.join('、')+'。':''}${issue.actualIndexes?.length?' 当前位置：'+issue.actualIndexes.map(i=>i+1).join('、')+'。':''}</p></li>`;
 });
 if(!items.length)return '';
 return `<div class="review-issues"><ul>${items.slice(0,2).join('')}</ul>${items.length>2?`<details><summary>另有 ${items.length-2} 项说明</summary><ul>${items.slice(2).join('')}</ul></details>`:''}</div>`;
}
function reviewSummaryMarkup(rows,kind,updated,version){
 const states=rows.map(r=>reviewRowState(r,kind));
 const changed=states.filter(s=>s.changed&&!s.blocked).length,blocked=states.filter(s=>s.blocked).length;
 const retained=states.filter(s=>s.retained.length).length,unchanged=states.filter(s=>!s.changed&&!s.blocked).length;
 const tone=blocked?'blocked':updated||retained?'retained':changed?'changed':'unchanged';
 if(kind==='应用'&&!blocked&&!updated&&!retained&&changed)return `<section class="review-summary is-compact" data-review-state="changed" aria-label="本次预览结论"><p class="review-compact-line"><strong>${rows.length} 个目标</strong><span aria-hidden="true">·</span><strong>${changed} 个有实际变更</strong><span aria-hidden="true">·</span><span>尚未应用</span></p></section>`;
 const title=kind!=='应用'?'撤销范围说明':blocked?'本次暂不能应用':updated?'预览已更新，请重新核对':retained?(changed?'可应用安全变更 · 部分删除将保留':'当前值不会改变 · 删除意图仍待处理'):changed?`已准备好 ${changed} 个目标的变更`:'当前值无需改变';
 const hint=kind!=='应用'?'撤销将依据历史贡献生成服务端计划。':blocked?'请先检查标记的目标；本批次不会执行任何变更。':updated?'本次尚未应用。查看更新后，需要再次确认。':retained?'只执行计划中的实际操作，未解决的删除意图继续保留。':changed?'核对下方当前值与目标结果，再审阅并执行。':'确认可记录本次方案选择，不会新增变量修改历史。';
 return `<section class="review-summary" data-review-state="${tone}" aria-label="本次预览结论"><div class="review-heading"><span class="review-symbol" aria-hidden="true">${blocked?'!':updated?'↻':retained?'!':changed?'→':'='}</span><div><h4>${title}</h4><p>${hint}</p>${blocked&&updated?`<p class="review-updated">预览已更新至版本 ${version}，尚未应用。</p>`:''}</div></div><div class="review-metrics"><span><b>${rows.length}</b> 个目标</span><span><b>${changed}</b> 个有实际变更</span><span><b>${unchanged}</b> 个值不变</span>${retained?`<span class="review-attention"><b>${retained}</b> 个含删除保留</span>`:''}${blocked?`<span class="review-attention"><b>${blocked}</b> 个阻塞</span>`:''}</div></section>`;
}
function reviewTargetNav(rows,kind){
 if(rows.length<2)return '';
 const buttons=rows.map((r,index)=>{const state=reviewRowState(r,kind);return `<button class="review-target" data-review-jump="${esc(reviewTargetKey(r))}" data-review-state="${state.kind}" aria-label="定位${r.scope==='User'?'用户':'系统'} ${esc(r.name)}，${state.label}"><span class="review-target-number">${index+1}</span><strong>${esc(r.name)}</strong><span>${r.scope==='User'?'用户':'系统'}</span><small>${state.label}</small></button>`;}).join('');
 return rows.length>5?`<details class="review-targets"><summary>按目标定位 · 全部 ${rows.length} 个目标</summary><nav aria-label="预览目标">${buttons}</nav></details>`:`<nav class="review-targets" aria-label="预览目标"><span class="review-nav-label">按目标定位</span>${buttons}</nav>`;
}
function inspectPreviewText(){return checked.size?'查看批量预览':'查看方案预览'}
function inspectReviewMarkup(v,plan){
 const state=reviewState(plan,{ordering:v.mode==='append'});
 const text=state.blocked?(v.mode==='append'?'请在预览中确认自定义排序':'请检查作用域与模式'):state.retained.length?`${state.retained.length} 条删除保留提示 · 在预览中查看原因`:state.changed?'':'确认方案选择不会改变当前值';
 return `<div class="inspect-review" data-review-state="${state.kind}"><div class="inspect-review-summary"><strong>${state.label}</strong>${text?`<span>${text}</span>`:''}</div><button class="textbtn" id="inspectPreview">${inspectPreviewText()} →</button></div>`;
}
function returnToReviewTarget(target){
 const member=target.members[0];closeModal();selected=member.app;profileChoice[selected]=member.profile;
 focusedVariable=member.varIdx;compareShown=true;go('workbench');
 const field=$('varRows')?.querySelector(`[data-row="${member.varIdx}"] .vinput`);
 const heading=$('inspectTitle');(field||heading)?.focus();heading?.scrollIntoView?.({block:'nearest'});
}
function itemMatched(v){return unsupportedTarget(v)?false:demoPlan(v).matchResult.satisfied}
const isAppendMode=mode=>['+append','-append','append'].includes(mode);
function detectionText(v){
  if(unsupportedTarget(v))return '不支持的类型 · 只读';
  const result=demoPlan(v).matchResult,m=result.satisfied,info={total:result.totalCount,matched:result.matchedCount};
  if(v.special||!info.total)return m?'✓ 匹配':'! 不匹配';
  const ratio=`${info.matched}/${info.total}`;
  if(m)return `✓ 匹配 ${ratio}`;
  if(info.matched===info.total)return `! 存在额外差异 · 元素 ${ratio}`;
  return info.matched?`! 部分匹配 ${ratio}`:`! 不匹配 ${ratio}`;
}
function matchCount(p){return p.vars.filter(v=>itemMatched(v)).length}
function status(p){const n=matchCount(p);return n===p.vars.length?'完全匹配':n===0?'未匹配':'部分匹配'}
function colorStatus(p){return matchCount(p)===p.vars.length?'':'warn'}
/* 外壳展示层（只读 plan，不改任何规则）：状态种类 → 图标/配色；文字内容保持原样，仅给首个符号套上插槽 */
function kindOf(v){if(unsupportedTarget(v))return 'none';const r=demoPlan(v).matchResult;if(r.satisfied)return 'match';if(v.special||!r.totalCount)return 'none';if(r.matchedCount===r.totalCount)return 'extra';return r.matchedCount?'partial':'none'}
function appKind(p){const n=matchCount(p);return n===p.vars.length?'match':n?'partial':'none'}
function glyphed(text){const m=/^([✓!])\s(.*)$/s.exec(text);return m?`<span class="rs-glyph" aria-hidden="true">${m[1]}</span> <span class="rs-text">${esc(m[2])}</span>`:esc(text)}
function retrigger(el,cls){if(!el)return;el.classList.remove(cls);void el.offsetWidth;el.classList.add(cls)}
const ringMemo={},removalMemo={};let ringBooted=false;
/* 对照区：新出现的“待删除 / 结果排除”元素播放一次入场效果；同一变量首次展示与重复重绘不播放 */
function playRemovalFx(){
  const id=selected+'|'+profileChoice[selected]+'|'+focusedVariable,tokens=[...document.querySelectorAll('#compareActual .diff-token.is-removed,#compareAfter .diff-token.is-removed')],now=new Set(tokens.map(t=>t.dataset.relatedKey)),prev=removalMemo[id];
  removalMemo[id]=now;if(!prev)return;
  tokens.forEach(t=>{if(prev.has(t.dataset.relatedKey))return;const cls=t.classList.contains('is-ghost')?'fx-cut-in':'fx-lock-in';t.classList.add(cls);setTimeout(()=>t.classList.remove(cls),1100)});
}
function enterFx(){const s=$('screen');retrigger(s,'is-entering');clearTimeout(enterFx.t);enterFx.t=setTimeout(()=>s.classList.remove('is-entering'),900)}
function positionInk(){const ink=document.querySelector('.nav-ink'),tab=document.querySelector('.navtabs .navtab.active');if(!ink)return;if(!tab){ink.style.opacity='0';return}ink.style.opacity='1';ink.style.width=(tab.offsetWidth-20)+'px';ink.style.transform=`translateX(${tab.offsetLeft+10}px)`}
window.addEventListener('resize',positionInk);
function hasPendingValue(v){return value(v)!==v.baselineValue}
function dirty(p){return p.vars.filter(hasPendingValue).length}
function dir(i){return `--icon-bg:${workingApps[i].iconBg};--icon-fg:${workingApps[i].iconFg}`}
function curProfile(){return scheme()}
function notify(t){const el=$('toast');el.textContent=t;el.classList.remove('on');void el.offsetWidth;el.classList.add('on');clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove('on'),2600)}
function go(view){if(screen==='config'&&view!=='config')ConfigEditor.suspendDraft(editor);screen=view;closeDropdowns();closeModal();render();enterFx();(view==='config'?$('configBtn'):document.querySelector('[data-view="'+view+'"]'))?.focus()}
function renderAcceptedUI(){document.querySelectorAll('[data-view]').forEach(b=>{b.classList.toggle('active',b.dataset.view===screen);if(b.dataset.view===screen)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current')});$('configBtn').classList.toggle('active',screen==='config');if(screen==='config')$('configBtn').setAttribute('aria-current','page');else $('configBtn').removeAttribute('aria-current');$('footer').style.display=screen==='config'?'none':'';if(screen==='workbench')renderWorkbench();else if(screen==='windows')renderWindows();else if(screen==='history')renderHistory();else renderConfig();positionInk();refreshPreview()}
function bindListKeys(root,selector){
 root.onkeydown=e=>{
  if(e.altKey||e.ctrlKey||e.metaKey||e.shiftKey||!['ArrowUp','ArrowDown','Home','End'].includes(e.key))return;
  const current=e.target.closest(selector);if(!current||!root.contains(current))return;
  const buttons=[...root.querySelectorAll(selector)].filter(button=>!button.disabled),index=buttons.indexOf(current);
  if(index<0||!buttons.length)return;
  e.preventDefault();const next=e.key==='Home'?0:e.key==='End'?buttons.length-1:Math.max(0,Math.min(buttons.length-1,index+(e.key==='ArrowDown'?1:-1)));
  buttons[next].focus();
 };
}
function restoreAppFilters(locate=false){
 search='';filter='all';$('appSearch').value='';renderAppList();
 if(locate){const target=document.querySelector(`[data-app-select="${selected}"]`);target?.focus();if(target)retrigger(target.closest('.appitem'),'is-located')}
 else $('appSearch').focus();
}
function renderWorkbenchUI(){
 const listScroll=$('applist')?.scrollTop??0;
 $('screen').innerHTML=`<aside class="sidebar"><div class="sidehead"><div class="sidecaption"><strong>应用</strong><span class="sub mini" id="selectionCounter"></span></div><div class="search"><span>⌕</span><input id="appSearch" placeholder="搜索应用或方案…" value="${esc(search)}" aria-label="搜索应用"><button type="button" class="search-clear" id="clearAppSearch" aria-label="清空应用搜索" title="清空搜索 · Esc" ${search?'':'hidden'}><b class="ic ic-cut" aria-hidden="true"></b></button></div><div class="filters"><button class="filter" data-filter="all">全部</button><button class="filter" data-filter="matched">已匹配</button><button class="filter" data-filter="unmatched">需配置</button></div></div><div class="batchbar"><span>批量选择</span><span><button class="textbtn" id="selectAll" title="选择当前搜索与筛选结果；保留已有勾选">全选可见</button><button class="textbtn" id="selectNone">清空</button></span></div><div class="list-context" id="appListContext"></div><div class="applist" id="applist" aria-label="应用搜索结果"></div></aside><section class="workspace"><div class="workscroll"><div class="overview" id="overview"></div><div class="content"><div class="sectionbar"><h2>环境变量 <small id="varTotal"></small></h2><div class="sectionright"><label class="checklabel"><input id="onlyMismatch" type="checkbox" ${onlyMismatch?'checked':''}>仅看未匹配</label><button class="textbtn" id="compareToggle" aria-controls="inspect" aria-expanded="${compareShown}">${compareShown?'收起':'展开'} Windows 对照</button></div></div><div class="tablebox"><table class="vartable"><thead><tr><th>变量</th><th>作用域 / 模式</th><th>值来源</th><th>当前选定值</th><th>检测</th><th>操作</th></tr></thead><tbody id="varRows"></tbody></table></div><div class="inspect" id="inspect" ${compareShown?'':'hidden'}><div class="inspecthead"><strong id="inspectTitle" tabindex="-1">Windows 值对照</strong><span class="mini sub">元素编辑 · 当前快照</span><div id="inspectReview"></div></div><div class="inspectbody value-comparison"><section class="selection-pane"><div class="inspect-value-heading"><div class="selection-heading-copy"><div class="k">当前选定值</div><div class="inspector-meta" id="compareLabel"></div></div><div id="compareActions" class="comparison-actions"></div></div><div class="codevalue value-box" id="compareDesired"></div></section><div class="comparison-pair"><section><div class="inspect-value-heading"><div class="k">Windows 当前值</div><button class="textbtn" id="editWindowsElements">编辑元素</button></div><div class="codevalue value-box inspect-editable" id="compareActual" tabindex="0" role="button" aria-label="打开 Windows 值元素编辑器" title="双击打开元素列表"></div></section><section class="result-pane"><div class="inspect-value-heading"><div class="k">应用后的值</div><div class="pane-tally" id="compareTally" aria-label="结果构成"></div><span class="mini sub">预览 · 尚未应用</span></div><div class="codevalue value-box" id="compareAfter" aria-live="polite"></div></section></div><div class="comparison-serialized"><span class="k">最终写入字符串 · 不含删除项</span><pre id="compareSerialized"></pre></div><div class="comparison-footer"><span class="inspector-meta" id="compareResult"></span><span class="inspector-meta" id="compareSummary"></span></div><div id="compareNotice" aria-live="polite"></div><p class="comparison-hint comparison-link-help">${WindowsElements.linkLegendMarkup()}<span>Tab 切换区域，方向键浏览元素</span></p></div></div><div class="helpnote">输入与配置不会直接写入环境。执行前请审阅服务端冻结计划。</div></div></div></section>`;
 $('appSearch').addEventListener('input',e=>{search=e.target.value;renderAppList()});$('clearAppSearch').onclick=()=>{search='';$('appSearch').value='';renderAppList();$('appSearch').focus()};$('appSearch').onkeydown=e=>{if(e.key==='Escape'&&search&&!e.isComposing){e.preventDefault();e.stopPropagation();$('clearAppSearch').click()}};$('selectAll').onclick=()=>{visibleApps().forEach(i=>checked.add(i));renderAppList();renderFooter()};$('selectNone').onclick=()=>{checked.clear();renderAppList();renderFooter()};document.querySelectorAll('[data-filter]').forEach(el=>el.onclick=()=>{filter=el.dataset.filter;renderAppList()});$('onlyMismatch').onchange=e=>{onlyMismatch=e.target.checked;renderRows()};$('compareToggle').onclick=()=>{compareShown=!compareShown;$('inspect').hidden=!compareShown;$('compareToggle').textContent=(compareShown?'收起':'展开')+' Windows 对照';$('compareToggle').setAttribute('aria-expanded',String(compareShown));if(compareShown)retrigger($('inspect'),'is-revealing')};renderAppList();$('applist').scrollTop=listScroll;renderOverview();renderRows();renderFooter()
}
function visibleApps(){
 const q=search.trim().toLowerCase();
 return workingApps.map((_,i)=>i).filter(i=>{const a=workingApps[i],p=scheme(i),n=matchCount(p);return (a.name+' '+a.profiles.map(x=>x.name).join(' ')).toLowerCase().includes(q)&&(filter!=='matched'||n===p.vars.length)&&(filter!=='unmatched'||n!==p.vars.length)});
}
function renderAppList(){
 const list=$('applist'),listScroll=list.scrollTop;
 const visible=visibleApps(),hidden=[...checked].filter(i=>!visible.includes(i)).length,total=workingApps.length;
 const fully=workingApps.filter((a,i)=>matchCount(scheme(i))===scheme(i).vars.length).length;
 $('selectionCounter').textContent=`已选 ${checked.size} / ${total}${hidden?' · 其中 '+hidden+' 个在当前筛选中隐藏':''}`;
 $('selectAll').disabled=!visible.length;$('clearAppSearch').hidden=!search;
 const outside=!visible.includes(selected);
 $('appListContext').innerHTML=`<span role="status">显示 ${visible.length} / ${total}${outside?' · 当前查看未在结果中':''}</span>${outside?'<button class="textbtn" id="locateCurrentApp">定位当前应用</button>':''}`;
 if(outside)$('locateCurrentApp').onclick=()=>restoreAppFilters(true);
 document.querySelectorAll('[data-filter]').forEach(b=>{const n=b.dataset.filter==='all'?total:b.dataset.filter==='matched'?fully:total-fully;b.textContent=({all:'全部',matched:'已匹配',unmatched:'需配置'})[b.dataset.filter]+` ${n}`;b.classList.toggle('active',b.dataset.filter===filter);b.setAttribute('aria-pressed',String(b.dataset.filter===filter))});
 $('applist').innerHTML=visible.map(i=>{const a=workingApps[i],p=scheme(i),n=matchCount(p);const pct=Math.round(n/Math.max(p.vars.length,1)*100),prev=ringMemo[i],from=prev===undefined?(ringBooted?pct:0):prev;ringMemo[i]=pct;return `<div class="appitem ${selected===i?'active':''}${from!==pct?' sweep':''}" data-kind="${appKind(p)}" style="--p:${pct};--p0:${from}" data-app="${i}" tabindex="-1" role="group" aria-label="${esc(a.name)}" aria-current="${selected===i?'page':'false'}"><input class="appcheck" type="checkbox" data-check="${i}" ${checked.has(i)?'checked':''} aria-label="批量选择${esc(a.name)}"><button class="appselect" data-app-select="${i}" aria-label="查看${esc(a.name)}" aria-pressed="${selected===i}"><span class="appicon" style="${dir(i)}">${esc(a.icon)}</span><span class="apptext"><b>${esc(a.name)}</b><small title="${esc(p.name)}">${esc(p.name)}${dirty(p)?' · 有待应用修改':''}</small></span><span class="appstat ${colorStatus(p)}"><span class="stat-top"><span class="rs-glyph" aria-hidden="true">${n===p.vars.length?'✓':'!'}</span> ${n}/${p.vars.length}</span><small>${n===p.vars.length?'匹配':'需配置'}</small><span class="pips" aria-hidden="true">${p.vars.slice(0,10).map(x=>`<i class="${itemMatched(x)?'on':''}"></i>`).join('')}</span></span></button></div>`}).join('')||`<div class="empty recovery-empty"><span class="empty-sigil" aria-hidden="true"><b class="ic ic-void"></b></span><strong>没有符合条件的应用</strong><p>${search.trim()?'试试应用名或方案名，或清除当前条件。':'当前匹配筛选下没有应用。'}</p><button class="btn" id="resetAppFilters">清除搜索与筛选</button><small>当前查看与批量勾选保持不变</small></div>`;list.scrollTop=listScroll;ringBooted=true;
 $('resetAppFilters')?.addEventListener('click',()=>restoreAppFilters());bindListKeys(list,'[data-app-select]');
 $('applist').querySelectorAll('[data-app]').forEach(row=>{
  const i=+row.dataset.app,choose=()=>{selected=i;focusedVariable=0;onlyMismatch=false;renderWorkbench();enterFx();document.querySelector('[data-app-select="'+i+'"]')?.focus({preventScroll:true})};
  row.addEventListener('click',e=>{if(!e.target.closest('[data-check]'))choose()});
  row.addEventListener('keydown',e=>{if((e.key==='Enter'||e.key===' ')&&e.target===row){e.preventDefault();choose()}});
  row.querySelector('[data-check]').addEventListener('change',e=>{e.stopPropagation();if(e.target.checked)checked.add(i);else checked.delete(i);renderAppList();renderFooter();document.querySelector('[data-check="'+i+'"]')?.focus({preventScroll:true})});
 });
}
function renderOverview(){const a=appNow(),p=curProfile(),n=matchCount(p);$('overview').innerHTML=`<div class="overview-main"><div class="titleline"><div class="bigicon" data-kind="${appKind(p)}" style="${dir(selected)};--p:${Math.round(n/Math.max(p.vars.length,1)*100)}">${esc(a.icon)}</div><div class="app-title">${esc(a.name)}</div><span class="tag ${colorStatus(p)}" data-kind="${appKind(p)}"><span class="rs-glyph" aria-hidden="true">${n===p.vars.length?'✓':'!'}</span> Windows · ${status(p)} ${n}/${p.vars.length}</span></div><div class="meta"><span>最近应用：<strong>${esc(a.active||'无')}</strong></span><span class="sep"></span><span>默认：<strong>${esc(a.default)}</strong></span>${dirty(p)?'<span class="overview-dirty">有待应用修改</span>':''}${a.description?`<details class="overview-description"><summary>说明</summary><p class="demo-description">${esc(a.description)}</p></details>`:''}</div></div><div class="plan-side"><div class="plan-field" id="planField"><label>准备方案</label><button class="plan-button" id="planButton" aria-expanded="false" aria-controls="planMenu" aria-haspopup="true"><span>${esc(p.name)}</span><span class="sub">⌄</span></button><div class="dropdown" id="planMenu"><div class="drophelp">准备方案 · 仅更新待应用值</div>${a.profiles.map(opt=>`<button class="option ${opt.name===p.name?'active':''}" data-plan="${esc(opt.name)}"><span>${opt.name===p.name?'✓ ':''}${esc(opt.name)}${a.default===opt.name?' · 默认':''}</span><span class="state ${colorStatus(opt)}">${matchCount(opt)}/${opt.vars.length}</span></button>`).join('')}</div></div></div>`;
 $('planButton').onclick=()=>{const menu=$('planMenu'),open=!menu.classList.contains('show');closeDropdowns();menu.classList.toggle('show',open);$('planButton').setAttribute('aria-expanded',String(open));if(open){const selectedButton=menu.querySelector('.option.active')||menu.querySelector('.option');selectedButton?.focus()}};bindDropdownKeyboard('planButton','planMenu','[data-plan]');$('planMenu').querySelectorAll('[data-plan]').forEach(btn=>btn.onclick=()=>{profileChoice[selected]=btn.dataset.plan;queuePreparedChoice(selected);focusedVariable=0;closeDropdowns();renderWorkbench();enterFx();$('planButton')?.focus()})}
function closeDropdowns() {
 document.querySelectorAll('.dropdown.show').forEach(menu=>{
  const trigger=menu.id==='planMenu'?$('planButton'):menu.id==='revokeMenu'?$('revokeButton'):null;
  const ownsFocus=menu.contains(document.activeElement);menu.classList.remove('show');
  trigger?.setAttribute('aria-expanded','false');if(ownsFocus)trigger?.focus();
 });
}
function bindDropdownKeyboard(triggerId,menuId,selector) {
 const trigger=$(triggerId),menu=$(menuId);
 trigger.onkeydown=e=>{if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();if(!menu.classList.contains('show'))trigger.click()}};
 menu.onkeydown=e=>{
  const items=[...menu.querySelectorAll(selector)],current=items.indexOf(document.activeElement);
  if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){
   e.preventDefault();const index=e.key==='Home'?0:e.key==='End'?items.length-1:(current+(e.key==='ArrowDown'?1:-1)+items.length)%items.length;items[index]?.focus();
  }else if(e.key==='Escape'){e.preventDefault();closeDropdowns();trigger.focus()}
 };
}
function renderRows() {
  const p=curProfile();
  $('varTotal').textContent=`${p.vars.length} 项`;
  const ids=p.vars.map((v,i)=>i).filter(i=>!onlyMismatch||!itemMatched(p.vars[i]));
  $('varRows').innerHTML=ids.length?ids.map(i=>{
    const v=p.vars[i],m=itemMatched(v),pending=hasPendingValue(v);
    const src=['默认值',...(v.savedValue!==null?['已保存值']:[]),...v.choices.map((_,index)=>'候选值 '+(index+1)),...(v.editable!==false?['自定义值']:[])];
    return `<tr class="${focusedVariable===i?'sel':''}" data-row="${i}" data-record-id="${esc(v.recordId)}" data-kind="${kindOf(v)}" style="--i:${Math.min(ids.indexOf(i),10)}"><td><button class="inspect-variable" data-inspect="${i}" aria-label="查看 ${esc(v.name)} 的 Windows 值对比" aria-controls="inspect" title="在下方对比当前选定值、Windows 当前值和应用后的值"><span class="vname">${esc(v.name)}</span><small>Windows 值对比</small></button></td><td><span class="scope" data-mode="${esc(v.mode)}">${v.scope==='User'?'用户':'系统'} <span>· ${MODE[v.mode]||v.mode}</span></span></td><td><select class="source ${v.source==='自定义值'?'is-custom':''}" aria-label="${esc(v.name)} 值来源">${src.map(source=>`<option value="${esc(source)}" ${v.source===source?'selected':''}>${esc(source.startsWith('候选值 ')?'候选 · '+ConfigFormat.choices(v)[+source.slice(4)-1].label:source)}</option>`).join('')}</select></td><td><div class="running-value"><textarea rows="1" wrap="soft" class="vinput ${pending?'modified':''}" aria-label="${esc(v.name)} 当前选定值" title="${esc(value(v))}" spellcheck="false" ${v.editable===false?'readonly aria-readonly="true"':''}></textarea><span class="pending-state" ${pending?'':'hidden'} title="与初始或最近应用的选定值不同；Windows 检测独立计算">待应用</span></div></td><td><span class="result ${m?'':'warn'}" data-kind="${kindOf(v)}" title="比例按选定的非空元素逐项统计；重复项分别计数，不要求当前值具有相同副本数量；空元素保留但不计比例。替换模式还需核对额外内容。">${glyphed(detectionText(v))}</span></td><td><button class="actmini" title="查看历史操作撤销范围；不会恢复当前输入" data-undo="${i}">撤销预览</button></td></tr>`;
  }).join(''):`<tr><td colspan="6" class="empty"><div class="variable-empty"><span class="empty-sigil" aria-hidden="true"><b class="ic ic-check"></b></span><div><strong>${onlyMismatch?'当前方案没有未匹配的变量':'暂无变量'}</strong>${onlyMismatch?'<p>可返回完整列表查看所有变量。</p>':''}</div>${onlyMismatch?'<button class="btn" id="showAllVariables">显示全部变量</button>':''}</div></td></tr>`;
 if($('showAllVariables'))$('showAllVariables').onclick=()=>{onlyMismatch=false;$('onlyMismatch').checked=false;renderRows();$('onlyMismatch').focus()};
  $('varRows').querySelectorAll('[data-row]').forEach(tr=>{
    const i=+tr.dataset.row,v=p.vars[i],inp=tr.querySelector('.vinput'),src=tr.querySelector('.source');
    tr.onclick=e=>{if(e.target.closest('button,select,input,textarea'))return;focusedVariable=i;renderRows();};
    const focusVariable=()=>{focusedVariable=i;$('varRows').querySelectorAll('tr').forEach(row=>row.classList.remove('sel'));tr.classList.add('sel');renderInspector();};
    inp.onfocus=focusVariable;src.onfocus=focusVariable;
    inp.onchange=()=>{if(onlyMismatch&&itemMatched(v)){renderRows();$('onlyMismatch')?.focus()}};
    ConfigEditor.bindRawInput(inp,()=>value(v),next=>{
      if(v.editable===false){inp.value=value(v);return;}
      setSelectedValue(v,next);src.value='自定义值';
      sizeSelectedValueInput(inp);
      inp.classList.toggle('modified',v.dirty);inp.title=value(v);tr.querySelector('.pending-state').hidden=!v.dirty;
      const m=itemMatched(v),result=tr.querySelector('.result'),kind=kindOf(v),changed=result.dataset.kind!==kind;result.innerHTML=glyphed(detectionText(v));result.classList.toggle('warn',!m);result.dataset.kind=kind;tr.dataset.kind=kind;src.classList.toggle('is-custom',true);if(changed){retrigger(tr,'stamp');retrigger(result,'stamp')}
      renderInspector();renderFooter();renderAppList();renderOverview();
      $('varTotal').textContent=`${p.vars.length} 项`;
    });
    sizeSelectedValueInput(inp);
    src.onchange=e=>{
      const prev=value(v);v.matchBaseline=null;v.removalState=null;v.source=e.target.value;if(v.source==='自定义值'&&v.custom===null)v.custom=prev;
      v.dirty=hasPendingValue(v);queueSelection(v);focusedVariable=i;renderRows();renderFooter();renderAppList();renderOverview();
      ($('varRows').querySelector('[data-row="'+i+'"] .source')||$('onlyMismatch'))?.focus();
    };
    tr.querySelector('[data-inspect]').onclick=()=>{focusedVariable=i;compareShown=true;renderRows();$('inspect').hidden=false;$('compareToggle').setAttribute('aria-expanded','true');$('compareToggle').textContent='收起 Windows 对照';$('inspectTitle').focus();$('inspectTitle').scrollIntoView?.({block:'nearest'});};
    tr.querySelector('[data-undo]').onclick=()=>showPreview('撤销','单项撤销',[{app:selected,profile:p.name,varIdx:i}]);
    if(unsupportedTarget(v)){inp.readOnly=true;inp.setAttribute('aria-readonly','true');src.disabled=true;tr.querySelector('[data-undo]').disabled=true;}
  });
  renderInspector();
}
function renderInspectorUI() {
  const v=curProfile().vars[focusedVariable]||curProfile().vars[0];if(!v)return;
  if(unsupportedTarget(v)){renderUnsupportedInspector(v);return;}
  $('inspectTitle').textContent='Windows 值对照 · '+v.name;
  $('inspect').querySelector('.inspecthead > .mini').textContent=v.mode==='replace'?'整值替换 · 当前快照':'元素编辑 · 当前快照';
  const plan=demoPlan(v),view=WindowsElements.comparisonMarkup(plan);
  const invalid=plan.valid===false;
  $('compareDesired').innerHTML=view.selected;$('compareActual').innerHTML=view.actual;
  $('compareAfter').innerHTML=invalid?`<div class="stale-result"><strong>编辑上下文已变化，请检查作用域与模式</strong><p>编辑目标或模式发生变化，原编辑意图不能用于新上下文。当前输入仍保留。</p></div>`:view.after;
  $('compareAfter').closest('.comparison-pair').classList.toggle('is-plan-stale',invalid);
  $('compareAfter').closest('.result-pane').querySelector('.inspect-value-heading > .mini').textContent=invalid?'等待重新预览':'预览 · 尚未应用';
  $('compareTally').innerHTML=invalid?'':view.tally||'';
  $('compareSerialized').closest('.comparison-serialized').hidden=invalid;
  $('compareSerialized').textContent=invalid?'':plan.after.exists?(plan.after.raw===''?'（空字符串）':plan.after.raw):'（不存在）';
  $('compareLabel').textContent=(selectionEdit(v)?'选定值已修改 · ':'')+(v.scope==='User'?'用户':'系统')+' · '+MODE[v.mode]+' · '+v.source;
  $('compareResult').textContent=detectionText(v);$('compareSummary').textContent=invalid?'':planSummary(plan)||'无需改变当前值';
  let review=$('inspectReview');if(!review){review=document.createElement('div');review.id='inspectReview';$('inspect').querySelector('.inspecthead').append(review);}
  const inspectState=reviewState(plan,{ordering:v.mode==='append'});
  review.dataset.reviewState=inspectState.kind;review.innerHTML=inspectReviewMarkup(v,plan);$('inspectPreview').onclick=()=>$('previewButton').click();
  $('compareActions').innerHTML=v.matchBaseline?'<button class="textbtn" id="restoreSelectedEdit" aria-label="恢复本次选定值编辑，不改变当前 Windows 值">恢复编辑</button>':'';
  if($('restoreSelectedEdit'))$('restoreSelectedEdit').onclick=()=>restoreSelectedValue(v);
  const notice=$('compareNotice');
  const refreshed=!invalid&&v.matchBaseline&&JSON.stringify(v.matchBaseline.snapshot)!==JSON.stringify(demoSnapshot(v));
  notice.textContent=refreshed?'已按最新当前值自动更新预览，当前输入已保留。':'';
  notice.hidden=!notice.textContent;
  const editable=v.mode!=='replace',actual=$('compareActual'),editButton=$('editWindowsElements');
  const open=()=>openWindowsElements(v.scope,v.name,[value(v)],v.special,v.mode,null,null,plan);
  editButton.hidden=!editable;editButton.onclick=editable?open:null;
  actual.classList.toggle('inspect-editable',editable);
  for(const [attribute,content] of [['role','button'],['tabindex','0'],['aria-label','打开 Windows 值元素编辑器'],['title','双击打开元素列表']]){
    if(editable)actual.setAttribute(attribute,content);else actual.removeAttribute(attribute);
  }
  actual.ondblclick=editable?open:null;
  actual.onkeydown=editable?e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();open()}}:null;
  WindowsElements.bindComparison($('inspect').querySelector('.value-comparison'));
  playRemovalFx();
}
function sameSnapshot(plan,v){
 const now=demoSnapshot(v);
 return now.exists===plan.before.exists&&now.raw===plan.before.raw&&now.type===plan.before.type&&now.revision===plan.baseRevision;
}
function historyChangeFromPlan(plan,extra={}){
 return Object.freeze({...plan.audit,...extra,scope:plan.targetKey.scope,name:plan.targetKey.name,before:plan.beforeRaw,after:plan.afterRaw,whole:plan.special,mode:plan.mode,operations:plan.operations,plan,unchanged:!plan.operations.length});
}
function renderFooterUI(){if(screen!=='workbench')return;if($('inspectPreview'))$('inspectPreview').textContent=inspectPreviewText()+' →';const names=checked.size?[...checked].map(i=>workingApps[i].name+' / '+scheme(i).name).join('、'):appNow().name+' / '+curProfile().name;const dirtyN=workingApps.reduce((n,a)=>n+a.profiles.reduce((k,p)=>k+dirty(p),0),0);$('footer').innerHTML=`<div class="footer-summary"><strong>${checked.size?'批量 · 已选 '+checked.size+' 个应用':'单应用 · '+esc(names)}</strong><small>${dirtyN?`${dirtyN} 项待应用修改 · `:''}配置与环境独立保存</small></div><div class="btndrop"><button class="btn" id="revokeButton" title="预览历史操作的撤销范围" aria-expanded="false" aria-controls="revokeMenu" aria-haspopup="true">撤销配置 ⌄</button><div class="dropdown" id="revokeMenu" style="top:auto;bottom:calc(100% + 6px);width:235px"><button class="option" data-revoke="var">撤销当前变量</button><button class="option" data-revoke="scheme">撤销当前方案</button><button class="option" data-revoke="app">撤销当前应用全部方案</button><button class="option" data-revoke="batch">撤销已勾选方案</button></div></div><button class="btn primary" id="previewButton">预览并应用 →</button>`;$('previewButton').onclick=()=>{const ids=checked.size?[...checked]:[selected];showPreview('应用',checked.size?'批量方案预览':'单应用方案预览',ids.flatMap(i=>scheme(i).vars.map((v,j)=>({app:i,profile:scheme(i).name,varIdx:j}))))};$('revokeButton').onclick=()=>{const dd=$('revokeMenu'),op=!dd.classList.contains('show');closeDropdowns();dd.classList.toggle('show',op);$('revokeButton').setAttribute('aria-expanded',String(op));if(op)dd.querySelector('[data-revoke]')?.focus()};bindDropdownKeyboard('revokeButton','revokeMenu','[data-revoke]');$('revokeMenu').querySelectorAll('[data-revoke]').forEach(b=>b.onclick=()=>{let mode=b.dataset.revoke,targets=[];if(mode==='batch'){if(!checked.size)return notify('请先勾选应用');targets=[...checked].flatMap(i=>scheme(i).vars.map((v,j)=>({app:i,profile:scheme(i).name,varIdx:j})))}else if(mode==='var')targets=[{app:selected,profile:scheme().name,varIdx:focusedVariable}];else if(mode==='scheme')targets=scheme().vars.map((v,j)=>({app:selected,profile:scheme().name,varIdx:j}));else targets=appNow().profiles.flatMap(p=>p.vars.map((v,j)=>({app:selected,profile:p.name,varIdx:j})));showPreview('撤销',b.textContent,targets)})}
function newValue(v,actual){
 return demoPlan(v,actual).afterRaw;
}
function valueStreamMarkup(content,className='',label='元素值'){
 return `<div class="value-stream ${className}" role="group" aria-label="${esc(label)}">${content}</div>`;
}
function rawStreamMarkup(raw,{whole=false,comparison=false,className='',label='元素值'}={}){
 return valueStreamMarkup(WindowsElements.staticMarkup(raw,{whole,comparison}),className,label);
}
function decorateValueStreams(root,{play=true}={}){
 if(!root)return;
 root.querySelectorAll('.value-stream').forEach(stream=>{
  [...stream.querySelectorAll('.diff-token,.value-state-chip')].forEach((token,index)=>token.style.setProperty('--token-i',Math.min(index,7)));
  if(play)stream.classList.add('fx-stream-enter');
  const tokens=[...stream.querySelectorAll('.comparison-static-token')];
  if(tokens.length){
   if(!stream.closest('.history-change[data-linked-elements]'))WindowsElements.bindComparison(stream);
   stream.onfocusin=e=>{const token=e.target.closest('.comparison-static-token');if(token)tokens.forEach(item=>item.tabIndex=item===token?0:-1)};
   stream.onkeydown=e=>{
    if(e.altKey||e.ctrlKey||e.metaKey||e.shiftKey||!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'].includes(e.key))return;
    const token=e.target.closest('.comparison-static-token'),at=tokens.indexOf(token);if(at<0)return;
    e.preventDefault();const next=e.key==='Home'?0:e.key==='End'?tokens.length-1:Math.max(0,Math.min(tokens.length-1,at+(['ArrowRight','ArrowDown'].includes(e.key)?1:-1)));tokens[next].focus();
   };
  }
 });
 root.querySelectorAll('.value-link-group').forEach(group=>WindowsElements.bindComparison(group));
 root.querySelectorAll('.history-change[data-linked-elements]').forEach(group=>WindowsElements.bindComparison(group));
}
function previewRowMarkup(r,kind,index){
 const sources=r.members.map(m=>esc(workingApps[m.app]?.name||'手动编辑')+' / '+esc(m.profile)+(r.plan?.valid===false?' · '+esc(MODE[m.v.mode]||m.v.mode):'')).join('、');
 const meta=esc(r.members.length>1?'批量合并':MODE[r.v.mode]||(r.plan?.source==='windows-element-edit'?'手动编辑':r.v.mode));
 const state=reviewRowState(r,kind),target=reviewTargetKey(r);
 const manual=r.plan?.kind==='element-edit'||r.plan?.source==='windows-element-edit';
 // Manual correspondence is the server's frozen occurrence audit. Scheme
 // comparisonMarkup intentionally treats replace plans as whole strings.
 const view=manual?{
   actual:WindowsElements.historyMarkup(r.plan.before.exists?r.plan.before.raw:undefined,{whole:!!r.plan.special,elements:r.plan.audit?.elements||[],side:'before'}),
   after:r.plan.valid===false?'<span class="invalid-result">编辑上下文或目标值存在冲突</span>':WindowsElements.historyMarkup(r.plan.after.exists?r.plan.after.raw:undefined,{whole:!!r.plan.special,elements:r.plan.audit?.elements||[],side:'after'})
 }:r.plan?WindowsElements.comparisonMarkup(r.plan):null;
 const before=valueStreamMarkup(view?view.actual:WindowsElements.staticMarkup(r.before,{whole:r.whole}),'is-before','Windows 当前');
 const after=view?valueStreamMarkup(view.after,'is-after','目标结果'):`<div class="operation-note"><b class="ic ic-undo" aria-hidden="true"></b><span>${esc(r.after)}</span></div>`;
 const ordering=r.needsOrdering;
 return `<section class="diffrow value-link-group review-row" data-preview-index="${index}" data-review-key="${esc(target)}" data-review-state="${state.kind}"><div class="k review-row-heading" tabindex="-1" data-review-control="heading"><strong>${esc(r.v.name)}</strong><span class="scope-chip" data-scope="${r.scope==='User'?'user':'system'}">${r.scope==='User'?'用户':'系统'}</span><span class="review-state">${state.label}</span><span class="review-source">${sources} · ${meta}</span></div>${ordering?'<p class="banner warn review-limitation">请先确认此目标的自定义排序，再重新审阅服务端计划。</p>':''}${kind!=='应用'?'<p class="review-limitation">此计划由服务端根据持久历史及当前环境生成。</p>':''}${reviewIssueMarkup(r.plan)}${state.blocked||state.retained.length?`<button class="textbtn review-return" data-review-control="return">返回工作台检查 →</button>`:''}<div class="preview-flow"><section><span class="flow-label">Windows 当前</span>${before}</section><i class="flow-arrow ic ic-arrow" aria-hidden="true"></i><section><span class="flow-label">${kind==='应用'?'目标结果 · 尚未应用':'操作说明'}</span>${after}</section></div><details class="raw-disclosure"><summary data-review-control="raw">完整原始字符串</summary><div class="pair del"><span>Windows 当前</span><span data-preview-before></span></div><div class="pair add"><span>${kind==='应用'?'目标结果':'操作说明'}</span><span data-preview-after></span></div></details>${r.plan&&planSummary(r.plan)?`<p class="plan-summary">${esc(planSummary(r.plan))}</p>`:''}</section>`;
}
function renderWindowsUI() {
  const items=Object.keys(windows).sort();
  const wholeFor=k=>workingApps.some(a=>a.profiles.some(p=>p.vars.some(v=>key(v)===k&&v.special)));
  $('screen').innerHTML=`<div class="fullpage windows-page" style="width:100%"><div class="pageh"><div><h2>Windows 环境</h2><p>点击元素直接编辑 · 方向键定位元素</p></div><span class="tag neutral">${items.length} 项变量</span></div>
    <table class="outline-table"><thead><tr><th style="width:88px">作用域</th><th style="width:170px">变量</th><th>Windows 当前值</th><th style="width:104px">操作</th></tr></thead><tbody>${items.map((k,index)=>
      `<tr><td><span class="scope-chip" data-scope="${k.startsWith('User')?'user':'system'}">${k.startsWith('User')?'用户':'系统'}</span></td><td><strong>${esc(k.split('|')[1])}</strong></td><td><div class="codevalue value-stream windows-current" tabindex="-1" role="group" aria-label="${esc(k.split('|')[1])} 当前值" data-windows-value="${esc(k)}">${WindowsElements.staticMarkup(windows[k],{whole:wholeFor(k),comparison:true,interactive:true})}</div><pre class="windows-raw-value" id="windowsRaw${index}" data-windows-raw="${esc(k)}" hidden></pre></td><td class="windows-actions"><button class="textbtn" data-windows-edit="${esc(k)}">编辑</button><button class="textbtn" data-windows-show-raw="${esc(k)}" aria-controls="windowsRaw${index}" aria-expanded="false">原文</button></td></tr>`
    ).join('')}</tbody></table></div>`;
  const open=(k,index=null)=>{
    const [scope,name]=k.split('|');openWindowsElements(scope,name,[],wholeFor(k));
    if(index!==null)$('modal').querySelectorAll('.element-row .element-input')[index]?.focus();
  };
  $('screen').querySelectorAll('[data-windows-value]').forEach(el=>{
    const k=el.dataset.windowsValue;
    el.closest('tr').querySelector('[data-windows-raw]').textContent=windows[k];
    el.querySelectorAll('[data-value-index]').forEach(token=>token.onclick=()=>open(k,+token.dataset.valueIndex));
    el.ondblclick=e=>{if(!e.target.closest('[data-value-index]'))open(k)};
    el.addEventListener('keydown',e=>{if(e.target===el&&(e.key==='Enter'||e.key===' ')){e.preventDefault();open(k)}});
  });
  $('screen').querySelectorAll('[data-windows-edit]').forEach(button=>button.onclick=()=>open(button.dataset.windowsEdit));
  $('screen').querySelectorAll('[data-windows-show-raw]').forEach(button=>button.onclick=()=>{
    const raw=button.closest('tr').querySelector('[data-windows-raw]'),expanded=raw.hidden;raw.hidden=!expanded;button.setAttribute('aria-expanded',String(expanded));button.textContent=expanded?'收起原文':'原文';
  });
  $('footer').innerHTML=`<div class="footer-summary"><strong>Windows 环境</strong><small>直接编辑当前值 · 审阅后执行真实后端计划</small></div><button class="btn" id="toWork">返回工作台</button>`;
  $('toWork').onclick=()=>go('workbench');decorateValueStreams($('screen'),{play:false});
}
function changeCountText(counts){return [['added','新增'],['removed','删除'],['replaced','替换'],['moved','移动']].filter(([kind])=>counts[kind]).map(([kind,label])=>`${label} ${counts[kind]}`).join(' · ')||'整体值修改'}
function historyValue(raw){return raw===undefined?'（不存在）':raw===''?'（空字符串）':esc(raw)}
function historyElementValue(raw){return raw===undefined?'—':raw===''?'（空元素）':esc(raw)}
function historyChangeMarkup(c){
  const changed=c.whole?[]:(c.elements||[]).filter(e=>e.added||e.removed||e.replaced||e.moved);
  return `<section class="history-change"${c.elements?.length?' data-linked-elements':''}><div class="history-change-heading"><strong>${c.scope==='User'?'用户':'系统'} / ${esc(c.name)}</strong><span class="sub mini">${c.whole?'整体值':'元素列表'}${c.mode?' · '+esc(c.mode==='batch'?'批量合并':MODE[c.mode]||'元素编辑'):''}${c.unchanged?' · 无需变化':''}</span></div>
    ${c.app?`<p class="history-source">来源：${esc(c.app)} / ${esc(c.profile)}</p>`:''}
    ${!c.elements&&c.operations?.length?`<p class="history-counts">${esc(planSummary(c.plan))}</p>`:''}
    ${c.counts&&!c.whole?`<p class="history-counts">${esc(changeCountText(c.counts))} <span class="sub mini">同一元素可同时计入替换与移动</span></p>`:''}
    <div class="history-structure"><div><span class="flow-label">修改前 · 记录快照</span>${valueStreamMarkup(WindowsElements.historyMarkup(c.before,{whole:c.whole,elements:c.elements,side:'before'}),'','历史修改前')}</div><div><span class="flow-label">修改后 · 记录快照</span>${valueStreamMarkup(WindowsElements.historyMarkup(c.after,{whole:c.whole,elements:c.elements,side:'after'}),'','历史修改后')}</div></div>${c.elements?.length?'<p class="stream-help">悬停或聚焦元素与下方明细，追踪同一项的修改前后。方向键定位，明细使用 ↑↓。</p>':''}<details class="raw-disclosure"><summary>完整原始字符串</summary><div class="history-values"><div><span class="k">修改前</span><pre class="history-before">${historyValue(c.before)}</pre></div><div><span class="k">修改后</span><pre class="history-after">${historyValue(c.after)}</pre></div></div></details>
    ${changed.length?`<table class="history-element-table"><thead><tr><th>位置（原 → 新）</th><th>状态</th><th>原元素</th><th>新元素</th></tr></thead><tbody>${changed.map((e,index)=>`<tr data-related-key="history:${esc(e.elementId)}" tabindex="${index===0?0:-1}"><td>${e.beforeIndex===null?'—':e.beforeIndex+1} → ${e.afterIndex===null?'—':e.afterIndex+1}</td><td>${[['added','新增'],['removed','删除'],['replaced','替换'],['moved','移动']].filter(([kind])=>e[kind]).map(([kind,label])=>`<span class="diff-label is-${kind}">${label}</span>`).join('')}</td><td><code>${historyElementValue(e.before)}</code></td><td><code>${historyElementValue(e.after)}</code></td></tr>`).join('')}</tbody></table>`:''}
  </section>`;
}
function renderHistoryUI(){
  const summary=h=>`<span class="history-when"><span class="sr-only">时间：</span>${esc(h.when)}</span><span class="act-chip"><span class="sr-only">操作：</span>${esc(h.action)}</span><span class="history-description"><span class="sr-only">描述：</span>${esc(h.detail)}</span>`;
  $('screen').innerHTML=`<div class="fullpage history-page" style="width:100%"><div class="pageh"><div><h2>操作历史</h2><p>持久操作记录 · 展开查看修改前后与元素变化</p></div><span class="tag neutral">${history.length} 条记录</span></div><div class="history-list-head" aria-hidden="true"><span>时间</span><span>操作</span><span>描述与详情</span></div><ol class="history-list" aria-label="操作历史记录">${history.map((h,historyIndex)=>`<li data-history-index="${historyIndex}">${h.changes?.length?`<details class="history-details"><summary class="history-summary">${summary(h)}<span class="history-expand">查看详情</span></summary><div class="history-detail-body">${h.changes.map(historyChangeMarkup).join('')}<p class="sub mini">恢复前需要审阅新的环境计划。</p></div></details>`:`<div class="history-summary history-static">${summary(h)}</div>`}</li>`).join('')}</ol></div>`;
  $('screen').querySelectorAll('[data-history-index]').forEach(row=>{
    const h=history[+row.dataset.historyIndex];
    row.querySelectorAll('.history-change').forEach((section,index)=>{
      const c=h.changes[index];
      section.querySelector('.history-before').textContent=c.before===undefined?'（不存在）':c.before===''?'（空字符串）':c.before;
      section.querySelector('.history-after').textContent=c.after===undefined?'（不存在）':c.after===''?'（空字符串）':c.after;
      const table=section.querySelector('.history-element-table');
      if(table){bindListKeys(table,'tbody tr[data-related-key]');table.onfocusin=e=>{const row=e.target.closest('tr[data-related-key]');if(row)table.querySelectorAll('tbody tr').forEach(item=>item.tabIndex=item===row?0:-1)}};
    });
    const details=row.querySelector('.history-details');
    if(details){
      let focusedDetail=false;
      details.addEventListener('focusin',e=>{focusedDetail=!!e.target.closest('.history-detail-body')});
      details.addEventListener('focusout',e=>{if(details.open&&!details.contains(e.relatedTarget))focusedDetail=false});
      details.addEventListener('toggle',()=>{
        const caption=details.querySelector('.history-expand');caption.textContent=details.open?'收起详情':'查看详情';
        if(details.open)decorateValueStreams(details);
        else if(focusedDetail){details.querySelector('summary').focus();focusedDetail=false}
      });
    }
  });
  $('footer').innerHTML=`<div class="footer-summary"><strong>操作日志</strong><small>历史来自实际执行记录</small></div><button class="btn" id="toWork">返回工作台</button>`;$('toWork').onclick=()=>go('workbench');
}
function configProfile(){const a=apps[editor.app];return a.profiles.find(p=>p.name===editor.profile)||a.profiles[0]}
function configVar(){return configProfile().vars[editor.varIdx]||configProfile().vars[0]}
const CONFIG_FIELDS = [['defaultValue','默认值'],['scope','作用域'],['mode','模式'],['special','整体处理'],['editable','运行时自由输入'],['choices','候选值']];
function configAttributes(v){return Object.fromEntries(CONFIG_FIELDS.map(([field])=>[field,field==='choices'?ConfigFormat.choices(v):v[field]]));}
function configTree(node,kind){
 if(kind==='variable')return {name:node.name,...clone(configAttributes(node))};
 if(kind==='profile')return {name:node.name,vars:node.vars.map(v=>configTree(v,'variable'))};
 return {name:node.name,default:node.default,profiles:node.profiles.map(p=>configTree(p,'profile'))};
}
function captureConfigBaselines(){
 configOriginals=new WeakMap();configurationBaselineById=new Map();
 for(const app of productState.configurationBaseline){configurationBaselineById.set(app.recordId,{name:app.name,default:app.default});for(const profile of app.profiles){configurationBaselineById.set(profile.recordId,{name:profile.name});for(const v of profile.vars)configurationBaselineById.set(v.recordId,clone(configAttributes(v)));}}
 bindConfigBaselines();
}
function bindConfigBaselines(){
 for(const app of apps)for(const node of [app,...app.profiles,...app.profiles.flatMap(p=>p.vars)]){
  const baseline=configurationBaselineById.get(node.recordId);if(baseline)configOriginals.set(node,baseline);
 }
}
function createConfigEntry(kind,fields={},context={app:editor.app,profile:editor.profile}){
 const fail=(field,error)=>({ok:false,field,error});
 if(!['app','profile','variable'].includes(kind))return fail('name','新增类型无效');
 const nameOK=text=>typeof text==='string'&&text.trim()!==''&&!text.includes('\0');
 if(kind==='app'&&!nameOK(fields.appName))return fail('appName','请填写应用名称，不能仅包含空白或 NUL');
 if(kind!=='variable'&&(!nameOK(fields.profileName)||fields.profileName==='default_option'))return fail('profileName','请填写方案名称，不能使用保留名 default_option');
 if(!nameOK(fields.name)||fields.name.includes('='))return fail('name','变量名不能为空、仅为空白，或包含 =、NUL');
 const app=apps[context.app],profile=app?.profiles.find(p=>p.name===context.profile);
 if(kind!=='app'&&!app)return fail('appName','应用已不存在，请重新选择');
 if(kind==='variable'&&!profile)return fail('profileName','方案已不存在，请重新选择');
 if(kind==='app'&&apps.some(a=>a.name===fields.appName))return fail('appName','应用名称已存在');
 if(kind==='profile'&&app.profiles.some(p=>p.name===fields.profileName))return fail('profileName','此应用中已存在同名方案');
 const scope=fields.scope??'User',mode=fields.mode??'replace';
 if(!['User','System'].includes(scope))return fail('scope','作用域必须是 User 或 System');
 if(!Object.hasOwn(MODE,mode))return fail('mode','应用模式无效');
 const canonical=ConfigFormat.matchName(fields.name);
 if(kind==='variable'&&profile.vars.some(v=>v.scope===scope&&ConfigFormat.matchName(v.name)===canonical))return fail('name','同一方案中已存在此作用域的同名变量');
 const targetVars=apps.flatMap(a=>a.profiles.flatMap(p=>p.vars)).filter(v=>v.scope===scope&&ConfigFormat.matchName(v.name)===canonical);
 const special=targetVars[0]?.special??false;
 const variable=mkVar([fields.name,scope,special?'replace':mode,'',{special}],kind==='variable'?Math.max(-1,...profile.vars.map(v=>v.id))+1:0);
 const newProfile={name:fields.profileName,vars:[variable]};
 const newApp={name:fields.appName,icon:Array.from(fields.appName||'')[0]||'A',iconBg:'#edf3ef',iconFg:'#286c51',active:null,default:fields.profileName,profiles:[newProfile]};
 const candidate=kind==='app'?[...apps,newApp]:apps.map(a=>a!==app?a:{...a,profiles:kind==='profile'?[...a.profiles,newProfile]:a.profiles.map(p=>p!==profile?p:{...p,vars:[...p.vars,variable]})});
 const problems=ConfigFormat.errors(candidate);
 if(problems.length)return fail('name','请先修正配置：'+problems[0].message);
 if(kind==='app'){apps.push(newApp);editor.app=apps.length-1;draftProfileChoice[editor.app]=newApp.default;editor.profile=newProfile.name;editor.varIdx=0;}
 else if(kind==='profile'){app.profiles.push(newProfile);editor.app=context.app;editor.profile=newProfile.name;editor.varIdx=0;}
 else {profile.vars.push(variable);editor.app=context.app;editor.profile=profile.name;editor.varIdx=profile.vars.length-1;}
 editor.showAll=false;scheduleDraft();
 return {ok:true,app:editor.app,profile:editor.profile,varIdx:editor.varIdx};
}
function configChanges() {
  const addition=(a,p,v,kind,node)=>({app:a.name,profile:p?.name??'',name:v?.name??'',field:'addition',label:'新增'+{app:'应用',profile:'方案',variable:'变量'}[kind],before:null,after:configTree(node,kind)});
  return apps.flatMap(a=>!configOriginals.has(a)?[addition(a,null,null,'app',a)]:a.profiles.flatMap(p=>!configOriginals.has(p)?[addition(a,p,null,'profile',p)]:p.vars.flatMap(v=>{
    const original=configOriginals.get(v);if(!original)return [addition(a,p,v,'variable',v)];
    return CONFIG_FIELDS.filter(([field])=>JSON.stringify(field==='choices'?ConfigFormat.choices(v):v[field])!==JSON.stringify(original[field])).map(([field,label])=>({app:a.name,profile:p.name,name:v.name,field,label,before:original[field],after:field==='choices'?ConfigFormat.choices(v):v[field]}));
  })));
}
function updateConfigStatus() {
  const count=configChanges().length;
  const errors=configurationErrors();
  const changeUnit=configChanges().some(c=>c.field==='addition')?'配置变更':'属性修改';
  if ($('configStatus')) { $('configStatus').textContent=(count?`配置修改 · ${count} 项${changeUnit} · 草稿自动保存`:'配置属性未修改 · Ctrl+S 保存受管文件')+(errors.length?` · ${errors.length} 项错误，暂不能导出`:'');$('configStatus').classList.toggle('has-changes',count>0); }
  if($('downloadToml')){$('downloadToml').disabled=errors.length>0;$('downloadToml').title=errors.length?errors[0].message:'导出符合 V2 配置格式的新文件';}
  if($('saveConfig')){$('saveConfig').disabled=errors.length>0;$('saveConfig').title=errors.length?errors[0].message:'保存受管配置';}
  if ($('cancelConfig')) $('cancelConfig').disabled=count===0&&!ConfigEditor.hasDraft(editor);
  const badge=$('configDraftBadge');
  if (badge) { badge.hidden=!count; badge.textContent=`${count} 项${changeUnit}`; }
  renderConfigurationNotice();
}
function configurationErrors(){
 // Before acknowledgement validate the current input locally; afterwards use
 // the complete authoritative error list for that exact draft revision.
 // Combining both lists repeats one fault with different message wording.
 if(configVersion===configAckVersion&&Array.isArray(productState?.workspace.errors))return productState.workspace.errors.map(error=>typeof error==='string'?{message:error}:error);
 return ConfigFormat.errors(apps);
}
function requireValidConfiguration(){const errors=configurationErrors();if(errors.length)throw new Error('配置草稿无效，不能保存或导出：'+errors[0].message);}
function renderConfigurationNotice(){
 $('configurationNotice')?.remove();if(!productState)return;
 const errors=configurationErrors();if(!errors.length)return;
 const notice=document.createElement('div');notice.id='configurationNotice';notice.className='banner warn';notice.setAttribute('role','status');
 const label=document.createElement('strong');label.textContent=`配置草稿有 ${errors.length} 项错误；工作台继续使用有效版本 ${productState.workspace.validRevision}。`;notice.append(label);
 const details=document.createElement('p');details.textContent=errors.map(error=>error.message).join('；');notice.append(details);
 if(screen!=='config'){const button=document.createElement('button');button.className='textbtn';button.textContent='返回配置草稿修正';button.onclick=()=>go('config');notice.append(button);}
 ($('screen').querySelector('.workscroll,.config-page,.fullpage')||$('screen')).prepend(notice);
}
function renderConfigUI() {
  $('screen').innerHTML=`<div class="config-page"><div class="editor-tabs"><div class="config-view-tabs" role="tablist" aria-label="配置视图"><button id="configTabVisual" role="tab" aria-selected="${editor.tab==='visual'}" tabindex="${editor.tab==='visual'?0:-1}" aria-controls="editorbody" class="navtab ${editor.tab==='visual'?'active':''}" data-edtab="visual" aria-current="${editor.tab==='visual'?'page':'false'}">可视化</button><button id="configTabRaw" role="tab" aria-selected="${editor.tab==='raw'}" tabindex="${editor.tab==='raw'?0:-1}" aria-controls="editorbody" class="navtab ${editor.tab==='raw'?'active':''}" data-edtab="raw" aria-current="${editor.tab==='raw'?'page':'false'}">配置 TOML</button><button id="configTabDiff" role="tab" aria-selected="${editor.tab==='diff'}" tabindex="${editor.tab==='diff'?0:-1}" aria-controls="editorbody" class="navtab ${editor.tab==='diff'?'active':''}" data-edtab="diff" aria-current="${editor.tab==='diff'?'page':'false'}">修改差异</button></div><span id="configDraftBadge" class="tag dirty" hidden></span><span class="config-file-label">受管配置</span></div><div id="editorbody" class="editor-body" role="tabpanel" aria-labelledby="configTab${{visual:'Visual',raw:'Raw',diff:'Diff'}[editor.tab]}"></div><div class="edfooter"><span id="configStatus" role="status"></span><button class="btn" id="cancelConfig" title="移除本轮新增的应用、方案和变量，并还原已有配置属性；运行草稿和环境快照保留">还原配置修改</button><button class="btn primary" id="downloadToml">导出 TOML 文件 ↓</button></div></div>`;
  document.querySelectorAll('[data-edtab]').forEach(b=>b.onclick=()=>{
    if(editor.tab==='visual'&&b.dataset.edtab!=='visual')ConfigEditor.suspendDraft(editor);
    editor.tab=b.dataset.edtab;renderConfig();document.querySelector(`[data-edtab="${editor.tab}"]`).focus();
  });
  document.querySelector('.editor-tabs').onkeydown=e=>{
    if(e.altKey||e.ctrlKey||e.metaKey||e.shiftKey||!e.target.matches('[data-edtab]'))return;
    if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;
    e.preventDefault();const tabs=[...document.querySelectorAll('[data-edtab]')],index=tabs.indexOf(e.target);
    tabs[e.key==='Home'?0:e.key==='End'?tabs.length-1:(index+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length].click();
  };
  $('cancelConfig').onclick=restoreConfig;$('downloadToml').onclick=downloadToml;
  renderEditorBody();updateConfigStatus();
}
function renderEditorBody(focusId) {
  const root = $('editorbody');
  if (editor.tab !== 'visual') {
    const raw = editor.tab === 'raw';
    root.innerHTML = `<div class="fullpage" style="width:100%"><div class="pageh"><div><h2>${raw ? '配置 TOML' : '当前修改差异'}</h2><p>${raw ? '按 V2 配置格式生成；此处预览当前草稿，导出前会检查字段与候选值规则。' : '比较本页配置修改的属性修改；不涉及系统环境写入。'}</p></div><button class="btn" id="copyText">复制内容</button></div><pre class="codetext" id="sourceText">${esc(raw ? generateToml(true) : generateDiff())}</pre></div>`;
    $('copyText').onclick = async event => {
      const button=event.currentTarget,text=$('sourceText').textContent;
      try {
        if (!navigator.clipboard) throw new Error('Clipboard unavailable');
        await navigator.clipboard.writeText(text);
        if(!button.isConnected)return;
        button.textContent='已复制';button.dataset.copyState='copied';button.setAttribute('aria-label','已复制内容');setTimeout(()=>{if(button.isConnected){button.textContent='复制内容';button.removeAttribute('data-copy-state');button.removeAttribute('aria-label')}},1800);notify('已复制');
      } catch { if(button.isConnected)notify('浏览器不允许直接复制，请手动选中复制'); }
    };
    return;
  }
  ConfigEditor.render({root,apps,editor,modeLabels:MODE,choices:ConfigFormat.choices,changes:configChanges,change:setConfigProperty,create:createConfigEntry,rerender:renderEditorBody,status:updateConfigStatus,preview:(raw,whole)=>rawStreamMarkup(raw,{whole,comparison:true,label:'配置默认值元素'}),errors:v=>ConfigFormat.errors(apps).filter(item=>item.variable===v),focusId});
}
function setConfigProperty(v,field,next,meta={}){
 const selectedChoice=v.source.startsWith('候选值 ')?+v.source.slice(4)-1:null;
 if(field==='defaultValue'&&v.source==='默认值')beginSelectedEdit(v,next);
 if(field==='choices'&&selectedChoice!==null){
  if(meta.action==='edit'&&meta.field==='value'&&meta.index===selectedChoice)beginSelectedEdit(v,next[selectedChoice].value);
  if(meta.action==='move'){
   if(selectedChoice===meta.from)v.source='候选值 '+(meta.to+1);
   else if(selectedChoice===meta.to)v.source='候选值 '+(meta.from+1);
  }else if(meta.action==='remove'){
   if(selectedChoice===meta.index){beginSelectedEdit(v,v.defaultValue);v.source='默认值';}
   else if(selectedChoice>meta.index)v.source='候选值 '+selectedChoice;
  }
 }
 v[field]=next;
 if(['scope','mode','special'].includes(field)){v.matchBaseline=null;v.removalState=null;}
 if(field==='special'&&next)v.mode='replace';
 if(field==='editable'&&!next&&v.source==='自定义值'){v.source='默认值';v.matchBaseline=null;v.removalState=null;}
 v.dirty=hasPendingValue(v);scheduleDraft();
}
function generateToml(preview=false){return ConfigFormat.serialize(apps,{validate:!preview}).replace(/^# V2 格式的演示配置；不包含浏览器运行草稿或模拟 Windows 值。/,'# V3 配置；兼容 V2 TOML 格式。')}
function generateDiff() {
  const display=(field,val)=>['special','editable'].includes(field)?(val?'是':'否'):['choices','addition'].includes(field)?JSON.stringify(val,null,2):String(val);
  return configChanges().map(c=>`${[c.app,c.profile,c.name].filter(Boolean).join(" / ")} · ${c.label}\n  原始：${c.field==="addition"?"（不存在）":display(c.field,c.before)}\n  ${c.field==="addition"?"新增":"修改"}：${display(c.field,c.after)}`).join('\n\n')||'目前没有配置属性修改。';
}
document.addEventListener('keydown', e => {
  if (e.key !== 'Tab' || !$('modal').classList.contains('open')) return;
  const controls = [...$('modal').querySelectorAll('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex="0"]')].filter(el=>!el.closest('[hidden]'));
  const first = controls[0], last = controls[controls.length - 1];
  if (!first) return;
  if (!$('modal').contains(document.activeElement)) { e.preventDefault();first.focus();return; }
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
});
function refreshVisibleState(){
 if(screen==='workbench'&&$('varRows')){
  if(renderedWorkingRevision!==productState.workspace.validRevision){renderWorkbench();refreshPreview();return;}
  renderInspector();renderAppList();renderOverview();renderFooter();
  // Keep each textarea node, selection and IME composition intact.
  $('varRows').querySelectorAll('[data-row]').forEach(row=>{const v=curProfile().vars[+row.dataset.row],result=row.querySelector('.result');if(!v||!result)return;result.innerHTML=glyphed(detectionText(v));result.classList.toggle('warn',!itemMatched(v));const unsupported=unsupportedTarget(v);row.querySelector('.vinput').readOnly=unsupported||v.editable===false;row.querySelector('.source').disabled=unsupported;row.querySelector('[data-undo]').disabled=unsupported;});
 }else if(screen==='config'&&$('editorbody'))updateConfigStatus();
 else if(!$('modal').classList.contains('open'))render();
 refreshPreview();
}

// Production state: facts arrive from the service; local edits remain intact
// until the corresponding queued mutation has been acknowledged.
const API=window.ProductRuntime;
let productState=null,configVersion=0,configAckVersion=0,draftTimer=null,draftNeeded=false,draftPromise=null;
let selectionSequence=0,projectionEpoch=0,projectionTimer=null,refreshPromise=null,refreshTimer=null;
let previewSequence=0,manualReview=null,booted=false;
const pendingSelections=new Map(),projections=new Map();
function report(error){notify(error?.message||String(error));console.error(error);}
function safely(promise){Promise.resolve(promise).catch(report);}
function beginSelectedEdit(){/* Baselines are captured exclusively by Workspace.select. */}
function mkVar(v,i){return {id:i,name:v[0],scope:v[1],mode:v[2],defaultValue:v[3],savedValue:null,source:'默认值',custom:null,choices:[],editable:true,special:!!v[4]?.special,baselineValue:v[3],matchBaseline:null,removalState:null,dirty:false};}
function eachVariable(fn){for(const a of workingApps)for(const p of a.profiles)for(const v of p.vars)fn(v,a,p);}
function choicesDTO(){
 // Existing prepared choices are mutated separately, never replayed from a
 // configuration draft. Only draft-only parent/profile choices belong here.
 return Object.fromEntries(apps.map((a,i)=>[a,(a.profiles.find(p=>p.name===draftProfileChoice[i])||a.profiles[0])]).filter(([a,p])=>a.recordId&&p?.recordId&&!workingApps.some(w=>w.recordId===a.recordId&&w.profiles.some(q=>q.recordId===p.recordId))).map(([a,p])=>[a.recordId,p.recordId]));
}
let preparedSequence=0;
const pendingPreparedChoices=new Map();
function queuePreparedChoice(index){
 const app=workingApps[index],profile=scheme(index);if(!app?.recordId||!profile?.recordId)return;
 const seq=++preparedSequence,appId=app.recordId,profileId=profile.recordId;
 pendingPreparedChoices.set(appId,{seq,profileId});
 if(modalAction){modalAction.reviewToken=null;modalAction.invalidated=true;}
 safely(API.enqueue(async()=>{
  const data=await API.request('/workspaces/'+productState.workspace.id+'/draft',{method:'PATCH',body:{profileChoices:{[appId]:profileId},expectedRevision:productState.workspace.revision}});
  if(pendingPreparedChoices.get(appId)?.seq===seq)pendingPreparedChoices.delete(appId);
  acceptBootstrap(data);refreshVisibleState();
 }));
}
function configSession(){return {configCreation:ConfigEditor.exportSession?.(editor,apps)||{draft:null,parked:[]}};}
function mergeApps(localApps,incoming,{preserveConfig=false}={}){
  function mergeNodes(local,remote,child){
    const result=remote.map((next,index)=>{
      const prior=local.find(n=>n.recordId&&n.recordId===next.recordId)||(!local[index]?.recordId?local[index]:null);
      if(!prior)return clone(next);
      const children=child&&next[child],oldChildren=child&&prior[child];
      const pending=pendingSelections.get(next.recordId);
      const runtimeFields=['source','custom','selectedRaw','savedValue','baselineValue','matchBaseline','removalState','dirty'];
      const keep={};
      if(preserveConfig)for(const field of Object.keys(prior))if(!runtimeFields.includes(field)&&field!==child&&field!=='recordId')keep[field]=prior[field];
      if(pending)for(const field of ['source','custom','selectedRaw','dirty'])keep[field]=prior[field];
      Object.assign(prior,next,keep);
      if(child)prior[child]=mergeNodes(oldChildren,children,child==='profiles'?'vars':null);
      return prior;
    });
    if(preserveConfig)for(const node of local)if(!node.recordId&&!result.includes(node))result.push(node);
    return result;
  }
  return mergeNodes(localApps,incoming,'profiles');
}
function acceptBootstrap(data,{replace=false,preserveConfig=configVersion>configAckVersion,importDraft=false}={}){
  if(!data?.workspace||!Array.isArray(data.apps)||!Array.isArray(data.workingApps)||!Array.isArray(data.configurationBaseline)||!Array.isArray(data.snapshots))throw new Error('服务端 bootstrap 数据不完整。');
  if(productState&&data.workspace.id===productState.workspace.id&&data.workspace.revision<productState.workspace.revision)return;
  const selectedId=workingApps[selected]?.recordId,editorId=apps[editor.app]?.recordId;
  const checkedIds=new Set([...checked].map(i=>workingApps[i]?.recordId));
  const editorProfileId=apps[editor.app]?.profiles.find(p=>p.name===editor.profile)?.recordId;
  const focusedRecordId=scheme()?.vars[focusedVariable]?.recordId;
  productState=data;
  if(replace){apps=clone(data.apps);workingApps=clone(data.workingApps);pendingSelections.clear();pendingPreparedChoices.clear();configAckVersion=configVersion;}
  else{apps=mergeApps(apps,data.apps,{preserveConfig});workingApps=mergeApps(workingApps,data.workingApps);}
  for(const tree of [apps,workingApps])tree.forEach(a=>{a.icon??=Array.from(a.name)[0]||'A';a.iconBg??='#edf3ef';a.iconFg??='#286c51';a.profiles.forEach(p=>p.vars.forEach((v,i)=>{v.id=i;v.choices=ConfigFormat.choices(v);}));});
  if(!preserveConfig||replace){
    draftProfileChoice={};apps.forEach((a,i)=>{draftProfileChoice[i]=a.profiles.find(p=>p.recordId===data.draftProfileChoices?.[a.recordId])?.name||a.default||a.profiles[0]?.name;});
  }
  profileChoice={};workingApps.forEach((a,i)=>{profileChoice[i]=a.profiles.find(p=>p.recordId===(pendingPreparedChoices.get(a.recordId)?.profileId||data.profileChoices?.[a.recordId]))?.name||a.default||a.profiles[0]?.name;});
  selected=Math.max(0,workingApps.findIndex(a=>a.recordId===selectedId));
  editor.app=Math.max(0,apps.findIndex(a=>a.recordId===editorId));
  const editorProfile=apps[editor.app]?.profiles.find(p=>p.recordId===editorProfileId);if(editorProfile)editor.profile=editorProfile.name;
  if(apps[editor.app]&&!apps[editor.app].profiles.some(p=>p.name===editor.profile))editor.profile=apps[editor.app].profiles[0]?.name||'';
  const focusedIndex=scheme()?.vars.findIndex(v=>v.recordId===focusedRecordId);if(focusedIndex>=0)focusedVariable=focusedIndex;
  focusedVariable=Math.max(0,Math.min(focusedVariable,(scheme()?.vars.length||1)-1));
  checked=new Set(workingApps.map((a,i)=>checkedIds.has(a.recordId)?i:-1).filter(i=>i>=0));
  windows={};targetTypes={};targetRevisions={};
  for(const snapshot of data.snapshots){const k=key(snapshot);if(snapshot.exists&&snapshot.supported!==false)windows[k]=snapshot.raw;targetTypes[k]=snapshot.type;targetRevisions[k]=snapshot.revision;}
  revision=data.workspace.revision;history=clone(data.history||[]);planCache=new WeakMap();projectionEpoch++;projections.clear();
  if(replace||!booted)captureConfigBaselines();
  else bindConfigBaselines();
  if(importDraft)ConfigEditor.importSession?.(editor,apps,data.uiDrafts?.configCreation);
  const mark=document.querySelector('.prototype-mark');mark.textContent=data.mode==='isolated'?'隔离环境':'Windows 环境';
  document.querySelector('.chrome .sub').textContent=data.workspace.fileConflict?'配置文件冲突':'V3';
  scheduleProjection();
  renderConfigurationNotice();
}
function scheduleDraft(){
  if(!productState)return;
  draftNeeded=true;configVersion++;projections.clear();projectionEpoch++;
  if(modalAction){modalAction.reviewToken=null;modalAction.invalidated=true;}
  clearTimeout(draftTimer);draftTimer=setTimeout(()=>safely(persistDraft()),180);
}
async function persistDraft(){
  clearTimeout(draftTimer);
  if(!draftNeeded)return draftPromise;
  draftNeeded=false;
  const version=configVersion,tree=clone(apps),uiDrafts=configSession(),choices=choicesDTO();
  draftPromise=API.enqueue(async()=>{
    const response=await API.request('/workspaces/'+productState.workspace.id+'/draft',{method:'PATCH',body:{apps:tree,profileChoices:choices,uiDrafts,expectedRevision:productState.workspace.revision}});
    configAckVersion=Math.max(configAckVersion,version);
    acceptBootstrap(response,{preserveConfig:version!==configVersion});
    if(screen==='config'&&$('configStatus'))updateConfigStatus();
    else if(screen==='workbench')refreshVisibleState();
  });
  try{await draftPromise;}catch(error){draftNeeded=true;throw error;}
}
async function flushInputs(){
  await persistDraft();await API.flush();
  if(draftNeeded)await flushInputs();
  if(pendingSelections.size)throw new Error('部分输入尚未保存，请重新编辑后再预览或保存。');
}
function queueSelection(v){
  if(unsupportedTarget(v)){notify('不支持的类型，只读；不能修改或应用。');return;}
  if(!v.recordId){scheduleDraft();return;}
  const id=v.recordId,seq=++selectionSequence,source=v.source,raw=value(v);
  pendingSelections.set(id,{seq,source,raw});projectionEpoch++;projections.delete(id);
  if(modalAction){modalAction.reviewToken=null;modalAction.invalidated=true;}
  // Persist a pending config mutation first, then serialize every input event.
  if(draftNeeded)safely(persistDraft());
  safely(API.enqueue(async()=>{
    const response=await API.request('/selections/'+id,{method:'PATCH',body:{source,raw,expectedRevision:productState.workspace.revision}});
    if(pendingSelections.get(id)?.seq===seq)pendingSelections.delete(id);
    acceptBootstrap(response);refreshVisibleState();
  }));
}
async function restoreSelectedValue(v){try{
  await flushInputs();const data=await API.enqueue(()=>API.request('/selections/'+v.recordId+'/restore',{method:'POST',body:{expectedRevision:productState.workspace.revision}}));
  acceptBootstrap(data);render();
}catch(error){report(error);}}
function unsupportedTarget(v){return productState?.snapshots.some(s=>key(s)===key(v)&&s.supported===false)||false;}
function readonlySnapshotMarkup(snapshot){
 return `<div class="codevalue"><span class="tag neutral">${esc(snapshot.type)} · 只读</span><pre class="codevalue" data-native-display>${esc(snapshot.display??'（无可用原生值显示）')}</pre><small class="sub">不支持的类型，不能按环境字符串编辑或应用。</small></div>`;
}
function renderUnsupportedInspector(v){
 const snapshot=demoSnapshot(v);
 $('inspectTitle').textContent='Windows 值对照 · '+v.name;$('inspect').querySelector('.inspecthead > .mini').textContent='不支持的类型 · 只读';
 $('compareDesired').innerHTML=WindowsElements.staticMarkup(value(v),{whole:!!v.special,comparison:true});
 $('compareActual').innerHTML=readonlySnapshotMarkup(snapshot);
 $('compareActual').classList.remove('inspect-editable');$('compareActual').ondblclick=null;$('compareActual').onkeydown=null;
 for(const attribute of ['role','tabindex','aria-label','title'])$('compareActual').removeAttribute(attribute);
 $('compareAfter').innerHTML='<div class="invalid-result">不支持的类型，只读；不能应用。</div>';
 $('compareAfter').closest('.comparison-pair').classList.add('is-plan-stale');
 $('compareAfter').closest('.result-pane').querySelector('.mini').textContent='只读 · 不能应用';
 $('compareTally').textContent='';$('compareSerialized').textContent='';$('compareSerialized').closest('.comparison-serialized').hidden=true;
 $('compareLabel').textContent=(v.scope==='User'?'用户':'系统')+' · '+v.source;
 $('compareResult').textContent='不支持的类型 · '+snapshot.type;$('compareSummary').textContent='原生值仅供查看，不作为环境字符串参与规则计算。';
 $('compareActions').replaceChildren();$('compareNotice').hidden=true;$('compareNotice').textContent='';
 $('editWindowsElements').hidden=true;$('editWindowsElements').onclick=null;
 $('inspectReview').innerHTML='<span class="sub">不支持的类型 · 只读</span><button class="textbtn" id="inspectPreview" disabled>不能应用</button>';
}
function renderFooter(){
 renderFooterUI();if(screen!=='workbench'||!$('previewButton'))return;
 const indices=checked.size?[...checked]:[selected];
 const unsupported=indices.some(i=>scheme(i)?.vars.some(unsupportedTarget));
 $('previewButton').disabled=unsupported;$('previewButton').title=unsupported?'包含不支持的原生类型，只读，不能应用。':'';
}
function currentRemovalState(v){return v.removalState;}
function demoSnapshot(v){
  const snapshot=productState?.snapshots.find(s=>key(s)===key(v));
  return snapshot?{...snapshot}:{exists:false,raw:null,type:null,revision:null,supported:false};
}
function demoPlan(v){
  if(unsupportedTarget(v))throw new Error('不支持的类型，只读；不能生成字符串计划。');
  const cached=projections.get(v.recordId);if(cached)return cached;
  // Immediate feedback only. Frozen previews and every execution always use POST /plans.
  return DemoPlan.build({target:{scope:v.scope,name:v.name},mode:v.mode,whole:v.special,snapshot:demoSnapshot(v),selectedRaw:value(v),baseline:v.matchBaseline,removalState:v.removalState,protectedValues:protectedSelections([v])});
}
function scheduleProjection(){
  clearTimeout(projectionTimer);projectionTimer=setTimeout(async()=>{
    if(!booted||draftNeeded||pendingSelections.size||modalAction)return;
    const epoch=projectionEpoch;
    try{
      await API.flush();
      for(const a of workingApps)for(const p of a.profiles)for(const v of p.vars){
        if(epoch!==projectionEpoch||modalAction)return;
        if(unsupportedTarget(v))continue;
        const receipt=await API.request('/plans',{method:'POST',body:{kind:'apply',recordIds:[v.recordId]}});
        if(epoch!==projectionEpoch)return;
        const target=receipt.targets.find(t=>t.members.includes(v.recordId));if(target)projections.set(v.recordId,target.plan);
      }
      if(screen==='workbench'&&$('varRows'))refreshVisibleState();
    }catch(error){if(epoch===projectionEpoch&&screen==='workbench'&&$('compareNotice')){$('compareNotice').hidden=false;$('compareNotice').textContent='服务端计划未就绪：'+error.message;}}
  },250);
}
function renderInspector(){
  renderInspectorUI();const v=scheme()?.vars[focusedVariable]||scheme()?.vars[0];
  // Number badges come from the existing CSS counter, not heading text.
  for(const [selector,label]of [['.selection-pane .inspect-value-heading .k','当前选定值'],['.comparison-pair > section:first-child .inspect-value-heading .k','Windows 当前值'],['.result-pane .inspect-value-heading .k','应用后的值']]){
    const heading=$('inspect')?.querySelector(selector);if(heading)heading.textContent=label;
  }
  if(v&&$('compareAfter')&&!unsupportedTarget(v)){
    const plan=demoPlan(v),current=demoSnapshot(v);
    const unchanged=plan.valid!==false&&!plan.operations.length&&['exists','raw','type'].every(field=>plan.after[field]===current[field]);
    $('compareAfter').closest('.result-pane').querySelector('.mini').textContent=plan.valid===false?'等待重新预览':unchanged?'与当前值一致':projections.has(v.recordId)?'服务端预览 · 尚未应用':'输入提示 · 等待服务端计划';
  }
}
function render(){
  if(!productState)return;
  if(!(screen==='config'?apps:workingApps).length&&(screen==='workbench'||screen==='config')){renderEmpty();return;}
  renderAcceptedUI();renderRecoveryNotice();renderConfigurationNotice();
}
let renderedWorkingRevision=null;
function renderWorkbench(){
  const active=document.activeElement,row=active?.closest('#varRows [data-record-id]');
  const focus=row?{id:row.dataset.recordId,selector:active.matches('.vinput')?'.vinput':active.matches('.source')?'.source':null,start:active.selectionStart,end:active.selectionEnd,direction:active.selectionDirection}:null;
  renderWorkbenchUI();renderedWorkingRevision=productState.workspace.validRevision;renderConfigurationNotice();
  if(focus?.selector){const next=[...document.querySelectorAll('#varRows [data-record-id]')].find(el=>el.dataset.recordId===focus.id)?.querySelector(focus.selector);next?.focus({preventScroll:true});if(next&&focus.start!=null)next.setSelectionRange(focus.start,focus.end,focus.direction);}
}
function renderConfig(){
  if(!apps.length){renderEmpty();return;}
  renderConfigUI();
  const file=document.querySelector('.config-file-label');if(file){file.textContent=productState.workspace.configPath;file.title=file.textContent;}
  const save=document.createElement('button');save.className='btn primary';save.id='saveConfig';save.textContent='保存配置';save.onclick=()=>safely(saveConfiguration());$('downloadToml').before(save);
  $('downloadToml').classList.remove('primary');
  updateConfigStatus();
}
let emptyCreation=null,emptyTimer=null;
function persistEmptyCreation(){
  clearTimeout(emptyTimer);const saved=clone(emptyCreation);
  return API.enqueue(async()=>{const data=await API.request('/workspaces/'+productState.workspace.id+'/draft',{method:'PATCH',body:{uiDrafts:{...productState.uiDrafts,emptyCreation:saved},expectedRevision:productState.workspace.revision}});acceptBootstrap(data);});
}
function renderEmpty(){
  document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===screen));
  $('configBtn').classList.toggle('active',screen==='config');$('footer').style.display='';
  emptyCreation??=clone(productState.uiDrafts?.emptyCreation||{stage:0,appName:'',profileName:'',name:'',scope:'User',mode:'replace'});
  const d=emptyCreation,stage=Math.max(0,Math.min(2,d.stage||0)),field=['appName','profileName','name'][stage],label=['应用名称','方案名称','首个变量名称'][stage];
  $('screen').innerHTML=`<section class="fullpage config-page"><div class="pageh"><div><h2>${stage===0?'尚无应用配置':esc(d.appName)+(stage===2?' / '+esc(d.profileName):'')}</h2><p>${stage===0?'导入 TOML，或先为应用起名。':'名称草稿已保留，首个变量确认后创建有效配置。'}</p></div><button class="btn" id="emptyImport">导入 TOML</button></div><form id="emptyCreate" class="field"><label>${label}<input name="${field}" required></label>${stage===2?'<details><summary>可选设置</summary><label>作用域<select name="scope"><option>User</option><option>System</option></select></label><label>模式<select name="mode"><option value="replace">替换</option><option value="+append">前置</option><option value="-append">后置</option><option value="append">自定义排序</option></select></label></details>':''}<p id="emptyError" role="status"></p>${stage?'<button type="button" class="btn" id="emptyBack">上一步</button>':''}<button class="btn primary">${stage===2?'创建首个变量':'继续'}</button><button type="button" class="textbtn" id="emptyCancel">取消名称草稿</button></form></section>`;
  const form=$('emptyCreate');form.elements[field].value=d[field]||'';if(stage===2){form.elements.scope.value=d.scope||'User';form.elements.mode.value=d.mode||'replace';}
  const capture=()=>Object.assign(d,Object.fromEntries(new FormData(form)));
  $('emptyImport').onclick=importFile;
  form.oninput=()=>{capture();clearTimeout(emptyTimer);emptyTimer=setTimeout(()=>safely(persistEmptyCreation()),180);};
  form.onchange=form.oninput;
  if($('emptyBack'))$('emptyBack').onclick=()=>{capture();d.stage=stage-1;safely(persistEmptyCreation());renderEmpty();};
  $('emptyCancel').onclick=()=>{emptyCreation={stage:0,appName:'',profileName:'',name:'',scope:'User',mode:'replace'};safely(persistEmptyCreation());renderEmpty();};
  form.onsubmit=async e=>{e.preventDefault();capture();if(!d[field]?.trim())return;
    if(stage<2){d.stage=stage+1;await persistEmptyCreation();renderEmpty();$('emptyCreate').querySelector('input').focus();return;}
    const result=createConfigEntry('app',d);if(!result.ok){$('emptyError').textContent=result.error;return;}
    try{emptyCreation=null;clearTimeout(emptyTimer);await flushInputs();screen='config';render();}catch(error){report(error);}
  };
  $('footer').innerHTML='<div class="footer-summary"><strong>配置与 Windows 环境独立</strong><small>创建配置后仍需审阅环境计划。</small></div>';renderRecoveryNotice();renderConfigurationNotice();
}
async function saveConfiguration(){await flushInputs();requireValidConfiguration();const result=await API.enqueue(()=>API.request('/workspaces/'+productState.workspace.id+'/save',{method:'POST',body:{expectedDiskHash:productState.workspace.fileRevision,expectedRevision:productState.workspace.revision}}));acceptBootstrap(result);captureConfigBaselines();if(screen==='config')updateConfigStatus();notify('配置已保存到 '+result.workspace.configPath);}
async function downloadToml(){try{await flushInputs();requireValidConfiguration();const text=await API.request('/workspaces/'+productState.workspace.id+'/export',{text:true});const blob=new Blob([text],{type:'text/plain;charset=utf-8'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='env.toml';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1500);notify('已导出受管配置');}catch(error){report(error);}}
async function restoreConfig(){try{await flushInputs();const result=await API.enqueue(()=>API.request('/workspaces/'+productState.workspace.id+'/restore',{method:'POST',body:{expectedRevision:productState.workspace.revision}}));ConfigEditor.clearDraft(editor);draftNeeded=false;acceptBootstrap(result,{replace:true});render();notify('已还原已保存配置，环境未改变');}catch(error){report(error);}}
function importFile(){
  const input=document.createElement('input');input.type='file';input.accept='.toml,text/plain';input.hidden=true;document.body.append(input);
  input.onchange=async()=>{try{const file=input.files[0];if(!file)return;await flushInputs();const result=await API.enqueue(async()=>API.request('/workspaces/import',{method:'POST',body:{text:await file.text(),filename:file.name}}));ConfigEditor.clearDraft(editor);draftNeeded=false;acceptBootstrap(result,{replace:true});render();notify('已导入受管配置');}catch(error){report(error);}finally{input.remove();}};
  input.addEventListener('cancel',()=>input.remove(),{once:true});input.click();
}
function targetRecordIds(targets){return [...new Set(targets.map(t=>workingApps[t.app]?.profiles.find(p=>p.name===t.profile)?.vars[t.varIdx]?.recordId).filter(Boolean))];}
function rowsFromReceipt(receipt){
  return receipt.targets.map(target=>{
    const members=target.members.map(id=>{let result;eachVariable((v,a,p)=>{if(v.recordId===id)result={v,app:workingApps.indexOf(a),profile:p.name,varIdx:p.vars.indexOf(v),selectedRaw:value(v)};});return result;}).filter(Boolean);
    const v=members[0]?.v||{scope:target.scope,name:target.name,mode:target.plan.mode,special:target.plan.special};
    return {v,members,scope:target.scope,name:target.name,mode:v.mode,whole:target.plan.special,plan:target.plan,needsOrdering:!!target.plan.needsOrdering,before:target.beforeSnapshot.exists?target.beforeSnapshot.raw:undefined,after:target.plan.after.exists?target.plan.after.raw:undefined,baseSnapshot:target.beforeSnapshot};
  });
}
async function showPreview(kind,title,targets){return openPlanPreview({kind:kind==='撤销'?'undo':'apply',recordIds:targetRecordIds(targets)},title,targets);}
async function openPlanPreview(intent,title,targets=[]){
  const seq=++previewSequence;
  try{await flushInputs();if(intent.kind==='apply'&&workingApps.some(a=>a.profiles.some(p=>p.vars.some(v=>intent.recordIds?.includes(v.recordId)&&unsupportedTarget(v)))))throw new Error('计划包含不支持的原生类型，只读，不能应用。');const receipt=await API.request('/plans',{method:'POST',body:intent});if(seq!==previewSequence)return;
    lastDialogFocus=document.activeElement;closeDropdowns();modalAction={intent,title,kind:intent.kind==='undo'?'撤销':'应用',targets,receipt,rows:rowsFromReceipt(receipt),version:receipt.version,reviewToken:null,renderedRevision:productState.workspace.revision};
    mountPreview();
  }catch(error){if(manualReview)restoreManual(false);report(error);}
}
function mountPreview(){
  $('modal').innerHTML='<div class="dialog preview-dialog" role="dialog" aria-modal="true" aria-labelledby="dialogTitle" aria-describedby="previewMeta previewDecisionHint"><div class="dialoghead"><div><h3 id="dialogTitle" tabindex="-1"></h3><p id="previewMeta"></p><span id="previewVersionStatus" role="status" aria-live="polite"></span></div><button class="close" id="closeDialog" aria-label="关闭">×</button></div><div class="dialogbody"></div><div class="dialogfoot"><p id="previewDecisionHint"></p><button class="btn" id="cancelDialog">取消</button><button class="btn primary" id="confirmDialog">审阅此计划</button></div></div>';
  $('modal').classList.add('open');$('closeDialog').onclick=closeModal;$('cancelDialog').onclick=closeModal;$('confirmDialog').onclick=()=>safely(confirmPreview());renderPreview();$('dialogTitle').focus();
}
function renderPreview(){
  const action=modalAction;if(!action)return;
  const {receipt,rows}=action,body=$('modal').querySelector('.dialogbody');if(!body)return;
  const scroll=body.scrollTop,expanded=[...body.querySelectorAll('[data-review-key] .raw-disclosure[open]')].map(e=>e.closest('[data-review-key]').dataset.reviewKey);
  $('dialogTitle').textContent=action.title;$('previewMeta').textContent=`${rows.length} 个环境目标 · 预览版本 ${receipt.version}`;
  body.innerHTML=reviewSummaryMarkup(rows,'应用',!!action.updated,receipt.version)+reviewTargetNav(rows,'应用')+rows.map((r,i)=>previewRowMarkup(r,'应用',i)).join('')+(receipt.issues?.length?reviewIssueMarkup({issues:receipt.issues}):'');
  body.querySelectorAll('[data-preview-index]').forEach(el=>{const r=rows[+el.dataset.previewIndex];el.querySelector('[data-preview-before]').textContent=r.before===undefined?'（不存在）':r.before;el.querySelector('[data-preview-after]').textContent=r.after===undefined?'（不存在）':r.after===''?'（空字符串）':r.after;if(expanded.includes(el.dataset.reviewKey))el.querySelector('.raw-disclosure').open=true;
    const beforeType=r.baseSnapshot.type,afterType=r.plan.after.type,heading=el.querySelector('.review-row-heading');
    heading.title='注册表类型：'+(afterType||beforeType||'不存在');
    if(beforeType!==afterType||!r.baseSnapshot.exists){const type=document.createElement('span');type.className='sub mini';type.textContent='类型：'+(beforeType||'不存在')+' → '+(afterType||'删除变量');heading.append(type);}
    const back=el.querySelector('.review-return');if(back)back.onclick=()=>{if(r.members.length)returnToReviewTarget(r);else closeModal();};
    if(r.needsOrdering){const button=document.createElement('button');button.className='btn';button.textContent='调整并确认排序';button.onclick=()=>safely(openOrdering(action,r));el.append(button);}
  });
  body.querySelectorAll('[data-review-jump]').forEach(button=>button.onclick=()=>{const row=[...body.querySelectorAll('[data-review-key]')].find(el=>el.dataset.reviewKey===button.dataset.reviewJump);row?.querySelector('.review-row-heading')?.focus();row?.scrollIntoView?.({block:'start'});});
  decorateValueStreams(body);body.scrollTop=scroll;
  const blocked=receipt.status==='blocked'||rows.some(r=>r.plan.valid===false||r.needsOrdering);
  $('confirmDialog').disabled=blocked||action.busy||!!API.pendingKey()||productState.capabilities.execute===false;
  $('confirmDialog').textContent=action.busy?'正在处理…':action.reviewToken?'执行已审阅计划':'审阅此计划';
  $('previewDecisionHint').textContent=action.error|| (API.pendingKey()?'执行结果待对账，请先查询原操作。':blocked?'请处理目标冲突或完成自定义排序。':action.reviewToken?'审阅凭据已就绪。再次点击执行此版本的计划。':'请核对全部目标，再点击审阅；审阅不会写入环境。');
  $('previewVersionStatus').textContent=action.updated?'预览已更新，请重新审阅。':'';
}
function refreshPreview(){if(modalAction?.invalidated&&!modalAction.busy){modalAction.reviewToken=null;if($('confirmDialog'))$('confirmDialog').textContent='更新并审阅计划';}return false;}
function replacePreview(action,receipt){action.receipt=receipt;action.rows=rowsFromReceipt(receipt);action.version=receipt.version;action.reviewToken=null;action.updated=true;action.invalidated=false;action.renderedRevision=productState.workspace.revision;action.error=null;renderPreview();}
async function confirmPreview(){
  const action=modalAction;if(!action||action.busy||$('confirmDialog').disabled)return;
  action.busy=true;renderPreview();
  try{
    await flushInputs();if(modalAction!==action)return;
    if(action.invalidated){const receipt=await API.request('/plans',{method:'POST',body:action.intent});replacePreview(action,receipt);return;}
    const receipt=action.receipt;
    if(!action.reviewToken){
      const result=await API.request('/plans/'+receipt.planId+'/review',{method:'POST',body:{version:receipt.version,digest:receipt.digest,renderedRevision:action.renderedRevision}});
      if(modalAction!==action)return;
      if(result.status==='preview_updated'){replacePreview(action,result.plan||result.replacementPlan);return;}
      if(!result.reviewToken)throw new Error(result.message||result.code||'审阅被拒绝，请检查计划。');
      action.reviewToken=result.reviewToken;return;
    }
    const result=await API.execute(receipt,action.reviewToken);action.reviewToken=null;
    if(result.state)acceptBootstrap(result.state);
    if(modalAction!==action)return;
    if(result.status==='preview_updated'){replacePreview(action,result.plan||result.replacementPlan);return;}
    if(['committed','verified'].includes(result.status)){
      if(manualReview){restoreManual(true);return;}
      action.busy=false;closeModal();render();notify('计划已执行并核验');
    }else if(result.status==='applied_partial_intent'){
      if(manualReview){action.error='安全部分已执行，未完成意图保留。';return;}
      action.busy=false;closeModal();render();notify('安全部分已执行；未解决的输入与删除意图仍保留');
    }else{action.error=result.message||result.error?.message||result.code||`执行状态：${result.status}，当前输入保留。`;if(API.pendingKey())action.error+=' 请查询原操作，勿重新执行。';}
  }catch(error){action.error=error.message;if(error.operationKey)action.error+=' 执行结果未知，必须查询原操作。';}
  finally{action.busy=false;if(modalAction===action)renderPreview();renderOperationStatus();}
}
function closeModal(){
  if(modalAction?.busy)return;
  previewSequence++;
  if(manualReview){restoreManual(false);return;}
  $('modal').classList.remove('open');modalAction=null;WindowsElements.dismiss();
  (lastDialogFocus?.isConnected?lastDialogFocus:$('previewButton')||document.querySelector('[data-view="'+screen+'"]'))?.focus();lastDialogFocus=null;
}
async function openOrdering(action,row){
  if(action.busy)return;
  const session=await API.request('/order-sessions',{method:'POST',body:{planId:action.receipt.planId,targetKey:row.scope+'|'+ConfigFormat.matchName(row.name)}});
  if(modalAction!==action)return;
  let ordered=[...session.rows];
  const draw=()=>{
    $('modal').innerHTML=`<div class="dialog element-dialog" role="dialog" aria-modal="true" aria-labelledby="dialogTitle"><div class="dialoghead"><div><h3 id="dialogTitle">${esc(row.name)} · 自定义排序</h3><p>完整保留所有元素及原文；确认后重新生成服务端计划。</p></div></div><div class="dialogbody"><ol class="element-list">${ordered.map((r,i)=>`<li class="element-row" data-order-id="${esc(r.id)}"><span class="element-order">${i+1}</span><code>${r.value===''?'（空元素）':esc(r.value)}</code><button class="textbtn" data-move="${i}" data-step="-1" ${i===0?'disabled':''}>上移</button><button class="textbtn" data-move="${i}" data-step="1" ${i===ordered.length-1?'disabled':''}>下移</button></li>`).join('')}</ol><p id="orderError" role="status"></p></div><div class="dialogfoot"><button class="btn" id="cancelOrder">取消排序</button><button class="btn primary" id="saveOrder">确认排序并更新预览</button></div></div>`;
    $('cancelOrder').onclick=mountPreview;
    $('modal').querySelectorAll('[data-move]').forEach(b=>b.onclick=()=>{const i=+b.dataset.move,j=i+(+b.dataset.step);[ordered[i],ordered[j]]=[ordered[j],ordered[i]];draw();$('modal').querySelectorAll('[data-order-id]')[j].querySelector('button:not(:disabled)')?.focus();});
    $('saveOrder').onclick=async()=>{const button=$('saveOrder');button.disabled=true;try{await API.request('/order-sessions/'+session.sessionId,{method:'PATCH',body:{order:ordered.map(r=>r.id),expectedRevision:session.revision}});action.orderSessions??={};action.orderSessions[row.scope+'|'+ConfigFormat.matchName(row.name)]=session.sessionId;const {orderSessionId,...intent}=action.intent;action.intent={...intent,orderSessionIds:Object.values(action.orderSessions)};const receipt=await API.request('/plans',{method:'POST',body:action.intent});mountPreview();replacePreview(action,receipt);}catch(error){$('orderError').textContent=error.message;button.disabled=false;}};
  };draw();$('cancelOrder').focus();
}
function restoreManual(verified){
  const current=manualReview;manualReview=null;modalAction=null;previewSequence++;
  $('modal').replaceChildren(current.fragment);$('modal').classList.add('open');current.resolve(verified?{verified:true,status:'verified'}:false);
}
async function openWindowsElements(scope,name,selectedValues=[],whole=false,mode=null,appendValues=null,appendEntries=null,selectionPlan=null,create=false){
  try{
    if(unsupportedTarget({scope,name}))throw new Error('不支持的类型，只读；不能打开元素编辑器。');
    await flushInputs();const session=await API.request('/edit-sessions',{method:'POST',body:{scope,name,source:screen==='windows'?'windows':'workbench',...(create?{create:true}:{})}});
    let server=session;const ids=new Map();lastDialogFocus=document.activeElement;
    WindowsElements.open({container:$('modal'),name,scope,raw:session.snapshot.exists?session.snapshot.raw:undefined,snapshot:session.snapshot,registryType:session.type,whole:session.whole,selectedValues,selectionPlan,
      showSuggestions:!!selectionPlan,appendValues:[],close:closeModal,currentSnapshot:()=>demoSnapshot({scope,name}),
      beforeApply:async()=>true,
      onApply:async(raw,audit)=>{
        const live=audit.elements.filter(e=>e.afterIndex!==null).sort((a,b)=>a.afterIndex-b.afterIndex);
        const rows=live.map(e=>{const id=ids.get(e.elementId)||(e.beforeIndex===null?null:server.originalRows[e.beforeIndex]?.id);return {...(id?{id}:{}),value:e.after};});
        const updated=await API.request('/edit-sessions/'+server.sessionId,{method:'PATCH',body:{rows,expectedRevision:server.revision,deleteVariable:rows.length===0,type:$('manualRegistryType')?.value||server.type}});
        live.forEach((e,i)=>ids.set(e.elementId,updated.rows[i].id));server=updated;
        const receipt=await API.request('/plans',{method:'POST',body:{kind:'manual',editSessionId:server.sessionId}});
        const fragment=document.createDocumentFragment();while($('modal').firstChild)fragment.append($('modal').firstChild);
        return new Promise(resolve=>{manualReview={fragment,resolve,session:server};modalAction={intent:{kind:'manual',editSessionId:server.sessionId},title:'审阅手动编辑 · '+name,kind:'应用',targets:[],receipt,rows:rowsFromReceipt(receipt),version:receipt.version,reviewToken:null,renderedRevision:productState.workspace.revision};mountPreview();});
      },onApplied:()=>{render();notify('手动编辑已执行并核验');}
    });
    const root=$('modal');root.querySelector('.element-demo').textContent='服务端编辑会话';root.querySelector('#dialogTitle').title='注册表类型：'+server.type;
    root.querySelector('#applyElements').textContent='预览手动编辑';root.querySelector('#elementStatus').textContent='草稿保留到执行核验完成';
    root.querySelector('.dialoghead p').textContent='保留原文、重复与空元素，提交后先审阅服务端计划。';
    const label=document.createElement('label');label.className='mini';label.innerHTML='类型 <select id="manualRegistryType"><option>REG_SZ</option><option>REG_EXPAND_SZ</option></select>';root.querySelector('.elements-toolbar-actions').prepend(label);$('manualRegistryType').value=server.type;
    $('manualRegistryType').onchange=()=>{root.querySelector('#applyElements').disabled=false;};
    if(create){root.querySelector('#addElement').click();}
  }catch(error){report(error);}
}
function renderWindows(){
  renderWindowsUI();
  for(const snapshot of productState.snapshots.filter(s=>s.exists&&s.supported===false)){
    const row=document.createElement('tr');row.dataset.unsupportedTarget=key(snapshot);
    row.innerHTML=`<td><span class="scope-chip" data-scope="${snapshot.scope==='User'?'user':'system'}">${snapshot.scope==='User'?'用户':'系统'}</span></td><td><strong>${esc(snapshot.name)}</strong></td><td>${readonlySnapshotMarkup(snapshot)}</td><td class="windows-actions"><button class="textbtn" disabled>编辑</button><button class="textbtn" disabled>删除变量</button></td>`;
    $('screen').querySelector('.windows-page tbody').append(row);
  }
  const total=$('screen').querySelector('.windows-page .pageh .tag');if(total)total.textContent=productState.snapshots.filter(s=>s.exists).length+' 项变量';
  const add=document.createElement('button');add.className='btn';add.id='createWindowsVariable';add.textContent='新增变量';add.onclick=showCreateWindows;document.querySelector('.windows-page .pageh').append(add);
  $('screen').querySelectorAll('[data-windows-edit]').forEach(edit=>{
    const snapshot=productState.snapshots.find(s=>key(s)===edit.dataset.windowsEdit);if(snapshot?.supported===false){edit.disabled=true;edit.title='此类型只支持查看';return;}
    const remove=document.createElement('button');remove.className='textbtn';remove.textContent='删除变量';remove.dataset.deleteWindows=edit.dataset.windowsEdit;
    remove.onclick=()=>safely(deleteWindowsVariable(snapshot));edit.parentElement.append(remove);
  });
}
function showCreateWindows(){
  lastDialogFocus=document.activeElement;$('modal').innerHTML='<div class="dialog" role="dialog" aria-modal="true" aria-labelledby="dialogTitle"><form id="createWindowsForm"><div class="dialoghead"><h3 id="dialogTitle">新增 Windows 变量</h3></div><div class="dialogbody field"><label>作用域<select name="scope"><option>User</option><option>System</option></select></label><label>变量名<input name="name" required></label><p>值与类型在元素编辑器中设置，随后审阅执行。</p></div><div class="dialogfoot"><button type="button" id="cancelDialog" class="btn">取消</button><button class="btn primary">继续编辑</button></div></form></div>';$('modal').classList.add('open');$('cancelDialog').onclick=closeModal;
  $('createWindowsForm').onsubmit=e=>{e.preventDefault();const fields=Object.fromEntries(new FormData(e.currentTarget));safely(openWindowsElements(fields.scope,fields.name,[],false,null,null,null,null,true));};$('createWindowsForm').elements.name.focus();
}
async function deleteWindowsVariable(snapshot){
  if(snapshot.supported===false)throw new Error('不支持的类型，只读；不能删除。');
  const session=await API.request('/edit-sessions',{method:'POST',body:{scope:snapshot.scope,name:snapshot.name,source:'windows'}});
  await API.request('/edit-sessions/'+session.sessionId,{method:'PATCH',body:{deleteVariable:true,expectedRevision:session.revision}});
  await openPlanPreview({kind:'manual',editSessionId:session.sessionId},'审阅删除变量 · '+snapshot.name);
}
function renderHistory(){
  renderHistoryUI();$('screen').querySelectorAll('[data-history-index]').forEach(row=>{const h=history[+row.dataset.historyIndex],id=h.operationId||h.id;if(!id||h.status!=='committed'&&h.detail!=='committed'&&h.resultStatus!=='committed')return;const button=document.createElement('button');button.className='textbtn';button.textContent='恢复历史前值';button.onclick=()=>safely(openPlanPreview({kind:'restore_history',historyIds:[id]},'审阅历史恢复'));row.append(button);});
}
function renderRecoveryNotice(){
  $('productRecovery')?.remove();if(!productState?.recovery?.length)return;
  const box=document.createElement('div');box.id='productRecovery';box.className='banner warn';box.innerHTML='<strong>有操作需要恢复处理</strong> ';
  for(const record of productState.recovery){const details=document.createElement('details');const caption=document.createElement('summary');caption.textContent=record.operationId+' · '+record.status;details.append(caption);const pre=document.createElement('pre');pre.className='codevalue';pre.textContent=JSON.stringify(record.details,null,2);details.append(pre);
    for(const [resolution,text]of [['restore_before','恢复操作前值'],['accept_current','接受当前实际值']]){const button=document.createElement('button');button.className='btn';button.textContent=text;button.onclick=()=>safely(openPlanPreview({kind:'recovery',operationId:record.operationId,resolution},'审阅恢复处理'));details.append(button);}box.append(details);
  }
  ($('screen').querySelector('.fullpage,.workscroll,.config-page')||$('screen')).prepend(box);
}
function renderOperationStatus(){
  $('operationStatus')?.remove();if(!API.pendingKey())return;
  const button=document.createElement('button');button.id='operationStatus';button.className='tool';button.textContent='查询待确认操作';button.onclick=()=>safely(resumeOperation());document.querySelector('.toolbar').prepend(button);
}
async function resumeOperation(){
  const result=await API.resume();if(!result)return;
  if(result.state)acceptBootstrap(result.state);else acceptBootstrap(await API.request('/bootstrap'));
  if(modalAction&&result.status==='preview_updated')replacePreview(modalAction,result.plan||result.replacementPlan);
  else if(['committed','verified'].includes(result.status)){if(manualReview)restoreManual(true);else{closeModal();render();}}
  else if(modalAction){modalAction.error=result.message||`操作状态：${result.status}`;renderPreview();}
  notify('原操作状态：'+result.status);renderOperationStatus();
}
async function refreshEnvironment(){
  if(refreshPromise)return refreshPromise;
  refreshPromise=(async()=>{await flushInputs();const data=await API.enqueue(()=>API.request('/environment/refresh',{method:'POST',body:{}}));acceptBootstrap(data);refreshVisibleState();
    if(modalAction&&!modalAction.busy){modalAction.reviewToken=null;modalAction.invalidated=true;refreshPreview();}
  })();
  try{await refreshPromise;}finally{refreshPromise=null;}
}
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'){closeModal();closeDropdowns();}
  if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='s'){e.preventDefault();if(!$('modal').classList.contains('open'))safely(saveConfiguration());}
  if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='f'&&screen==='workbench'&&!$('modal').classList.contains('open')){e.preventDefault();$('appSearch')?.focus();}
});
// The editor owns unfinished naming containers. Persist its serialized session
// after its own handlers have run, without replacing textarea DOM or caret.
for(const event of ['input','change','click'])$('screen').addEventListener(event,e=>{if(screen==='config'&&booted&&!e.target.closest('#cancelConfig,#downloadToml,#saveConfig'))queueMicrotask(scheduleDraft);});
// One Windows notification reader, independent of the mutation queue.
let environmentEventsTask=null,environmentEventsStopped=false,environmentEventCursor=null;
let environmentEventsWake=null,environmentEventsTimer=null,environmentEventsNeedRefresh=false;
function wakeEnvironmentEvents(){
  if(environmentEventsWake)environmentEventsWake();
  startEnvironmentEvents();
}
function waitEnvironmentEvents(ms){
  return new Promise(resolve=>{
    const finish=()=>{clearTimeout(environmentEventsTimer);environmentEventsTimer=null;environmentEventsWake=null;resolve();};
    environmentEventsWake=finish;
    if(ms!==undefined)environmentEventsTimer=setTimeout(finish,ms);
  });
}
function startEnvironmentEvents(){
  if(!booted||environmentEventsStopped||environmentEventsTask||productState?.mode!=='windows')return;
  environmentEventCursor??=productState.eventCursor;
  if(typeof environmentEventCursor!=='string'||!environmentEventCursor)return;
  environmentEventsTask=(async()=>{
    let backoff=1000;
    while(!environmentEventsStopped&&productState?.mode==='windows'){
      if(document.hidden){await waitEnvironmentEvents();continue;}
      const started=Date.now();
      try{
        if(environmentEventsNeedRefresh){
          await refreshEnvironment();
          if(environmentEventsStopped)break;
          environmentEventsNeedRefresh=false;
          if(document.hidden)continue;
        }
        const result=await API.request('/events?after='+encodeURIComponent(environmentEventCursor)+'&timeout=20');
        // API.request has no abort option. A pending read may finish after
        // pagehide; discard its result and schedule no further work.
        if(environmentEventsStopped)break;
        if(result.closing){environmentEventsStopped=true;break;}
        if(typeof result.cursor!=='string'||!result.cursor)throw new Error('环境通知游标无效。');
        environmentEventCursor=result.cursor;
        environmentEventsNeedRefresh=environmentEventsNeedRefresh||result.changed===true;
        if(environmentEventsNeedRefresh&&!document.hidden){
          await refreshEnvironment();
          if(environmentEventsStopped)break;
          environmentEventsNeedRefresh=false;
        }
        backoff=1000;
        // Quiet requests normally wait 20 seconds; bound rapid replies too.
        if(!document.hidden)await waitEnvironmentEvents(Math.max(0,1000-(Date.now()-started)));
      }catch{
        if(environmentEventsStopped)break;
        if(!document.hidden)await waitEnvironmentEvents(backoff);
        backoff=Math.min(30000,backoff*2);
      }
    }
  })().finally(()=>{environmentEventsTask=null;});
}
document.addEventListener('visibilitychange',wakeEnvironmentEvents);
window.addEventListener('pagehide',()=>{
  environmentEventsStopped=true;
  if(environmentEventsWake)environmentEventsWake();
  clearTimeout(refreshTimer);refreshTimer=null;
});
window.addEventListener('focus',()=>{if(!booted||environmentEventsStopped)return;wakeEnvironmentEvents();clearTimeout(refreshTimer);refreshTimer=setTimeout(()=>safely(refreshEnvironment()),200);});
$('refreshBtn').onclick=()=>safely(refreshEnvironment());$('configBtn').onclick=()=>go('config');$('resetBtn').onclick=importFile;
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>go(b.dataset.view));
document.addEventListener('click',e=>{if(!e.target.closest('#planField')&&!e.target.closest('.btndrop'))closeDropdowns();});
$('modal').onclick=e=>{if(e.target===$('modal'))closeModal();};
window.productReady=(async()=>{
  try{const state=await API.connect();acceptBootstrap(state,{replace:true,importDraft:true});booted=true;render();scheduleProjection();renderOperationStatus();startEnvironmentEvents();if(API.pendingKey())await resumeOperation();return state;}
  catch(error){$('screen').innerHTML='<section class="fullpage"><div class="pageh"><div><h2>服务连接失败</h2><p id="startupError"></p></div></div><button class="btn" id="retryConnection">重新连接</button></section>';$('startupError').textContent=error.message;$('retryConnection').onclick=()=>location.reload();report(error);return null;}
})();

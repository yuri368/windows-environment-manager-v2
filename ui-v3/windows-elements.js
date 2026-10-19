/* Element editing and annotations are UI-only. Only serialized live rows are applied. */
var WindowsElements = (() => {
  const escape = text => String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const escapeRaw = value => escape(value).replace(/\r/g,'&#13;');
  let session = null, sequence = 0;
  let dragPreview=null,dragSource=null,dragNativeImage=null,dragHotspot=null,dragLayout=null,pointerCleanup=null;
  function moveDragPreview(event){
    if(!dragPreview||!Number.isFinite(event.clientX)||!Number.isFinite(event.clientY))return;
    // Some engines emit a final source drag sample at (0,0). Do not jump there.
    if(event.clientX===0&&event.clientY===0)return;
    dragPreview.hidden=false;
    // Keep the row on its list's horizontal rail. Only its vertical offset
    // follows the pointer; the CSS transition gives the lift a gentle weight.
    const listTop=dragSource.parentElement.getBoundingClientRect().top;
    const top=Math.max(listTop,Math.min(listTop+dragHotspot.extent-dragHotspot.height,event.clientY-dragHotspot.y));
    dragPreview.style.setProperty('--drag-y',(top-dragHotspot.top)+'px');
    dragLayout?.preview(top,listTop);
  }
  function leaveDragPreview(event){
    if(dragPreview&&!event.relatedTarget&&[document,document.documentElement,document.body].includes(event.target))dragPreview.hidden=true;
  }
  function clearDragPreview(){
    const cleanup=pointerCleanup;pointerCleanup=null;cleanup?.();
    dragLayout?.clear();dragLayout=null;
    document.removeEventListener('dragover',moveDragPreview,true);
    document.removeEventListener('dragleave',leaveDragPreview,true);
    document.removeEventListener('drop',clearDragPreview,true);
    document.removeEventListener('dragend',clearDragPreview,true);
    dragPreview?.remove();dragPreview=null;
    dragNativeImage?.remove();dragNativeImage=null;dragHotspot=null;
    if(dragSource){dragSource.classList.remove('is-dragging');dragSource.parentElement?.classList.remove('is-dragging-list');dragSource=null;}
  }
  function startRowDragPreview(element,event){
    if(event.dataTransfer&&typeof event.dataTransfer.setDragImage!=='function')return;
    const rect=element.getBoundingClientRect(),preview=element.cloneNode(true);
    const sourceNodes=[element,...element.querySelectorAll('*')],nodes=[preview,...preview.querySelectorAll('*')];
    nodes.forEach((node,index)=>{
      const source=sourceNodes[index];
      // This is a paint-only copy: no duplicate identities, delegated actions,
      // form associations or focus stops. Keep control appearance unchanged.
      for(const {name} of [...node.attributes])if(name==='id'||name==='name'||name==='for'||name==='form'||name.startsWith('data-')||name.startsWith('on'))node.removeAttribute(name);
      for(const name of [...node.classList])if(name.startsWith('fx-')||['is-dragging','drop-target','is-noop','will-shift-up','will-shift-down','is-related-highlight','is-duplicate-highlight','is-link-source','is-focus'].includes(name))node.classList.remove(name);
      node.removeAttribute('draggable');
      if('value' in source){node.value=source.value;if(source.tagName==='TEXTAREA')node.textContent=source.value;}
      if('checked' in source)node.checked=source.checked;
      if(source.tagName==='SELECT')[...source.options].forEach((option,i)=>{node.options[i].selected=option.selected;});
      if(node.matches('input,textarea,select,button,a,[tabindex]'))node.tabIndex=-1;
    });
    // Snapshot inherited type and the resolved grid tracks before the source
    // becomes a placeholder. Inline textarea sizing is already cloned above.
    const style=getComputedStyle(element);
    for(const property of ['font-family','font-size','font-weight','font-style','line-height','letter-spacing','color','grid-template-columns','grid-template-rows','border-bottom-width','border-bottom-style','border-bottom-color'])preview.style.setProperty(property,style.getPropertyValue(property));
    Object.assign(preview.style,{left:rect.left+'px',top:rect.top+'px',width:rect.width+'px',minWidth:rect.width+'px',maxWidth:rect.width+'px',height:rect.height+'px',minHeight:rect.height+'px',maxHeight:rect.height+'px'});
    preview.classList.add('element-drag-preview');preview.setAttribute('aria-hidden','true');preview.setAttribute('inert','');
    document.body.appendChild(preview);dragPreview=preview;
    const list=element.parentElement,listRect=list.getBoundingClientRect();
    const last=[...list.querySelectorAll(':scope > [data-element-id]')].at(-1);
    const bottom=last?.getBoundingClientRect().bottom||listRect.bottom;
    dragHotspot={y:Math.max(0,Math.min(rect.height,event.clientY-rect.top)),top:rect.top,height:rect.height,extent:Math.max(rect.height,bottom-listRect.top)};
    dragSource=element;
    if(!event.dataTransfer)return;
    // Suppress the OS/browser's translucent drag bitmap. The opaque DOM row
    // above the modal is the only visible lifted row for the native fallback.
    const blank=document.createElement('canvas');blank.width=blank.height=1;blank.className='element-drag-native';blank.setAttribute('aria-hidden','true');blank.setAttribute('inert','');
    document.body.appendChild(blank);dragNativeImage=blank;
    try{event.dataTransfer.setDragImage(blank,0,0);}catch{clearDragPreview();return;}
    document.addEventListener('dragover',moveDragPreview,true);
    // Use one coordinate stream. Source `drag` samples can lag behind
    // document `dragover`, making fast moves alternate old and new positions.
    document.addEventListener('dragleave',leaveDragPreview,true);
    document.addEventListener('drop',clearDragPreview,true);
    document.addEventListener('dragend',clearDragPreview,true);
  }
  const normalized = value => value.trim();
  const matches = (raw,values,whole) => DemoPlan.matches(raw,values,whole);
  // State icons are decorative; badge text supplies the accessible state name.
  const PLATE_ICON={added:'add','manual-added':'add',replaced:'swap',moved:'move',removed:'cut',empty:'void',duplicate:'stack',pendingAdd:'add',pendingRemove:'minus',adopted:'check',match:'check'};
  const plateCells=kinds=>kinds.map(kind=>`<i class="st-cell st-${kind}" aria-hidden="true"><b class="ic ic-${PLATE_ICON[kind]}"></b></i>`).join('');
  const plateMarkup=kinds=>kinds.length?`<span class="st-plate" aria-hidden="true">${plateCells(kinds)}</span>`:'';
  const statusBadge=(kind,text,iconKind=kind,title='')=>`<span class="diff-label is-${kind} status-badge"${title?` title="${escape(title)}"`:''}>${plateCells([iconKind])}${escape(text)}</span>`;
  const tailMarkup=html=>html?`<span class="st-tail">${html}</span>`:'';
  const valueHint=(value,whole=false)=>value===''?statusBadge('empty',whole?'空字符串':'空元素'):'';
  const reducedMotion=()=>typeof matchMedia==='function'&&matchMedia('(prefers-reduced-motion: reduce)').matches;
  const canAnimate=()=>!reducedMotion()&&typeof Element.prototype.animate==='function';
  const fieldSizing=typeof CSS!=='undefined'&&typeof CSS.supports==='function'&&CSS.supports('field-sizing','content');
  function selectionInfo(raw,values=[],whole=false){
    const parts=values.flatMap(selectedRaw=>DemoPlan.build({mode:'-append',whole,actualRaw:raw,selectedRaw}).parts).map((part,index)=>({...part,index}));
    const effective=parts.filter(part=>whole||normalized(part.value)!=='');
    return {parts,total:effective.length,matched:effective.filter(part=>part.matched).length,missing:effective.filter(part=>!part.matched).map(({value,index})=>({value,index}))};
  }
  const isAppendMode = mode => ['+append','-append','append'].includes(mode);
  const pendingToken = (part,mode=null) => {
    const position=mode==='+append'?'开头':'末尾';
    const button=isAppendMode(mode)?`<button type="button" class="pending-add-button" data-add-pending="${part.index}" title="加入当前编辑列表${position}" aria-label="将 ${escape(part.value)} 加入当前编辑列表${position}">${iconFallback('＋')}</button>`:'';
    return `<span class="pending-add-token${button?' is-actionable':''}" data-selected-index="${part.index}"><span class="diff-value" data-raw-value="${escape(part.value)}">${escape(part.value)}</span>${tailMarkup(statusBadge('pending-add','待追加','pendingAdd'))}${button}</span>`;
  };
  function selectionMarkup(raw, values = [], whole = false, append = false, plan = null) {
    if (whole || !append) return escape(values.join(';')) || '<span class="empty-element">（空字符串）</span>';
    if (values.length===1 && values[0]==='') return '<span class="empty-element">（空字符串）</span>';
    const selectedPlan=plan||DemoPlan.build({mode:'-append',actualRaw:raw,selectedRaw:values.join(';')});
    const insertionIndexes=new Set(selectedPlan.operations.filter(op=>op.type==='insert').map(op=>op.selectedIndex));
    return selectedPlan.parts.map(part => {
      if (insertionIndexes.has(part.index)) return pendingToken(part);
      if (part.matched) return `<mark class="matched-element"><span data-raw-value="${escape(part.value)}">${escape(part.value)}</span></mark>`;
      return part.value === '' ? '<span class="empty-element" data-raw-value="">（空元素）</span>' : escape(part.value);
    }).join('<span class="element-separator">;</span>');
  }
  const iconFallback=symbol=>'<span class="icon-fallback" aria-hidden="true">'+symbol+'</span>';
  const removalToken=(op,editing=false)=>`<span class="pending-remove-token" data-pending-before-index="${op.beforeIndex}"${op.elementId===undefined?'':` data-element-id="${op.elementId}"`}><span class="diff-value" data-raw-value="${escape(op.value)}">${escape(op.value)}</span>${tailMarkup(statusBadge('removed','待删除','pendingRemove'))}${editing?`<button type="button" class="pending-remove-button" data-remove-pending="${op.beforeIndex}" aria-label="从本次编辑列表移除 ${escape(op.value)}" title="从本次编辑列表移除">${iconFallback('−')}</button>`:''}</span>`;
  function removalMarkup(plan,editing=false){
    const removals=(plan?.operations||[]).filter(op=>op.type==='remove');
    return removals.length?`<div class="pending-removals"><span class="pending-additions-title">方案待删除 · ${editing?'尚未从本次编辑列表移除':'确认应用后移除'}</span><div class="pending-additions-values">${removals.map(op=>removalToken(op,editing)).join('<span class="element-separator">;</span>')}</div></div>`:'';
  }
  function issuesMarkup(plan,editing=false){
    return (plan?.issues||[]).length?`<div class="plan-issues">${plan.issues.map(issue=>`<span class="plan-issue"><code>${escape(issue.value)}</code><span class="diff-label is-retained">${issue.reason==='duplicate'?(editing?'存在重复，需明确选择元素':'存在重复，删除需确认'):issue.reason==='shared'?'共享保留':issue.reason==='continuity'?'原元素已变更或无法确认，已保留':issue.reason==='stale'?'编辑上下文已变化':'目标计划冲突'}</span></span>`).join('')}</div>`:'';
  }
  function editorSuggestionToken(op,kind,modes) {
    const removing=kind==='remove',adopted=!!op.adopted,index=removing?op.beforeIndex:op.selectedIndex;
    const position=modes[index]==='+append'?'开头':'末尾';
    const actionable=removing||isAppendMode(modes[index]);
    const action=removing?(adopted?'撤销本次建议删除，恢复原始元素':'采纳建议删除：从本次编辑列表移除'):adopted?'撤销本次采纳':'采纳建议追加：加入本次编辑列表'+position;
    const removalIdentity=removing?` data-removal-id="${op.elementId}" data-removal-adopted="${adopted}"`:'';
    const button=actionable?`<button type="button" class="pending-${removing?'remove':'add'}-button" data-${removing?'remove':'add'}-pending="${index}"${removalIdentity} aria-pressed="${adopted}" title="${action}" aria-label="${action} ${escape(op.value)}">${iconFallback(adopted?'✓':removing?'−':'＋')}</button>`:'';
    const adoptedLabel=removing?'已采纳删除':'已加入本次编辑';
    const badge=adopted?statusBadge('adopted',adoptedLabel):removing?statusBadge('removed','建议删除','pendingRemove'):statusBadge('pending-add','建议追加','pendingAdd');
    return `<span class="pending-${removing?'remove':'add'}-token editor-suggestion-token${button?' is-actionable':''}${adopted?' is-adopted':''}" ${removing?'data-pending-before-index':'data-selected-index'}="${index}"${removing&&op.elementId!==undefined?` data-element-id="${op.elementId}"`:''}><span class="diff-value" data-raw-value="${escape(op.value)}">${escape(op.value)}</span>${tailMarkup(badge)}${button}</span>`;
  }
  function editorSuggestionsMarkup(plan,whole,modes) {
    if(whole&&plan?.valid!==false)return '';
    const title='<div class="element-suggestions-title">方案建议</div>';
    const hint='<p class="element-suggestions-hint">点击 − 或 ＋ 采纳方案建议；已采纳的卡片可点 ↶ 撤销。所有变化仅作用于本次编辑，确认应用前不会写入 Windows。</p>';
    if(plan?.valid===false)return `<section class="element-suggestions" aria-label="方案建议">${title}<p class="element-suggestions-empty invalid-result">编辑上下文或目标值存在冲突，请检查作用域、模式和方案。</p></section>`;
    const groups=[['remove','建议删除'],['insert','建议追加']].map(([kind,label])=>{
      const operations=(plan?.operations||[]).filter(op=>op.type===kind);
      return operations.length?`<div class="element-suggestions-group ${kind==='remove'?'pending-removals':'pending-additions'}"><span class="element-suggestions-label">${label}</span><div class="pending-additions-values">${operations.map(op=>editorSuggestionToken(op,kind,modes)).join('<span class="element-separator">;</span>')}</div></div>`:'';
    }).join('');
    const issues=issuesMarkup(plan,true);
    return `<section class="element-suggestions" aria-label="方案建议">${title}${hint}${groups}${issues}${groups?'':'<p class="element-suggestions-empty">没有尚待采纳的方案建议。</p>'}</section>`;
  }
  function pendingMarkup(raw, values = [], whole = false, editing = false, modes = [], plan = null) {
    if (whole && !editing) return '';
    const pendingPlan=plan||DemoPlan.build({mode:'-append',whole,actualRaw:raw,selectedRaw:values.join(';')});
    if(editing)return editorSuggestionsMarkup(pendingPlan,whole,modes);
    const missing=pendingPlan.operations.filter(op=>op.type==='insert').map(op=>({value:op.value,index:op.selectedIndex,mode:op.position==='head'?'+append':'-append'}));
    const additions=missing.length?`<div class="pending-additions"><span class="pending-additions-title">方案待追加 · ${editing?'尚未加入本次编辑结果':'尚未加入当前值'}</span><div class="pending-additions-values">${missing.map(part=>pendingToken(part,editing?modes[part.index]:null)).join('<span class="element-separator">;</span>')}</div></div>`:'';
    return removalMarkup(pendingPlan,editing)+additions+issuesMarkup(pendingPlan);
  }
  function highlight(raw, values = [], whole = false, willReplace = false, plan = null) {
    if (raw === undefined) return '<span class="empty-element">（不存在）</span>';
    if (raw === '') return '<span class="empty-element">（空字符串）</span>' + (willReplace ? '<span class="diff-label replacement-label">整体待替换</span>' : '');
    const parts = whole ? [raw] : raw.split(';');
    const removals=new Map((plan?.operations||[]).filter(op=>op.type==='remove').map(op=>[op.beforeIndex,op]));
    const content = parts.map((part,index) => {
      if(removals.has(index))return removalToken(removals.get(index));
      const issue=plan?.issues?.find(item=>item.actualIndexes?.includes(index));
      const rawValue=matches(part,values,whole)?`<mark class="matched-element" title="与准备方案中的元素匹配">${escape(part)}</mark>`:escape(part);
      return issue?`<span class="retained-element">${rawValue}<span class="diff-label is-retained">${issue.reason==='shared'?'共享保留':'删除需确认'}</span></span>`:rawValue;
    }).join('<span class="element-separator">;</span>');
    return willReplace ? `<span class="pending-replacement"><span class="diff-value" data-raw-value="${escape(raw)}">${content}</span><span class="diff-label replacement-label">整体待替换</span></span>` : content;
  }
  // Generic read-only element stream for Windows inventory, history and config views.
  // This is presentation-only: it splits an existing raw value but never computes an apply result.
  function staticMarkup(raw,{whole=false,comparison=false,interactive=false,annotations=[]}={}){
    if(raw===undefined)return '<span class="value-state-chip is-absent">（不存在）</span>';
    const parts=whole?[raw]:String(raw).split(';'),counts=new Map();
    parts.forEach(value=>counts.set(value,(counts.get(value)||0)+1));
    // Numeric CR entities survive HTML parsing; raw whitespace remains display data only.
    return parts.map((value,index)=>{
      const empty=value==='',duplicate=!whole?duplicateLabel(counts.get(value)):'';
      const body=`<span class="diff-value" data-raw-value="${escapeRaw(value)}">${empty?'<span class="empty-element">'+(whole?'（空字符串）':'（空元素）')+'</span>':escapeRaw(value)}</span>`;
      if(!comparison)return `<span class="diff-token static-token${duplicate?' is-duplicate':''}${empty?' is-empty':''}" data-duplicate-value="${escapeRaw(value)}"${empty?' data-empty="true"':''}><span class="token-position" aria-label="第 ${index+1} 项">${index+1}</span>${body}${tailMarkup(duplicate)}</span>`;
      const tag=interactive?'button':'span',action=interactive?` type="button" aria-label="${whole?'编辑当前完整字符串':'编辑当前值的第 '+(index+1)+' 个元素'}"`:'';
      const annotation=annotations[index],state=annotation?.kinds?.[0];
      return `<${tag} class="diff-token comparison-static-token${state?' is-'+state:''}${!whole&&counts.get(value)>1?' is-duplicate':''}" data-duplicate-value="${escapeRaw(value)}" data-value-index="${index}" data-related-key="${escape(annotation?.key??'item:'+index)}" tabindex="${index===0?0:-1}"${empty?' data-empty="true"':''}${action}>${body}${tailMarkup(valueHint(value,whole)+(annotation?.kinds||[]).map(kind=>statusBadge(kind,({added:'新增',removed:'删除',replaced:'替换',moved:'移动'})[kind])).join('')+duplicate)}</${tag}>`;
    }).join('<span class="element-separator">;</span>');
  }
  // History correspondence comes only from recorded occurrence IDs and indices.
  // No matching by value or position is inferred for records without element audit.
  function historyMarkup(raw,{whole=false,elements=[],side='before'}={}){
    const parts=raw===undefined?[]:whole?[raw]:String(raw).split(';');
    const annotations=parts.map((value,index)=>{
      const element=elements.find(item=>item.elementId!=null&&item[side+'Index']===index&&item[side]===value);
      return {key:element?'history:'+element.elementId:side+':'+index,
        kinds:element?['removed','added','replaced','moved'].filter(kind=>element[kind]&&(kind!=='removed'||side==='before')&&(kind!=='added'||side==='after')):[]};
    });
    return staticMarkup(raw,{whole,comparison:true,annotations});
  }
  // Display metadata comes from the frozen plan; it never supplies an apply result.
  function comparisonMarkup(plan){
    const whole=plan.kind!=='append',active=plan.valid!==false;
    const actual=plan.before.exists?(whole?[{value:plan.before.raw,key:'actual:whole'}]:plan.before.raw.split(';').map((value,index)=>({value,key:'actual:'+index,beforeIndex:index}))):[];
    const removals=new Set(active?plan.operations.filter(op=>op.type==='remove').map(op=>op.beforeIndex):[]);
    const additions=active?plan.operations.filter(op=>op.type==='insert'):[];
    const selectedParts=whole?[{value:plan.desiredRaw,index:0,matched:plan.matchResult.exact}]:plan.parts;
    const selected=selectedParts.map(part=>{
      const normalized=part.value.trim(),actualMatches=actual.filter(item=>item.value.trim()===normalized);
      const selectedMatches=selectedParts.filter(item=>item.value.trim()===normalized);
      const uniqueMatch=part.matched&&actualMatches.length===1&&selectedMatches.length===1;
      return {...part,key:uniqueMatch?actualMatches[0].key:'selected:'+part.index,added:additions.some(op=>op.selectedIndex===part.index)};
    });
    let after=whole?[{value:plan.after.raw,key:plan.operations.length?'selected:0':'actual:whole',wholeChange:active&&plan.operations.length>0}]:actual.map(item=>({...item,ghost:removals.has(item.beforeIndex)}));
    if(!whole){
      const groups=[];
      for(const op of additions){const last=groups.at(-1);if(last&&last.requestId===op.requestId&&last.position===op.position)last.items.push(op);else groups.push({requestId:op.requestId,position:op.position,items:[op]});}
      for(const group of groups){const items=group.items.map(op=>({value:op.value,key:'selected:'+op.selectedIndex,index:op.selectedIndex,added:true}));after=group.position==='head'?[...items,...after]:[...after,...items];}
      // If annotation metadata cannot represent the authoritative after, show that raw value without invented links.
      if(plan.after.exists&&after.filter(item=>!item.ghost).map(item=>item.value).join(';')!==plan.after.raw)after=plan.after.raw.split(';').map((value,index)=>({value,key:'after:'+index}));
    }
    // Ghosts explain removals but never participate in the serialized result.
    const render=(items,side)=>{
      const counts=new Map();items.filter(item=>!item.ghost).forEach(item=>counts.set(item.value,(counts.get(item.value)||0)+1));
      return items.map(item=>{
        const removed=item.ghost||(side==='actual'&&removals.has(item.beforeIndex));
        const issue=side==='actual'||side==='after'?plan.issues?.find(issue=>issue.actualIndexes?.includes(item.beforeIndex)):null;
        const matched=side==='selected'?item.matched:whole?item.value===plan.desiredRaw:matches(item.value,[plan.desiredRaw]);
        const added=(side==='after'||side==='selected')&&item.added;
        const classes=['diff-token','comparison-token',!whole&&counts.get(item.value)>1?'is-duplicate':'',item.ghost?'is-ghost':'',removed?'is-removed':added?'is-added pending-add-token':item.wholeChange?'is-replaced':matched?'is-matched':''].filter(Boolean).join(' ');
        const label=removed?statusBadge('removed',item.ghost?'结果排除':'待删除',item.ghost?'removed':'pendingRemove'):added?statusBadge('pending-add','待追加','pendingAdd'):item.wholeChange?statusBadge('replaced','整体待替换'):issue?'<span class="diff-label is-retained">'+(issue.reason==='shared'?'共享保留':'删除需确认')+'</span>':'';
        const dup=!whole?duplicateLabel(counts.get(item.value)):'';
        return '<span class="'+classes+'" tabindex="0" data-duplicate-value="'+escapeRaw(item.value)+'" data-related-key="'+item.key+'"'+(item.value===''?' data-empty="true"':'')+(item.beforeIndex===undefined?'':' data-before-index="'+item.beforeIndex+'"')+'>'+'<span class="diff-value" data-raw-value="'+escapeRaw(item.value)+'">'+(item.value===''?'<span class="empty-element">'+(whole?'（空字符串）':'（空元素）')+'</span>':escapeRaw(item.value))+'</span>'+tailMarkup(valueHint(item.value,whole)+label+dup)+'</span>';
      }).join('<span class="element-separator">;</span>');
    };
    const tally=!active?'':whole?'<span class="tally tally-'+(after.some(item=>item.wholeChange)?'replace">整体替换':'keep">无变化')+'</span>'
      :[['keep','保留',after.filter(item=>!item.ghost&&!item.added).length],['add','新增',after.filter(item=>item.added).length],['remove','删除',after.filter(item=>item.ghost).length]]
        .map(([kind,text,count])=>'<span class="tally tally-'+kind+(count?'':' is-zero')+'"><i class="tally-dot"></i>'+text+' <b>'+count+'</b></span>').join('');
    return {tally,selected:'<span class="selection-current">'+render(selected,'selected')+'</span>',
      actual:plan.before.exists?(render(actual,'actual')||'<span class="empty-element">（空字符串）</span>'):'<span class="empty-element">（不存在）</span>',
      after:!active?'<span class="invalid-result">编辑上下文或目标值存在冲突</span>':(render(after,'after')||'<span class="empty-element">'+(plan.after.exists?'（空字符串）':'（不存在）')+'</span>')+(after.some(item=>item.ghost)?'<span class="comparison-ghost-hint">应用后预览。划线元素不计入最终写入值，当前尚未应用。</span>':'')};
  }
  const comparisonBindings=new WeakMap();
  const linkLegendMarkup=()=>'<span class="link-legend"><span class="link-key key-source">当前触碰</span><span class="link-key key-identity">同项前后</span><span class="link-key key-duplicate">重复同值</span></span>';
  // Raw equality is a separate hover-only display group, never occurrence identity.
  const duplicateValueFor=element=>element?.classList.contains('is-duplicate')&&element.hasAttribute('data-duplicate-value')?element.dataset.duplicateValue:null;
  function paintDuplicateGroup(root,value){
    root.querySelectorAll('[data-duplicate-value]').forEach(element=>element.classList.toggle('is-duplicate-highlight',value!==null&&element.dataset.duplicateValue===value));
  }
  function paintLinkSource(root,source){
    root.querySelectorAll('.is-link-source').forEach(element=>{if(element!==source)element.classList.remove('is-link-source')});
    if(source&&root.contains(source))source.classList.add('is-link-source');
  }
  function bindComparison(root){
    comparisonBindings.get(root)?.();
    const panes=new Map();
    root.querySelectorAll('.comparison-token').forEach(token=>{
      const pane=token.closest('.value-box,.value-stream');
      if(pane){if(!panes.has(pane))panes.set(pane,[]);panes.get(pane).push(token)}
    });
    const selectTab=token=>{
      const items=panes.get(token?.closest('.value-box,.value-stream'));
      items?.forEach(item=>item.tabIndex=item===token?0:-1);
    };
    for(const items of panes.values())selectTab(items.find(item=>item===document.activeElement)||items[0]);
    const keyFor=target=>target?.closest?.('[data-related-key]')?.dataset.relatedKey??null;
    const hoverFor=target=>{const token=target?.closest?.('[data-related-key],[data-duplicate-value]');return token&&root.contains(token)?token:null};
    let hovered=null,focused=root.contains(document.activeElement)?keyFor(document.activeElement):null;
    const paint=()=>{const key=hovered?keyFor(hovered):focused,duplicateValue=duplicateValueFor(hovered);root.classList.toggle('is-linking',key!==null||duplicateValue!==null);root.querySelectorAll('[data-related-key]').forEach(node=>node.classList.toggle('is-related-highlight',node.dataset.relatedKey===key));paintDuplicateGroup(root,duplicateValue);paintLinkSource(root,hovered)};
    const handlers={
      pointerover:e=>{const next=hoverFor(e.target);if(next!==hovered){hovered=next;paint()}},
      pointerout:e=>{const next=hoverFor(e.relatedTarget);if(next!==hovered){hovered=next;paint()}},
      pointerleave:()=>{hovered=null;paint()},
      focusin:e=>{const token=e.target.closest('.comparison-token');if(token)selectTab(token);focused=keyFor(e.target);paint()},
      focusout:e=>{focused=root.contains(e.relatedTarget)?keyFor(e.relatedTarget):null;paint()},
      keydown:e=>{
        if(e.altKey||e.ctrlKey||e.metaKey||e.shiftKey||e.isComposing||!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'].includes(e.key))return;
        const token=e.target.closest('.comparison-token'),items=panes.get(token?.closest('.value-box,.value-stream')),at=items?.indexOf(token);
        if(at===undefined||at<0)return;
        e.preventDefault();const next=e.key==='Home'?0:e.key==='End'?items.length-1:Math.max(0,Math.min(items.length-1,at+(['ArrowRight','ArrowDown'].includes(e.key)?1:-1)));items[next].focus();
      }
    };
    paint();
    for(const [type,handler] of Object.entries(handlers))root.addEventListener(type,handler);
    comparisonBindings.set(root,()=>{for(const [type,handler] of Object.entries(handlers))root.removeEventListener(type,handler)});
  }
  const serialized = () => session.rows.map(row => row.value).join(session.whole ? '' : ';');
  const valid = () => !session.rows.some(row => row.value.includes('\0') || (!session.whole && row.value.includes(';')));
  function preserveRawLineEndings(raw,textareaValue,edit) {
    const boundaries=[0];
    let normalizedRaw='';
    for(let offset=0;offset<raw.length;){
      if(raw[offset]==='\r'){
        offset+=raw[offset+1]==='\n'?2:1;
        normalizedRaw+='\n';
      }else{
        normalizedRaw+=raw[offset++];
      }
      boundaries.push(offset);
    }
    const next=textareaValue.replace(/\r\n?/g,'\n');
    const spliceRaw=(start,end,inserted)=>{
      let before=raw.slice(0,boundaries[start]);
      const after=raw.slice(boundaries[end]);
      // A formerly standalone CR must not absorb the LF made adjacent by this edit.
      if(before.endsWith('\r')&&(inserted+after).startsWith('\n'))before=before.slice(0,-1)+'\n';
      const result=before+inserted+after;
      return result.replace(/\r\n?/g,'\n')===next?result:next;
    };
    // beforeinput identifies which occurrence changed when normalized characters repeat.
    if(edit&&edit.raw===raw&&edit.value===normalizedRaw){
      const {start,end}=edit,insertedLength=next.length-normalizedRaw.length+(end-start);
      if(insertedLength>=0&&next.slice(0,start)===normalizedRaw.slice(0,start)&&
          next.slice(start+insertedLength)===normalizedRaw.slice(end)){
        return spliceRaw(start,end,next.slice(start,start+insertedLength));
      }
    }
    if(normalizedRaw===next)return raw;
    let prefix=0;
    while(prefix<normalizedRaw.length&&prefix<next.length&&normalizedRaw[prefix]===next[prefix])prefix++;
    let suffix=0;
    while(suffix<normalizedRaw.length-prefix&&suffix<next.length-prefix&&normalizedRaw[normalizedRaw.length-1-suffix]===next[next.length-1-suffix])suffix++;
    return spliceRaw(prefix,normalizedRaw.length-suffix,next.slice(prefix,next.length-suffix));
  }
  function textareaEditRange(input,event,raw) {
    if(event.isComposing)return null;
    const value=input.value,type=event.inputType;
    let start=input.selectionStart,end=input.selectionEnd;
    if(!Number.isInteger(start)||!Number.isInteger(end))return null;
    if(type==='deleteContentBackward'&&start===end){
      if(start===0)return null;
      start-=1;
      if(start>0&&/[\uDC00-\uDFFF]/.test(value[start])&&/[\uD800-\uDBFF]/.test(value[start-1]))start-=1;
    }else if(type==='deleteContentForward'&&start===end){
      if(end===value.length)return null;
      end+=1;
      if(end<value.length&&/[\uD800-\uDBFF]/.test(value[end-1])&&/[\uDC00-\uDFFF]/.test(value[end]))end+=1;
    }else if(!['insertText','insertLineBreak','insertParagraph','insertFromPaste','insertFromDrop','insertReplacementText','deleteContentBackward','deleteContentForward','deleteByCut','deleteByDrag'].includes(type))return null;
    return {raw,value,start,end};
  }
  function syncRowsToDisplayOrder() {
    const liveById=new Map(session.rows.map(row=>[row.id,row]));
    session.rows=session.displayOrder.filter(id=>liveById.has(id)).map(id=>liveById.get(id));
  }
  function clearAdoptionForId(id) {
    for(const [index,adoption] of session.adoptionMap)if(adoption.elementId===id)session.adoptionMap.delete(index);
  }
  function removeLiveRow(id) {
    const row=session.rows.find(item=>item.id===id);
    if(row&&session.originalById.has(id))session.deletedDrafts.set(id,{id:row.id,value:row.value});
    session.rows=session.rows.filter(row=>row.id!==id);
    if(!session.originalById.has(id))session.displayOrder=session.displayOrder.filter(item=>item!==id);
    clearAdoptionForId(id);
    session.manualAddedIds.delete(id);
    syncRowsToDisplayOrder();
  }
  function countsFor(rows) {
    const counts = new Map();
    for (const row of rows) counts.set(row.value, (counts.get(row.value) || 0) + 1);
    return counts;
  }
  const duplicateLabel = count => count > 1
    ? statusBadge('duplicate',`重复 ×${count}`,'duplicate',`此栏有 ${count} 个原始文本完全相同的元素；仅提示，不自动去重`) : '';
  function editStateFor(row) {
    const original = session.originalById.get(row.id);
    if (!original) return {added:true,replaced:false,moved:false};
    const existing = session.rows.filter(item => session.originalById.has(item.id));
    const remaining = session.originalRows.filter(item => session.rows.some(current => current.id === item.id));
    return {added:false,replaced:row.value !== original.value,
      moved:existing.findIndex(item => item.id === row.id) !== remaining.findIndex(item => item.id === row.id)};
  }
  // Presentation compares every original stable ID, including ghosts; Audit still compares live originals.
  function displayMoved(id){
    if(!session.originalById.has(id))return false;
    const originalIndex=session.originalRows.findIndex(row=>row.id===id);
    const displayIndex=session.displayOrder.filter(item=>session.originalById.has(item)).indexOf(id);
    return displayIndex>=0&&displayIndex!==originalIndex;
  }
  function displayMovementCounts(){
    const liveIds=new Set(session.rows.map(row=>row.id));
    return session.originalRows.reduce((counts,row)=>{
      if(displayMoved(row.id))counts[liveIds.has(row.id)?'moved':'ghost-moved']++;
      return counts;
    },{moved:0,'ghost-moved':0});
  }
  function stateFor(row){
    const business=session.plan?.audit.elements.find(element=>element.elementId===row.id)||editStateFor(row);
    return {...business,moved:displayMoved(row.id)};
  }
  const manualAddition=(row,state)=>!!state.added&&session.manualAddedIds.has(row.id);
  function labels(state, removed = false,row) {
    return (row?valueHint(row.value,session.whole):'')+(removed ? [['removed','删除'],...(state.moved?[['moved','幽灵移动']]:[])] : [
      ...(state.added ? [[row&&manualAddition(row,state)?'manual-added':'added',row&&manualAddition(row,state)?'手动新增':'新增']] : []), ...(state.replaced ? [['replaced','替换']] : []),
      ...(state.moved ? [['moved','移动']] : [])
    ]).map(([kind,text]) => statusBadge(kind,text)).join('');
  }
  function auditSnapshot() {
    const elements=session.rows.map((row,afterIndex)=>{
      const beforeIndex=session.originalRows.findIndex(original=>original.id===row.id);
      const state=editStateFor(row);
      return {elementId:row.id,beforeIndex:beforeIndex<0?null:beforeIndex,afterIndex,
        before:beforeIndex<0?undefined:session.originalRows[beforeIndex].value,after:row.value,
        ...state,removed:false};
    });
    session.originalRows.forEach((row,beforeIndex)=>{
      if(!session.rows.some(current=>current.id===row.id))elements.push({elementId:row.id,beforeIndex,afterIndex:null,
        before:row.value,after:undefined,added:false,removed:true,replaced:false,moved:false});
    });
    return {before:session.original,after:serialized(),whole:session.whole,elements,
      counts:Object.fromEntries(['added','removed','replaced','moved'].map(kind=>[kind,elements.filter(element=>element[kind]).length]))};
  }
  function token(row, state, removed = false, duplicateCount = 1, ghost = false) {
    const matched = matches(row.value,session.selectedValues || [],session.whole);
    const classes = [removed?'is-removed':'',ghost?'is-ghost':'',state.added?'is-added':'',manualAddition(row,state)?'is-manual-added':'',state.replaced?'is-replaced':'',state.moved?'is-moved':'',matched?'is-matched':'',duplicateCount>1?'is-duplicate':''].filter(Boolean).join(' ');
    return `<span class="diff-token ${classes}" data-element-id="${row.id}" data-duplicate-value="${escapeRaw(row.value)}" data-duplicate-count="${duplicateCount}"${row.value===''?' data-empty="true"':''} tabindex="-1"><span class="diff-value" data-raw-value="${escape(row.value)}">${row.value === '' ? `<span class="empty-element">${session.whole?'（空字符串）':'（空元素）'}</span>` : escape(row.value)}</span>${tailMarkup(labels(state,removed,row)+duplicateLabel(duplicateCount)+(matched&&!removed?statusBadge('match','匹配'):''))}</span>`;
  }
  function renderComparison() {
    const root=session.container;
    root.querySelector('#elementOriginal').innerHTML=session.original === undefined ? '<span class="empty-element">（不存在）</span>'
      : session.originalRows.map(original=>{const current=session.rows.find(row=>row.id===original.id);return token(original,stateFor(current??original),!current,session.originalCounts.get(original.value));}).join('<span class="element-separator">;</span>');
    const liveById=new Map(session.rows.map(row=>[row.id,row]));
    const current=session.displayOrder.map(id=>{
      const row=liveById.get(id);
      return row?token(row,stateFor(row),false,session.currentCounts.get(row.value)):token(session.deletedDrafts.get(id)??session.originalById.get(id),stateFor(session.originalById.get(id)),true,1,true);
    }).join('<span class="element-separator">;</span>');
    root.querySelector('#elementPreview').innerHTML=current || `<span class="empty-element">${session.plan.after.exists?'（空字符串）':'（不存在）'}</span>`;
    root.querySelector('#elementSerialized').textContent=session.plan.after.exists?serialized():'（不存在）';
    paintRelated();
  }
  function paintRelated() {
    if (!session) return;
    const id = session.hoveredId ?? session.focusedId;
    session.container.querySelector('.element-dialog')?.classList.toggle('is-linking', id !== null);
    session.container.querySelectorAll('[data-element-id]').forEach(element => {
      element.classList.toggle('is-related-highlight', id !== null && Number(element.dataset.elementId) === id);
    });
    paintDuplicateGroup(session.container,session.container.contains(session.hoveredElement)?duplicateValueFor(session.hoveredElement):null);
    paintLinkSource(session.container,session.hoveredElement);
  }
  function elementAt(target) {
    if (!target || target.nodeType !== 1) return null;
    const element = target.closest('[data-element-id]');
    return element && session.container.contains(element) ? element : null;
  }
  function bindRelated() {
    const root = session.container;
    const handlers = {};
    handlers.pointerover = event => {
      if (!session) return;
      const element = elementAt(event.target);
      const id = element ? Number(element.dataset.elementId) : null;
      if (session.hoveredElement !== element) { session.hoveredElement = element; session.hoveredId = id; paintRelated(); }
    };
    handlers.pointerout = event => {
      if (!session) return;
      const next = elementAt(event.relatedTarget);
      const id = next ? Number(next.dataset.elementId) : null;
      if (session.hoveredElement !== next) { session.hoveredElement = next; session.hoveredId = id; paintRelated(); }
    };
    handlers.pointerleave = () => {
      if (session) { session.hoveredElement = null; session.hoveredId = null; paintRelated(); }
    };
    handlers.focusin = event => {
      if (!session) return;
      const element = elementAt(event.target);
      session.focusedId = element ? Number(element.dataset.elementId) : null;
      paintRelated();
    };
    handlers.focusout = event => {
      if (!session) return;
      const next = elementAt(event.relatedTarget);
      session.focusedId = next ? Number(next.dataset.elementId) : null;
      paintRelated();
    };
    for (const [type, handler] of Object.entries(handlers)) root.addEventListener(type, handler);
    session.relatedHandlers = handlers;
  }
  function paintRow(element,row) {
    const matched=matches(row.value,session.selectedValues || [],session.whole),state=stateFor(row);
    element.classList.toggle('is-matched',matched);
    element.classList.toggle('is-manual-added',manualAddition(row,state));
    for(const flag of ['added','replaced','moved'])element.classList.toggle('is-'+flag,!!state[flag]);
    const duplicateCount = session.currentCounts.get(row.value);
    element.classList.toggle('is-duplicate', duplicateCount > 1);
    element.dataset.duplicateValue = row.value;
    element.dataset.duplicateCount = duplicateCount;
    element.classList.toggle('is-empty',row.value==='');
    element.querySelector('.element-match-state').innerHTML=matched?statusBadge('match','匹配'):'';
    element.querySelector('.element-change-state').innerHTML=labels(state,false,row)+duplicateLabel(duplicateCount);
    element.querySelector('.element-plate').innerHTML='';
    element.querySelector('.element-input').setAttribute('aria-invalid',String(row.value.includes('\0') || (!session.whole && row.value.includes(';'))));
  }
  function candidatePlan(){
    const values=session.appendValues,additions=DemoPlan.build({mode:'-append',actualRaw:serialized(),selectedRaw:values.join(';')});
    const originalPlan=session.selectionPlan;
    if(originalPlan?.valid===false)return {...originalPlan,operations:[]};
    const seenRemovalIds=new Set();
    const removals=(originalPlan?.operations||[]).filter(op=>op.type==='remove').flatMap(op=>{
      const original=session.originalRows[op.beforeIndex];
      if(!original||original.value!==op.value||seenRemovalIds.has(original.id))return [];
      seenRemovalIds.add(original.id);
      const row=session.rows.find(row=>row.id===original.id);
      const adopted=session.removalAdoptions.has(original.id);
      if(adopted)return !row&&session.displayOrder.includes(original.id)?[{...op,elementId:original.id,adopted:true}]:[];
      return row&&row.value===op.value?[{...op,elementId:original.id}]:[];
    }).sort((a,b)=>a.beforeIndex-b.beforeIndex);
    const adopted=[...session.adoptionMap].flatMap(([selectedIndex,entry])=>{
      const row=session.rows.find(row=>row.id===entry.elementId);
      return row&&row.value===entry.adoptedRaw?[{type:'insert',selectedIndex,value:entry.adoptedRaw,adopted:true}]:[];
    });
    const adoptedIndexes=new Set(adopted.map(op=>op.selectedIndex));
    const inserts=[...additions.operations.filter(op=>op.type==='insert'&&!adoptedIndexes.has(op.selectedIndex)),...adopted]
      .sort((a,b)=>a.selectedIndex-b.selectedIndex);
    return {...additions,operations:[...removals,...inserts],issues:originalPlan?.issues||[]};
  }
  // Legend chips double as live counters. Counts come from the audit/live rows; they never feed back into the plan.
  function additionCounts() {
    const manualAdded=session.plan.audit.elements.filter(element=>element.added&&session.manualAddedIds.has(element.elementId)).length;
    return {added:session.plan.audit.counts.added-manualAdded,manualAdded};
  }
  function paintLegend() {
    const counts=session.plan.audit.counts,rows=session.rows,additions=additionCounts();
    const live={
      ...displayMovementCounts(),
      added:additions.added,
      'manual-added':additions.manualAdded,
      duplicate:session.whole?0:rows.filter(row=>session.currentCounts.get(row.value)>1).length,
      empty:session.whole?0:rows.filter(row=>row.value==='').length,
      match:rows.filter(row=>matches(row.value,session.selectedValues||[],session.whole)).length
    };
    session.container.querySelectorAll('[data-legend-count]').forEach(node=>{
      const kind=node.dataset.legendCount,count=kind in live?live[kind]:counts[kind]||0;
      node.textContent=count;
      node.closest('.lg')?.classList.toggle('is-zero',!count);
    });
  }
  // One-shot feedback for the suggestion card that was just toggled. Re-rendering recreates the node, so the
  // entry/exit state is replayed with a class that CSS keyframes pick up and that removes itself afterwards.
  function applySuggestionFx(pending) {
    const fx=session.suggestFx;session.suggestFx=null;
    if(!fx)return;
    const selector=fx.type==='add'?`[data-selected-index="${fx.index}"]`:`[data-pending-before-index="${fx.index}"]`;
    const token=pending.querySelector('.editor-suggestion-token'+selector);
    if(!token)return;
    const name=fx.adopt?'fx-adopt':'fx-unadopt';
    token.classList.add(name);
    setTimeout(()=>token.classList.remove(name),700);
  }
  const fxKey=element=>(element.closest('#elementPreview')?'p':'l')+element.dataset.elementId;
  function layoutSnapshot(root,fx) {
    const positions=new Map(),shards=[];
    if(fx?.kind==='remove'&&fx.id!=null){
      const live=root.querySelector(`#elementList > .element-row[data-element-id="${fx.id}"]`);
      if(live){
        const rect=live.getBoundingClientRect(),node=live.cloneNode(true),fields=[...live.querySelectorAll('input,textarea')];
        node.querySelectorAll('input,textarea').forEach((field,index)=>{const value=fields[index]?.value??'';field.value=value;if(field.tagName==='TEXTAREA')field.textContent=value;else field.setAttribute('value',value);});
        shards.push({key:fxKey(live),node,height:rect.height});
      }
    }
    root.querySelectorAll('#elementList > [data-element-id], #elementPreview [data-element-id]').forEach(element=>{
      const rect=element.getBoundingClientRect();positions.set(fxKey(element),{x:rect.left,y:rect.top});
    });
    return {positions,shards,scroll:root.querySelector('.elements-body')?.scrollTop??0};
  }
  // FLIP: rows and preview tokens glide from their old position, and the touched element gets a landing mark.
  // Deletion: the old live row is redrawn as two halves that part along a jagged crack while the trace row fades in underneath.
  const CRACK=[[41,0],[47,24],[42,42],[51,60],[46,80],[55,100]];
  function playBreak(shard,target) {
    const rect=target.getBoundingClientRect();
    if(!rect.width||!rect.height)return;
    const poly=side=>`polygon(${(side==='left'?[[0,0],...CRACK,[0,100]]:[...CRACK,[100,100],[100,0]]).map(([x,y])=>x+'% '+y+'%').join(',')})`;
    const wrap=document.createElement('div');wrap.className='break-fx';wrap.setAttribute('aria-hidden','true');
    wrap.style.cssText=`left:${rect.left}px;top:${rect.top}px;width:${rect.width}px;height:${Math.max(rect.height,shard.height)}px`;
    ['left','right'].forEach(side=>{
      const half=document.createElement('div'),clone=shard.node.cloneNode(true);
      half.className='break-half is-'+side;half.style.clipPath=poly(side);
      [clone,...clone.querySelectorAll('*')].forEach(n=>[...n.attributes].forEach(a=>{if(a.name.startsWith('data-')||a.name==='id'||a.name==='for'||a.name==='draggable')n.removeAttribute(a.name);}));
      clone.querySelectorAll('input,textarea,button').forEach(n=>{n.tabIndex=-1;n.disabled=true;});
      clone.classList.remove('is-dragging','is-related-highlight','is-duplicate-highlight','is-link-source','is-focus');half.appendChild(clone);wrap.appendChild(half);
    });
    wrap.insertAdjacentHTML('beforeend',`<svg class="break-crack" viewBox="0 0 100 100" preserveAspectRatio="none"><polyline pathLength="1" points="${CRACK.map(point=>point.join(',')).join(' ')}"/></svg>`);
    for(let index=0;index<7;index++){
      const [x,y]=CRACK[Math.min(index%CRACK.length,CRACK.length-1)],angle=(index*57+20)*Math.PI/180,dist=18+index*5;
      wrap.insertAdjacentHTML('beforeend',`<i class="break-chip" style="left:${x+(index%2?1:-1)*2}%;top:${y}%;--dx:${Math.round(Math.cos(angle)*dist*(index%2?1:-1))}px;--dy:${Math.round(Math.abs(Math.sin(angle))*dist*.8+4)}px;--r:${(index%2?1:-1)*(40+index*23)}deg;--s:${(.55+(index%3)*.28).toFixed(2)}"></i>`);
    }
    document.body.appendChild(wrap);
    setTimeout(()=>wrap.remove(),1000);
  }
  function playLayoutFx(root,before,fx) {
    if(!fx||!before)return;
    if(fx.kind==='remove'&&canAnimate()&&(root.querySelector('.elements-body')?.scrollTop??0)===before.scroll){
      before.shards.forEach(shard=>{const target=[...root.querySelectorAll('#elementList > [data-element-id]')].find(element=>fxKey(element)===shard.key);if(target)playBreak(shard,target);});
    }
    const names={move:'fx-move',enter:'fx-enter',remove:'fx-remove',restore:'fx-restore'};
    const marked=fx.id==null?[]:[...root.querySelectorAll(`#elementList > [data-element-id="${fx.id}"], #elementPreview [data-element-id="${fx.id}"]`)];
    if(names[fx.kind]){
      marked.forEach(element=>{element.classList.add(names[fx.kind]);setTimeout(()=>element.classList.remove(names[fx.kind]),900);});
    }
    if(!canAnimate()||(root.querySelector('.elements-body')?.scrollTop??0)!==before.scroll)return;
    root.querySelectorAll('#elementList > [data-element-id], #elementPreview [data-element-id]').forEach(element=>{
      const old=before.positions.get(fxKey(element));if(!old)return;
      const rect=element.getBoundingClientRect(),dx=old.x-rect.left,dy=old.y-rect.top;
      if(Math.abs(dx)<1&&Math.abs(dy)<1)return;
      element.animate([{transform:`translate(${dx}px,${dy}px)`},{transform:'translate(0,0)'}],{duration:Math.round(Math.min(320,150+Math.hypot(dx,dy)*.35)),easing:'cubic-bezier(.2,.75,.25,1)'});
    });
  }
  function update() {
    const root=session.container,raw=serialized();
    session.currentCounts = countsFor(session.rows);
    session.plan=DemoPlan.buildElementEdit({target:{scope:session.scope,name:session.name},snapshot:session.snapshot||{exists:session.original!==undefined,raw:session.original===undefined?null:session.original,type:session.registryType,revision:0},afterRaw:raw,audit:auditSnapshot()});
    const changed=session.plan.operations.length>0,candidates=candidatePlan(),additions=additionCounts(),movement=displayMovementCounts();
    const operationCounts={add:0,remove:0,replace:0,move:0};
    for(const operation of session.plan.operations){
      const kind=String(operation.type||operation.action).toLowerCase(),key=kind==='insert'?'add':kind;
      if(key in operationCounts)operationCounts[key]++;
    }
    const hasSuggestions=session.showSuggestions!==false&&!session.whole&&candidates.valid!==false&&((candidates.operations||[]).some(op=>!op.adopted)||(candidates.issues||[]).length>0);
    const structureChanged=JSON.stringify(session.rows.map(row=>({id:row.id,value:row.value})))!==JSON.stringify(session.originalRows)
      ||session.displayOrder.some((id,index)=>id!==session.originalRows[index]?.id)||session.displayOrder.length!==session.originalRows.length;
    root.querySelector('#elementTotal').textContent=`有效${session.whole?'字符串':'元素'} ${session.rows.length} · 已移除 ${session.originalRows.filter(row=>!session.rows.some(live=>live.id===row.id)).length}`;
    root.querySelector('#elementValidation').textContent=valid()?'':session.whole?'字符串不能包含 NUL。':'一个元素内不能包含分号或 NUL；请将多项内容拆成多个元素。';
    root.querySelector('#applyElements').disabled=!valid()||!session.plan.operations.length;
    root.querySelector('#restoreElements').disabled=!structureChanged;
    root.querySelector('#elementStatus').textContent=changed?`本次编辑：新增 ${additions.added} · 手动新增 ${additions.manualAdded} · 删除 ${operationCounts.remove} · 替换 ${operationCounts.replace} · 展示移动：移动 ${movement.moved} · 幽灵移动 ${movement['ghost-moved']} · 确认后生效`:`本次编辑：0 项变化${hasSuggestions?' · 有尚未采纳的方案建议':''}${structureChanged?' · 结果值未改变':''} · 展示移动：移动 ${movement.moved} · 幽灵移动 ${movement['ghost-moved']}`;
    root.querySelectorAll('[data-element-id]').forEach(el=>{if(!el.classList.contains('element-row'))return;const row=session.rows.find(row=>row.id===Number(el.dataset.elementId));if(row)paintRow(el,row);});
    const pending = root.querySelector('#pendingElements');
    pending.innerHTML = session.showSuggestions===false?'':pendingMarkup(raw,session.appendValues,session.whole,true,session.appendModes,candidates);
    pending.hidden = !pending.innerHTML;
    paintLegend();
    root.querySelector('.legend-match').hidden=session.showSuggestions===false;
    applySuggestionFx(pending);
    session.suggestionButtons=new Set(pending.querySelectorAll('[data-add-pending],[data-remove-pending]'));
    const editingSession=session;
    pending.querySelectorAll('[data-add-pending]').forEach(button=>{
      button.onclick=()=>adoptPending(Number(button.dataset.addPending),button,editingSession);
    });
    pending.querySelectorAll('[data-remove-pending]').forEach(button=>button.onclick=()=>toggleRemoval(button,editingSession));
    // Arrow keys / Home / End walk between suggestion buttons so a run of adopt/undo needs no mouse.
    pending.onkeydown=event=>{
      const step={ArrowDown:1,ArrowRight:1,ArrowUp:-1,ArrowLeft:-1}[event.key];
      if(step===undefined&&event.key!=='Home'&&event.key!=='End')return;
      const buttons=[...pending.querySelectorAll('[data-add-pending],[data-remove-pending]')],at=buttons.indexOf(document.activeElement);
      if(at<0||event.altKey||event.ctrlKey||event.metaKey)return;
      event.preventDefault();
      buttons[event.key==='Home'?0:event.key==='End'?buttons.length-1:Math.min(buttons.length-1,Math.max(0,at+step))]?.focus();
    };
    renderComparison();
  }
  function open(options) {
    dismiss();
    const original=options.raw,rawValues=original===undefined?[]:options.whole?[original]:original.split(';');
    const originalRows=rawValues.map(value=>({id:++sequence,value}));
    const entries=Array.isArray(options.appendEntries)?options.appendEntries:null;
    const appendValues=entries?entries.map(entry=>entry.value):[...(options.appendValues || [])];
    const appendModes=entries?entries.flatMap(entry=>entry.value.split(';').map(()=>entry.mode)):[];
    session={...options,appendValues,appendModes,original,originalRows,originalById:new Map(originalRows.map(row=>[row.id,row])),originalCounts:countsFor(originalRows),currentCounts:new Map(),rows:originalRows.map(row=>({...row})),displayOrder:originalRows.map(row=>row.id),adoptionMap:new Map(),manualAddedIds:new Set(),removalAdoptions:new Set(),deletedDrafts:new Map(),dragging:null,hoveredId:null,focusedId:null};
    const container=options.container;
    container.innerHTML=`<div class="dialog element-dialog" role="dialog" aria-modal="true" aria-labelledby="dialogTitle">
      <div class="dialoghead"><div><div class="eyebrow">Windows 环境 / ${options.scope==='User'?'用户':'系统'}</div><h3 id="dialogTitle" title="注册表类型：${escape(options.registryType)}（演示）">${escape(options.name)} <span class="element-demo">演示数据</span></h3><p>调整元素顺序或内容，应用后更新本页展示。</p></div><button class="close" id="closeDialog" aria-label="关闭元素编辑器">×</button></div>
      <div class="dialogbody elements-body"><div class="elements-main"><div class="elements-toolbar"><div class="elements-toolbar-info"><span id="elementTotal" class="elements-count" aria-live="polite"></span><span>${options.whole?'整体字符串 · 不拆分元素':'按分号排列 · Alt + ↑ / ↓ 调整顺序'}</span></div><div class="elements-toolbar-actions"><button class="textbtn" id="restoreElements" disabled title="恢复打开弹窗时的值，不改变页面快照">还原本次编辑</button><button class="btn" id="addElement" ${options.whole?'hidden':''}>＋ 新增元素</button></div></div>
        <div class="change-legend" role="group" aria-label="状态图例与计数"><span class="legend-group"><span class="legend-heading">本次变更</span><span class="lg legend-added"><i class="st-cell st-added"><b class="ic ic-add"></b></i>新增<em data-legend-count="added">0</em></span><span class="lg legend-manual-added"><i class="st-cell st-manual-added"><b class="ic ic-add"></b></i>手动新增<em data-legend-count="manual-added">0</em></span><span class="lg legend-replaced"><i class="st-cell st-replaced"><b class="ic ic-swap"></b></i>替换<em data-legend-count="replaced">0</em></span><span class="lg legend-moved" title="普通原始元素的展示位置变化；实际应用仍依据有效元素顺序"><i class="st-cell st-moved"><b class="ic ic-move"></b></i>移动<em data-legend-count="moved">0</em></span><span class="lg legend-moved" title="已移除原始元素的展示位置变化，不写入结果"><i class="st-cell st-moved"><b class="ic ic-move"></b></i>幽灵移动<em data-legend-count="ghost-moved">0</em></span><span class="lg legend-removed"><i class="st-cell st-removed"><b class="ic ic-cut"></b></i>删除<em data-legend-count="removed">0</em></span></span><span class="legend-group"><span class="legend-heading">提示</span><span class="lg legend-duplicate"><i class="st-cell st-duplicate"><b class="ic ic-stack"></b></i>重复<em data-legend-count="duplicate">0</em></span><span class="lg legend-empty"><i class="st-cell st-empty"><b class="ic ic-void"></b></i>空元素<em data-legend-count="empty">0</em></span><span class="lg legend-match"><i class="st-cell st-match"><b class="ic ic-check"></b></i>匹配<em data-legend-count="match">0</em></span></span></div>
        <div class="element-list" id="elementList"></div><div class="element-validation" id="elementValidation" role="status"></div><div id="pendingElements" hidden></div>
      </div><aside class="elements-compare" aria-label="元素变更对照">
        <details class="element-raw" open><summary>元素变更对照</summary><p class="comparison-hint">${linkLegendMarkup()}</p><div class="raw-comparison"><div class="raw-pane"><label>打开时的值</label><pre id="elementOriginal"></pre></div><div class="raw-pane"><label>编辑后的值 · 含删除标记</label><pre id="elementPreview"></pre></div></div><details class="serialized-result" open><summary>实际结果字符串（不含标记或删除项）</summary><pre id="elementSerialized"></pre></details></details>
      </aside></div><div class="dialogfoot element-footer"><span id="elementStatus" role="status">仅更新演示数据</span><button class="btn" id="cancelDialog" title="丢弃弹窗内修改，保留页面快照">取消修改</button><button class="btn primary" id="applyElements" disabled>应用（演示）</button></div></div>`;
    container.classList.add('open');
    bindRelated();
    container.querySelector('#closeDialog').onclick=options.close;container.querySelector('#cancelDialog').onclick=options.close;
    container.querySelector('#addElement').onclick=()=>{const row={id:++sequence,value:''};session.rows.push(row);session.manualAddedIds.add(row.id);session.displayOrder.push(row.id);session.fx={kind:'enter',id:row.id};renderRows(row.id);};
    container.querySelector('#restoreElements').onclick=()=>{session.rows=session.originalRows.map(row=>({...row}));session.displayOrder=session.originalRows.map(row=>row.id);session.adoptionMap.clear();session.manualAddedIds.clear();session.removalAdoptions.clear();session.deletedDrafts.clear();session.dragging=null;session.hoveredId=null;session.focusedId=null;renderRows(session.rows[0]?.id);if(!session.rows.length)container.querySelector('#addElement').focus();};
    container.querySelector('#applyElements').onclick=async()=>{
      if(!session||!valid()||container.querySelector('#applyElements').disabled)return;
      const current=session,plan=session.plan;
      const applyButton=container.querySelector('#applyElements');
      applyButton.disabled=true;
      try {
      let allowed=current.beforeApply?current.beforeApply(plan):true;
      if(allowed&&typeof allowed.then==='function')allowed=await allowed;
      if(session!==current)return;
      if(allowed===false){
        container.querySelector('#elementValidation').textContent='当前值已变化，草稿仍保留。为避免覆盖新值，本次未应用。可复制下方草稿，取消后按最新值重新编辑。';
        const latest=current.currentSnapshot?.();
        if(latest){let notice=container.querySelector('#editorLatestSnapshot');if(!notice){notice=document.createElement('pre');notice.id='editorLatestSnapshot';notice.className='codevalue';container.querySelector('#elementValidation').after(notice);}notice.textContent='最新当前值：\n'+(latest.exists?latest.raw:'（不存在）');}
        return;
      }
      if(session!==current)return;
      let result=current.onApply(plan.afterRaw,plan.audit,plan);
      if(result&&typeof result.then==='function')result=await result;
      if(session!==current||result===false||result?.status==='stale'||result?.verified===false)return;
      current.close();current.onApplied?.(plan);
      } catch(error) {
        if(session===current)container.querySelector('#elementValidation').textContent=error.message||'应用失败，全部草稿仍保留。';
      } finally {
        if(session===current)applyButton.disabled=!valid();
      }
    };
    renderRows();container.querySelector('#closeDialog').focus();
  }
  function toggleRemoval(button,editingSession) {
    if(!session||session!==editingSession||session.whole||session.selectionPlan?.valid===false||!button.isConnected||!session.suggestionButtons.has(button)||!session.container.querySelector('#pendingElements').contains(button))return;
    const beforeIndex=Number(button.dataset.removePending);
    if(!Number.isInteger(beforeIndex)||beforeIndex<0)return;
    const original=session.originalRows[beforeIndex];
    if(!original||button.dataset.removalId!==String(original.id))return;
    const adopted=session.removalAdoptions.has(original.id);
    if(button.dataset.removalAdopted!==String(adopted)||button.getAttribute('aria-pressed')!==String(adopted))return;
    const candidate=candidatePlan();
    if(candidate.valid===false||!candidate.operations.some(op=>op.type==='remove'&&op.beforeIndex===beforeIndex&&op.elementId===original.id&&!!op.adopted===adopted&&op.value===original.value))return;
    const row=session.rows.find(row=>row.id===original.id);
    if(adopted){
      if(row||session.displayOrder.filter(id=>id===original.id).length!==1)return;
      session.rows.push({...original});
      syncRowsToDisplayOrder();
      session.removalAdoptions.delete(original.id);
      session.deletedDrafts.delete(original.id);
      session.fx={kind:'restore',id:original.id};session.suggestFx={type:'remove',index:beforeIndex,adopt:false};
      renderRows(original.id);
      return;
    }
    if(!row||row.value!==original.value||session.displayOrder.filter(id=>id===original.id).length!==1)return;
    session.removalAdoptions.add(original.id);
    removeLiveRow(original.id);
    session.fx={kind:'remove',id:original.id};session.suggestFx={type:'remove',index:beforeIndex,adopt:true};
    renderRows(session.rows[0]?.id);
  }
  // Cancel a deletion from the trace row itself: restore its last live draft at the current display position.
  function restoreGhost(id) {
    const original=session.originalById.get(id);
    if(!original||session.rows.some(row=>row.id===id)||session.displayOrder.filter(item=>item===id).length!==1)return;
    session.rows.push({...(session.deletedDrafts.get(id)??original)});
    syncRowsToDisplayOrder();
    session.deletedDrafts.delete(id);
    session.removalAdoptions.delete(id);
    session.fx={kind:'restore',id};
    renderRows(id);
  }
  function adoptPending(index,button,editingSession) {
    if(!session||session!==editingSession||session.whole||session.selectionPlan?.valid===false||!button.isConnected||!session.suggestionButtons.has(button)||!session.container.querySelector('#pendingElements').contains(button))return;
    const mode=session.appendModes[index];
    if(!isAppendMode(mode))return;
    const adopted=session.adoptionMap.get(index);
    if(adopted){
      const row=session.rows.find(row=>row.id===adopted.elementId);
      if(row&&row.value===adopted.adoptedRaw){session.fx={kind:'none'};session.suggestFx={type:'add',index,adopt:false};removeLiveRow(row.id);renderRows(session.rows[0]?.id);}
      return;
    }
    const candidate=selectionInfo(serialized(),session.appendValues).missing.find(part=>part.index===index);
    if(!candidate)return;
    const row={id:++sequence,value:candidate.value};
    session.rows.push(row);
    if(mode==='+append')session.displayOrder.unshift(row.id);else session.displayOrder.push(row.id);
    session.adoptionMap.set(index,{elementId:row.id,adoptedRaw:row.value,mode});
    syncRowsToDisplayOrder();
    session.fx={kind:'enter',id:row.id};session.suggestFx={type:'add',index,adopt:true};
    renderRows(row.id);
  }
  function move(id,offset) {
    const index=session.displayOrder.indexOf(id),target=index+offset;
    if(index<0||target<0||target>=session.displayOrder.length)return;
    [session.displayOrder[index],session.displayOrder[target]]=[session.displayOrder[target],session.displayOrder[index]];
    syncRowsToDisplayOrder();session.fx={kind:'move',id};session.focusPrefer=offset>0?'down':'up';renderRows(id);
  }
  function rowDisplayOrder() {
    const liveById=new Map(session.rows.map(row=>[row.id,row]));
    return session.displayOrder.map((id,displayIndex)=>{
      const row=liveById.get(id);
      return row?{row,displayIndex,ghost:false}:{row:session.deletedDrafts.get(id)??session.originalById.get(id),displayIndex,ghost:true};
    });
  }
  function elementActionsMarkup(id,displayIndex,total,ghost=false) {
    const position=displayIndex+1,qualifier=ghost?' 个已移除元素，仅调整展示位置':' 个元素';
    const action=ghost?'<button class="ibtn ibtn-restore" data-restore-ghost="'+id+'" aria-label="取消删除第 '+position+' 个元素，恢复删除前编辑的内容到当前位置" title="取消删除 · 恢复删除前编辑的内容（保持当前位置）"><b class="ic ic-undo" aria-hidden="true"></b></button>':'<button class="ibtn element-delete" data-delete="'+id+'" aria-label="删除第 '+position+' 个元素" title="删除此元素"><b class="ic ic-trash" aria-hidden="true"></b></button>';
    return '<div class="element-actions"'+(session.whole?' hidden':'')+'><button class="ibtn" data-move-up="'+id+'" aria-label="上移第 '+position+qualifier+'" title="'+(ghost?'上移 · 仅调整已移除元素的展示位置':'上移 (Alt+↑)')+'"'+(displayIndex===0?' disabled':'')+'><b class="ic ic-up" aria-hidden="true"></b></button><button class="ibtn" data-move-down="'+id+'" aria-label="下移第 '+position+qualifier+'" title="'+(ghost?'下移 · 仅调整已移除元素的展示位置':'下移 (Alt+↓)')+'"'+(displayIndex===total-1?' disabled':'')+'><b class="ic ic-down" aria-hidden="true"></b></button>'+action+'</div>';
  }
  function ghostRowMarkup(row,displayIndex,total) {
    const beforeIndex=session.originalRows.findIndex(original=>original.id===row.id);
    return `<div class="element-ghost-row${displayMoved(row.id)?' is-moved':''}" data-element-id="${row.id}" draggable="${!session.whole}" data-duplicate-value="${escapeRaw(row.value)}"><span class="element-order" title="原始位置：第 ${beforeIndex+1} 项">${displayIndex+1}</span><span class="element-ghost-body"><span class="element-ghost-value" data-raw-value="${escape(row.value)}">${row.value===''?'<span class="empty-element">（空元素）</span>':escape(row.value)}</span><span class="element-ghost-state">${valueHint(row.value,session.whole)}${statusBadge('removed','本次编辑已移除')}${displayMoved(row.id)?statusBadge('moved','幽灵移动'):''}<span class="element-ghost-note">仅展示痕迹 · 不写入结果</span></span></span>${elementActionsMarkup(row.id,displayIndex,total,true)}</div>`;
  }
  function renderRows(focusId) {
    clearDragPreview();
    const root=session.container;
    const fx=session.fx,prefer=session.focusPrefer||'up',before=fx?layoutSnapshot(root,fx):null;
    // Adopting/undoing a suggestion keeps keyboard focus on that suggestion's button (re-created by update()).
    const keepSuggestion=session.suggestFx?{...session.suggestFx}:null;
    session.fx=null;session.focusPrefer=null;
    session.hoveredId = null;
    session.hoveredElement = null;
    session.focusedId = null;
    const ordered=rowDisplayOrder(),total=ordered.length;
    root.querySelector('#elementList').innerHTML=ordered.map(({row,displayIndex,ghost})=>ghost?ghostRowMarkup(row,displayIndex,total):`<div class="element-row" data-element-id="${row.id}" draggable="${!session.whole}">
      <span class="element-order" title="当前顺序">${displayIndex+1}</span><label class="element-input-wrap"><span class="sr-only">第 ${displayIndex+1} ${session.whole?'条字符串':'个元素'}</span>${session.whole?`<textarea class="element-input" rows="3" spellcheck="false" data-element-input="${row.id}">${escape(row.value)}</textarea>`:`<textarea class="element-input is-line" rows="1" wrap="soft" spellcheck="false" placeholder="（空元素）" data-element-input="${row.id}">${escape(row.value)}</textarea>`}</label>
      <span class="element-states"><span class="st-plate element-plate" aria-hidden="true"></span><span class="element-match-state"></span><span class="element-change-state"></span></span>${elementActionsMarkup(row.id,displayIndex,total)}</div>`).join('')+(session.whole||!session.rows.length&&!ordered.length?'':'<div class="element-drop-end" aria-hidden="true">放到末尾</div>')+(session.rows.length?'':'<div class="empty">结果列表为空。确认应用将移除变量；保留一个空元素则保存为空值。</div>');
    const fit=input=>{if(!input.classList.contains('is-line')||fieldSizing)return;input.style.height='auto';if(input.scrollHeight>0)input.style.height=input.scrollHeight+2+'px';};
    root.querySelectorAll('[data-element-input]').forEach(input=>{
      const id=Number(input.dataset.elementInput),row=session.rows.find(row=>row.id===id);input.value=row.value;
      let pendingEdit=null;
      input.onbeforeinput=event=>{pendingEdit=textareaEditRange(input,event,row.value);};
      input.addEventListener('blur',()=>{pendingEdit=null;});
      input.oninput=()=>{
        const edit=pendingEdit;pendingEdit=null;
        const value=preserveRawLineEndings(row.value,input.value,edit);
        if(value!==row.value)clearAdoptionForId(id);row.value=value;fit(input);update();};
      input.onkeydown=e=>{
        if(e.altKey&&['ArrowUp','ArrowDown'].includes(e.key)&&!session.whole){e.preventDefault();move(id,e.key==='ArrowUp'?-1:1);}
      };
      fit(input);
    });
    root.querySelectorAll('[data-move-up]').forEach(button=>button.onclick=()=>move(Number(button.dataset.moveUp),-1));
    root.querySelectorAll('[data-move-down]').forEach(button=>button.onclick=()=>move(Number(button.dataset.moveDown),1));
    root.querySelectorAll('[data-delete]').forEach(button=>button.onclick=()=>{const deletedId=Number(button.dataset.delete),index=session.rows.findIndex(row=>row.id===deletedId),keepsGhost=session.originalById.has(deletedId);session.fx=keepsGhost?{kind:'remove',id:deletedId}:{kind:'none'};removeLiveRow(deletedId);renderRows(session.rows[Math.min(index,session.rows.length-1)]?.id);if(!session.rows.length)root.querySelector('#addElement').focus();});
    root.querySelectorAll('[data-restore-ghost]').forEach(button=>button.onclick=()=>restoreGhost(Number(button.dataset.restoreGhost)));
    const list=root.querySelector('#elementList');
    let dragPositions=[],dragHeight=0,dragSlot=-1;
    const clearDrop=()=>{
      list.querySelectorAll('.drop-target').forEach(row=>row.classList.remove('drop-target','is-noop'));
      list.querySelectorAll('.will-shift-up,.will-shift-down').forEach(row=>row.classList.remove('will-shift-up','will-shift-down'));
      const pill=list.querySelector('.drop-pill');if(pill)pill.hidden=true;
      const gap=list.querySelector('.element-drop-gap');if(gap)gap.hidden=true;
      list.querySelectorAll('[data-element-id]').forEach(row=>row.style.removeProperty('--reorder-y'));
    };
    // Preview a complete row-sized slot. Original geometry drives hit-testing,
    // so the animated neighbours cannot change the target under the pointer.
    const markDrop=(zone,targetId)=>{
      const order=session.displayOrder,from=order.indexOf(session.dragging);if(from<0)return;
      const rest=order.filter(id=>id!==session.dragging),to=targetId===null?rest.length:rest.indexOf(targetId);if(to<0)return;
      const noop=to===from;clearDrop();zone.classList.add('drop-target');zone.classList.toggle('is-noop',noop);
      if(!noop){
        const [lo,hi,cls]=to<from?[to,from-1,'will-shift-down']:[from+1,to,'will-shift-up'];
        order.slice(lo,hi+1).forEach(id=>{
          const row=list.querySelector(`[data-element-id="${id}"]`);if(!row)return;
          row.classList.add(cls);row.style.setProperty('--reorder-y',(to<from?dragHeight:-dragHeight)+'px');
        });
      }
      const remaining=dragPositions.filter(row=>row.id!==session.dragging);
      const gapTop=remaining.slice(0,to).reduce((sum,row)=>sum+row.height,0);
      let gap=list.querySelector('.element-drop-gap');
      if(!gap){gap=document.createElement('div');gap.className='element-drop-gap';gap.setAttribute('aria-hidden','true');list.appendChild(gap);}
      gap.hidden=false;gap.style.top=gapTop+'px';gap.style.height=dragHeight+'px';
      let pill=list.querySelector('.drop-pill');
      if(!pill){pill=document.createElement('div');pill.className='drop-pill';pill.setAttribute('aria-hidden','true');list.appendChild(pill);}
      pill.hidden=false;pill.classList.toggle('is-noop',noop);
      pill.textContent=noop?'位置不变':`${to<from?'↑':'↓'} ${targetId===null?'放到末尾 · 第 ':'放到第 '}${to+1} 位`;
      pill.style.top=Math.max(gapTop-11,2)+'px';
    };
    root.querySelectorAll('.element-row,.element-ghost-row').forEach(element=>{
      const id=Number(element.dataset.elementId);
      const beginDrag=(e,preservePointer=false)=>{if(!preservePointer)clearDragPreview();clearDrop();session.dragging=id;if(e.dataTransfer){e.dataTransfer.setData('text/plain',String(id));e.dataTransfer.effectAllowed='move';}
        list.querySelectorAll('[data-element-id]').forEach(row=>{row.getAnimations?.().forEach(animation=>animation.cancel());row.classList.remove('fx-move');});
        dragPositions=[...list.querySelectorAll(':scope > [data-element-id]')].map(row=>({id:Number(row.dataset.elementId),height:row.getBoundingClientRect().height}));
        dragHeight=element.getBoundingClientRect().height;dragSlot=session.displayOrder.indexOf(id);session.dragTarget=undefined;
        startRowDragPreview(element,e);dragSource=element;
        dragLayout={clear:clearDrop,preview:(top,listTop)=>{
          const remaining=dragPositions.filter(row=>row.id!==id),relativeTop=top-listTop;
          let cursor=0,index=0;
          for(const row of remaining){if(relativeTop<cursor+row.height/2)break;cursor+=row.height;index++;}
          // A small dead band stops pointer noise from reversing the gap at
          // a boundary while its neighbours are still easing into place.
          if(Math.abs(index-dragSlot)===1){
            const boundaryIndex=Math.min(index,dragSlot),boundary=remaining.slice(0,boundaryIndex).reduce((sum,row)=>sum+row.height,0)+remaining[boundaryIndex].height/2;
            if((index>dragSlot&&relativeTop<boundary+6)||(index<dragSlot&&relativeTop>boundary-6))index=dragSlot;
          }
          const target=remaining[index]?.id??null;
          if(session.dragTarget===target)return;
          dragSlot=index;session.dragTarget=target;
          const zone=target===null?list.querySelector('.element-drop-end'):list.querySelector(`[data-element-id="${target}"]`);
          if(zone)markDrop(zone,target);
        }};
        element.classList.add('is-dragging');list.classList.add('is-dragging-list');};
      element.ondragstart=e=>{if(pointerCleanup||session.whole||e.target.matches('input,textarea,button')){e.preventDefault();return;}beginDrag(e);};
      element.ondragend=()=>{if(session)session.dragging=null;clearDragPreview();clearDrop();};
      const handle=element.querySelector('.element-order');
      handle.addEventListener('pointerdown',down=>{
        if(session.whole||down.button!==0||down.isPrimary===false)return;
        down.preventDefault();clearDragPreview();
        const editingSession=session,pointerId=down.pointerId;let started=false;
        const finish=(event,cancel=false)=>{
          if(event?.pointerId!==undefined&&event.pointerId!==pointerId)return;
          if(!session||session!==editingSession){clearDragPreview();return;}
          if(started&&!cancel&&event)moveDragPreview(event);
          const target=session.dragTarget;
          if(started)session.dragging=null;
          clearDragPreview();clearDrop();
          if(started&&!cancel&&target!==undefined&&target!==id)dropBefore(id,target);
        };
        const pointerMove=event=>{
          if(event.pointerId!==pointerId)return;
          event.preventDefault();
          if(!started){if(Math.hypot(event.clientX-down.clientX,event.clientY-down.clientY)<4)return;started=true;beginDrag(down,true);}
          moveDragPreview(event);
        };
        const pointerUp=event=>finish(event),pointerCancel=event=>finish(event,true),blur=()=>finish(null,true);
        const escape=event=>{if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();finish(null,true);}};
        document.addEventListener('pointermove',pointerMove,{capture:true,passive:false});
        document.addEventListener('pointerup',pointerUp,true);document.addEventListener('pointercancel',pointerCancel,true);
        document.addEventListener('keydown',escape,true);window.addEventListener('blur',blur);
        handle.addEventListener('lostpointercapture',pointerCancel);
        pointerCleanup=()=>{
          document.removeEventListener('pointermove',pointerMove,true);document.removeEventListener('pointerup',pointerUp,true);
          document.removeEventListener('pointercancel',pointerCancel,true);document.removeEventListener('keydown',escape,true);window.removeEventListener('blur',blur);
          handle.removeEventListener('lostpointercapture',pointerCancel);
          try{if(handle.hasPointerCapture?.(pointerId))handle.releasePointerCapture(pointerId);}catch{}
        };
        try{handle.setPointerCapture?.(pointerId);}catch{}
      });
    });
    // Dropping always inserts the dragged element before the target (V3.6); the end strip appends after the last visible row.
    const dropBefore=(dragged,targetId)=>{
      const from=session.displayOrder.indexOf(dragged);if(from<0)return false;
      const order=session.displayOrder.filter(id=>id!==dragged);
      const to=targetId===null?order.length:order.indexOf(targetId);if(to<0||to===from)return false;
      order.splice(to,0,dragged);session.displayOrder=order;syncRowsToDisplayOrder();session.fx={kind:'move',id:dragged};renderRows(dragged);return true;
    };
    // Animated neighbours expose empty list space. It remains a valid drop
    // surface even when no row is directly underneath the lifted element.
    list.ondragover=e=>{if(session.dragging!==null)e.preventDefault();};
    list.ondrop=e=>{
      if(session.dragging===null||session.dragTarget===undefined)return;
      e.preventDefault();const dragged=session.dragging,target=session.dragTarget;
      session.dragging=null;clearDragPreview();clearDrop();dropBefore(dragged,target);
    };
    root.querySelectorAll('.element-row,.element-ghost-row').forEach(element=>{
      const id=Number(element.dataset.elementId);
      element.ondragover=e=>{if(session.dragging!==null)e.preventDefault();};
      element.ondrop=e=>{if(session.dragging===null)return;e.preventDefault();const dragged=session.dragging,target=session.dragTarget===undefined?id:session.dragTarget;session.dragging=null;clearDragPreview();clearDrop();if(dragged===target)return;dropBefore(dragged,target);};
    });
    const endZone=root.querySelector('.element-drop-end');
    if(endZone){
      endZone.ondragover=e=>{if(session.dragging!==null)e.preventDefault();};
      endZone.ondrop=e=>{if(session.dragging===null)return;e.preventDefault();const dragged=session.dragging,target=session.dragTarget===undefined?null:session.dragTarget;session.dragging=null;clearDragPreview();clearDrop();if(target===null&&session.displayOrder[session.displayOrder.length-1]===dragged)return;dropBefore(dragged,target);};
    }
    update();
    playLayoutFx(root,before,fx);
    if(keepSuggestion){
      const selector=keepSuggestion.type==='add'?`[data-selected-index="${keepSuggestion.index}"]`:`[data-pending-before-index="${keepSuggestion.index}"]`;
      const kept=root.querySelector(`#pendingElements ${selector} button`);
      if(kept){kept.focus();return;}
    }
    if(focusId)(root.querySelector(`[data-element-input="${focusId}"]`)||root.querySelector(`.element-ghost-row[data-element-id="${focusId}"] [data-move-${prefer}]:not(:disabled)`)||root.querySelector(`.element-ghost-row[data-element-id="${focusId}"] [data-move-up]:not(:disabled)`)||root.querySelector(`.element-ghost-row[data-element-id="${focusId}"] [data-move-down]:not(:disabled)`))?.focus();
  }
  function dismiss() {
    clearDragPreview();
    if (session) {
      session.container.querySelectorAll('.is-related-highlight,.is-duplicate-highlight,.is-link-source').forEach(element=>element.classList.remove('is-related-highlight','is-duplicate-highlight','is-link-source'));
      for (const [type, handler] of Object.entries(session.relatedHandlers || {})) session.container.removeEventListener(type, handler);
    }
    session=null;
  }
  return {highlight,staticMarkup,historyMarkup,selectionInfo,selectionMarkup,pendingMarkup,comparisonMarkup,linkLegendMarkup,bindComparison,open,dismiss};
})();

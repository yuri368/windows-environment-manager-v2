/* Configuration presentation only. Model writes belong to the supplied change callback. */
(function () {
  'use strict';
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const normalized = raw => String(raw).replace(/\r\n?/g, '\n');
  const fact = value => value == null ? '（无）' : String(value) === '' ? '（空字符串）' : String(value).replace(/\r/g, '\\r').replace(/\n/g, '\\n');

  // Same textarea boundary mapping as the element editor: untouched raw line endings survive edits.
  function preserveRaw(raw, text, edit) {
    const boundaries = [0]; let view = '';
    for (let i = 0; i < raw.length;) {
      if (raw[i] === '\r') { i += raw[i + 1] === '\n' ? 2 : 1; view += '\n'; }
      else view += raw[i++];
      boundaries.push(i);
    }
    const next = normalized(text);
    const splice = (start, end, inserted) => {
      let before = raw.slice(0, boundaries[start]); const after = raw.slice(boundaries[end]);
      if (before.endsWith('\r') && (inserted + after).startsWith('\n')) before = before.slice(0, -1) + '\n';
      const result = before + inserted + after;
      return normalized(result) === next ? result : next;
    };
    if (edit && edit.raw === raw && edit.value === view) {
      const length = next.length - view.length + edit.end - edit.start;
      if (length >= 0 && next.slice(0, edit.start) === view.slice(0, edit.start) && next.slice(edit.start + length) === view.slice(edit.end))
        return splice(edit.start, edit.end, next.slice(edit.start, edit.start + length));
    }
    if (view === next) return raw;
    let prefix = 0, suffix = 0;
    while (prefix < view.length && prefix < next.length && view[prefix] === next[prefix]) prefix++;
    while (suffix < view.length - prefix && suffix < next.length - prefix && view[view.length - 1 - suffix] === next[next.length - 1 - suffix]) suffix++;
    return splice(prefix, view.length - suffix, next.slice(prefix, next.length - suffix));
  }
  function beforeEdit(input, event, raw) {
    if (event.isComposing) return null;
    const value = input.value, type = event.inputType;
    let start = input.selectionStart, end = input.selectionEnd;
    if (!Number.isInteger(start) || !Number.isInteger(end)) return null;
    if (type === 'deleteContentBackward' && start === end) {
      if (!start) return null;
      start--;
      if (start > 0 && /[\uDC00-\uDFFF]/.test(value[start]) && /[\uD800-\uDBFF]/.test(value[start - 1])) start--;
    } else if (type === 'deleteContentForward' && start === end) {
      if (end === value.length) return null;
      end++;
      if (end < value.length && /[\uD800-\uDBFF]/.test(value[end - 1]) && /[\uDC00-\uDFFF]/.test(value[end])) end++;
    } else if (!['insertText','insertLineBreak','insertParagraph','insertFromPaste','insertFromDrop','insertReplacementText','deleteContentBackward','deleteContentForward','deleteByCut','deleteByDrag'].includes(type)) return null;
    return {raw, value, start, end};
  }
  function bindText(input, read, write) {
    if (!input) return;
    let pending;
    // Assigning value rather than textarea HTML also preserves a leading newline on first render.
    input.value = normalized(read());
    input.onbeforeinput = event => { pending = beforeEdit(input, event, String(read())); };
    input.oninput = () => { const next = preserveRaw(String(read()), input.value, pending); pending = null; write(next); };
  }
  function listKeys(list, selector) {
    list.onkeydown = event => {
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || event.isComposing || !['ArrowUp','ArrowDown','Home','End'].includes(event.key)) return;
      const buttons = [...list.querySelectorAll(selector)], current = event.target.closest(selector), index = buttons.indexOf(current);
      if (index < 0) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : Math.max(0, Math.min(buttons.length - 1, index + (['ArrowDown','ArrowRight'].includes(event.key) ? 1 : -1)));
      buttons[next]?.focus();
    };
  }
  const sessions = new WeakMap();
  function sessionFor(editor) {
    if (!sessions.has(editor)) sessions.set(editor, {draft:null, conflict:null});
    return sessions.get(editor);
  }
  function clearDraft(editor) { sessions.delete(editor); }
  function hasDraft(editor) { return !!sessions.get(editor)?.draft; }
  function suspendDraft(editor) { const draft=sessions.get(editor)?.draft; if(draft)draft.suspended=true; }
  function render(options) {
    const {root, apps, editor, modeLabels = {}, change, rerender, status = () => {}} = options;
    const app = apps[editor.app], profile = app?.profiles.find(p => p.name === editor.profile) || app?.profiles[0];
    const session = sessionFor(editor), draft = session.draft;
    if (!profile) { root.innerHTML = '<p class="helpnote">此应用尚无可编辑方案。</p>'; return; }
    const variable = profile.vars[editor.varIdx];
    const sameApp = root.dataset.editorApp === String(editor.app), sameProfile = sameApp && root.dataset.editorProfile === profile.name;
    const scrolls = [...root.querySelectorAll('[data-editor-list],.editor-mid')].map(node => ({list: node.dataset.editorList, top: node.scrollTop, left: node.scrollLeft}));
    const navOpen = root.querySelector('.cfg-navigation')?.open ?? !(root.ownerDocument.defaultView.matchMedia?.('(max-width:700px)').matches);
    root.dataset.editorApp = String(editor.app); root.dataset.editorProfile = profile.name; root.dataset.editorVariable = String(editor.varIdx);
    const choices = v => (options.choices ? options.choices(v) : v.choices || []).map(c => typeof c === 'string' ? {label:c, value:c} : {label:String(c.label ?? ''),value:String(c.value ?? '')});
    const currentChoices = variable ? choices(variable) : [];
    const field = (id, label, body, className = '') => `<div class="field ${className}"><label for="${id}">${label}</label>${body}</div>`;
    const belongs = (d, kind, a, p) => d?.kind === kind && (kind === 'app' || d.app === a) && (!['variable','choice'].includes(kind) || (kind === 'choice' ? d.variable === p : d.profile === p));
    const atOrigin = draft && !draft.suspended && draft.originApp === app && draft.originProfile === profile && draft.originVariable === (variable ?? null);
    const activeDraft = atOrigin && draft.kind !== 'choice' && ((draft.kind === 'app') || (draft.app === app && (draft.kind === 'profile' || draft.profile === profile)));
    const containerDraft = activeDraft && ['app','profile'].includes(draft.kind);
    const variableStep = activeDraft && (draft.kind === 'variable' || draft.stage === 'variable');
    const draftRules = variableStep ? `<details class="cfg-create-options" id="cfgCreateOptions"><summary>其他属性（可选）</summary><section class="cfg-variable cfg-inline-form cfg-pending-rules" id="editorInspector" aria-label="待创建变量属性"><div class="cfg-form-heading"><h3>作用域与应用模式</h3></div><div class="cfg-rules">
      ${field('cfgScope','作用域',`<select id="cfgScope"><option value="User" ${draft.scope === 'User' ? 'selected' : ''}>用户 · User</option><option value="System" ${draft.scope === 'System' ? 'selected' : ''}>系统 · System</option></select>`)}
      ${field('cfgMode','应用模式',`<select id="cfgMode">${Object.entries(modeLabels).map(([key,label])=>`<option value="${escape(key)}" ${draft.mode === key ? 'selected' : ''} ${draft.inheritedWhole && key !== 'replace' ? 'disabled' : ''}>${escape(label)} · ${escape(key)}</option>`).join('')}</select>`)}
    </div><p class="cfg-hint" id="cfgCreateHint" ${draft.inheritedWhole ? '' : 'hidden'}>${draft.inheritedWhole ? '同一目标已定义整体处理，沿用 replace 模式。' : ''}</p></section></details>` : '';
    const createForm = d => `<form id="cfgCreateForm" class="cfg-create-form cfg-create-${d.kind}" aria-label="${d.stage === 'app-name' ? '新增应用' : d.stage === 'profile-name' ? '新增方案' : '新增变量'}"><div class="cfg-create-fields">
      ${d.stage === 'app-name' ? field('cfgCreateAppName','应用名称',`<input id="cfgCreateAppName" autocomplete="off" value="${escape(d.appName)}">`) : ''}
      ${d.stage === 'profile-name' ? field('cfgCreateProfileName','方案名称',`<input id="cfgCreateProfileName" autocomplete="off" value="${escape(d.profileName)}">`) : ''}
      ${d.kind === 'variable' || d.stage === 'variable' ? field('cfgCreateName','变量名',`<input id="cfgCreateName" autocomplete="off" spellcheck="false" value="${escape(d.name)}">`) : ''}
      </div><p id="cfgCreateError" class="cfg-create-error" role="alert" aria-live="polite">${escape(d.error || '')}</p><div class="cfg-create-actions"><button type="submit" class="cfg-confirm" id="cfgCreateSubmit">${d.stage === 'app-name' ? '创建应用' : d.stage === 'profile-name' ? '创建方案' : '添加变量'}</button><button type="button" class="cfg-text-action" id="cfgCreateCancel">取消</button></div></form>`;
    const conflict = (kind, a, p) => belongs(session.conflict,kind,a,p) ? `<div class="cfg-create-conflict" id="cfgCreateConflict"><span>已有未完成的新增。</span><button type="button" class="cfg-text-action" id="cfgCreateResume">继续填写</button><button type="button" class="cfg-text-action" id="cfgCreateDiscard">放弃并新建</button></div>` : '';
    const form = v => `<section class="cfg-variable cfg-inline-form" id="editorInspector" aria-label="${escape(profile.name)} / ${escape(v.name)} 配置属性">
      <div class="cfg-form-heading"><h3><data id="cfgName" value="${escape(v.name)}">${escape(v.name)}</data></h3><div class="cfg-facts" aria-label="当前选定值信息"><span>来源 <strong id="cfgSource">${escape(fact(v.source))}</strong></span><span class="cfg-saved-fact">已保存 <strong id="cfgSaved" title="${escape(v.savedValue ?? '')}">${escape(fact(v.savedValue))}</strong></span></div></div>
      <div class="cfg-rules">
        ${field('cfgScope','作用域',`<select id="cfgScope"><option value="User" ${v.scope === 'User' ? 'selected' : ''}>用户 · User</option><option value="System" ${v.scope === 'System' ? 'selected' : ''}>系统 · System</option></select>`)}
        ${field('cfgMode','应用模式',`<select id="cfgMode">${Object.entries(modeLabels).map(([key, label]) => `<option value="${escape(key)}" ${v.mode === key ? 'selected' : ''} ${v.special && key !== 'replace' ? 'disabled' : ''}>${escape(label)} · ${escape(key)}</option>`).join('')}</select>`)}
        <div class="cfg-switches"><label class="checklabel" for="cfgSpecial"><input type="checkbox" id="cfgSpecial" ${v.special ? 'checked' : ''}>整体处理 <span class="cfg-field-key">special</span></label><label class="checklabel" for="cfgEditable"><input type="checkbox" id="cfgEditable" ${v.editable !== false ? 'checked' : ''}>允许运行时自由输入 <span class="cfg-field-key">editable</span></label></div>
      </div>
      <div class="cfg-default-block"><div class="cfg-default-heading"><label for="cfgDefault">默认值 <span class="cfg-field-key">default_value</span></label>${options.openElements ? '<button type="button" class="cfg-text-action" id="cfgEditElements">按元素编辑</button>' : ''}</div>
      <textarea id="cfgDefault" class="cfg-raw-input" rows="2" spellcheck="false" aria-describedby="cfgRawHelp"></textarea>
      <p class="cfg-hint" id="cfgRawHelp">保留空白、空值与换行。${v.special ? '整体处理：分号属于字符串内容。' : '普通变量：分号分隔元素。'}</p>
      </div>
      <section class="cfg-choices" id="cfgChoices" aria-labelledby="cfgChoicesHeading"><div class="cfg-section-heading"><h3 id="cfgChoicesHeading">候选值 <span class="cfg-count">${currentChoices.length}</span></h3><button type="button" class="cfg-text-action" id="cfgAddChoice">${belongs(draft,'choice',app,v)&&draft.suspended?'继续新增候选':'＋ 新增候选'}</button></div>
        <div class="cfg-choice-list"><div class="cfg-choice-columns" aria-hidden="true"><span>序号</span><span>标签</span><span>完整值</span><span>操作</span></div>${currentChoices.length ? currentChoices.map((choice, index) => `<div class="cfg-choice-row" data-cfg-choice="${index}"><span class="cfg-choice-order" aria-hidden="true">${index + 1}</span><div class="cfg-choice-cell">${field(`cfgChoiceLabel${index}`,'标签',`<textarea rows="1" id="cfgChoiceLabel${index}" data-choice-label="${index}" spellcheck="false" placeholder="候选标签" aria-label="第 ${index + 1} 个候选值的标签"></textarea>`)}</div><div class="cfg-choice-cell">${field(`cfgChoiceValue${index}`,'值',`<textarea rows="1" id="cfgChoiceValue${index}" data-choice-value="${index}" spellcheck="false" placeholder="（空字符串）" aria-label="第 ${index + 1} 个候选值的完整值"></textarea>`)}</div><div class="cfg-choice-actions"><button type="button" data-choice-up="${index}" ${index === 0 ? 'disabled' : ''} aria-label="上移第 ${index + 1} 个候选值">上移</button><button type="button" data-choice-down="${index}" ${index === currentChoices.length - 1 ? 'disabled' : ''} aria-label="下移第 ${index + 1} 个候选值">下移</button><button type="button" data-choice-remove="${index}" aria-label="删除第 ${index + 1} 个候选值">删除</button></div></div>`).join('') : '<p class="cfg-empty">暂无候选值。可添加标签与完整值。</p>'}</div>
      <div id="cfgChoiceDraftSlot">${belongs(draft,'choice',app,v)&&!draft.suspended ? `<form id="cfgChoiceDraft" class="cfg-choice-row cfg-choice-draft" aria-label="新增候选"><span class="cfg-choice-order" aria-hidden="true">＋</span><div class="cfg-choice-cell">${field('cfgChoiceDraftLabel','标签',`<textarea id="cfgChoiceDraftLabel" rows="1" spellcheck="false" aria-label="新候选标签">${escape(draft.label)}</textarea>`)}</div><div class="cfg-choice-cell">${field('cfgChoiceDraftValue','完整值',`<textarea id="cfgChoiceDraftValue" rows="1" spellcheck="false" aria-label="新候选完整值">${escape(draft.value)}</textarea>`)}</div><div class="cfg-choice-actions"><button type="submit" id="cfgChoiceDraftAdd">添加</button><button type="button" id="cfgChoiceDraftCancel">取消</button></div><p id="cfgChoiceDraftError" class="cfg-create-error" role="alert" aria-live="polite">${escape(draft.error || '')}</p></form>` : conflict('choice',app,v)}</div></section>
      <div id="cfgValidation" class="cfg-validation" role="status" aria-live="polite"></div>
    </section>`;
    const visibleProfiles = editor.showAll ? app.profiles : [profile];
    root.innerHTML = `<div class="editor-shell cfg-editor-shell"><details class="cfg-navigation" ${navOpen ? 'open' : ''}><summary>应用与方案 <span>${escape(app.name)} / ${escape(profile.name)}</span></summary><aside class="editor-left">
      <section class="editor-nav-group"><div class="editor-section"><strong id="editorAppsHeading">应用</strong><span class="mini sub">${apps.length} 项</span><button type="button" class="cfg-text-action cfg-create-entry" id="cfgAddApp" aria-label="新增应用">${belongs(draft,'app')&&!atOrigin?'继续新增':'＋ 新增'}</button></div>${belongs(draft,'app')&&atOrigin&&draft.stage==='app-name' ? createForm(draft) : conflict('app')}<div class="editor-nav-list" data-editor-list="apps" aria-labelledby="editorAppsHeading">${apps.map((a, index) => `<button type="button" class="editor-item ${editor.app === index ? 'active' : ''}" data-editapp="${index}" aria-current="${editor.app === index}" title="${escape(a.name)}">${escape(a.name)}</button>`).join('')}</div></section>
      <section class="editor-nav-group"><div class="editor-section"><strong id="editorProfilesHeading">方案</strong><span class="mini sub">${app.profiles.length} 项</span><button type="button" class="cfg-text-action cfg-create-entry" id="cfgAddProfile" aria-label="新增方案">${belongs(draft,'profile',app)&&!atOrigin?'继续新增':'＋ 新增'}</button></div>${belongs(draft,'profile',app)&&atOrigin&&draft.stage==='profile-name' ? createForm(draft) : conflict('profile',app)}<div class="editor-nav-list" data-editor-list="profiles" aria-labelledby="editorProfilesHeading">${app.profiles.map(p => `<button type="button" class="editor-item ${profile.name === p.name ? 'active' : ''}" data-editplan="${escape(p.name)}" aria-current="${profile.name === p.name}" title="${escape(p.name)}">${escape(p.name)}${app.default === p.name ? ' · 默认' : ''}</button>`).join('')}</div></section>
      </aside></details><main class="editor-mid cfg-editor-main"><div class="cfg-page-heading"><div><h2>${escape(app.name)}</h2><p>${escape(profile.name)} · ${profile.vars.length} 项变量 · 默认方案 ${escape(app.default)}</p></div><select id="editorView" class="view-select" aria-label="配置方案显示范围"><option value="current" ${!editor.showAll ? 'selected' : ''}>当前方案</option><option value="all" ${editor.showAll ? 'selected' : ''}>全部方案</option></select></div>
      <div class="cfg-variable-list">${visibleProfiles.map(p => `<section class="cfg-scheme-group" aria-label="${escape(p.name)}">${editor.showAll ? `<div class="cfg-scheme-heading"><h3>${escape(p.name)}</h3><span>${p.vars.length} 项变量${p.name === app.default ? ' · 默认方案' : ''}</span></div>` : ''}${p.vars.length ? `<div class="cfg-variable-strip" aria-label="${escape(p.name)}变量">${p.vars.map((v, index) => {const active = p.name === profile.name && index === editor.varIdx; return `<button type="button" class="cfg-variable-select ${active ? 'is-active' : ''}" data-tableplan="${escape(p.name)}" data-tablevar="${index}" aria-current="${active}" aria-expanded="${active}" ${active ? 'aria-controls="editorInspector"' : ''} title="${escape(v.name)} · ${v.scope === 'System' ? '系统' : '用户'} · ${escape(modeLabels[v.mode] || v.mode)}"><strong>${escape(v.name)}</strong><span class="cfg-variable-meta">${v.scope === 'System' ? '系统' : '用户'}</span></button>`;}).join('')}</div>` : '<p class="cfg-empty">此方案尚无变量。</p>'}<div class="cfg-variable-entry"><button type="button" class="cfg-text-action" ${!editor.showAll && p === profile ? 'id="cfgAddVariable"' : ''} data-add-variable="${escape(p.name)}">${belongs(draft,'variable',app,p)&&!atOrigin?'继续新增变量':'＋ 新增变量'}</button></div>${belongs(draft,'variable',app,p)&&atOrigin ? createForm(draft) : conflict('variable',app,p)}${p.name === profile.name ? (activeDraft ? draftRules : variable ? form(variable) : '') : ''}</section>`).join('')}</div><p class="cfg-page-note">默认值来源跟随配置修改；其他来源保留选定值。到工作台预览后再应用。</p>
      </main></div>`;
    const find = id => root.querySelector(`[id="${id}"]`);
    // Named containers remain in this existing session draft until their first
    // variable passes the original atomic configuration-creation callback.
    const namedApp = draft?.kind === 'app' && draft.stage !== 'app-name';
    const namedProfile = ['app','profile'].includes(draft?.kind) && ['profile-empty','variable'].includes(draft.stage);
    const pendingButton = (id, name, active) => `<button type="button" id="${id}" class="editor-item cfg-container-draft ${active ? 'active' : ''}" aria-current="${active}"><span>${escape(name)}</span><small>草稿</small></button>`;
    const appList = root.querySelector('[data-editor-list="apps"]'), profileList = root.querySelector('[data-editor-list="profiles"]');
    if (namedApp) appList.insertAdjacentHTML('beforeend',pendingButton('cfgDraftApp',draft.appName,containerDraft));
    if (namedProfile && (draft.kind === 'app' ? containerDraft : draft.app === app)) profileList.insertAdjacentHTML('beforeend',pendingButton('cfgDraftProfile',draft.profileName,containerDraft));
    if (containerDraft) {
      find('cfgCreateForm')?.remove();
      const naming = ['app-name','profile-name'].includes(draft.stage);
      const title = draft.kind === 'app' ? (namedApp ? draft.appName : '新增应用') : app.name;
      root.querySelector('.cfg-navigation > summary').innerHTML=`应用与方案 <span>${escape(title)} / ${namedProfile ? escape(draft.profileName) : '新增草稿'}</span>`;
      const subtitle = namedProfile ? draft.profileName+' · 0 项变量 · 草稿' : draft.kind === 'app' && namedApp ? '尚无方案 · 草稿' : '先命名，再按需添加内容';
      const nextLabel = draft.stage === 'app-empty' ? '＋ 新增方案' : '＋ 新增变量';
      const nextId = draft.stage === 'app-empty' ? 'cfgDraftNext' : 'cfgAddVariable';
      root.querySelector('.cfg-editor-main').innerHTML = `<div class="cfg-page-heading"><div><h2>${escape(title)}</h2><p>${escape(subtitle)}</p></div></div><section class="cfg-container-workspace" aria-label="未完成的配置草稿">${naming ? createForm(draft) : variableStep ? createForm(draft)+draftRules : `<h3>${escape(draft.stage === 'app-empty' ? '此应用尚无方案' : '此方案尚无变量')}</h3><p>可以稍后继续添加。</p><button type="button" class="cfg-text-action" id="${nextId}">${nextLabel}</button>`}<p class="cfg-hint">未完成的草稿仅在本页保留，不包含在导出文件中。添加首个变量后自动纳入配置。</p>${namedApp || namedProfile ? `<button type="button" class="cfg-text-action" id="cfgDraftDiscard">放弃此${draft.kind === 'app' ? '应用' : '方案'}草稿</button>` : ''}</section>`;
      if (namedApp) {
        appList.querySelectorAll('[data-editapp]').forEach(node=>{node.classList.remove('active');node.setAttribute('aria-current','false');});
        profileList.innerHTML = namedProfile ? pendingButton('cfgDraftProfile',draft.profileName,true) : '';
        const heading=root.querySelector('#editorProfilesHeading').parentElement.querySelector('.mini');
        if(heading)heading.textContent=namedProfile?'1 项草稿':'0 项';
      } else if (namedProfile) {
        profileList.querySelectorAll('[data-editplan]').forEach(node=>{node.classList.remove('active');node.setAttribute('aria-current','false');});
      }
    }
    const refresh = focusId => rerender(focusId);
    const entryId = d => d.kind === 'app' ? 'cfgAddApp' : d.kind === 'profile' ? 'cfgAddProfile' : d.kind === 'choice' ? 'cfgAddChoice' : 'cfgAddVariable';
    const newDraft = (kind, targetProfile = profile) => ({kind, stage:kind === 'app' ? 'app-name' : kind === 'profile' ? 'profile-name' : 'variable', app, profile:targetProfile, originApp:app, originProfile:targetProfile, originVariable:targetProfile.vars[editor.varIdx] || targetProfile.vars[0] || null, appName:'', profileName:'', name:'', scope:'User', mode:'replace', previousMode:'replace', inheritedWhole:false, error:''});
    const sameRequest = (a,b) => a?.kind === b?.kind && (a.kind === 'app' || a.app === b.app) && (a.kind !== 'variable' || a.profile === b.profile) && (a.kind !== 'choice' || a.variable === b.variable);
    const focusDraft = d => find(d.kind === 'choice' ? 'cfgChoiceDraftLabel' : d.stage === 'app-name' ? 'cfgCreateAppName' : d.stage === 'profile-name' ? 'cfgCreateProfileName' : d.stage === 'app-empty' ? 'cfgDraftNext' : d.stage === 'profile-empty' ? 'cfgAddVariable' : 'cfgCreateName')?.focus();
    const openCreate = request => {
      if (session.draft && !sameRequest(session.draft, request)) { session.conflict = request; refresh('cfgCreateResume'); return; }
      session.draft ||= request; session.draft.suspended=false; session.conflict = null;
      const d=session.draft;
      if (d.originApp && apps.includes(d.originApp)) { editor.app=apps.indexOf(d.originApp); editor.profile=d.originProfile.name; editor.varIdx=Math.max(0,d.originProfile.vars.indexOf(d.originVariable)); }
      refresh(); focusDraft(session.draft);
      (find('cfgCreateForm') || find('cfgChoiceDraft'))?.scrollIntoView?.({block:'nearest',behavior:'instant'});
    };
    find('cfgAddApp').onclick = () => openCreate(newDraft('app'));
    find('cfgAddProfile').onclick = () => openCreate(newDraft('profile'));
    const discard = () => { const id=entryId(draft);session.draft=null;session.conflict=null;refresh(id);find(id)?.focus({preventScroll:true}); };
    if (containerDraft) {
      if(find('cfgDraftDiscard'))find('cfgDraftDiscard').onclick=discard;
      const advance = stage => { draft.stage=stage;draft.error='';refresh();focusDraft(draft); };
      if (find('cfgDraftNext'))find('cfgDraftNext').onclick=()=>advance('profile-name');
      if (find('cfgAddVariable'))find('cfgAddVariable').onclick=()=>advance('variable');
      if (namedApp)find('cfgAddProfile').onclick=()=>{if(!namedProfile)advance('profile-name');else focusDraft(draft);};
    }
    for(const id of ['cfgDraftApp','cfgDraftProfile'])if(find(id))find(id).onclick=()=>openCreate(draft);
    root.querySelectorAll('[data-add-variable]').forEach(button => button.onclick = () => openCreate(newDraft('variable',app.profiles.find(p=>p.name===button.dataset.addVariable))));
    if (session.conflict && find('cfgCreateConflict')) {
      find('cfgCreateResume').onclick = () => { const d=session.draft; session.conflict=null; if(d)d.suspended=false; if(d?.originApp && apps.includes(d.originApp)) { editor.app=apps.indexOf(d.originApp); editor.profile=d.originProfile.name; editor.varIdx=Math.max(0,d.originProfile.vars.indexOf(d.originVariable)); } refresh(); focusDraft(d); };
      find('cfgCreateDiscard').onclick = () => { const request=session.conflict; session.draft=null; session.conflict=null; openCreate(request); };
    }
    if (activeDraft && find('cfgCreateForm')) {
      const d = draft, createFormNode = find('cfgCreateForm');
      const showError = (message, field = 'name') => { d.error = `${{appName:'应用名称',profileName:'方案名称',name:'变量名',scope:'作用域',mode:'应用模式'}[field] || '配置'}：${message}`; find('cfgCreateError').textContent=d.error; };
      const checkTarget = () => {
        const canonical=window.ConfigFormat.matchName(d.name), scope=d.scope;
        const whole=!!canonical && apps.some(a=>a.profiles.some(p=>p.vars.some(v=>v.scope===scope&&window.ConfigFormat.matchName(v.name)===canonical&&v.special)));
        if(whole&&!d.inheritedWhole){d.previousMode=d.mode;d.mode='replace';}
        if(!whole&&d.inheritedWhole)d.mode=d.previousMode;
        d.inheritedWhole=whole;
        const mode=find('cfgMode'),hint=find('cfgCreateHint');
        if(mode){mode.value=d.mode;mode.disabled=whole;[...mode.options].forEach(o=>o.disabled=whole&&o.value!=='replace');}
        if(hint){hint.hidden=!whole;hint.textContent=whole?'同一目标已定义整体处理，沿用 replace 模式。':'';}
        if(d.kind==='variable' && canonical && d.profile.vars.some(v=>v.scope===scope&&window.ConfigFormat.matchName(v.name)===canonical)){
          showError(`${scope==='User'?'用户':'系统'}作用域已有此变量；可在下方改为${scope==='User'?'系统':'用户'}。`);
          if(!find('cfgLocateScope')){const action=root.ownerDocument.createElement('button');action.type='button';action.id='cfgLocateScope';action.className='cfg-text-action';action.textContent='调整作用域';action.onclick=()=>{find('cfgCreateOptions').open=true;find('cfgScope')?.focus();};find('cfgCreateError').after(action);}
        } else {d.error='';find('cfgCreateError').textContent='';find('cfgLocateScope')?.remove();}
      };
      for(const [id,key] of [['cfgCreateAppName','appName'],['cfgCreateProfileName','profileName'],['cfgCreateName','name']])if(find(id))find(id).oninput=e=>{d[key]=e.target.value;d.error='';find('cfgCreateError').textContent='';if(key==='name')checkTarget();};
      if(find('cfgScope'))find('cfgScope').onchange=e=>{d.scope=e.target.value;checkTarget();};
      if(find('cfgMode'))find('cfgMode').onchange=e=>{d.mode=e.target.value;};
      const cancel=()=>{
        if (containerDraft && d.stage === 'variable') { d.stage='profile-empty';d.error='';refresh('cfgAddVariable'); }
        else if (d.kind === 'app' && d.stage === 'profile-name') { d.stage='app-empty';d.error='';refresh('cfgDraftNext'); }
        else discard();
      };
      find('cfgCreateCancel').onclick=cancel;
      createFormNode.onkeydown=e=>{if(e.isComposing||e.keyCode===229)return;if(e.key==='Escape'){e.preventDefault();e.stopPropagation();cancel();}};
      createFormNode.onsubmit=e=>{
        e.preventDefault();if(e.isComposing||e.keyCode===229||d.submitting)return;
        if (d.stage === 'app-name') {
          if(!d.appName.trim()||d.appName.includes('\0')){showError('请填写应用名称，不能仅包含空白或 NUL','appName');return;}
          if(apps.some(a=>a.name===d.appName)){showError('应用名称已存在','appName');return;}
          d.stage='app-empty';d.error='';refresh('cfgDraftNext');return;
        }
        if (d.stage === 'profile-name') {
          if(!d.profileName.trim()||d.profileName.includes('\0')||d.profileName==='default_option'){showError('请填写方案名称，不能使用保留名 default_option','profileName');return;}
          if(d.kind==='profile'&&app.profiles.some(p=>p.name===d.profileName)){showError('此应用中已存在同名方案','profileName');return;}
          d.stage='profile-empty';d.error='';refresh('cfgAddVariable');return;
        }
        if(!apps.includes(d.app) && d.kind!=='app'){showError('原应用已不存在，请取消并重新选择。','appName');return;}
        if(d.kind==='variable'&&!d.app.profiles.includes(d.profile)){showError('原方案已不存在，请取消并重新选择。','profileName');return;}
        d.submitting=true;find('cfgCreateSubmit').disabled=true;
        const result=options.create?.(d.kind,{appName:d.appName,profileName:d.profileName,name:d.name,scope:d.scope,mode:d.mode},{app:apps.indexOf(d.app),profile:d.profile?.name});
        if(!result?.ok){d.submitting=false;find('cfgCreateSubmit').disabled=false;showError(result?.error||'未能创建，请检查填写内容。',result?.field||'name');find('cfgCreate'+(result?.field||'name')[0].toUpperCase()+(result?.field||'name').slice(1))?.focus();return;}
        session.draft=null;session.conflict=null;refresh('cfgDefault');find('cfgDefault')?.scrollIntoView?.({block:'nearest'});
      };
      if(variableStep)checkTarget();
    }
    const navFocus = (selector, value) => [...root.querySelectorAll(selector)].find(node => node.dataset[selector.includes('editapp') ? 'editapp' : 'editplan'] === String(value))?.focus({preventScroll:true});
    if(find('editorView'))find('editorView').onchange = event => { suspendDraft(editor); editor.showAll = event.target.value === 'all'; refresh('editorView'); };
    root.querySelectorAll('[data-editapp]').forEach(button => button.onclick = () => { if(containerDraft||editor.app!==+button.dataset.editapp)suspendDraft(editor); editor.app = +button.dataset.editapp; editor.profile = apps[editor.app].default; editor.varIdx = 0; refresh(); navFocus('[data-editapp]', editor.app); });
    root.querySelectorAll('[data-editplan]').forEach(button => button.onclick = () => { if(containerDraft||editor.profile!==button.dataset.editplan)suspendDraft(editor); editor.profile = button.dataset.editplan; editor.varIdx = 0; refresh(); navFocus('[data-editplan]', editor.profile); });
    root.querySelectorAll('[data-editor-list]').forEach(list => listKeys(list, '.editor-item'));
    const variableButtons = [...root.querySelectorAll('[data-tableplan]')];
    variableButtons.forEach(button => {
      button.onclick = () => { if(editor.profile!==button.dataset.tableplan||editor.varIdx!==+button.dataset.tablevar)suspendDraft(editor); editor.profile = button.dataset.tableplan; editor.varIdx = +button.dataset.tablevar; refresh(); [...root.querySelectorAll('[data-tableplan]')].find(node => node.dataset.tableplan === editor.profile && +node.dataset.tablevar === editor.varIdx)?.focus({preventScroll:true}); };
      button.onkeydown = event => {
        if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || event.isComposing || !['ArrowUp','ArrowDown','ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
        event.preventDefault(); const index = variableButtons.indexOf(button);
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? variableButtons.length - 1 : Math.max(0, Math.min(variableButtons.length - 1, index + (['ArrowDown','ArrowRight'].includes(event.key) ? 1 : -1)));
        variableButtons[next]?.click();
      };
    });
    if (variable && !activeDraft) {
      const updateFeedback = () => {
        find('cfgSource').textContent = fact(variable.source);
        find('cfgSaved').textContent = fact(variable.savedValue);
        find('cfgSaved').title = variable.savedValue ?? '';
        const edits = (options.changes?.() || []).filter(c => c.app === app.name && c.profile === profile.name && c.name === variable.name);
        for (const [name, id] of [['defaultValue','cfgDefault'],['scope','cfgScope'],['mode','cfgMode'],['special','cfgSpecial'],['editable','cfgEditable'],['choices','cfgChoices']]) {
          const control = find(id), changed = edits.some(item => item.field === name);
          control?.classList.toggle('is-config-edited', changed);
          root.querySelector(`label[for="${id}"]`)?.classList.toggle('is-config-edited', changed);
        }
        const errors = options.errors?.(variable) || [];
        find('cfgValidation').textContent = Array.isArray(errors) ? errors.map(e => typeof e === 'string' ? e : e.message || String(e)).join('；') : String(errors);
        find('cfgValidation').hidden = !find('cfgValidation').textContent;
        status();
      };
      const write = (name, next, meta) => { change(variable, name, next, meta); updateFeedback(); };
      find('cfgScope').onchange = event => { write('scope', event.target.value); refresh('cfgScope'); };
      find('cfgMode').onchange = event => { write('mode', event.target.value); refresh('cfgMode'); };
      find('cfgSpecial').onchange = event => { write('special', event.target.checked); refresh('cfgSpecial'); };
      find('cfgEditable').onchange = event => { write('editable', event.target.checked); };
      bindText(find('cfgDefault'), () => variable.defaultValue, next => write('defaultValue', next));
      if (options.openElements) find('cfgEditElements').onclick = () => options.openElements(variable, variable.defaultValue, raw => { write('defaultValue', raw); refresh('cfgDefault'); });
      const editChoice = (index, field, next) => { const list = choices(variable); if (!list[index]) return; list[index][field] = next; write('choices', list, {action:'edit', index, field}); };
      currentChoices.forEach((choice, index) => {
        bindText(find(`cfgChoiceLabel${index}`), () => choices(variable)[index]?.label ?? '', next => editChoice(index, 'label', next));
        bindText(find(`cfgChoiceValue${index}`), () => choices(variable)[index]?.value ?? '', next => editChoice(index, 'value', next));
      });
      find('cfgAddChoice').onclick = () => openCreate({kind:'choice',app,profile,variable,originApp:app,originProfile:profile,originVariable:variable,label:'',value:'',error:''});
      if (belongs(draft,'choice',app,variable) && find('cfgChoiceDraft')) {
        const d=draft, formNode=find('cfgChoiceDraft');
        bindText(find('cfgChoiceDraftLabel'),()=>d.label,next=>{d.label=next;d.error='';find('cfgChoiceDraftError').textContent='';});
        bindText(find('cfgChoiceDraftValue'),()=>d.value,next=>{d.value=next;d.error='';find('cfgChoiceDraftError').textContent='';});
        const cancel=()=>{session.draft=null;session.conflict=null;refresh('cfgAddChoice');find('cfgAddChoice')?.focus({preventScroll:true});};
        find('cfgChoiceDraftCancel').onclick=cancel;
        formNode.onkeydown=e=>{if(e.isComposing||e.keyCode===229)return;if(e.key==='Escape'){e.preventDefault();e.stopPropagation();cancel();}if(e.key==='Enter'&&e.ctrlKey){e.preventDefault();formNode.requestSubmit();}};
        formNode.onsubmit=e=>{
          e.preventDefault();if(e.isComposing||e.keyCode===229||d.submitting)return;
          const list=choices(variable), fail=(field,message)=>{d.error=`${field}：${message}`;find('cfgChoiceDraftError').textContent=d.error;find(field==='标签'?'cfgChoiceDraftLabel':'cfgChoiceDraftValue')?.focus();};
          if(!d.label.trim())return fail('标签','请输入显示名称');
          if(d.value.includes('\0')||d.value.length>32766)return fail('完整值','不能包含 NUL，长度不能超过 32766 个 UTF-16 单元');
          if(list.some(c=>c.label===d.label))return fail('标签','显示名称与已有候选重复');
          if(list.some(c=>c.value===d.value))return fail('完整值','实际值与已有候选重复');
          d.submitting=true;find('cfgChoiceDraftAdd').disabled=true;
          const index=list.length;write('choices',[...list,{label:d.label,value:d.value}],{action:'add',index});
          session.draft=null;session.conflict=null;refresh(`cfgChoiceLabel${index}`);
        };
      }
      for (const action of ['up','down','remove']) root.querySelectorAll(`[data-choice-${action}]`).forEach(button => button.onclick = () => {
        const index = +button.dataset['choice' + action[0].toUpperCase() + action.slice(1)], list = choices(variable);
        if (index < 0 || index >= list.length) return;
        let focus, meta;
        if (action === 'remove') { list.splice(index, 1); focus = list.length ? `cfgChoiceLabel${Math.min(index, list.length - 1)}` : 'cfgAddChoice'; meta = {action:'remove', index}; }
        else { const target = index + (action === 'up' ? -1 : 1); if (target < 0 || target >= list.length) return; [list[index], list[target]] = [list[target], list[index]]; focus = `cfgChoiceLabel${target}`; meta = {action:'move', from:index, to:target}; }
        write('choices', list, meta); refresh(focus);
      });
      updateFeedback();
    } else status();
    for (const saved of scrolls) {
      if (saved.list === 'profiles' && !sameApp || !saved.list && !sameProfile) continue;
      const node = saved.list ? root.querySelector(`[data-editor-list="${saved.list}"]`) : root.querySelector('.editor-mid');
      if (node) { node.scrollTop = saved.top; node.scrollLeft = saved.left; }
    }
    if (options.focusId) find(options.focusId)?.focus({preventScroll:true});
  }
  window.ConfigEditor = {render, bindRawInput:bindText, clearDraft, hasDraft, suspendDraft};
})();

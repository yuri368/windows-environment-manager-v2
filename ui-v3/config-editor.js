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
  function render(options) {
    const {root, apps, editor, modeLabels = {}, change, rerender, status = () => {}} = options;
    const app = apps[editor.app], profile = app?.profiles.find(p => p.name === editor.profile) || app?.profiles[0];
    if (!profile) { root.innerHTML = '<p class="helpnote">此应用尚无可编辑方案。</p>'; return; }
    const variable = profile.vars[editor.varIdx];
    const sameApp = root.dataset.editorApp === String(editor.app), sameProfile = sameApp && root.dataset.editorProfile === profile.name;
    const scrolls = [...root.querySelectorAll('[data-editor-list],.editor-mid')].map(node => ({list: node.dataset.editorList, top: node.scrollTop, left: node.scrollLeft}));
    const navOpen = root.querySelector('.cfg-navigation')?.open ?? !(root.ownerDocument.defaultView.matchMedia?.('(max-width:700px)').matches);
    root.dataset.editorApp = String(editor.app); root.dataset.editorProfile = profile.name; root.dataset.editorVariable = String(editor.varIdx);
    const choices = v => (options.choices ? options.choices(v) : v.choices || []).map(c => typeof c === 'string' ? {label:c, value:c} : {label:String(c.label ?? ''),value:String(c.value ?? '')});
    const currentChoices = variable ? choices(variable) : [];
    const field = (id, label, body, className = '') => `<div class="field ${className}"><label for="${id}">${label}</label>${body}</div>`;
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
      <section class="cfg-choices" id="cfgChoices" aria-labelledby="cfgChoicesHeading"><div class="cfg-section-heading"><h3 id="cfgChoicesHeading">候选值 <span class="cfg-count">${currentChoices.length}</span></h3><button type="button" class="cfg-text-action" id="cfgAddChoice">添加候选值</button></div>
        <div class="cfg-choice-list"><div class="cfg-choice-columns" aria-hidden="true"><span>序号</span><span>标签</span><span>完整值</span><span>操作</span></div>${currentChoices.length ? currentChoices.map((choice, index) => `<div class="cfg-choice-row" data-cfg-choice="${index}"><span class="cfg-choice-order" aria-hidden="true">${index + 1}</span><div class="cfg-choice-cell">${field(`cfgChoiceLabel${index}`,'标签',`<textarea rows="1" id="cfgChoiceLabel${index}" data-choice-label="${index}" spellcheck="false" placeholder="候选标签" aria-label="第 ${index + 1} 个候选值的标签"></textarea>`)}</div><div class="cfg-choice-cell">${field(`cfgChoiceValue${index}`,'值',`<textarea rows="1" id="cfgChoiceValue${index}" data-choice-value="${index}" spellcheck="false" placeholder="（空字符串）" aria-label="第 ${index + 1} 个候选值的完整值"></textarea>`)}</div><div class="cfg-choice-actions"><button type="button" data-choice-up="${index}" ${index === 0 ? 'disabled' : ''} aria-label="上移第 ${index + 1} 个候选值">上移</button><button type="button" data-choice-down="${index}" ${index === currentChoices.length - 1 ? 'disabled' : ''} aria-label="下移第 ${index + 1} 个候选值">下移</button><button type="button" data-choice-remove="${index}" aria-label="删除第 ${index + 1} 个候选值">删除</button></div></div>`).join('') : '<p class="cfg-empty">暂无候选值。可添加标签与完整值。</p>'}</div>
      </section>
      <div id="cfgValidation" class="cfg-validation" role="status" aria-live="polite"></div>
    </section>`;
    const visibleProfiles = editor.showAll ? app.profiles : [profile];
    root.innerHTML = `<div class="editor-shell cfg-editor-shell"><details class="cfg-navigation" ${navOpen ? 'open' : ''}><summary>应用与方案 <span>${escape(app.name)} / ${escape(profile.name)}</span></summary><aside class="editor-left">
      <section class="editor-nav-group"><div class="editor-section"><strong id="editorAppsHeading">应用</strong><span class="mini sub">${apps.length} 项</span></div><div class="editor-nav-list" data-editor-list="apps" aria-labelledby="editorAppsHeading">${apps.map((a, index) => `<button type="button" class="editor-item ${editor.app === index ? 'active' : ''}" data-editapp="${index}" aria-current="${editor.app === index}" title="${escape(a.name)}">${escape(a.name)}</button>`).join('')}</div></section>
      <section class="editor-nav-group"><div class="editor-section"><strong id="editorProfilesHeading">方案</strong><span class="mini sub">${app.profiles.length} 项</span></div><div class="editor-nav-list" data-editor-list="profiles" aria-labelledby="editorProfilesHeading">${app.profiles.map(p => `<button type="button" class="editor-item ${profile.name === p.name ? 'active' : ''}" data-editplan="${escape(p.name)}" aria-current="${profile.name === p.name}" title="${escape(p.name)}">${escape(p.name)}${app.default === p.name ? ' · 默认' : ''}</button>`).join('')}</div></section>
      </aside></details><main class="editor-mid cfg-editor-main"><div class="cfg-page-heading"><div><h2>${escape(app.name)}</h2><p>${escape(profile.name)} · ${profile.vars.length} 项变量 · 默认方案 ${escape(app.default)}</p></div><select id="editorView" class="view-select" aria-label="配置方案显示范围"><option value="current" ${!editor.showAll ? 'selected' : ''}>当前方案</option><option value="all" ${editor.showAll ? 'selected' : ''}>全部方案</option></select></div>
      <div class="cfg-variable-list">${visibleProfiles.map(p => `<section class="cfg-scheme-group" aria-label="${escape(p.name)}">${editor.showAll ? `<div class="cfg-scheme-heading"><h3>${escape(p.name)}</h3><span>${p.vars.length} 项变量${p.name === app.default ? ' · 默认方案' : ''}</span></div>` : ''}${p.vars.length ? `<div class="cfg-variable-strip" aria-label="${escape(p.name)}变量">${p.vars.map((v, index) => {const active = p.name === profile.name && index === editor.varIdx; return `<button type="button" class="cfg-variable-select ${active ? 'is-active' : ''}" data-tableplan="${escape(p.name)}" data-tablevar="${index}" aria-current="${active}" aria-expanded="${active}" ${active ? 'aria-controls="editorInspector"' : ''} title="${escape(v.name)} · ${v.scope === 'System' ? '系统' : '用户'} · ${escape(modeLabels[v.mode] || v.mode)}"><strong>${escape(v.name)}</strong><span class="cfg-variable-meta">${v.scope === 'System' ? '系统' : '用户'}</span></button>`;}).join('')}</div>${p.name === profile.name && variable ? form(variable) : ''}` : '<p class="cfg-empty">此方案尚无变量。</p>'}</section>`).join('')}</div><p class="cfg-page-note">默认值来源跟随配置修改；其他来源保留选定值。到工作台预览后再应用。</p>
      </main></div>`;
    const find = id => root.querySelector(`[id="${id}"]`);
    const refresh = focusId => rerender(focusId);
    const navFocus = (selector, value) => [...root.querySelectorAll(selector)].find(node => node.dataset[selector.includes('editapp') ? 'editapp' : 'editplan'] === String(value))?.focus({preventScroll:true});
    find('editorView').onchange = event => { editor.showAll = event.target.value === 'all'; refresh('editorView'); };
    root.querySelectorAll('[data-editapp]').forEach(button => button.onclick = () => { editor.app = +button.dataset.editapp; editor.profile = apps[editor.app].default; editor.varIdx = 0; refresh(); navFocus('[data-editapp]', editor.app); });
    root.querySelectorAll('[data-editplan]').forEach(button => button.onclick = () => { editor.profile = button.dataset.editplan; editor.varIdx = 0; refresh(); navFocus('[data-editplan]', editor.profile); });
    root.querySelectorAll('[data-editor-list]').forEach(list => listKeys(list, '.editor-item'));
    const variableButtons = [...root.querySelectorAll('[data-tableplan]')];
    variableButtons.forEach(button => {
      button.onclick = () => { editor.profile = button.dataset.tableplan; editor.varIdx = +button.dataset.tablevar; refresh(); [...root.querySelectorAll('[data-tableplan]')].find(node => node.dataset.tableplan === editor.profile && +node.dataset.tablevar === editor.varIdx)?.focus({preventScroll:true}); };
      button.onkeydown = event => {
        if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || event.isComposing || !['ArrowUp','ArrowDown','ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
        event.preventDefault(); const index = variableButtons.indexOf(button);
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? variableButtons.length - 1 : Math.max(0, Math.min(variableButtons.length - 1, index + (['ArrowDown','ArrowRight'].includes(event.key) ? 1 : -1)));
        variableButtons[next]?.click();
      };
    });
    if (variable) {
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
      find('cfgAddChoice').onclick = () => { const list = choices(variable); const index = list.length; write('choices', [...list, {label:'',value:''}], {action:'add', index}); refresh(`cfgChoiceLabel${index}`); };
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
  window.ConfigEditor = {render, bindRawInput:bindText};
})();

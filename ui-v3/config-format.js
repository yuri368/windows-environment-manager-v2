/* V2-compatible export of the browser demo's configuration. No file or system access. */
var ConfigFormat = (() => {
  const modes=new Set(['replace','+append','-append','append']);
  const choices=v=>(v.choices||[]).map((item,index)=>typeof item==='string'?{label:'候选值 '+(index+1),value:item}:{label:item.label,value:item.value});
  const matchName=name=>Array.from(name,c=>c.toUpperCase().length===1?c.toUpperCase():c).join('');
  const quote=value=>JSON.stringify(String(value)).replace(/\u007f/g, '\\u007f');
  function errors(apps){
    const issues=[],targets=new Map();
    const add=(variable,field,message)=>issues.push({variable,field,message});
    const valueError=value=>typeof value!=='string'?'必须是字符串':value.includes('\0')?'不能包含 NUL 字符':value.length>32766?'不能超过 32766 个 UTF-16 单元':null;
    apps.forEach(app=>{
      if(!app.name?.trim())add(null,'name','应用名称不能为空');
      if(!app.profiles.some(p=>p.name===app.default))add(null,'default','默认方案必须指向现有方案');
      app.profiles.forEach(profile=>{
        if(!profile.name?.trim()||profile.name==='default_option')add(null,'profile','方案名称不能为空或使用保留名 default_option');
        const names=new Map();
        profile.vars.forEach(v=>{
          if(!v.name||/[=\0]/.test(v.name))add(v,'name','变量名不能为空或包含 =、NUL');
          if(!['User','System'].includes(v.scope))add(v,'scope','作用域必须是 User 或 System');
          if(!modes.has(v.mode))add(v,'mode','操作模式无效');
          if(typeof (v.editable??true)!=='boolean')add(v,'editable','editable 必须是布尔值');
          if(v.special&&v.mode!=='replace')add(v,'special','特殊变量只允许 replace 模式');
          const problem=valueError(v.defaultValue);if(problem)add(v,'defaultValue','默认值'+problem);
          const labels=new Set(),values=new Set();
          choices(v).forEach((choice,index)=>{
            if(typeof choice.label!=='string'||!choice.label.trim())add(v,'choices','候选 '+(index+1)+' 的显示名称不能为空');
            if(labels.has(choice.label))add(v,'choices','候选显示名称重复：'+choice.label);
            if(values.has(choice.value))add(v,'choices','候选 '+(index+1)+' 的实际值与其他候选重复');
            const invalid=valueError(choice.value);if(invalid)add(v,'choices','候选 '+(index+1)+' 的实际值'+invalid);
            labels.add(choice.label);values.add(choice.value);
          });
          const name=matchName(v.name),siblings=names.get(name)||[];siblings.push(v);names.set(name,siblings);
          const target=v.scope+'|'+name,variants=targets.get(target)||[];variants.push(v);targets.set(target,variants);
        });
        for(const vars of names.values())if(vars.length>2||new Set(vars.map(v=>v.scope)).size!==vars.length)vars.forEach(v=>add(v,'scope','同名变量只允许用户与系统各一条'));
      });
    });
    for(const vars of targets.values())if(new Set(vars.map(v=>!!v.special)).size>1)vars.forEach(v=>add(v,'special','同一作用域和变量名的 Special 属性须在各方案中一致'));
    return issues;
  }
  function serialize(apps,{validate=true}={}){
    const invalid=validate?errors(apps):[];if(invalid.length)throw new Error(invalid.map(item=>item.message).join('\n'));
    const record=v=>{
      const fields=['default_value = '+quote(v.defaultValue),'scope = '+quote(v.scope),'mode = '+quote(v.mode),'editable = '+String(v.editable??true)];
      if(v.special)fields.push('special = true');
      const options=choices(v);if(options.length)fields.push('choices = ['+options.map(c=>'{ label = '+quote(c.label)+', value = '+quote(c.value)+' }').join(', ')+']');
      return '{ '+fields.join(', ')+' }';
    };
    const lines=['# V2 格式的演示配置；不包含浏览器运行草稿或模拟 Windows 值。',''];
    for(const app of apps){
      lines.push('['+quote(app.name)+']','default_option = '+quote(app.default),'');
      for(const profile of app.profiles){
        lines.push('['+quote(app.name)+'.'+quote(profile.name)+']');
        const groups=new Map();for(const v of profile.vars){const key=matchName(v.name);if(!groups.has(key))groups.set(key,[]);groups.get(key).push(v)}
        for(const vars of groups.values())lines.push(quote(vars[0].name)+' = '+(vars.length===1?record(vars[0]):'[ '+vars.map(record).join(', ')+' ]'));
        lines.push('');
      }
    }
    return lines.join('\n');
  }
  return {choices,errors,serialize};
})();

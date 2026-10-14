# GUI 状态与编辑入口修复 — 2026-10-14

用户批注1–2：幽灵移动三处与汇总/Footer统一；普通移动仍独立。批注3：重复元信息行移除，toolbar明确有效/已移除数量，作用域保留标题，类型可悬停查看。批注4–8：sidebar与检测结果circle统一几何居中。批注9：仅workbench replace关闭元素编辑；选定整值与preview保留，Windows页直接编辑和append保留。

两目录完整回归：24脚本/462项通过，新增19项专项。旧150项对应fixture改走Windows直接编辑或检查新文案，业务raw/serialization/Audit/History断言保持。full-test-report.json为本次执行结果；旧443项仅历史。

Browser：独立localhost后台验收页，真实操作replace双击无弹窗/整值输入、append打开、幽灵移动、Windows环境页MAVEN_HOME直接编辑。01–03截图及browser JSON为真实浏览器证据；04与narrow-layout为CDP模拟（CSS640×1067，dialog client/scroll609），非原生缩放实测。临时缓存禁用/尺寸覆盖均已撤销。资源版本query避免旧缓存混用。

图标实测：result外圈与文字容器中心偏差约0；sidebar约0.005 CSS像素舍入；内层mask上下margin相等（3或3.5px）。没有逐图标translateY补丁。

GPT使用原架构会话，首次连接发送失败，刷新后精简消息成功并批准方案。最终源码审查及CI后续记录。

主目录H:\Yuri368\环境变量配置V3；镜像H:\Yuri368\环境变量配置V3-git\ui-v3。主目录预先存在未调用的staticMarkup/rawStream/previewRow辅助函数继续保留，其源码hash/query差异不是本轮新增。DemoPlan核心hash保持950e4f532eaa9a43444ef10ff8b2ba819981ccbbc32202a6a19bf06f669d4bc4；无V2/真实Registry/main/Release修改。

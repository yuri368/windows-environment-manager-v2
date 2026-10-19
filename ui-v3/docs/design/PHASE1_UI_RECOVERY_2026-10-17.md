# 第一阶段 UI 接手与验证记录

2026-10-17。范围：V3 浏览器演示的工作台和 Windows 当前值表，依据 `ASTRA_NEXT_UI_PLAN_2026-10-16.md` 第一阶段。第二阶段预览／历史布局、已完成的配置新增不在本轮修改内。

## 版本与实际文件

- 计划起点：`5314fd7`。
- Claude 未提交的四文件现场保存为 `573dcfa`：`temp.html`、`ui-polish.css`、`surface-ui.css`、`shell-ui.css`。
- 本轮补齐代码：`8a371679182493ca54d59bc62758925956131874`。
- 分支：`v3/phase1-ui-recovery-20261017`。没有修改 main。
- Git 镜像：`H:\Yuri368\环境变量配置V3-git\ui-v3`。
- 实际预览目录：`H:\Yuri368\环境变量配置V3`。四个展示文件已同步，并逐文件检查 SHA256 一致。

## 修改内容

继承并核实 Claude 的紧凑布局：应用标题和方案并列，移除重复应用前缀；正常审阅摘要并入对照标题，异常提示仍单独显示；选定值的来源信息上移；输入框随内容增长。Windows 表移除整列灰底外框，保留各元素铭牌与常驻“编辑／原文”。

本轮补齐：

- 收窄对照标题的绿色菱形选择器，避免误命中 blocked／retained 等状态文字。
- 恢复 Claude 删除的示例说明，改为默认折叠的“示例说明”，保留内容且不增加默认概览高度。
- 对照标题的状态容器缺失时补回标题内部；辅助标题选择器只匹配直接子元素。
- Windows 普通行以 44px 为起点，多元素自然增长；窄屏缩减页边距，长元信息可折行。
- 更新所改样式资源的版本参数，避免实际页面复用上一版 CSS 缓存。

没有修改 DemoPlan、匹配、删除资格、Audit、快照数据、无缝刷新或版本绑定确认逻辑。Windows 表仍是模拟当前值，不声称访问真实 Registry。

## 本轮验证

完整证据位于 `design-evidence/phase1-recovery-2026-10-17/`。

| 验证 | 实际结果 | 限定 |
| --- | --- | --- |
| 完整 `npm test` | 本轮最终产品冻结后运行一次，exit 0 | 既有 33 个脚本；日志 `final-npm-test.txt` |
| 工作台审阅四态 | 4/4，exit 0 | JSDOM，日志 `final-inspect-review.txt` |
| Chrome 渲染与交互 | 13/13，exit 0 | 独立 headless Google Chrome，最终测试实际 HTTP 页面 |
| 页面尺寸 | 1383×851、800px、600px | 截图及 `browser-report.json` |
| 原目录同步 | 四文件 SHA256 一致 | 哈希记录 `final-test-metadata.json` |
| `git diff --check` | exit 0 | Git 仅提示换行标准化，无 whitespace error |

Chrome 使用独立临时 profile，访问 `http://127.0.0.1:8000/temp.html`。检查了标准样例三值标题及首行可见、长 textarea 不内部裁剪、悬停源与重复联动、预览取消后的输入和范围、四态提示、Windows 重复与空项、Special 整值、原文 CRLF/CR/LF、键盘 occurrence 定位及删除恢复取消、窄屏与 reduced-motion。四态中的 retained／blocked 使用演示内存 fixture，没有向真实系统写入。

原生桌面控制接口本轮不可用，因此以上鼠标／键盘动作由独立 Chrome 自动化完成。真实中文 IME、200% 缩放、原生桌面鼠标拖拽和负责人最终审美验收**未验证**。

## 协作记录

通过已授权的 ChatGPT-Bridge 技能向架构会话发送本轮边界说明，队列 `6638ba87-5601-4697-9946-b685f3a4be66` 已完成，回复原则同意第一阶段有限恢复，未要求扩大范围。归档 `CHATGPT_START_REVIEW.txt`。这是一份边界审阅，不是 ChatGPT 独立运行源码测试。

收尾汇报队列 `1cf25b87-8c06-4301-88cd-f98e99f27cef` 也已 complete，回复归档 `CHATGPT_FINAL_REVIEW.txt`。ChatGPT 确认未发现架构性返工要求，没有要求启动第二阶段；它明确声明网页读取 GitHub 引用返回 404，未独立取得差异或复现测试。因此不把这份边界审阅称作独立代码验收。产品分支的实际 `git push` 已 exit 0；远端存在性由 Git 再核验。

收尾时 Bridge 把用户长消息后的“显示更多”UI 文字混入正文，引发严格比对失败。修复该本地集成的原文提取后，通过同一 requestId／queueId 恢复，取回已存在的回复；没有再次发送。发送与恢复身份记录见 `chatgpt-sync.json`，集成修复独立保存在 ChatGPT-Bridge 仓库。

AGY 首次失败，按负责人建议进行了两次有限重试，其中一次成功，一次仍为地区限制。只向其提供文本上下文，没有声称它独立看过页面。对其建议逐项核对，未采纳会重加输入限高、把模拟值称真实 Registry 或把 After 一律当干净结果的意见。详见 `AGY_DISPOSITION.md` 和 `AGY_REVIEW_1.md`。

DSH CLI 的安装与实际可用性另记 `DSH_CLI_STATUS.md`：隔离安装无进展后中止，未安装成功，也未启动模型会话。当前进程未配置 `DEEPSEEK_API_KEY`，没有读取凭据文件或复制 Desktop 登录状态。它没有参与本轮审阅。

## 影响与风险

桌面标准样例的三值核对更早进入首屏，超长输入仍允许页面滚动；不保证任意长度永远保持首屏。Windows 表密度提高，原文与逐元素编辑入口保留。长变量名允许自然换行。

本地 HTTP 服务仅监听 `127.0.0.1:8000`。初次后台启动方式被自动审批拦截，随后直接运行静态服务成功；HTTP 返回 200 且加载当前资源版本。没有修改代理、地区、真实环境变量或 Registry。

DSH 状态文档最初误写到实际目录的 `ui-v3/design-evidence/phase1-recovery-2026-10-17/` 下，已复制到正确 Git 证据目录。清理多余文档及空目录的命令被自动审批以 `blocked by policy` 拦截，未给出更具体原因；原文档保留，不被页面引用。

# V3 产品实施与验收记录

日期：2026-10-19。完整完成标准继续采用 [Astra 实施方案](../architecture/V3_PRODUCT_IMPLEMENTATION_2026-10-18.md) 第 17–18 节。本文件记录真实证据及缺口，不把阶段交付视为产品完成。

## 已落盘实现

独立 `envmanager_v3` 提供持久文件/Windows 原始值适配、TOML 工作副本、SQLite 配置与输入、冻结计划、审阅凭据、执行日志、条件补偿、恢复待办及贡献撤销。正式网页入口为 `index.html`，通过鉴权 loopback HTTP 接入；共享 JS 规则由固定 Node worker 执行，规则版本绑定三份规则/worker 原始字节。未引入 V2 运行依赖。

System 通道使用一次提升会话、对端进程身份绑定的本地 named pipe、固定受保护包、规则复算和管理员可写的持久回执。User 始终由启动用户进程写入。未知结果不重发；重启时只读取原回执并对账。实现不等于已经完成真实 UAC 验收。

非法配置草稿与最后有效执行工作区分开保存；有效修改才晋级。配置文件基线独立保存，未保存差异在重启后仍可识别。配置草稿、运行输入和环境值独立。

## 实际执行证据

源码证据目录：`design-evidence/product-implementation-2026-10-18/`。Git 镜像中的对应目录位于 `ui-v3/design-evidence/`。日志包含当次验证范围，不将早期失败日志当作最终通过证据。

| 证据 | 实际范围 |
| --- | --- |
| `ui-regression.log` | 已接受演示的完整 npm 回归；包括原文、元素身份、建议/幽灵与配置创建 |
| `product-ui.log` | 正式前端的 JSDOM 契约；不能替代真实后端或视觉验收 |
| `product-integration.log`、`service-http-latest.log` | HTTP、鉴权、真实 Service/FileEnvironment、保存与历史 |
| `service-contributions.log` | 真实规则、持久文件、Executor 和逐贡献撤销；共享/重复/连续性保护 |
| `valid-working-draft.log` | 非法草稿隔离、原子晋级、运行输入保留及保存基线重启恢复 |
| `native-pipe.log` | 普通用户真实跨进程 pipe，严格对端 PID 与 150 KB 消息往返；没有 Registry 或 UAC |
| `sandbox-user-adapter.json` | DefaultBox 原生 User 适配器原文、类型、空值和删除读回 |
| `sandbox-native-notifications.json` | DefaultBox 原生通知、同 scope 多目标已知写入及未知事件撤销旧快照 |
| `sandbox-native-service.json` | DefaultBox 原生 User 的完整计划/审阅/执行、按稳定 ID 编辑、CRLF、重启历史及明确删除；广播注入，未测试 System/UAC |
| `process-crash-recovery.log` | 修复后真实子进程 5 个退出边界均通过；重启不自动重写环境 |
| `backend-full-latest.log` | 431 项执行，430 通过、1 项普通权限 native pipe 默认 opt-in 跳过；该 native 项已单独真实运行通过 |
| `backend-final-2026-10-19.log` | 最终源码 482 项执行，481 通过、1 项 native pipe 默认 opt-in 跳过；84.807 秒 |
| `../product-frontend-verification-2026-10-19.md` | 最终产品 fixture 31/31、既有 UI 33/33 的执行摘要及时间边界；摘要不冒充原始 stdout |

三个 Sandboxie 结果均由 MCP 工具核验执行进程与命令进程属于指定 `DefaultBox`，不是通过命令行参数自行宣称隔离：

- User adapter：`65eb6e0bac3a4711ae33bbe4fcb6633a`。
- Native notifications：`c85e7d4faf474595ba310a568376b172`。
- Native service：`b7c67bc82f4e45319ac5dad60c12333d`。

真实浏览器已经操作正式页面并验证导入、运行自定义值、审阅与执行分离、执行后关闭弹窗、受管配置保存、命名草稿重启恢复、只填变量名称新增、直接元素编辑以及持久历史。桌面验证使用标注中的 1632×816 视口；这不是原生 Windows DPI 证明。截图 `product-editor-desktop.png` 来自正式页面及隔离 File 后端。

2026-10-19 在正式 File 页面补验：重启后新增变量仍显示未保存差异；包含 NUL 的非法配置草稿保留并禁用保存/导出，工作台继续按最后有效配置生成服务端计划；修正草稿后原运行自定义输入保留。直接编辑第二个元素的冻结预览只标记该 occurrence 为替换，摘要为“替换 1 个元素”，空元素和其他值未被标为整值替换。取消预览返回编辑草稿，再取消编辑，环境文件未提交该修改。浏览器捕获的 error 日志为空。截图为 `product-editor-2026-10-19.png` 和 `product-manual-review-2026-10-19.png`。

同日补齐四撤销入口及多目标排序的真实浏览器验证。`tests_v3/browser_scope_acceptance.py` 启动新的 FileEnvironment/Service，先通过真正的计划/审阅/执行生成 3 条贡献历史，保持进程连续；不是把虚构历史塞进生产页面。单变量撤销 X 从 `B;C;A;X` 到 `B;C;X`；当前方案同时撤销 Y 的 Q；当前应用全部方案额外撤销 C、保留另一应用的 B；已勾选 A/B 的当前方案撤销 A/B、保留未勾选 Other 方案的 C。最后一种已从页面独立审阅并实际提交，读回 X=`C;X`、Y=`Z`。

双排序目标 U/T 起初均阻塞；确认 U 后 T 仍阻塞，确认 T 后保留 U 的会话并允许重新审阅。页面实际提交后 U=`V;BASE;U`、T=`BASE;T;W`，持久历史显示两目标原快照及结果。修正了 MOVE 后仍显示“末尾追加”的摘要；31 项最终产品 fixture 重新通过，浏览器重载后的历史摘要已复验。原始页面文本证据为 `browser-undo-order-2026-10-19.json`，截图 `product-order-history-2026-10-19.png`。这些是 File 证据，不是 Windows 原生排序/UAC 证明。

安装验收迭代：DefaultBox 的 `FakeAdminRights=y` 使原用户 helper 安全检查拒绝；新增同步 `check-user`，身份失败会在备份/替换前明确阻断。经用户授权临时设为 n 后，原生 frozen helper 检查通过（job `4980b437cd1a409bb80e77a70a351db1`），并恢复 y。第二轮真实安装和升级均完成，安装后/升级后 EXE 检查为 ok，但自定义卸载 MsgBox 没有响应静默参数，等待直到外层 300 秒超时（job `3a9ba0731da6458aafe8bb29954bd9b7`）；已确认该轮没有残留 Setup/卸载进程，未重跑未知操作。模拟管理员和降权均读回 y。修复为 SuppressibleMsgBox 后，最新候选已重新构建，完整生命周期复验结果另行登记；不能把这次超时算通过。

初次 2026-10-19 联合测试曾有一次升级 helper 未在旧测试窗口内 ready，完整日志保留为 `backend-full-2026-10-19.log`。没有证据确定历史阻塞原因。升级子任务增加有界准备阶段诊断并对齐生产准备预算后，升级 50/50 定向测试、该目标连续五次及以上最终全套回归通过。

## 完整矩阵的剩余门槛

| Astra 范围 | 状态与仍需补齐的证据 |
| --- | --- |
| C01–C04 配置与草稿 | 配置/HTTP/原文测试及真实浏览器已覆盖主要流程；有效工作区分离和重启后的配置差异已与冻结前端合并复验 |
| E01–E02 原始环境状态 | File/mock 与 DefaultBox 原生 User 已验证；真实 VM System 类型与权限仍待验证 |
| P01–P05 规则、排序和元素 UI | 共享规则与旧 UI 回归已执行；四撤销范围和多排序目标已补齐正式 UI/File 提交证据；所有窄屏状态尚需完整验收 |
| R01–R04 连续性与刷新 | 可信审计、未知通知、ABA/重启与草稿保护已有测试；真实 VM 睡眠/恢复、跨标签页和最终通知 UI 仍需验收 |
| X01–X06 执行与恢复 | 故障注入与幂等已有测试；“仅值相等就标补偿”已修复，5 个实际子进程退出边界均通过；原生提升回执仍需真实 VM 验收 |
| U01–U03 权限和 helper | 固定包/pipe/回执实现、mock 和普通用户真实 pipe 已验证；真实 UAC 成功/取消/其他管理员账户、父进程退出和 System 写入仍需隔离 Windows VM |
| H01–H02 历史与撤销 | 贡献级测试已覆盖共享/累计/重复与作用域；四撤销入口、批量真实提交和排序原快照历史已补验；失败/部分/恢复记录展示仍需完整验收 |
| S01–S02 本地边界与资源 | loopback/Origin/CSRF/路径/恶意 after 及缺失运行时测试；022316-448 新包已核对最后源码、115 文件和15生产资源，无漂移 |
| L01–L02 布局和输入 | 已有桌面及窄屏检查；391/600px 全状态、IME、原生拖动和 100/125/150/200% Windows DPI 尚未全部人工验证 |
| D01–D02 发布与通知 | 已实际编译用户/机器候选包并运行 File smoke；持续升级锁与完整备份组件及入口已实现并通过测试，安装器接线与最后源码候选包正在收尾；干净 VM 安装/升级/卸载及真实广播仍待验收 |

安装包的路径、原始 hash、打包依赖/许可证和每轮 smoke 见 [BUILD.md](BUILD.md)。候选包未经代码签名；源文件改变后不能继续宣称旧包与当前源码一致。

本地 ChatGPT Bridge 的 CDP 连接曾返回 `127.0.0.1:9222` 拒绝连接，没有发送网页协作消息；不得将本轮子 Agent 讨论表述成已取得 ChatGPT 网页确认。

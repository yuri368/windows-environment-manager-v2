# V3 产品实施与验收记录

日期：2026-10-19。完成标准采用 [Astra 实施方案](../architecture/V3_PRODUCT_IMPLEMENTATION_2026-10-18.md) 第 17–18 节及负责人后续明确调整的首版范围：多用户、另一管理员账户和真实断电实验暂缓。具体证据与未验证范围分别记录，不把阶段交付视为产品完成。

## 2026-10-19 C 盘差分系统原生补验

最新原生安装、真实混合 UAC 取消、环境清理、在用保护、长路径修复、机器升级/卸载/重装、数据保留，以及负责人报告的 IME/鼠标拖动/100%–200% 系统缩放人工验收，见 [原生补验报告](NATIVE_ACCEPTANCE_2026-10-19.md)。以下较早记录保留其当时范围；旧的“未验证”描述不能覆盖新增证据，新增证据也不代表干净 Windows VM 或已暂缓项目通过。

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

最终生命周期 **通过**：候选 `022316-448` 的 user Setup，MCP job `743b38d15a8940aea1cac977fecc9871`，已核验 command/child 都在 DefaultBox；2026-10-18 18:25:15–18:28:43 UTC，Completed/exit 0。全新目录先安装、运行已安装 EXE/File 检查，写入明确标识的 SQLite 历史测试夹具和原始未完成 TOML，再真实升级、启动、核对完整备份，最后静默卸载。程序 EXE 已移除，测试草稿/历史及原始字节保留，boxed User/System 环境清单前后相等。历史夹具为合成数据，不冒充真实应用历史；真实执行历史由前述 Service/browser 验证。完整工具结果 `sandbox-installer-lifecycle-final.json`，原始安装/升级/卸载日志位于 `C:\Sandbox\Yuri368\DefaultBox\drive\C\EnvManagerV3Acceptance\20261019-user-lifecycle-3\`。finally 恢复后，SbieIni 查询 `FakeAdminRights=y`、`DropAdminRights=y`。没有执行宿主 Setup 或宿主环境变量提交；本通过范围仍不是干净 Windows VM 或真实 UAC。

另已由 `022316-448/dist/EnvManagerV3/EnvManagerV3.exe` 本身启动 File 后端提供正式浏览器页面（8788），读取先前验收工作区；配置草稿、运行自定义值和未保存差异仍保留。正式编辑器截图 `packaged-editor-2026-10-19.png`，页面 error 日志为空。该步骤不依赖外部 Python/Node 启动服务。

初次 2026-10-19 联合测试曾有一次升级 helper 未在旧测试窗口内 ready，完整日志保留为 `backend-full-2026-10-19.log`。没有证据确定历史阻塞原因。升级子任务增加有界准备阶段诊断并对齐生产准备预算后，升级 50/50 定向测试、该目标连续五次及以上最终全套回归通过。

## 完整矩阵的剩余门槛

2026-10-19 追加真实浏览器验收：新同源标签页只有 HttpOnly cookie、没有独立 sessionStorage 中的 CSRF，原版本会拒绝修改。已让鉴权后的 GET bootstrap 通过不可缓存响应头提供该凭据；没有放宽 Cookie、Origin 或 Fetch Metadata 检查，也没有加入重试写入。31 项 HTTP 测试通过，未鉴权/伪造 cookie/跨源请求均不能读出 token。双标签页实际确认非法 NUL 配置草稿、另一页最新运行原文和修正后的有效草稿各自保留；目标输入改变后点击旧执行凭据只返回 version 2 预览，不写旧值，重新审阅后才实际提交 File。另一页刷新历史由 3 条变 4 条，原始环境读回与新记录一致。证据 `browser-cross-tab-before-fix.json`、`browser-cross-tab-after-fix.json`、`product-cross-tab-stale-2026-10-19.png`。

同轮使用 `tests_v3/browser_failure_acceptance.py` 的真实 Service/Executor/File 故障注入生成 4 条持久历史和 1 条恢复待办：补偿完成、共享撤销的部分意图、不可补偿的外部 File 值冲突。既未伪造 DB 历史，也不冒充原生 Windows 故障。发现并修复恢复/历史恢复计划缺少方案匹配字段时前端崩溃的问题：展示其冻结前后快照，无 occurrence 审计 ID 时不编造对应关系。恢复按钮提前、原始完整日志折叠。真实页面取消“恢复前值”预览，再独立审阅并执行“接受当前实际值”，保留 `external-fixture-value` 并清除待办。重启后历史显示已确认保留当前值，原始历史快照仍为 old-a/new-a；新增只读处理结果投影，未改写原历史/Audit。旧历史恢复因连续性不符被正确拒绝；同进程的新真实提交可打开两目标历史恢复预览。证据 `browser-failure-recovery-2026-10-19.json` 和 `product-recovery-*.png`。该过程首个旧版预览错误留有记录，不能宣称整个过程中没有错误。

实际 CSS 宽度 391/600px 补查工作台、Windows 环境、配置视图、元素编辑、冻结预览、历史和恢复入口；长中文路径、重复及空元素保留。表格按现有布局提供横向滚动，配置文件长路径新增 anywhere 换行，避免溢出。配置 Tab 的 ArrowRight 切换已实际操作。截图命名 `product-*-391.png` / `product-*-600.png`，尺寸证据 `browser-layout-2026-10-19.json`。未把浏览器尺寸/缩放仿真当作 Windows DPI、原生 IME 或拖动验收；这些人工项目仍待验。

最终后端回归 `backend-recovery-final-2026-10-19.log`：488 项，487 通过、1 项 native pipe opt-in 跳过，84.765 秒。产品前端 34/34 契约通过（含真实恢复 receipt 形状、原文/空值/删除、折叠审计、仅生成预览）；原始 stdout `product-ui-csrf-2026-10-19.log`。当前候选 `024952-878` 的源码/stage/115 包文件/15生产资源一致，File frozen EXE 两次鉴权启动和升级备份/互斥 smoke 通过。

| Astra 范围 | 状态与仍需补齐的证据 |
| --- | --- |
| C01–C04 配置与草稿 | 配置/HTTP/原文测试及真实浏览器已覆盖主要流程；有效工作区分离和重启后的配置差异已与冻结前端合并复验 |
| E01–E02 原始环境状态 | File/mock 与 DefaultBox 原生 User 已验证；真实 VM System 类型与权限仍待验证 |
| P01–P05 规则、排序和元素 UI | 共享规则与旧 UI 回归已执行；四撤销范围和多排序目标已补齐正式 UI/File 提交证据；主要窄屏编辑/预览状态已补查；原生拖动/IME 仍待验 |
| R01–R04 连续性与刷新 | 可信审计、未知通知、ABA/重启、跨标签页原文/草稿保留及旧计划更新已有证据；DefaultBox 原生通知正式 UI 已补验；真实 VM 睡眠/恢复及宿主通知仍待验 |
| X01–X06 执行与恢复 | 故障注入与幂等已有测试；“仅值相等就标补偿”已修复，5 个实际子进程退出边界均通过；原生提升回执仍需真实 VM 验收 |
| U01–U03 权限和 helper | 固定包/pipe/回执实现、mock 和普通用户真实 pipe 已验证；真实 UAC 成功/取消/其他管理员账户、父进程退出和 System 写入仍需隔离 Windows VM |
| H01–H02 历史与撤销 | 贡献级共享/累计/重复、四撤销入口、批量提交和排序历史已验；补偿/部分意图/恢复历史与真实页面恢复对账已补验，原始记录未改写 |
| S01–S02 本地边界与资源 | loopback/Origin/CSRF/路径/恶意 after 及缺失运行时测试；024952-878 已核对最终源码、115 文件和15生产资源，无漂移 |
| L01–L02 布局和输入 | 正式页面主要视图及编辑/预览/恢复状态的391/600px已补查；原生 IME、拖动和100/125/150/200% Windows DPI仍待人工验收 |
| D01–D02 发布与通知 | 最终用户/机器包已编译并核对冻结源码；DefaultBox 用户安装/启动/升级/卸载/数据保留已真实通过。干净 VM、机器安装/多用户/原生广播和真实 UAC 仍待验收 |

安装包的路径、原始 hash、打包依赖/许可证和每轮 smoke 见 [BUILD.md](BUILD.md)。候选包未经代码签名；源文件改变后不能继续宣称旧包与当前源码一致。

本地 ChatGPT Bridge 的 CDP 连接曾返回 `127.0.0.1:9222` 拒绝连接，没有发送网页协作消息；不得将本轮子 Agent 讨论表述成已取得 ChatGPT 网页确认。

最终冻结包 `024952-878` 另由自身 EXE 在 File 后端启动 8788 正式页面。工作区原始运行输入、配置草稿和 1 项未保存差异仍在；配置应用模式严格只有 replace/+append/-append/append，恢复标签不再进入下拉框。最终页面 error/warn 日志为空，截图 `packaged-final-editor-2026-10-19.png`。此为本机浏览器/File 验收，未执行宿主环境提交。

最终候选 `024952-878` 的 DefaultBox 用户安装生命周期也已完成：job `c300f85b0abb400297ae580e03224b6b`，command/child Verified，Completed/exit 0，2026-10-18 18:53:14–18:56:40 UTC。实际首次安装、已安装 EXE/File 检查、第二次 Setup 升级及完整备份、静默卸载、原始未完成 TOML 和合成历史保留均通过，boxed User/System 清单前后相等。测试后已查询 FakeAdminRights=y、DropAdminRights=y；没有宿主 Setup 或宿主环境提交。工具原始结果 `sandbox-installer-lifecycle-latest.json`，安装日志保留在 `C:\Sandbox\Yuri368\DefaultBox\drive\C\EnvManagerV3Acceptance\20261019-user-lifecycle-5\`。本轮只补齐用户安装器沙盒范围，VM/System/UAC/多用户门槛仍未验证。前一候选同样通过的 job `761d83aa59f74577aa5e0ddfb240137f` 单列为历史证据 `sandbox-installer-lifecycle-024527.json`。


## DefaultBox 原生通知正式页面补验（2026-10-19）

在 Windows 11 IoT 企业版 LTSC 10.0.26100 / build 26100 上，通过 MCP 明确指定 DefaultBox 启动 `tests_v3/browser_native_acceptance.py`，job `00a85ea4367245618aed8e234e6c60bb` 的 worker/child 均 Verified，Completed/exit 0。fixture 还在导入环境适配器之前调用 SbieApi_QueryProcess 验证实际进程归属；宿主直接调用已观察到拒绝，且未创建数据目录。该工具是显式 opt-in 验收入口，不进入默认 unittest discover 或产品包。

真实 Service 使用 WindowsEnvironment + 原生 RegistryChangeListener，正式浏览器页面显示 Windows 后端。仅创建原本不存在的 User 变量 `V3_UI_NATIVE_5939F1E64E27`，初值为带前后空白、空元素与 CRLF 的 ` A;B;;中文\r\n尾部 `。页面选择自定义来源并输入 ` B;草稿;;中文\n保留 `，焦点和 selectionStart/End 均在该输入。独立 sandbox 进程通知 fixture 改为 `外部;B;;中文 ` 后，页面未点击刷新、未 reload，即显示新 Windows 当前值及服务端预览；运行草稿原文、输入焦点、光标位置 13/13 均保留，error/warn 日志为空。该外部变化不通过 Service 自身 adapter，原生通知/long-poll/页面刷新链路实际参与。

结束由 fixture finally 删除唯一测试变量，读回 absent；boxed User/System 完整清单哈希恢复一致。没有 System 或宿主 Registry 写入，也没有执行广播、睡眠/恢复或 UAC 测试，不能扩大为这些项目通过。FakeAdminRights/DropAdminRights 均保持 y，本轮未改沙盒配置。工具结果 `sandbox-browser-native-notification.json`，页面及输入证据 `browser-native-notification-2026-10-19.json`，截图 `product-native-notification-2026-10-19.png`。

本轮仅新增验收脚本和文档，生产源码未改；对候选 `024952-878` 再执行 verify-bundle，currentSourceMatchesStaged=true，115 包文件、15 生产资源、30 许可证全部一致，原包 hash 继续有效。README 已补正式运行入口，RUNNING.md 已补候选包独立启动及 File 试用方式。原生 IME/拖动/DPI 需要可用的 Windows 桌面人工控制；浏览器 DOM 控制不覆盖这些操作；原生控制的实际可用性与后续中止情况见下文，未将 fill、合成事件或 viewport 仿真计作原生验收。


## 原生桌面验收中止说明（2026-10-19）

进一步按 computer-use 技能初始化独立 `@oai/sky` 接口，list_apps 和启动已有 Chrome 均成功，因此不能再将原生接口描述为不存在。选择唯一返回的 Chrome 窗口并首次 get_window_state 时，工具因无法可靠识别当前浏览器网址、无法可靠执行其策略检查而主动停止，并要求结束本轮操作。遵守中止要求，没有通过其他桌面接口继续输入、拖动或改变 DPI；IME/拖动/DPI 均未产生通过证据。

为此次尝试启动的 `native-desktop-runtime` 仅使用 File 后端，3 条初始记录由真正的 Service 计划/审阅/执行生成，不能算作原生验收。后续已依据完整命令行及进程 ID 关闭两个属于该 fixture 的 Python 进程（8793），确认无匹配进程残留，保留数据便于追查。没有关闭用户 Chrome 或其他应用。现有 DefaultBox 原生通知证据和 `024952-878` 候选包未受此尝试影响。

继续完成 §18 仍需要可恢复快照的 Windows VM（干净安装、System/UAC、机器安装及多用户等）和可正常工作的原生桌面控制或人工验收（IME、拖动、各档 DPI）。负责人已说明暂时没有 VM，先完成其余项目；这不取消原验收门槛，也不构成宿主 System 写入许可。


## V3 产品持续集成

产品实现、证据与剩余门槛已同步到 GitHub 的 `v3/product-implementation-20261018` 任务分支，未合并 main 或公开 Release。此前 `tests.yml` 只运行 V2 测试，`ui-prototype.yml` 只运行旧演示 npm test；不能用其绿色状态证明 V3 后端通过。

新增独立 `.github/workflows/v3-product.yml`：Windows/Ubuntu，Python 3.13、Node 26.10.0，仅安装 requirements-v3.txt 和 UI 开发依赖，运行 tests_v3 默认 File/mock 测试与产品 JSDOM 契约。tee 后读取真实退出码，始终上传两套原始日志、环境版本和退出码。默认 opt-in native pipe 仍跳过，不执行 Sandboxie、安装器或 Windows 手工 fixture。远端结果以具体 commit/run 为准，工作流存在本身不代表通过，也不代替 VM/UAC/DPI 验收。


V3 产品远端回归已于 commit `3ae084c27ca3f3aac63fa97733b3154853edc763` 实际通过：[run 39158581142](https://github.com/yuri368/windows-environment-manager-v2/actions/runs/39158581142)。Windows job `117766859953`：488 项执行，487 通过、1 项 opt-in native pipe 跳过，102.135 秒；Ubuntu job `117766860110`：488 项执行，481 通过、7 项 Windows 平台/opt-in 测试跳过，38.116 秒。两边产品 JSDOM 均 34/34 通过。步骤状态归档 `ci-product-39158581142.json`，逐测试原始结果行摘录 `ci-product-windows-39158581142.txt`、`ci-product-ubuntu-39158581142.txt`；摘录明确不是完整 job 日志，完整日志与 artifact 可从该 run 获取。

最初 run `39157492362` 两平台均失败：Linux 的 launcher 测试模拟 Windows 却调用宿主不存在的 msvcrt，包模拟还修改全局 os.name 影响 pathlib；Windows 的两项临时路径断言使用未规范化路径，与服务规范化路径不一致。修复限定于测试：隔离平台/锁 mock、临时路径先 resolve，保留生产路径/权限/校验规则。中间 run `39157707701` 和 `39157792181` 仍保留 Linux 平台模拟失败；不能用最终通过覆盖这些事实。没有修改生产代码、放宽校验或跳过失败用例。候选 `024952-878` 的运行代码和既有包 hash 不变。

该 CI 验证 File/mock/真实普通文件与 HTTP/Node 链路，既不是安装包独立启动验收，也不补齐真实 UAC/System、多用户、睡眠恢复或原生 DPI/IME/拖动证据。这些门槛继续保持未验证。

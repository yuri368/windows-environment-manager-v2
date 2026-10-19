# V3 正式产品实现方案与执行交接

日期：2026-10-18。性质：一轮架构分析的实施方案，不是实现完成报告。

## 1. 立即生效的关键裁决

1. **独立 V3 产品，保留已接受的原生 HTML/CSS/JS UI。** Python 层为仓库根目录 `envmanager_v3/`；不得复制、修改或导入根目录 V2 `envmanager/`。不引入 React、PySide6，也不重新设计 UI。首版采用 Python 本地 HTTP + 默认浏览器，提供独立 EXE 启动器和安装包；desktop webview 不作为首版交付前提。
2. **保留现有 `DemoPlan` 为唯一业务计划规则源。** 本方案选定 Python 管理一个私有 Node 子进程，加载正式资源中的同一份 `demo-plan.js`。该进程没有 HTTP 端口，只使用 stdin/stdout 有界 JSON 消息。浏览器可以使用同一模块作编辑反馈，但最终工作台结果、预览、执行、历史均消费后端返回的冻结计划。禁止再写一套 Python 增删算法，禁止把浏览器生成的 `after` 或 `audit` 直接当执行凭据。
3. **承接已分派适配器任务。** `envmanager_v3/environment.py` 与 `tests_v3/test_environment.py` 由当前 Sol 任务负责，包含 stdlib `FileEnvironment` 和原始 `winreg` 的 `WindowsEnvironment`。基础 DTO 保持 `scope/name/exists/raw/type/revision`，类型使用 `REG_SZ`、`REG_EXPAND_SZ` 字符串；写入带 expected。本方案不要求重做该模块；较高层的执行日志、批次协调、预览确认、通知及 UAC 不塞入该适配器。
4. **后端权威边界：意图 → 服务端会话 → DemoPlan → 冻结计划 → 审阅凭据 → 执行。** 执行接口只接收 `planId/version/digest/reviewToken/idempotencyKey`，不接收目标值。服务端重新读取所有相关目标、核对输入及保护集合版本；变化时只更新预览并返回，绝不在同一个确认请求里执行新计划。
5. **持久化采用 TOML + SQLite。** TOML 保存配置，SQLite 保存有效配置映射、配置草稿、运行选择、计划、执行日志、历史和恢复信息。SQLite 使用 Python 标准库。TOML 用 `tomlkit` 保留未编辑文本和注释，`tomllib` 做独立解析校验。UI-only 元素幽灵、动画和采纳来源不进入计划或历史。
6. **真实 Registry 写入许可独立于实现许可。** 本任务授权实现 Windows 适配代码和隔离测试；不授权工程师运行宿主真实 Registry 写入。后续宿主验收须说明精确目标、before/after、备份和恢复方式并取得用户具体确认。本轮只写本文，不实现代码、不运行测试、不启动服务。
7. **执行是带持久日志的可恢复多步提交，不承诺 Windows 跨变量原子事务。** 全目标预检后逐项执行、回读验证；失败执行条件补偿；不能证明安全恢复的目标进入 `recovery_required`。崩溃重启只对账，不自动重放未确认写入。
8. **User 正常权限；System 按本次计划短暂升权。** 浏览器、HTTP 服务、配置和数据库始终普通用户权限。仅固定 System 环境键的执行 helper 经 UAC 启动；User 写入始终由原普通用户进程完成。UAC 取消前不能写入混合批次的 User 项。
9. **必须交付整个链路。** `append` 自定义排序、撤销、导入/保存、历史、刷新、UAC、故障恢复、安装与启动都是首版完成门槛，不允许用演示数据或永久禁用按钮替代。

## 2. 现场状态、优先级和协作边界

- 预览源：`H:\Yuri368\环境变量配置V3`，非 Git；最近已接受 UI 以此处 `compact-ui.css`、`config-editor.js`、`temp.html` 及依赖资源为准。
- 镜像：`H:\Yuri368\环境变量配置V3-git`；本轮只读检查时分支已为 `v3/product-implementation-20261018`，HEAD 为 `cea967532817f68769e53e549d961fbf9dc836cc`，当时存在旧 phase2 的 `ui-v3/shell-ui.css`、`surface-ui.css`、`temp.html` 修改和未跟踪证据目录。
- 随后负责人补充：已创建产品分支并 checkpoint 已接受 UI。此后状态以实施工程师最新 `git status`/HEAD 为准，不依据上述较早观察重置分支、覆盖文件或重新提交其他人的修改。
- Bridge 9222 不可用，DefaultBox Python 进程归属已由负责人验证。本方案不依赖再启动 Bridge 或第二轮架构咨询。DefaultBox 归属不等于真实 UAC、安装包或宿主写入已经验收。
- 优先级：负责人本轮正式产品需求 > 最新专项契约及其后附修订 > 已接受 UI 语义 > 旧 draft。旧 UI-only 的实现范围被本任务升级，但原文、身份、删除资格、建议采纳、冻结计划和确认边界继续有效。V2 原地迁移、默认整理、旧协调架构不随产品化恢复。
- 已核查 README、架构索引、SEAMLESS_REFRESH、WINDOWS_SNAPSHOT_BOUNDARY、V3.2/V3.3、V3.4/V3.6/V3.7、GUI_FEEDBACK 及 supplement、CLAUDE_UI_REPAIR、CONFIG_CREATION（含 10-18 最新补充）、CONFIG_INLINE_AND_DUPLICATE_HOVER、GUI_DISPLAY_MOVEMENT、GUI_STATUS_FOLLOWUP、HISTORY_ELEMENT_LINKAGE、PLAN_REVIEW_PRESENTATION；并阅读 `demo-plan.js`、`config-format.js`、`temp.html` 关键生成/执行代码、`windows-elements.js` 审计/应用代码。旧 migration/changeplan drafts 只作方向参考。
- 源码事实：`recordsFor` 对同目标聚合；`confirmPreview` 只同步更新内存；`applyDemoPlan` 无真实适配器；`renderPreview` 对 append 与撤销禁用；`downloadToml` 只下载；`reset(true)` 注入虚构环境；元素编辑器 `onApply` 现在是同步回调。以下章节明确替换这些缺口。

## 3. 技术栈与依赖

| 层 | 首版选择 | 实施要求 |
|---|---|---|
| Python | 3.11+，发布固定一个经验证的 x64 解释器版本 | stdlib dataclasses/json/sqlite3/http.server/threading/subprocess/winreg/ctypes；独立入口 `python -m envmanager_v3` |
| HTTP | 有界 `ThreadingHTTPServer` + 显式路由表 | 仅 127.0.0.1 随机端口；读请求可并行，状态修改经单一协调锁；不作为公网服务器 |
| UI | 当前原生网页资源 | 正式入口 `ui-v3/index.html`；保留 CSS 加载次序、token/ghost/原文/键盘交互；`temp.html` 保留为开发参考 |
| 计划规则 | 现有 `demo-plan.js`，受控 Node worker | 打包固定 Node runtime；启动时握手协议版本和规则文件 SHA-256；不依赖用户 PATH，也不启动 Node 网络服务 |
| TOML | `tomlkit==0.13.3` 起步，锁定并验证 | 此版本来自现有 V2 依赖记录，不声称是最新版；V3 自己定义依赖，不导入 V2 模块 |
| 持久化 | TOML + stdlib SQLite | WAL、synchronous=FULL、foreign_keys=ON；DB 位于本地数据目录，单写连接/线程 |
| 构建 | PyInstaller onedir + Inno Setup | 构建依赖锁定；Node、网页资源、许可证一起打包；不采用首次启动联网安装运行库 |
| 测试 | unittest + 当前 Node/JSDOM 检查 + 浏览器集成 | 测试依赖仅开发使用；隔离环境与真实环境采用同一 HTTP/服务/计划/执行路径 |

选择 Node 是为保留已验收规则并缩短正式接线路径，不是另建服务架构。worker 使用 Node 内建模块，通过 `vm` 加载现有 IIFE 或给同一文件增加不改变浏览器全局的 CommonJS 导出；前者 vm 不是安全沙盒，加载对象只能是随产品发布的可信文件。worker 超时/崩溃仅使本次计划请求失败，不能降级采用浏览器计算结果写系统。安装包须包含 Node 许可证和固定哈希清单。

## 4. 最小接口裁决（供适配器与前端立即对齐）

```text
Environment.read(scope, name) -> Snapshot
Environment.list(scope) -> Snapshot[] + per-item read errors
Environment.compare_and_write(expected: Snapshot, desired: ValueState) -> Snapshot
Environment.compare_and_delete(expected: Snapshot) -> Snapshot

Snapshot = {scope, name, exists, raw, type, revision}
ValueState = {exists, raw, type}
exists=false => raw=null, type=null
exists=true  => raw:string, type:REG_SZ|REG_EXPAND_SZ
revision     => opaque string to API clients; never accepted as deletion evidence alone
```

若已分派实现的方法名称不同，在 `environment.py` 外通过薄封装统一，不推倒重写。文件适配器用锁和原子替换保证其内部 CAS；Windows 适配器的 expected 比较是读-比较-写检测，不可声称跨进程 Registry 原子 CAS。业务层必须在执行前后补足核验和恢复。

```json
{
  "intent": {
    "kind": "scheme_apply",
    "workspaceId": "ws-uuid",
    "workspaceRevision": 17,
    "selectionRevision": 24,
    "requestIds": ["variable-record-uuid"],
    "orderSessionId": null
  },
  "plan": {
    "planId": "plan-uuid", "version": 1, "digest": "sha256:...",
    "status": "ready", "targets": [], "issues": [], "requiredScopes": ["User"]
  },
  "execute": {
    "planId": "plan-uuid", "version": 1, "digest": "sha256:...",
    "reviewToken": "opaque-one-use", "idempotencyKey": "uuid"
  }
}
```

`targets` 由后端提供，每项包含真实 before、确定类型的 after、operations、audit（如有）、matchResult、issues、参与记录和目标依赖版本。intent 不接收 frontend baseline/removalState/protectedValues/operations/after。手动编辑的输入为后端编辑会话对应的 live rows 与显式新增/删除意图，由服务端校验身份并重建审计；它不是执行接口的任意 after 入口。

## 5. 目录与模块责任

主目录开发，按明确文件清单同步镜像；镜像中的网页位于 `ui-v3/`，Python 包仍在根。开发模式通过启动参数确定网页资源根，发布模式只用打包资源根，不能由 HTTP 请求改变。

```text
envmanager_v3/
  __init__.py, __main__.py       # 入口、CLI、正常退出；无导入时系统读写
  application.py                # 组装依赖，唯一应用协调对象
  environment.py                # Sol 已负责：FileEnvironment / WindowsEnvironment
  dto.py                        # DTO、严格字段校验、错误码；不携带 UI 对象
  configuration.py              # TOML 解析/完整校验/有效树与文档 AST
  config_files.py                # 固定工作区文件、哈希条件保存、备份与恢复
  repository.py                 # SQLite schema、事务、按 ID 查询
  sessions.py                   # 选择编辑基线、元素会话、配置与输入版本
  planner.py                    # Node worker 生命周期、请求关联、计划封存
  planner_worker.cjs             # 适配 DemoPlan、验证完整元素身份/审计
  snapshots.py                   # 目标缓存、opaque revision、已知/未知变化传播
  execution.py                   # 单写队列、全批预检、逐项执行、条件补偿
  recovery.py                    # 启动对账和显式恢复；不自动重放
  history.py                     # 历史查询、撤销范围、可撤销贡献与恢复预览
  http_api.py                    # 固定路由、鉴权、限额、JSON DTO
  windows_events.py              # RegNotify、环境变更广播；File 后端不调用
  elevation.py                   # UAC 启动、私有 pipe、helper 生命周期
  elevated_worker.py             # 固定 System 键计划执行及持久回执
ui-v3/
  index.html                    # 保留当前 DOM/样式资源；去掉初始化 mock
  app.js                        # 从 temp inline script 提取的原生事件与渲染
  api-client.js                  # 会话鉴权、错误、操作查询；禁止自动重试执行
  product-state.js               # 服务端事实缓存、seq、dirty、focus恢复
  demo-plan.js                   # 唯一规则源，名字可保留；不是生产 mock
  element-audit.js               # 从 windows-elements 抽出的纯身份审计 helper
  windows-elements.js           # 原 UI 与会话投影，异步应用适配
  config-editor.js, config-format.js, compact-ui.css, ...
tests_v3/
  test_environment.py            # 已分派；不要并行修改
  test_config.py, test_sessions.py, test_planner.py
  test_execution.py, test_recovery.py, test_http.py, test_history.py
  fixtures/                     # 成对输入/计划 JSON、TOML、异常文本
  integration/                  # 同 HTTP 路径 File 后端端到端、Windows VM验收
packaging/v3/
  requirements-runtime.txt, requirements-build.txt
  EnvManagerV3.spec, installer.iss, build.ps1, verify-package.ps1
docs/product-v3/                 # 实施后写：操作手册、恢复说明、验收证据
```

模块数量允许按自然大小合并，例如 `history.py` 与 `recovery.py` 首期共用 repository；不要建立通用插件、事件总线、DI 框架或多服务。`environment.py` 是唯一普通权限 Registry I/O 适配层；System helper 复用同一白名单适配能力，不导入业务 UI。所有写入口最终只能抵达 `execution.py` 或其受控 System helper。

## 6. 数据语义与身份

### 6.1 环境值与精确字符

- `exists=false/raw=null/type=null`、`exists=true/raw=""`、`exists=true/raw=";"` 是三个不同状态。不能用 truthy、`dict.get(...) or ...`、空数组回退将它们合并。
- 普通值完整按 `;` 切片；`""` 是一个空元素，`"A;"` 是 A 与一个空元素，`";"` 是两个空元素。无变量才是零个原始元素。保留重复、顺序、首尾空格与所有原始 CR/LF。
- `trim + case-sensitive` 只用于现有方案匹配/缺失分析；不得用于 Registry 字符串、序列化或身份。选定重复项参与现有匹配计数，实际新增只补一次缺失的 normalized 值。Python 不重新实现 JS trim 语义，交由同一 DemoPlan。
- Special 是配置中 scope + 变量名的全局一致元数据，不是由当前选中方案推断；完整字符串一个元素，分号不拆分。Windows 未声明目标默认普通编辑。
- 现有支持的 Registry type 保持不变，包括 `%FOO%` 原样，不展开，不因值包含 `%` 擅改类型。新增目标默认 `REG_EXPAND_SZ`（明确的 V3 产品规则，支持引用）；预览须给出最终类型，用户可在直接创建/编辑的可选属性中明确选 `REG_SZ`。类型修改必须产生独立可见操作，不能在零操作计划里偷偷改变。
- DemoPlan 现有缺失状态可能携带 null 类型；由 worker 在冻结前按上述规则补全，after 不存在统一清空 type。类型补全/变化属于同一计划构建流程，不允许执行器临时推断。after/raw 未变但 type 变时仍需写入并记录 TYPE_CHANGE。
- Windows 非字符串 Registry 类型、不合法 UTF-16/NUL、异常长度或不可解码数据不伪装成 absent。inventory 返回 `readError/unsupportedType` 与原始类型提示；目标写入被阻止，别为了纳入 DTO 强行转换成文本。首版无需修改这些异常值，但不能因此无法浏览其他正常目标。
- 用户输入/配置值上限沿用 32766 UTF-16 单元，Python 用 UTF-16 单元计数；不得按 Python 字符数或 UTF-8 字节数。API 传输 JSON 字符串保真，日志避免把原始值放 URL。未编辑 textarea 的 CRLF/CR/LF 与前导换行继续用现有 beforeinput 映射保留。
- 名称判重统一采用 `ConfigFormat.matchName` 的逐字符单字符大写规则，不能继续让 `temp.key`/DemoPlan 全字符串 `.toUpperCase()` 与配置判重不一致。worker/前端共用一个纯 helper；Windows 上额外核对注册表实际命名碰撞，发现不同规则折叠不一致则明确拒绝该冲突配置，不静默合并。禁止对元素值使用此名称规则。

### 6.2 配置 ID、元素 ID 与编辑基线

- 正式 app/profile/variable/choice 采用服务端 UUID，SQLite sidecar 映射；TOML 不加入内部 ID。配置编辑提交按 ID 操作，展示下标只作导航。加载相同文件时按精确 app/profile 路径 + canonical target 关联旧 ID；重命名视为新身份，不能用相似度继承执行权。
- 命名但未完成的 app/profile/variable 保存在独立 `config_drafts`，草稿 UUID 与正式 ID 分开。添加首变量时完整校验并原子创建正式子树；失败不留下半个正式节点。取消只清理当前草稿，暂停的其他草稿继续存在。
- 编辑器 original occurrence ID 由后端打开会话时签发，例如 `editorUUID:o:0`，对应固定 snapshot 原位置和原文。new 行 ID 是该会话命名空间中新建 ID，由服务端分配或校验一次性 client nonce 后映射；同值新行不能冒充 original。
- submitted live rows 必须 ID 唯一、所属会话正确、每个 original 最多一次；缺失 original 表示明确删除，新增条目不得自带 beforeIndex/beforeRaw。服务端从不可变 originalRows 和 submitted live rows 重建完整审计，校验全部 old/new 索引覆盖，绝不信任前端布尔标记。
- `element-audit.js` 提取现有 `editStateFor/auditSnapshot` 的纯部分，浏览器与 Node 使用同一代码。业务 MOVE 按存活 original 的相对顺序；ghost 展示移动、manualAddedIds、adoptionMap、removalAdoptions、deletedDrafts 不进入该 helper 的业务输入。
- 原有 ghost 取消删除恢复删除瞬间草稿；绿色建议删除撤销恢复原始值；保持相同 ID 与当前 displayOrder 位置。恢复后的 live rows 才传给后端重新建计划。
- selection 编辑基线在服务端接受第一笔真正改变值的命令之前捕获。它记录服务端旧 Selected、实际 snapshot 和 context；用户连续输入、刷新、取消预览均不能替换它。source/target/mode/special 改变按契约结束旧上下文，保留用户可见草稿但不迁移删除权。
- removalState 只在当前后端 session 内是有效连续性证据。数据库可保存输入草稿与 lastAppliedSelected，但不能在进程重启时从旧 removalState 恢复自动删除权。重启后保留用户输入，旧删除意图标为 continuity 未解决；需要删除时显式编辑 occurrence。

### 6.3 后端工作区、配置草稿与运行状态

配置编辑仍可以即时影响工作台，但必须是服务端完整校验成功的 **有效工作区版本**。设计三层：磁盘配置基线 → 最后有效 working configuration → 可包含非法值/空容器的编辑草稿。非法修改继续展示和持久化草稿，不替换有效工作区；工作台明确说明使用哪个有效版本。创建成功尚未保存 TOML 时，工作台/预览可以使用该有效 working revision，并标明“配置尚未保存”；历史必须保存当时的配置内容指纹和来源，不能重启后找不到解释。

配置保存是单独动作，写成功才更新 config baseline。运行 apply 不保存 TOML；配置 restore 不改 Windows/history。配置 changes 按对象 ID 关联，保持原 `WeakMap` 行为的稳定性。成功 apply 的 savedValue 是当次 selectedRaw，不是合并后的完整 Path；active 只在该应用本次请求的所有记录完全解决后更新，不能某一变量成功就将整个方案标为成功。

## 7. 计划生成、共享与排序

### 7.1 私有 worker 协议

```json
{"requestId":"rpc-uuid","protocol":1,"method":"buildBatch","args":{"target":{},"snapshot":{},"requests":[],"protectedValues":[]}}
```

允许方法仅 `hello/captureBaseline/createRemovalState/syncRemovalState/advanceRemovalState/build/buildBatch/buildElementEdit/validateConfig` 及明确实现的审计/排序/撤销构建函数。`args` 全部由 Python 会话层构造，不透传 HTTP JSON。stdout 只输出协议结果，stderr 输出有界诊断；每条消息限制长度、超时、requestId，worker 顺序处理。已写入的执行不能因为 worker重启而重新执行。

`planner.py` 封存结果时绑定 `engineVersion/rulesHash/workspaceRevision/selectionRevision/editorRevision/protectionRevision` 和逐目标 snapshot revision。不可变计划保存到 SQLite，digest 由后端规范 JSON（稳定键顺序，数组保序，字符串不归一化）计算；浏览器只显示 digest，不参与摘要算法。后续执行器不得重新算 after；需要重算则产生新 version 并重新审阅。

### 7.2 模式与批次

- replace：整体覆写 selectedRaw，空串也是存在的值；普通 replace 的匹配判断仍沿用现有集合满足判断，真正计划比较 exact raw。不要把“匹配”当作无需写入。Special 同样整体替换。
- +append/-append：复用当前 build/buildBatch 的前置/后置、保留现有 occurrence、不自动整理和编辑基线删除规则。多个请求的前置顺序与当前 buildBatch 输入顺序一致，输出应覆盖专门 golden case，禁止因按名字排序悄悄改变结果。
- 同目标只生成一份计划、一次最终写入。请求顺序按 UI 批次选定顺序冻结；相同 ADD 合并；普通/Special 混用、replace 与 append 混用、不同整体替换值均整批阻塞。一个目标 blocked 则全批不启动，用户必须改范围再预览。
- duplicate/shared/continuity 是不执行的删除意图，可执行安全 ADD/REMOVE 仍在 after 中。确认安全变更后状态是 `applied_partial_intent`，保留原输入、基线和未完成意图；与执行中途失败的 partial/recovery 状态明确区别。
- 零 operations、零未解决意图：允许确认选择并持久化选择状态，不新增环境修改历史。零 operations 有未解决删除：保留 pending，不把方案标成完成。

### 7.3 共享保护的准确范围

服务端计算保护集合，浏览器不能传入空集合绕过它。最低包含：其他当前有效选定方案的目标、同批其他成员最终目标；再按 V3.2 的书面范围，保护未勾选应用各方案中当前实际匹配的声明，以及 V3 历史仍有效的已应用贡献。对非当前方案没有用户自定义输入时使用已记录成功选定值，否则使用配置默认声明；不把所有 choices 都视为已激活。scope 分开；元素 normalized 比较沿用规则。

当前 `protectedSelections` 只扫每应用当前方案，不足以覆盖书面契约中的未勾选方案范围。补足是产品写入前必要修复；输出明确 `protectedBy:[{appId,profileId,variableId,reason}]`，区分“当前选择”“已匹配声明”“已应用贡献”。保护不等于软件所有权，不声称扫描所有外部软件。前端撤销预览可对具体共享项选择保留/明确撤销，默认保留；这一显式选择需进入 plan digest，不能由 apply 请求临时带 `force`。

### 7.4 补齐 append 自定义排序

不能简单删除 `needsOrdering` 禁用条件。流程是：后端先按现有规则得到候选结果（仍不执行）→ 给候选 surviving original 与新增项签发 ID → 复用现有元素列表的拖动/上下移动交互作排序确认 → 上传 `orderSessionId + orderedIds + candidateVersion` → worker 验证是候选 ID 的完整唯一排列 → 生成含稳定身份 MOVE 的最终冻结计划。

排序阶段仅允许排列，不接受任意 after/raw，不允许借排序删除/替换候选；要编辑内容则进入独立手动编辑会话。取消排序保留方案输入。候选因刷新变化时排序版本过期，保留旧排序草稿供查看，不按文本套用到新候选；重新确认新候选后才能预览。多个 append 请求同目标只有一个合并排序会话，不能最后提交者覆盖前一方案。

### 7.5 手动 Windows 编辑

打开编辑器先请求服务端会话和 snapshot；Windows 页只提供 snapshot、target、static Special 元数据，不注入方案建议。工作台 append 入口可提供单独的 suggestion projection，但只有最终 live rows 构成手动计划。工作台 replace 禁止进入元素编辑器的已接受交互保持。

直接新增变量使用名称/作用域/原始值/类型的紧凑表单，服务端校验目标不存在；已有目标必须转为明确编辑，不静默覆盖。删除整个变量是显式 `delete_variable` 意图，和把值设为空串分开，适用于 Special。普通删除全部 live rows 是 absent；缺失变量打开后没有 live rows 且不修改仍是 no-op。

编辑器“应用”首先生成/取得服务端最新 session plan，展示其 before/after 后走同一 review/execute 路径。既有弹窗可承载审阅，不必再造新页面。异步结果不明时保持弹窗草稿并显示操作 ID；收到 verified success 才关闭。取消/过期/UAC拒绝/回读失败均不丢 rows、IDs、displayOrder、deletedDrafts。

## 8. 快照、refresh 与确认协议

### 8.1 revision 的真实含义

环境适配器 revision 是观察令牌，不是“拥有变量”的证明。`snapshots.py` 维护 `{bootId, targetSequence, contentDigest, continuityEpoch}`，向 HTTP 只输出 opaque revision。哈希覆盖 exists/raw/type（可记录实际名称拼写），而不是展开后的进程环境；不能只用 raw 哈希检测 A→B→A。

FileEnvironment 可用持久 target counter 精确记录写入；未知直接文件变更仍撤销连续性。Windows 正式版只读环境键，通过 native 通知、窗口 refocus、手动 refresh、执行前读回触发扫描，不使用高频轮询。`RegNotifyChangeKeyValue` 是 key 级、一次性通知，要重注册；它不提供可靠逐 value 操作日志，也有无法检测的恢复方式。[Microsoft RegNotifyChangeKeyValue](https://learn.microsoft.com/en-us/windows/win32/api/winreg/nf-winreg-regnotifychangekeyvalue)

**不能作出的承诺：** 普通 Registry 读取+通知无法证明所有外部 ABA 都没有发生。若收到无法归因的 key 事件、监听中断/重建、休眠恢复或后端重启，则受影响范围的自动删除连续性失效；同文本出现不恢复资格。相关计划只保留可安全操作。未知 key 级事件不能定位变量时，保守撤销该 scope 活跃删除会话的资格。这是实机可观察能力的明确边界，可能使同键其他变量的未知写入影响删除预览；不能伪称实现了完整 value 级版本追踪。

普通内容变化以 target 定位；**已知本产品写入**使用完整、经回读验证的计划 audit 传播到同目标所有会话，包括非当前方案。无关目标的已知写入不使当前预览失效。无法排除交叠外部修改时不能仅凭“看起来等于 after”把事件归因给自己，应降为未知连续性。原 DemoPlan `advanceRemovalState` 的 integer +1 要通过内部 targetSequence 传入，不能直接拿 HTTP opaque revision 做加法。

### 8.2 刷新与输入竞态

前端选定值修改带 `clientSequence` 和 `expectedSelectionRevision`；服务端按会话顺序接受，旧请求不覆盖新输入。输入可短暂 debounce，但打开预览、切换上下文、保存前必须 flush 已发生的输入并等待 ACK。响应带 revision，前端丢弃旧版本，不显示旧计划为最新。

工作台每次收到有效投影展示服务端 plan；输入尚未确认时标“计算中”，禁用执行，不把浏览器乐观结果标为最终可写值。refresh 不改 OriginalSelected、不重新 capture MatchedBaseline；目标计划更新后保留光标、原文展开、滚动和合理焦点。选择/配置被其他标签页修改时按 revision 冲突提示，不 last-write-wins。

### 8.3 review 与 execute

1. `POST /plans` 返回 planId/version/digest。初始打开预览完成渲染后可登记该初始版本已展示，维持现有首次预览一次最终确认的交互。
2. 相关 snapshot/context/protection 改变，计划 version 增加、所有旧 reviewToken 失效；预览不关闭。更新后的版本必须经过用户单独点击“已查看更新，继续确认”获得 reviewToken；此操作绝不写 Registry。
3. `POST /plans/{id}/execute` 先在 SQLite 认领 idempotencyKey，再在单写协调区检查 plan status、digest、review token、工作区/选择/保护依赖、所有 target 最新完整状态。发现变化只返回 `409 PLAN_REFRESHED` 与新预览，零写入；再次审阅之后才能执行。
4. 请求因网络超时返回未知时，客户端只 `GET /operations/{id}` 或按 idempotencyKey 查询，禁止自动再点、创建新 key 或重建计划后自动执行。相同 key 重复请求返回同一 operation；相同 key 配不同 plan 返回冲突；同一 plan 已执行不能换 key 再执行。
5. 抢占执行后冻结该计划依赖的配置/选择修改，其他读请求照常；相关 mutation 返回 `423 OPERATION_IN_PROGRESS`。执行完再处理新的输入。多个窗口共享同一服务/单写锁。

## 9. TOML、持久状态与文件边界

### 9.1 默认路径和简化文件策略

默认数据根 `%LOCALAPPDATA%\EnvManagerV3\`：`settings.json`、`state.sqlite3`、`workspaces/<uuid>/config.toml`、`backups/`、`logs/`。配置导入通过浏览器选择 `.toml` 上传内容，后端保存工作副本；默认不覆写导入来源文件。导出是有效 TOML 下载；“保存配置”写当前工作副本，UI 显示路径和最后保存时间。这样既是真实文件保存，也不把任意路径写入能力开放给网页。

可在启动器 `--data-dir` 明确指定一个本地绝对目录（例如 H 盘工作区），启动时固定，禁止 HTTP 修改；解析真实路径、拒绝设备/UNC/重解析点逃逸及不可写目录。首版不支持网络盘 SQLite，也不自动搬迁数据。不开放任意 filesystem API、任意命令、任意 Registry key path 或动态模块路径。用户如要配置外部文件原位保存，作为明确启动绑定扩展；首版现有工作副本保存+导出已构成完整可运行路径。

### 9.2 文件保真和事务

- V2 同结构 UTF-8 TOML 可直接导入，保留 default_option、default_value、scope、mode、editable、special、choices 和双 scope 数组；缺省 default_option 取首方案，序列化只在明确修改或另存时补齐。无效结构/未知字段提供定位错误，不静默删字段。
- 保存所有语义字符串的真实值；未修改文件可逐字节原样返回。可视化编辑用 tomlkit AST 局部修改，保留未碰触注释/布局；若某语法形式不能局部保持，生成合法等值 TOML 并显示文本 diff，不能无提示整体格式化。解析/序列化必须通过 round-trip 验证，特别是反斜杠、引号、控制字符、DEL、CRLF、中文和前导 LF。
- 文件并发：读取基线 bytes hash，保存时比较；磁盘已外改返回 `FILE_CHANGED`，保留草稿并提供重新载入或另存，禁止直接覆盖。
- 保存流程：SQLite 记录 save intent(oldHash/newHash、备份路径) → 写同目录临时文件 flush/fsync → 校验回读 → 再核对原文件 hash → 原子 replace → 回读新文件 → SQLite 提交新 config revision。过程不使用 shell 拼接文件命令。
- 文件与 SQLite 不可能同一原子事务。崩溃恢复依据文件实际 hash 为 old/new/other 完成对账；other 标为文件冲突，不能自动覆盖。备份在替换前已落盘，失败不能谎报保存完成。
- 浏览器输入非法时 `saveDraft` 仍可保存到 DB，但正式 Save/Export 不能通过。Ctrl+S 改为保存当前工作配置；有未完成命名草稿时提示这些草稿另已保留，不把空容器写进 TOML。

### 9.3 SQLite 最小表

| 表 | 关键内容 |
|---|---|
| meta | schemaVersion、安装/数据版本 |
| workspaces | ID、文件相对路径、磁盘 hash、有效 config revision、内容快照 |
| config_nodes | 稳定 UUID、parentId、kind、精确名称、记录关联 |
| config_drafts | workspaceId、draftId、owner/parentId、草稿内容、baseRevision、updatedAt |
| selections | variableId、source kind、choiceId、selectedRaw、lastAppliedSelected、pending 标记 |
| plans | planId/version、digest、engineVersion、依赖、完整冻结计划、状态 |
| operations | operationId、unique idempotencyKey、planId/version、事务阶段、结果分类 |
| operation_targets | 操作顺序、before/after、write intent、verified readback、补偿状态、错误 |
| history | 已完成执行记录引用、UTC 时间、来源、确切 snapshots/审计/部分结果 |
| contributions | 应用/方案/记录与历史实际操作的关系、是否仍可证明、撤销关联 |
| config_saves | 文件保存意图与最终 hash，用于跨存储对账 |

不把 UI 数组下标持久化为业务身份；时间存 UTC，界面本地化。DB 失败/磁盘满时停止新系统写入，保留已存在的恢复凭据。schema 升级前用 SQLite backup API 备份，事务迁移；旧可执行文件遇到更高 schema 只读报错，不能初始化覆盖。恢复/活动操作涉及的历史不可清理，首版不做自动过期删除。

## 10. 真正执行、故障补偿与启动恢复

### 10.1 执行顺序

```text
计划准备 → 审阅 → 确认认领 → 获取所需 System helper（如有）
→ 全目标重读/权限预检 → durable PREPARED
→ 对每项：durable WRITE_INTENT → expected compare → write/delete → 回读 → durable VERIFIED
→ 全批终验 → durable COMMITTED（含 selections/history） → 广播通知

任一步出错 → 停止后续写入 → 对已写项逆序条件补偿
→ ROLLED_BACK 或 RECOVERY_REQUIRED
```

1. 执行器在任何环境写入前持久保存整个计划的 before/after 和 scope，以及本次确认的规则版本。不能先写 Registry 再尝试建历史。
2. 全目标检查包含 read errors、支持的类型、长度、Registry 权限、配置和共享依赖、适配器身份、精确 before/revision。File 后端 plan 不能拿给 Windows 后端执行。
3. 全批过检之后仍须逐目标在写前 compare expected；多变量不存在跨目标的系统级锁。写入顺序固定在计划中，首版 mixed batch 先 User 后 System；System helper 必须提前准备成功才开始 User，失败逆序补偿。
4. 每个目标持久 WRITE_INTENT 后调用适配器；写成功立即读取 exists/raw/type 验证。Registry 写调用返回成功不等于业务成功。每个目标至多一次正常写入；补偿是另列的恢复操作。
5. 全批结束重新读取全部目标，核对确实等于各自 after；所有回读和操作完成状态、selection 更新、历史写入在一个 SQLite 事务内提交。若该提交失败，不能显示成功或更新 active；标为实际值已变但记录待对账。
6. 广播失败单独记 `notification_warning`，不得回滚已正确提交的值。广播只通知其他程序重新读环境，不保证已经运行的任意进程立即改变其环境块。[Microsoft Environment Variables](https://learn.microsoft.com/en-us/windows/win32/procthread/environment-variables)
7. RegSetValueEx 类 API 是逐值写入接口，本方案不使用 Registry 事务与 SQLite 拼接出虚构原子性。适配器实现需正确处理字符串类型和终止符；封装 `winreg` 之外的 ctypes 调用必须检查返回码。[Microsoft RegSetValueExW](https://learn.microsoft.com/en-us/windows/win32/api/winreg/nf-winreg-regsetvalueexw)

### 10.2 有限的并发保证

本产品的进程锁、target revision 与 expected 比较能串行化自己的操作，不能锁住任意第三方 Registry 写入者。最终 compare 与 write 之间仍有很短的竞态窗口；读后验证也不能证明期间没有等值 ABA。必须在手册和风险记录中保留此事实，不声称严格原子 CAS。检测到变化则停止并报告；没有被系统观测到的竞争不能被软件凭空消除。

因此自动补偿只限本次仍存活执行中、具备已确认写入/回读证据、current==after 且无已知 continuity 失效的目标。补偿前重新读取，恢复精确 before 的存在性/原始字符串/类型，恢复后再回读。发现 current 既非本次 after，或观察到外部改变，则保留 current 并记录冲突；不采用 force restore，也不按文本删除“可能是我们加的”元素。

### 10.3 崩溃恢复表

启动先读取未终结 operations 和 System receipts，只读取系统对账；恢复未完成之前禁用新的冲突写入，浏览/导出/草稿保存仍可用。

| 最后持久阶段 | 当前状态 | 处理 |
|---|---|---|
| PREPARED，无 WRITE_INTENT | 与 before 相同 | 标为未执行/中断，原 plan 不自动重放 |
| WRITE_INTENT，无 VERIFIED | 等于 before | 记录“当前为原值，是否曾写后又变回无法证明”，不补发 |
| WRITE_INTENT，无 VERIFIED | 等于 after | 标为可能已写，收集 helper 回执；不能仅凭相等声称已提交 |
| WRITE_INTENT，无 VERIFIED | 两者皆非/读失败 | recovery_required，保留证据与当前值 |
| 全部 VERIFIED，无 COMMITTED | 当前仍等于 after，证据完整 | 可完成记录对账并明确标“恢复确认”；不重新写 Registry；不恢复旧自动删除资格 |
| 部分 VERIFIED | 任意 | 显示每项已验证/未知/未执行，提供新恢复预览 |
| COMMITTED，HTTP 响应丢失 | 任意 | 返回同一 operation 的已提交结果；最新检测单独显示外部变化 |
| COMPENSATING 中断 | 任意 | 按每项目标对账，继续恢复须新预览确认，不自动补发 |

用户选择恢复时创建新的 recovery plan，引用 original operationId，读取最新 snapshot、展示拟恢复值与冲突，走 review/execute。若无法证明可安全整体恢复，用户可以进入现有手动编辑器逐 occurrence 决策。历史保留失败/补偿记录和实际影响；“回滚完整”必须全部回读 before 成功，“回滚不完整”不能写成无影响。

主进程磁盘满时，已写项的 WRITE_INTENT 仍应存在。不得不断重试写；停在错误页面，提供操作 ID 和恢复入口。System helper 的独立回执用于补足其已执行而主进程未记录的情况。

## 11. User / System 权限与 helper 协议

### 11.1 正常运行与精确升权范围

- 主 EXE manifest 为 asInvoker，正常启动不申请管理员。User 是启动用户的 `HKCU\Environment`；System 是 `HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment`。不写 Process、Volatile Environment、任意服务键或配置中给出的路径。
- Windows 读操作尽量使用最小读权限；System read 被策略禁止时显示权限错误，不用“空快照”代替。User 写失败不自动把整个程序重启为管理员。
- 计划包含实际 System 写入才用 ShellExecuteExW `runas` 启动固定安装路径的 `--elevated-worker`。纯 User、纯 no-op、读/保存 TOML 均无需 UAC。
- helper 只处理本次操作的 System 目标；不读写管理员账户的 HKCU，不接管用户 DB、不重启主 UI。混合计划 User 始终由原用户进程写，避免“输入另一个管理员密码”导致写错用户环境。

### 11.2 有界通信与授权

1. 主进程创建随机命名的本机 named pipe，启用拒绝远程客户端、first-instance 标志和显式 DACL（启动用户 SID + Administrators/SYSTEM）；启动 helper 参数只含 pipe 名、父 PID、操作 ID 和随机握手 nonce，不含原文值/任意脚本路径。
2. 双方核对 pipe 对端 PID、父进程 handle、用户 SID/会话及协议版本；nonce 一次使用。取得主程序固定位置与规则哈希；不能仅相信客户端自报 SID 或 executable path。UAC 取消返回明确 cancelled，混合计划保持零写。
3. 握手后传入有界 frozen execution envelope：operationId、planId/version/digest、操作者 SID、规则版本、System targets 的 exact before/after、操作顺序、关联确认凭据。helper 自行校验 schema、白名单 scope/key、支持的 type/length、重复目标、before 状态和 digest，拒绝任何绝对文件写入路径或可执行命令字段。
4. helper 运行相同 V3 规则代码校验计划一致性（含传入冻结 inputs 与 operations/after 一致），但权限批准来自本次 UAC 与绑定计划，不把哈希当作身份认证。必要的 System 目标/数量在原预览中完整展示；提升后计划若变化则退出并要求重新预览，不能让 helper 执行新值。
5. helper 为本次 operation 创建自己的 durable receipt，再回报 PREPARED。主进程必须等所有权限/预检完成后才进入全批写入。每个系统目标的 WRITE_INTENT/VERIFIED/COMPENSATED 先写 helper receipt，再返回 ACK。
6. helper 不暴露网络端口、不接受新的计划或任意后续请求。协议允许当前计划的逐项 commit、受条件约束的 compensation、query、finish。父进程断开后停止接收写命令，保留 receipt 并退出；不能自行继续余下目标或猜测回滚。主进程在 command 发出后断线视为结果未知，只查询原 operation。

生产 helper receipt 固定存于 `%ProgramData%\EnvManagerV3\receipts\<operatorSid>\<operationId>.json`，只允许管理员/SYSTEM写、操作者读；目录由安装器/提升进程创建并核验 ACL 与非 reparse point。路径由 SID/UUID构造，不能接受客户端指定位置。每次状态更新临时文件+flush+原子替换；采用固定递增 receiptSequence。只有提升进程可更新它；普通用户进程导入 SQLite 的副本不是权限凭据。残留 receipt 的查询也必须核对 SID、operationId、计划 digest，不能因收到某路径就读取任意文件。

开发阶段可先以进程内 helper double 验证协议；正式 System 功能必须用真正 UAC/pipe/receipt 验收。ShellExecuteExW 使用参数数组转 Windows command line，不经 PowerShell/cmd 拼接。安装在普通用户可修改目录的 EXE 不能声称具有抵抗该用户恶意进程篡改的能力；首版安全边界是防浏览器跨站调用、任意 API 参数和错误目标，不能防已控制同一账户的恶意软件。正式机器安装将程序/Node/规则资源放 Program Files，写权限归管理员。

## 12. 撤销、历史恢复与共享冲突

必须区分三个用户动作；不能给它们共用一个“把当前值改为旧字符串”的实现。

### 12.1 撤销配置（现有四个范围入口）

当前变量/当前方案/当前应用全部方案/已勾选方案，先按稳定记录 ID 解析范围，再查询 V3 已成功历史的实际贡献，按 canonical target 合并。scope、原始操作来源和 requestId 均保留，不能按显示名称猜历史。

- 存在可证明连续性的贡献：从当前快照逆向移除仍属于该记录的实际 ADD；恢复该记录实际 REMOVE/REPLACE/MOVE 需要完整稳定映射与无冲突条件。只撤销记录的贡献，不删除原来就存在且当时 no-op 匹配的元素。
- 一个新增值被多个记录共同满足时记录多条关联，不能为每个记录生成一次 ADD；撤销其中一个仍有其他依赖则默认保留。批量撤销时从保护集合排除本次真正撤销的贡献，但保留未勾选应用和当前其他意图。
- 后续修改、重复同文、未知连续性或其他成功操作覆盖使局部逆操作不可证实时，显示对应 unresolved，不自动按字符串找一个 occurrence。全范围撤销先合并完整净结果，不能按历史先删后加掩盖共享冲突。
- 无 V3 操作记录（刚导入或手动配置的环境）：允许“按当前配置匹配移除”的显式模式，清楚标为解除配置、不是恢复历史 before。普通值按当前 uniquely matched occurrence 形成候选，重复必须在元素编辑器选择；共享默认保留；Special exact-match 才能提出整变量删除。选定空字符串不能被解释为所有空元素删除，须明确空值存在性决策。此模式仍是服务器生成的独立 revoke plan。
- 不自动执行旧 V2 的切换协调：切换 UI 中的方案只改变选择；应用新方案按当前规则执行，不能因为 active 变化偷偷撤销旧方案所有变量。需要撤销的内容通过明确撤销入口完成。

### 12.2 撤销一条历史 / 恢复历史前状态

历史条目至少保存 immutable before/after（含 exists/type）、actual operations、完整参与记录、规则版本和回读结果。只有具备原计划 lineage 的元素才跨栏关联；无 audit 的整体替换只画整体，禁止重建相似文本映射。

“恢复此操作之前”以该条 history.before 为候选，重新读取当前值。current 完整等于 recorded after 且无已知外改仍须生成新恢复预览；不是立即写。已改变时默认阻止整值覆盖，展示原 before、当时 after、当前三者，允许用户转手动编辑或显式逐目标选择恢复整个值。明确覆盖选择必须列出被覆盖的当前内容和共享影响，绑定新计划/版本，不能通用 `force=true` 绕过检查。

撤销与恢复本身生成新的历史，引用原 historyId，不能删掉原记录或把旧记录改写为“从未发生”。部分撤销记录哪些贡献已撤销，重复操作只处理剩余贡献。同条历史多目标可以选范围，但新的范围须重新预览。

### 12.3 UI 完成要求

删除 `recordsFor` 中“撤销后值需由真实操作日志计算”的占位字符串；四种范围必须返回真实目标/结果、共享保留项与阻塞原因。现有预览布局复用，同一目标内紧凑列出受保护来源和按项保留/明确移除选择。无可撤销内容时解释原因，不能制造执行按钮或虚构成功历史。

## 13. HTTP API、DTO 与本地边界

### 13.1 端点清单

所有路径位于 `/api/v1`，除启动交换外均需会话鉴权。JSON mutation 使用严格字段白名单，拒绝未知执行字段。

| 路由 | 请求要点 | 响应/责任 |
|---|---|---|
| POST /session/exchange | 一次性启动票据 | session、CSRF token、启动模式、backendKind |
| GET /bootstrap | 无 | 配置路径/版本、有效树、选定值、快照、恢复待办、能力 |
| POST /workspaces/import | UTF-8 TOML内容+显示文件名 | 完整校验、受管副本、workspaceId；不把文件名当路径 |
| GET /workspaces/{id} | 无 | 磁盘基线+有效 revision+草稿提示 |
| PUT /workspaces/{id}/draft | baseRevision、draftId、字段/命名草稿 | 保留非法草稿、errors、有效树是否晋级 |
| POST /workspaces/{id}/create | kind、parentId、fields、expectedRevision | 全配置校验后正式原子创建；UUID映射 |
| POST /workspaces/{id}/restore | expectedRevision | 恢复已保存基线，清空配置草稿；不改 Windows |
| POST /workspaces/{id}/save | expectedDiskHash、expectedRevision | 条件文件保存，保存 receipt、新 revision |
| GET /workspaces/{id}/export | 指定有效 revision | 下载合法 TOML；禁止导出非法草稿 |
| PATCH /selections/{recordId} | source/choiceId/raw、clientSequence、expectedRevision | 服务端首次 capture、新 selection revision+plan投影 |
| POST /selections/{recordId}/restore | expectedRevision | 恢复编辑前输入，结束旧编辑基线 |
| GET /environment | scope，可分页 | snapshot-only 列表/逐项目标错误/static Special |
| POST /environment/refresh | knownRevision、targets 可选 | 读取与差异事件、updated plans；永不写环境 |
| POST /edit-sessions | target、source、workspaceId | immutable originalRows/snapshot、sessionId |
| PUT /edit-sessions/{id} | expectedEditorRevision、liveRows、新增nonce | 服务端审计、正式 editor plan；不接收 after |
| DELETE /edit-sessions/{id} | sessionId | 取消未执行编辑；不修改环境 |
| POST /order-sessions | candidatePlanId/version | 服务器候选 ID 列表 |
| PUT /order-sessions/{id} | candidateVersion、orderedIds | 完整排列校验、冻结排序结果 |
| POST /plans | intent kind+record/session/history IDs+scope决策 | server planId/version/digest/targets/issues |
| POST /plans/{id}/review | version、digest、renderedRevision | one-use reviewToken；更新后须显式 review |
| POST /plans/{id}/execute | 第4节 execute DTO | 202 operationId 或冲突；无 raw/after |
| GET /operations/{id} | 无 | durable stage、目标进度、错误、恢复状态 |
| GET /operations/by-key/{key} | idempotencyKey | 网络结果未知时定位原操作 |
| GET /history | cursor、limit、target/record过滤 | 持久历史摘要，UTC与结果分类 |
| GET /history/{id} | 无 | 原始前后/审计/贡献，不现场重算历史 |
| GET /recovery | 无 | 未完成操作对账，不写系统 |
| POST /app/quit | CSRF、expected no-running-op | 有执行中拒绝退出；正常关闭 worker/HTTP/DB |

撤销、历史恢复、新建/整键删除的业务 intent 经 `POST /plans` 区分 `scheme_apply/manual_edit/create_variable/delete_variable/revoke/history_restore/recovery`，不要另外开任意 `/registry/write`。

统一错误：`{error:{code,message,fieldErrors?,operationId?,currentRevision?,replacementPlan?}}`。关键码：VALIDATION_ERROR、FILE_CHANGED、STALE_CONTEXT、PLAN_REFRESHED、EDITOR_STALE、REVIEW_REQUIRED、ORDER_REQUIRED、TARGET_CONFLICT、ACCESS_DENIED、UAC_CANCELLED、EXECUTION_UNKNOWN、RECOVERY_REQUIRED、STORAGE_FAILED。400/422 表示输入，401/403 鉴权，409 状态冲突，423 正在操作，503 worker/存储暂不可用；失败不返回假的 success DTO。

### 13.2 本地 HTTP 安全实现

- 仅绑定 `127.0.0.1:0`，核对 Host 为实际 ip:port；禁止 0.0.0.0 和任意 localhost 别名回退。拒绝非同源 Origin、跨站 Fetch Metadata；无 Origin 的普通顶层页面请求可返回静态壳，但 API 仍必须鉴权。
- 启动票据通过 URL fragment 交给本地页面，立即 `history.replaceState` 清除；一次交换后失效。使用 HttpOnly SameSite=Strict session cookie + 每次 mutation 的 CSRF header（内存中保存）；检查准确 Origin，禁止 CORS `*`。GET 不作写入，知道端口不等于有执行权限。
- 静态文件从打包 allowlist 提供，不用能任意列目录的默认 handler；不暴露数据根、配置原文件、日志、Python源码或 `.git`。正确 MIME、nosniff、CSP 限制为自身资源，无远程脚本。inline script 提取后用 CSP 避免 unsafe-eval。
- body 默认上限 2 MiB，TOML 导入明确可配上限 8 MiB；有请求/worker超时、JSON深度/条目/字符串长度限制，错误消息不回显 token。大历史用分页，不因超时重新执行。
- 限制同一 SID/数据目录仅一个主实例；新启动器通过受限本机控制通道请求打开现有页面。控制通道只支持 open/status，不允许执行计划。关闭浏览器不会杀掉运行中的操作；下次打开查询同一服务，产品提供明确“退出程序”。
- 日志写 operationId、错误码、target 名等必要信息，原始值默认只在受权限保护的 DB/备份中保存；导出诊断须由用户选择。

## 14. 前端逐项接线表

| 当前代码/行为 | 必须替换为 | 保留的 UI 语义 |
|---|---|---|
| `reset(true)`、initialApps、extraDemoWindows | bootstrap + 真实配置/快照；首次无配置显示导入/创建 | 不能自动把演示路径写真实环境 |
| `resetBtn` 重置模拟数据 | 改为“还原配置/丢弃当前草稿”的明确动作；开发 demo 才有 reset | 不触碰真实 Windows 或历史 |
| `demoSnapshot`、windows/targetTypes 全局 | 服务端 snapshot cache；错误与不存在分开 | Windows 页仅快照与直接编辑 |
| `demoPlan/newValue/itemMatched` | 读取后端 plan/match projection；本地即时计算仅未确认提示 | 三值对照同一计划、原文保真 |
| `beginSelectedEdit/setSelectedValue` | 有序 selection mutation；后端 capture baseline | 连续输入/取消预览保持原意图 |
| `protectedSelections` | 后端 protection projection | 不由 UI 传任意保护值 |
| `recordsFor/showPreview` | POST /plans，冻结版本及 target顺序 | 现有预览摘要/导航/原文/单滚动区 |
| `refreshPreview/confirmPreview` | review+execute 状态机 | 变化后单独审阅，点击发现变化只返回 |
| `applyDemoPlan` | 从生产入口移除；由 operation结果更新快照 | 不乐观宣布系统成功 |
| `history.unshift` | GET /history+operation committed 推送/查询后刷新 | 实际 before/after、稳定历史元素联动 |
| `openWindowsElements`、同步 `onApply` | 打开后端 session，上传 live rows，await execution | stale保留草稿，只有 verified成功才close |
| `auditSnapshot` | 共用纯 element-audit，服务端重新验证 | 展示移动与业务MOVE分开 |
| `createConfigEntry` | 原紧凑表单+后端全候选校验创建 | 命名步骤、UUID、首次变量原子创建 |
| `configOriginals/configChanges` | 后端稳定 ID 基线+本地编辑缓存 | 新父树只计一次，restore不碰环境 |
| `ConfigEditor` 暂停草稿 | 序列化独立 config_drafts，重启恢复草稿 | 未完成容器不进有效配置/TOML |
| `downloadToml/Ctrl+S` | Export 下载；Ctrl+S 保存工作副本 | 值/choices原文、非法草稿继续可编辑 |
| `refreshBtn/window.focus` | 合并一次 refresh 请求；回包按revision | 焦点/输入/打开预览不丢 |
| append 的永久 disabled | order-session完整链路 | 显式排序确认，复用编辑器操作样式 |
| revoke 的永久 disabled | 后端 revoke/history plan | 共享默认保留，按scope/范围真实执行 |
| “演示/仅页面数据”文案 | 正式 backend状态、保存/执行/权限/失败说明 | 不添加无关技术字段挤占主界面 |

避免一次重写 app.js：先将现有 inline script 原样提取，通过窄 API 适配逐入口替换。保留 `compact-ui.css`、现有图标/动效、原文与候选布局。不能保留一个按钮走真实后端、另一个按钮仍调用内存 apply 的混合生产模式。demo fixtures 只在显式开发入口使用；生产 bundle 不加载 demo-configs.js、不降级到 FileEnvironment。

多标签页、worker等待、UAC期间使用现有按钮 disabled/忙碌文案，必要时显示 operation进度；失败恢复输入，不用全页 reload 代替状态协调。保持 600px/391px 布局、Tab/方向键/Home/End、中文 IME 与 reduced-motion，不以产品化为由裁剪值。

## 15. 启动、打包、升级与数据兼容

### 15.1 正式启动路径

1. `EnvManagerV3.exe` 解析固定参数与已保存数据目录，获取单实例锁。
2. 打开 SQLite、检查 schema、扫描未完成日志；启动私有 Node worker 并校验规则 hash。任一步失败显示可理解错误和日志位置，不能打开空白网页或注入示例数据。
3. 在操作系统分配的 loopback 端口启动 HTTP，取得启动票据，再用系统默认浏览器打开。后端就绪前不打开页面。
4. bootstrap 载入有效 TOML/草稿与真实 Windows inventory。无配置时仍能浏览 Windows 与手动编辑，工作台显示导入/创建入口。首次安装不扫描和迁移 V2 文件，也不自动应用任何方案。
5. 恢复待办存在时先呈现恢复状态，禁止冲突写入；操作查询与日志不会因为页面关闭丢失。退出时等当前写操作到可恢复终点，停止worker与监听、提交DB并释放锁。

开发 CLI 显式选择 `--backend file --data-dir <isolated-local-dir> --environment-file <isolated-json>`。正式 EXE 固定使用 windows 后端，除明确标注的隔离测试模式外不允许静默 fallback。File 后端只是同一产品链路的测试注入，不是发布的默认演示产品。

### 15.2 包装与安装

- 初版发布 Windows x64 onedir 包与 Inno Setup 安装程序；构建机不要求运行 V2。根 V2 pyproject/入口保留，V3 构建 spec 明确收集 `envmanager_v3`、生产网页白名单、Node runtime 与 licenses，不收集 tests/design-evidence/演示数据。
- 安装到 Program Files 的安装器可以请求管理员；安装权限与每次运行权限分开。快捷方式指向主 EXE（asInvoker），不勾“总以管理员运行”。用户软件快捷方式惯用目录 `H:\ProgramFiles\AppLinks` 可作为用户明确选择的附加快捷方式位置，不硬编码成必需目录。
- 安装器创建受保护的 System receipt 根目录；运行数据始终在用户数据目录。默认不修改 PATH、不设置开机服务/计划任务、不写环境变量，安装与卸载不负责恢复用户已执行的环境方案。
- 无 Node/Python 的干净 VM 必须能安装、启动、保存 TOML、重启读历史、User写入、System UAC、失败恢复。打包应固定工具/runtime版本及SHA，附依赖许可证。是否签名按实际发布资源记录，不能把未签名包描述成可信发行商。
- 升级前禁止有进行中的操作；备份 SQLite 和工作区 manifest，升级程序文件不覆盖配置/草稿/历史。卸载默认保留用户数据与未完成恢复回执；删除数据是单独明确动作，不跟随普通卸载自动进行。公开发布/上传 Release 仍需负责人决定，本方案要求先产出可安装候选包与验证证据。

### 15.3 兼容而不迁移 V2 核心

同格式 V2 TOML 是数据导入来源；V2 可执行程序、PySide6、service/switching/removal 逻辑与其状态库均不进入 V3。读取旧配置前不修改原文件，导入到 V3 工作副本。`value` 旧字段等非当前格式只报告可定位的迁移诊断，不能猜测改成 default_value；确需转换时单独另存新文件并给出差异。

V2 历史没有本 V3 的完整冻结计划与执行证据，不自动迁移成可撤销贡献。初次加载可显示“无 V3 历史”，依然可以匹配现有 Windows 和显式编辑/解除配置。现有浏览器导出的合法 TOML 应能直接导入；其会话内 initialApps.active、savedValue、模拟 history 不能当作真实执行记录。

## 16. 分阶段实施与并行安排

所有阶段是完整产品任务中的交付点，不能在某个阶段之后声称产品完成。下面的文件名是责任边界，可小幅合并，禁止让多个 agent 同时覆盖同一核心文件。

### P0：固定起点与接口（主 Sol，立即）

- 核对最新产品分支/checkpoint、未提交 phase2 差异；记录真实 HEAD，保留它们，不 reset、不自动 stash、不覆盖。最新 compact/config-editor 以预览源核对。
- 在 V3 层追加 AGENTS/README 的正式实施范围说明（实施阶段才做），明确允许独立V3/隔离测试、真实宿主写入需具体确认；避免后续 agent 被旧 UI-only 文句阻塞。
- 确认 DTO 与已分派 environment 任务一致。为名称/type/revision 建议另设边界helper，不擅改正在实现的 adapter 签名。
- 建立独立 V3 runtime/build requirements 与启动入口；根 V2文件不动。阶段提交仅纳入本任务明确文件，旧phase2修改由负责人checkpoint处理。

### P1：两条后端基础线并行

**A：环境适配线（当前 Sol 任务已在执行）**

- 文件：`environment.py`、`tests_v3/test_environment.py`。
- 交付：File持久化、expected比较、absent/empty/type保真、原始winreg适配；导入模块零宿主读写；所有自动测试默认File/注入mock。
- 主agent只审阅接口，不平行覆盖它。不要求该任务承担UAC/HTTP/SQLite/全批事务。

**B：规则和数据线（另一 Sol；无子agent能力时主agent依次完成）**

- 文件：`planner.py`、`planner_worker.cjs`、`element-audit.js`、`dto.py`、`sessions.py`、对应tests/fixtures。
- 使用当前DemoPlan生成基线golden数据，覆盖build/buildBatch/removalState/元素审计；验证worker结果一致，再加名称统一、类型补全、排序和撤销的明确扩展。新增纯规则放同一规则模块，不做Python副本。
- 确认完整审计不能被伪造after/beforeIndex/ID绕过，worker失败不降级执行。

**C：配置持久化线（Sol，B的DTO定稿后可并行）**

- 文件：`configuration.py`、`config_files.py`、`repository.py`、config tests。
- 完成导入/完整校验/工作副本/非法与命名草稿/条件保存/文件恢复；实际roundtrip测试。SQLite schema由此任务单一所有者维护，其他任务提交schema需求，不自行追加冲突迁移。

P1 门槛：可通过服务层读真实形状配置与持久File快照，取得服务端冻结计划。此时还不是产品完成。

### P2：执行与恢复主干（主 Sol，高推理）

- 文件：`execution.py`、`recovery.py`、`history.py`、`snapshots.py`、execution/recovery/history tests。
- 接入A/B/C；File后端完成计划→review→execute→readback→SQLite历史→重启→撤销/恢复。
- 注入每个持久/写入边界失败：write前、write后ACK前、verification后commit前、SQLite失败、第二目标失败、补偿失败、网络响应丢失。
- 在执行主干稳定前不开放Windows生产写入UI。测试不能仅断言日志状态，必须读回持久环境文件、重新启动应用实例并验证恢复决策。

### P3：HTTP与现有UI接线（两条Sol线）

- 后端线：`http_api.py/application.py/api tests`，按第13节接口提供实际服务；本地安全检查与operation查询在此完成。
- UI线：`index.html/app.js/api-client.js/product-state.js` 与必要的`windows-elements/config-editor`异步适配。以第14节逐项清单勾完，不改compact视觉方向。
- UI线可临时使用契约fixture开发，但阶段结束必须切到真实Python/FileEnvironment端到端；fixture不得进入生产路径。
- append排序、撤销四范围、config草稿保存与重启、Windows直接编辑、review刷新都在该阶段接完。集成者单独持有`app.js`，避免拆成两个agent同时维护其全局状态。

P3 门槛：同一正式UI完成所有用户流程，经后端/真实文件/SQLite执行；仍只代表隔离后端通过。

### P4：Windows/UAC/通知（主 Sol 或专项Sol）

- 文件：`windows_events.py/elevation.py/elevated_worker.py` 与Windows integration tests。
- 在已验证隔离的Sandboxie或VM执行native User写入/删除/回读；System与不同管理员凭据的UAC必须在快照VM做真实权限验证，Sandboxie模拟管理员不能替代。
- 验证mixed batch UAC取消零写、System helper断线/回执恢复、广播结果分类、外部冲突与连续性撤销。
- 本阶段宿主只允许事先明确授权的精确目标写入；未授权时完成代码、隔离测试、安装VM验收，并把宿主项准确标为待人工验证。不能为赶进度在宿主写测试Path。

### P5：发布候选和最终验收（Sol + Luna）

- Sol负责PyInstaller/Inno配置、干净VM安装升级卸载、恢复路径、最终代码核查。
- Luna适合整理接线清单、测试日志索引、文件哈希、版本/许可证清单、操作手册，不让Luna独立决定UAC/恢复/删除语义。
- 运行既有UI回归与新增产品测试，浏览器真实交互验收后生成可安装候选包、checksums、完整报告。修复失败项后只重复相关检查及必要整体回归，不无限增加测试范围。
- 最后按实际分支提交阶段成果，给出SHA/包路径/测试证据/未验证项。无需再次召集Astra做相同分析；遇到此方案不能覆盖的核心语义冲突再提交具体问题。

## 17. 验收矩阵（全部为后续必须执行，本轮未运行）

| 编号 | 场景 | 必须观察到的结果 | 证据层级 |
|---|---|---|---|
| C01 | 导入V2同格式TOML，双scope、choices、default_option | 内容完整可编辑，原文件未改，工作副本保存后重启一致 | 配置单测+UI/File端到端 |
| C02 | 非法值、重复名、Special冲突、空容器命名草稿 | 保留草稿和定位错误，不污染有效配置；恢复/取消符合最新契约 | 单测+真实浏览器 |
| C03 | CR/LF/CRLF、前导LF、空格、DEL、反斜杠、中文/emoji | 未触及raw保真，TOML roundtrip相等，32766 UTF-16边界正确 | 字符串/序列化测试 |
| C04 | 配置保存外部修改、临时写失败、replace后DB提交失败 | 不覆盖外改，重启按hash对账，无虚假保存成功 | 故障注入+重启 |
| E01 | absent/empty/";"/"A;"，删最后项，明确空行 | exists/raw/type精确区分，预览/实际/历史一致 | adapter+规则+UI |
| E02 | REG_SZ/REG_EXPAND_SZ、引用不展开、异常REG类型 | 保持类型/原文，异常不伪装不存在；type-only也有计划 | adapter+Windows VM |
| P01 | V3.2五组确定增删例、missing-origin、不猜REPLACE | 与现有规则golden一致，安全删除/追加准确 | worker parity |
| P02 | 重复occurrence、同值不同空格、shared、初始重复后消失 | 不自动获得删除权；显式编辑只操作指定ID | 规则+端到端 |
| P03 | 同target多请求，mixedSpecial，replace冲突，head/tail顺序 | 一目标一计划一写，冲突全批零写，确定请求顺序 | 规则+执行 |
| P04 | append排序/取消/候选过期/恶意ID列表 | 完整排列才能确认；最新候选重审，无任意after入口 | 规则+UI |
| P05 | ghost移动/删除草稿/绿色撤销/新增再删 | display与live审计分开，新增再删无虚假REMOVE | 既有回归+UI |
| R01 | 已知手动编辑后所有同目标会话刷新 | 可信未改原occurrence保资格，inactive会话同步 | 会话+集成 |
| R02 | 未知变化、A→B→A通知、监听中断、重启 | 原删除资格撤销且不复活；安全ADD可继续 | 注入+Windows VM |
| R03 | 无关目标已知变化、相关目标确认时变化 | 无关计划保持；相关只更新，需独立review再confirm | HTTP+UI |
| R04 | 编辑器stale、快速输入乱序、跨标签页修改 | 草稿/ID/焦点保留，旧响应不覆盖，拒绝旧计划 | 真实浏览器 |
| X01 | before任一不符、权限不足、规则worker死掉 | 全批启动前零写；错误明确，不fallback | 执行注入 |
| X02 | 双击确认、超时ACK丢失、重复idempotencyKey | 只有一operation，一次写；查询原结果 | HTTP+故障注入 |
| X03 | 第二目标失败，第三方改第一目标 | 停止后续写；只条件补偿，不覆盖第三方，recovery_required准确 | 持久File多实例 |
| X04 | 各WRITE_INTENT/VERIFIED/COMMIT处终止进程 | 重启只对账，无自动重放；历史结果可追溯 | 子进程故障测试 |
| X05 | 磁盘满、DB提交失败、helper receipt失败 | 写前durability失败不写；已写部分明确待恢复 | 故障注入 |
| X06 | no-op确认、安全部分意图完成、active更新 | no-op不造修改历史；部分保留输入/基线；全部解决才active | 服务+UI |
| U01 | 普通User编辑与配置保存 | 主UI不升权，正确当前用户scope | 原生Windows VM |
| U02 | System成功/UAC取消/另账户凭据/mixed批 | 取消前零写；不写管理员HKCU；目标计划绑定 | 真实UAC VM |
| U03 | pipe伪造/断线、parent退出、未知操作ID | 不执行新命令，不扩大路径；回执可对账 | helper安全集成 |
| H01 | 四撤销范围、共享多贡献、重复、历史前状态恢复 | 实际合并计划、保护默认保留；不凭匹配声称所有权 | 规则+UI+持久化 |
| H02 | 重启后历史/过滤/联动、失败和部分记录 | 原snapshot/Audit不重算；无伪造元素关系 | DB+UI |
| S01 | 外站POST/Origin/Host/无token/越权路径/恶意after | 请求拒绝，零文件/Registry副作用 | HTTP集成 |
| S02 | 安装包资源缺失、Node不可用、生产后端错误 | 明确错误，不演示fallback、不静默加载旧规则 | 安装VM |
| L01 | 391/600px、桌面、长值/重复/空元素、键盘、IME | 已接受紧凑交互保留，无文本截断或错ID | 真浏览器+Windows人工 |
| L02 | Windows 100/125/150/200% DPI、原生拖动 | 记录真实DPI/viewport，不把DPR仿真报成原生 | Windows人工 |
| D01 | 干净VM安装、升级、卸载、无开发runtime | EXE可独立运行，数据保留，快捷方式正常，不改PATH | 可安装发布验证 |
| D02 | 广播失败与新进程读取环境 | 值写成功与通知异常分开；已有进程行为如实说明 | Windows VM |

沿用 `ui-v3/package.json` 的既有33个脚本作为回归起点，但旧脚本依赖demo初始化的断言需分成：纯规则/展示回归继续保留，生产数据加载行为改由产品端到端覆盖。不能为了沿用测试把mock再加回生产入口。现有README的606/621等历史计数是当时证据，不是本次产品测试通过数。

建议后续验证入口（实施后才可运行）：

```powershell
# 纯隔离测试；tests_v3 默认不得实例化宿主Windows写入适配器
python -m unittest discover -s tests_v3 -p 'test_*.py'

# 独立本地数据目录，由实施者创建；完整产品路径使用FileEnvironment
python -m envmanager_v3 --backend file --data-dir '<隔离绝对目录>' --environment-file '<隔离绝对目录>\environment.json'

# UI既有回归，在镜像 ui-v3 目录执行其锁定依赖安装与 npm test
# Windows/UAC测试使用单独显式 opt-in 的VM测试入口，不混入默认discover。
```

## 18. 最终完成门槛与交接结论

**只有以下条件同时满足，才可称为可运行正式 V3：**

1. 可安装候选包在无开发环境的 Windows VM 启动，界面就是已接受紧凑 UI；没有必需的手动 Node/Python 服务启动步骤。
2. TOML 导入、校验、实际保存、草稿恢复、重启读取与数据兼容完成；配置/输入/环境三个来源明确分开。
3. User/System真实适配、冻结计划、review-bound确认、append排序、手动编辑、批量冲突、持久历史、四范围撤销与恢复全部有端到端证据。
4. 部分写入、断线和崩溃可以基于持久证据对账；不会把未知结果自动重跑，不会把补偿失败显示为已恢复。
5. 生产所有操作入口均真实接线，`applyDemoPlan`/虚构Windows/演示history不会出现在生产执行路径。DemoPlan文件名可以保留，但其角色是正式共享规则，不是mock执行器。
6. 原文/重复/空值/REG类型/稳定ID/删除资格/ghost/建议采纳与当前契约一致，旧V2排序/自动清理/协调规则没有混入。
7. 实际测试报告列明分支SHA、规则hash、包hash、测试后端、Windows版本、真实UAC/浏览器/DPI证据以及尚未验证项。宿主未经授权的写入验收明确标待办，不能用VM通过伪称宿主已通过。
8. V2 baseline原样保留，main未被直接修改，旧phase2成果未被覆盖，阶段commit清晰。公开发布另由负责人决定。

关键剩余工程风险是：JS计划与Python会话的版本接合、异步UI不丢草稿、非原子跨Registry/SQLite执行、Windows外部变化的可观察性，以及System helper与持久回执。以上均有明确接口/失败状态/验收项；不再以架构原则清单代替实现。

**本轮交付边界：** 只生成本方案文件；只读核查代码/契约/Git状态并查阅官方接口资料；未实现或改动产品代码，未运行测试，未启动外部服务，未读写真实 Registry，未进行 Git mutation。适配器和完整产品是否完成，以后续实施与验收为准。现在交回 Codex/Sol，按 P0→P5 推进，不需要等待第二轮重复分析。

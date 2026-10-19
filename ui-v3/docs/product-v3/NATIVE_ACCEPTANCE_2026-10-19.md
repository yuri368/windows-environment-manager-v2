# C 盘差分系统原生验收：2026-10-19

本记录补充 ACCEPTANCE.md 中较早的沙盒及 CI 证据，不将本机差分系统等同于干净 VM。当前验收工作目录为 `C:\EnvManagerV3Acceptance\20261019-child-native`，源码在其 `source` 子目录。H 盘项目、旧镜像和 VHDX 文件均未由本轮操作修改。

## 授权与实际启动边界

负责人明确允许在 C 盘差分系统中安装和测试，禁止写其他盘，特别是父盘 `H:\ventoy\boot\Win11.vhdx`。每个原生验收入口检查 C 盘磁盘 Location 指向 `\ventoy\boot\Win11_2.vhdx`；先前发现启动父盘时已停止，清理当时准备目录后，重新确认启动子盘才继续。

操作系统为 Windows 11 IoT Enterprise LTSC 10.0.26100。产品及 Setup 从正常未提升用户启动，UAC 保持系统配置，由负责人手动处理。需拒绝授权的验收先显示独立提示框；其他 UAC 按负责人约定选择是。未修改 UAC 或全局长路径策略。

只使用原本不存在的两个目标，未编辑现有 Path 或其他业务变量：

- User：`V3_ACCEPTANCE_20261019_USER`
- System：`V3_ACCEPTANCE_20261019_SYSTEM`

原始 User/System 全量 28 项的名称、类型和未展开值保留在本地 `environment-before.json`，不提交其实际内容。写入全部经过已安装产品的计划、审阅和执行 API，不使用脚本直接写环境注册表。

## 真实安装暴露的问题与修复

初始候选的 machine Setup 发生原生 access violation，未成功安装。`machine-acl.iss` 的 `lstrcpynW` 目的参数误声明为 `var String`，传入了 Pascal 字符串槽地址；改为字符串缓冲区传参。SDDL 转换返回的是缓冲区长度，复制后按首个 NUL 截断，避免将容量尾部的 NUL 当成实际文本。

`packaging_v3/acl-probe.iss` 为可选原生回归：只读取源目录 ACL、在内存中执行转换和往返校验。它故意从 InitializeSetup 返回 False，因此退出码 1 是预期的提前结束；通过条件是日志包含 `V3_ACL_PROBE_OK`，且没有 FAILED 或 access violation，不能将退出码 1 单独视为成功。

修复 ABI 后，machine Setup 的提升控制客户端创建的邮箱请求被原用户 holder 拒绝读取，安装仍阻断。machine 分支的 status/finish 改用 `ExecAsOriginalUser`，与 holder 身份一致；保留新 nonce、backup ID、同步退出码、fresh status、每文件 lease 检查和失败闭锁，不放宽目录权限。Sol 对该修改的只读审查未发现授权或锁语义退化。

原生安装随后成功，退出码 0，最终 status 和 finish 均得到成功确认。程序目录为 `C:\Program Files\EnvManagerV3`；实际读回 owner 为 Administrators，protected DACL 为 BA/SY full、AU read/execute。恢复回执根 `C:\ProgramData\EnvManagerV3\system-receipts` 为 BA/SY full、AU read。已安装 EXE 的 Windows 后端检查和未提升服务启动成功。

原始失败日志、候选及中间安装失败均保留，不能用最终成功覆盖失败事实。仅修安装脚本、复用原 frozen runtime 的第三个中间候选 SHA256 为 `3d7d28a7d57731b6dcdd584b9a7fbb1a7db09d2ec7bdbf4bb65e384165bdd120`；它仍暴露下面记录的长路径升级问题，不作为完整升级验收通过的最终包。

## 环境执行、取消、重启与恢复

首次原计划验证取消，但负责人误点是，实际 operation `35dd9127c3a646fca68906a959b8ee81` 提交成功。此结果明确记为成功写入，不计取消通过。两个目标均为 REG_EXPAND_SZ，读回分别精确保留：

- User：` A;A;;中文\r\n尾部 `，其中 `\r\n` 表示实际 CRLF。
- System：` %V3_ACCEPTANCE_20261019_USER%;S;;中文 `，变量引用未被展开。

同一 execute 载荷及 idempotency key 重放返回原 operation ID，没有新增操作。鉴权退出并重新启动已安装服务后，历史仍在、恢复待办为 0。跨服务重启后历史恢复因无法证明 revision 连续性返回 `UNDO_CONFLICT`；没有绕过该保护。改用当前快照生成两个明确删除计划，User 和 System 删除均提交成功。

第一次 System 删除的 UAC 被取消，operation `248641a3208942ccb165f5a7376689fa` 返回 `UAC_CANCELLED`；保留独立取消证据后，新的当前快照计划 `e42c853ee4d24da08acfde116705c12d` 清理成功。不是对未知执行盲目重试。

随后在两个目标均不存在时，重新生成混合 User/System 计划。提示负责人明确选择否后，operation `933c481c718541f3878b858603c88802` 返回 cancelled / `UAC_CANCELLED`；两个目标均未写入。全量 28 项环境变量的名称、类型和原始值与初始清单完全一致。证据为本地 `mixed-cancel-verification.json`、`environment-restored-verification.json`。

## 安装在用检查及升级状态

已安装 Windows 服务运行时，machine Setup 返回退出码 7，明确报告程序文件在用。已安装 EXE SHA256 未变，原服务继续运行，没有调用关闭或强杀进程的接口。证据为 `machine-in-use-install.log`、`machine-in-use-verification.json`。

鉴权退出服务并确认进程结束后，同版本重装在备份阶段返回 `BACKUP_FAILED`。源工作区备份文件路径约 203 字符，加入 `.incomplete-<id>/shadow/` 后超过 Windows 传统路径限制；本机 LongPathsEnabled 为 0。该次失败未替换应用文件；数据及机器回执共 7 个文件的 SHA256 完全保留。证据为 `machine-upgrade-install.log`、`machine-upgrade-verification.json`。

修复在升级模块的 I/O 边界使用 Windows extended-length 路径，SQLite 使用编码的本地文件 URI 与 `win32-longpath` VFS；连接源快照仍为只读。清单、回执和包含关系判断继续使用普通逻辑路径，不改变核心模型或失败闭锁。

旧 frozen EXE 在独立目录复现 `BACKUP_FAILED`：源路径 223 字符、shadow 路径 291 字符。新 frozen EXE 对同一夹具通过：已发布备份最大路径 279 字符，原始文件、非法草稿、SQLite 内容、持锁、fresh status 和 finish 均验证成功。结果见 `frozen-longpath-20261019.json`。

新候选 `packaging_v3/output/20261019-105247-728` 的真实同版本 machine 重装退出码 0，finish 确认成功；前述 7 个应用数据及机器回执文件全部 SHA256 不变，安装 EXE 与新包一致。此为真实 Setup 升级成功，不只是 helper 模拟。日志为 `machine-longpath-upgrade-install.log`。

编译过程最初传入相对 ISCC 路径，在脚本切换工作目录后解析失败；此时 frozen EXE 与两类 smoke 已成功。随后使用绝对路径验证固定编译器 49 个文件哈希，完成两类 Setup 编译、finalize 和 verify-bundle；没有把第一次 build.ps1 调用描述为 exit 0。最终核对源码与 stage 无漂移，115 包文件、15 生产资源、30 许可证及 16 V3 模块一致。

本地完整后端回归运行 491 项：488 通过、1 项 opt-in 跳过，新增路径夹具的两项断言失败，因为默认 TMP 较短，未让 payload 超过 260。只修测试公共父路径构造后，默认 TMP 下新增 3 项及完整升级相关 57 项均通过；未将最初全套结果改写为全部通过。原始日志 `backend-regression.log` 和补验日志 `upgrade-regression-final.log` 均保留。

升级后自动审批拒绝了 Windows 后端服务启动，工具只返回 `blocked by policy`，没有更具体理由。该启动命令未执行，未换接口重试。只读、immutable SQLite 检查返回 integrity=ok、历史 3 条、committed 执行 3 条及 cancelled 执行 2 条。升级后实际服务启动和新包上的真实 System 执行尚未补验；前述成功执行与混合取消来自先前安装的 frozen runtime。

真实静默 machine 卸载退出码 0，已安装主 EXE 移除，前述 7 个应用数据及机器回执文件全部 SHA256 不变。随后用新候选重装同样退出码 0、finish 确认成功，7 个文件仍全部保留；Program Files 和回执根的 Administrators owner / protected DACL 均正确，已安装 EXE SHA256 为 `4b11c2b840a6d7cac8cc3649ee178f4b30dec3ca3031393726a985a3950f4f79`。程序保持已安装、服务未运行，未再次尝试被拦截的服务启动。

生命周期结束后再次全量只读核对，28 项环境变量与初始值和类型完全一致，两个临时目标均不存在，LongPathsEnabled 仍为 0。经裁剪的真实结果集中在 `native-acceptance-20261019.json`；该文件同时保留升级修复前失败和修复后成功，不能只取最后一条当作所有尝试都成功。

## 修复后的远端完整回归

源码与报告提交 `604884a16480faa803620fa801331dadf20d2b4b` 的 [V3 product checks 39192586479](https://github.com/yuri368/windows-environment-manager-v2/actions/runs/39192586479) 已 success。Windows：491 项执行、490 通过、1 项 opt-in 跳过，127.673 秒，新增 3 项长路径测试均通过；Ubuntu：491 项执行、481 通过、10 项平台/opt-in 跳过，38.461 秒。两平台的产品 JSDOM 契约均 34/34。旧 UI run 39192586524、V2 run 39192586507 同样 success。摘要及原始结果行见 `ci-native-fixes-20261019.json`，完整日志在对应 GitHub run。

此记录归档后只更新文档，没有再改生产或 packaging 输入；不能将 CI 的 File/mock 通过扩大为升级后实际 Windows 服务启动、干净 VM 或多用户验收通过。

## 尚不能扩大声称通过的范围

### 后续补验：新包启动、真实执行与恢复

此前被自动审批拦截的启动，在负责人后续要求继续后已成功执行。已安装新 EXE（SHA256 `4b11c2b840a6d7cac8cc3649ee178f4b30dec3ca3031393726a985a3950f4f79`）的 Windows 自检通过、正式 HTTP 服务启动成功，原 3 条历史逐项保留；User/System 原生监听正常，恢复待办为 0。

新包经计划、审阅、真实 UAC 后提交混合 User/System 操作 `1de75edf1a2f4bb5963d2d0bf4fcc9bb`；两个临时目标精确读回预期原文和 REG_EXPAND_SZ 类型，零错误、零警告。随后在同一服务会话中，以历史恢复计划提交 `10d36d8f97d54a76955158e620e80ebf`，恢复同样 committed、零错误、零警告。全量 28 项名称、类型、未展开原文与操作前完全一致，两个目标均不存在。

鉴权退出、重新启动同一安装 EXE 后，5 条历史逐项保留、recovery 0、双 scope 监听正常。此前 CI 默认跳过的 ordinary-token 原生管道测试另行 opt-in：1 项通过、0 跳过，0.146 秒；核对跨进程 kernel peer PID 和 150000 字符消息，测试本身不发起 UAC 或 Registry 写入。裁剪结果见 `upgraded-native-20261019.json`；完整私有状态和认证信息仍仅保存在本地。

### 负责人调整首版范围

2026-10-19，负责人明确要求多用户及另一管理员账户暂缓，等待实际 issue 后再处理；本轮也不再将真实断电实验列为发布阻断。现有持久日志、条件补偿和崩溃对账仍保留，自动化故障测试继续有效，不将这些测试等同于真实断电实验通过。

“无开发环境”指安装包独立运行不依赖外部 Node/Python；清空系统环境变量不能替代干净系统。已完成 frozen EXE 的 System32-only PATH 隔离启动 smoke，无需删除用户环境变量。干净 VM 仍属于独立平台验证，未做不声明已做。

原生桌面接口此前因无法识别网址而中止，不记为通过，也未换输入接口绕过。随后由已安装产品正常启动独立 File 后端人工夹具 `http://127.0.0.1:8796/`，负责人实际使用中文输入法和鼠标拖动，反馈“输入和拖动都正常”；对 Windows 100%／125%／150%／200% 系统缩放、浏览器保持 100%、输入框及底部按钮可用且无重叠的检查，反馈“正常”。这些结果记录为负责人报告的人工验收，没有采集自动化原生 DPI 遥测。

操作后只读 DOM 补证确认：元素身份顺序为 5/1/2/3/4，原文依次为 ` D `、`A`、`A`、空字符串、`中文输入验收`；重复项、空项、空格及全部原有身份均保留。未提交执行，隔离环境文件仍为原始 `A;A;;中文; D `。结果与截图见 `desktop-manual-20261019.json`、`desktop-manual-20261019.png`。该检查来自已安装原生修复候选，后续启动/退出补齐另行执行 frozen 验证。

额度重置后的收尾审查已完成，结果及唯一提示问题的修正见 `CANDIDATE_REVIEW_2026-10-19.md`。提示修正和相对编译器路径修正提交 `b81c842` 的完整远端 V3 回归通过：Windows 491 项（490 通过、1 跳过），Ubuntu 491 项（481 通过、10 跳过），两边产品 UI 契约均 34/34。结果行见 `ci-history-message-20261019.json`。其后补齐的单实例重开另行验证，不能沿用这次 CI 冒称已覆盖新增代码。

单实例重开提交 `31d5732` 的本地完整后端回归 504 项执行、503 通过、1 项 opt-in 跳过，102.569 秒；远端 V3 product run 39197441713 两平台 success：Windows 504 项（503 通过、1 跳过），Ubuntu 504 项（494 通过、10 跳过），产品 UI 契约均 34/34。摘要见 `ci-single-instance-20261019.json`；不将此结果扩大为其后退出入口改动的验证。

本轮操作系统包含开发工具，尚不能证明干净系统无开发 runtime 的完整安装生命周期。多登录用户、另一管理员凭据、真实断电属于负责人已暂缓的项目，不再据此阻断本轮个人候选；原生 IME/拖动/DPI 已有上述人工反馈。旧沙盒结果或成功取消不替代尚缺的对应证据。

ChatGPT Bridge 只读检查仍返回 `BROWSER_UNAVAILABLE` / `ECONNREFUSED 127.0.0.1:9222`，worker 未运行；本轮没有发送网页协作消息，也没有为启动 Bridge 写其 H 盘工作目录。既有 Astra 实施方案继续约束实现，子 Agent 审查不能冒充网页确认。

完整环境清单、启动 ticket、cookie/CSRF 和未裁剪服务状态只保留本地，不提交 Git。失败安装/备份证据不自动删除。

## 后续交互修改：整行拖动

负责人在原生操作通过后继续要求：拖动时保持整行原尺寸并跟随鼠标，取消缩小卡片。第一版 `4a8e742` 将 native drag image 换成实测整行，自动化 UI 回归通过，但负责人反馈“还是老样子，没变化”，并提供半透明原生拖影截图。此项明确记录为未通过，不沿用前述一般排序验收。

第二版 `aab1f62` 保留原有 HTML5 排序和全部元素身份，改为不透明 DOM 整行跟随，透明 1px canvas 隐藏浏览器默认拖影。完整 UI npm 回归 exit 0、元素 20 项、产品 runtime 43 项通过；新 File 服务和新标签页已打开，并核对服务实际发送的新代码。负责人再次要求继续调整，截图已显示不透明整行浮层，因此该版不记原生拖动验收通过。具体尝试与失败截图见 `drag-feedback-20261019.json`。

### 最新已安装候选的原生提交与还原

`20261019-122936-875` 随后已在 C 盘差分系统完成机器覆盖升级，安装器 exit 0，安装 EXE SHA256 与候选一致。20 份既有数据/系统回执文件哈希全部保留，ProgramFiles 和系统回执所有者、受保护 DACL 及标准用户只读边界核对通过。已安装 Windows 后端只对事先确认不存在的 `User|V3_ACCEPTANCE_20261019_USER` 和 `System|V3_ACCEPTANCE_20261019_SYSTEM` 执行审阅绑定的混合提交，两个目标均 `verified`；随后通过历史还原，两个目标均还原为不存在。前后 28 项完整环境清单的原始值及类型一致，历史 5 → 7 且含两次操作，恢复队列为空。脱敏证据见 [`latest-native-20261019.json`](latest-native-20261019.json)；完整清单和认证材料仅留本地。

### 纵向拖动、反馈及幽灵行后续修复

负责人明确要求横向对齐、整行缓慢上下跟随及相邻行主动让位。初版基本符合，但快速跨行存在异常抖动；停止源 `drag` 和文档 `dragover` 两路坐标竞争后，负责人反馈仅“缓解了一些”，并补充拖动缺少移动标记、两栏对照反馈及幽灵行无法拖动。

当前源码改为序号抓手 pointer capture，在松手时走同一 `displayOrder → live rows → update → renderComparison` 路径；普通行和幽灵行共用拖动处理。命中使用冻结行高，目标不变时不反复修改相邻行；原位释放不播放虚假的移动落座。检查覆盖拖动/按钮反馈等价、重复同值互换、打开时的值及身份顺序不变、两栏移动徽标、幽灵删除草稿/当前位置恢复、快速反向、取消和事件清理。完整 UI npm 回归、32 项元素检查及 43 项产品 runtime 检查通过；这仍是 JSDOM 证据，不能代替鼠标手感验收。

`http://127.0.0.1:8798/` 隔离 File 测试页已刷新并打开含普通行/幽灵行的弹窗；JS 和 CSS 的服务响应 SHA256 均与当前源码一致。当前修复未进入上述已安装包，等待负责人实际拖动反馈后再构建候选。

### 松手重复移动修复与新构建

负责人随后反馈普通拖动、移动反馈和幽灵拖动“看起来正常”，但松手时整行先回到起点再播放一次移动。根因是拖动浮层清理后，旧列表位置被当作 FLIP 起点。源码 `c37a732` 在清理前记录实际浮层和相邻行位置，落座从当前显示位置接续，并使用不含位移/缩放的落座高亮。新增普通 pointer、native fallback、已到位浮层和幽灵行的动画关键帧检查；元素检查增至 36，完整 UI npm 和 43 项产品 runtime 回归通过。测试页已刷新，新的手感反馈仍待负责人确认。

新候选 `20261019-130846-568` 已完成完整构建及两类安装器生成，尚未覆盖安装。504 项后端执行、503 通过、1 项 opt-in pipe 默认跳过。新冻结 EXE 的 File 启停/升级 helper smoke 通过，额外 File 产品链路三次启动证明导入源文件不变、非法 NUL 草稿与命名 UI 草稿重启保留、修正/条件保存、review-bound 执行及重复确认的一条历史持续存在。详见 `candidate-20261019-130846.json`、`frozen-product-lifecycle-20261019.json`。此轮没有新的宿主 Registry 写入，也没有据此声称原生视觉或干净 VM 验收通过。

### 负责人确认与当前候选安装验收

负责人随后明确反馈：“现在直接落在目标位置，正常”。因此源码 File 页面 8798 的普通/幽灵拖动及连续落座记为人工接受；该反馈不是 JSDOM 或鼠标自动化产生。

随后 `20261019-130846-568` 完成 C 盘机器覆盖升级，安装器 exit 0；预先额外保留验收目录内的操作数据和系统回执备份。安装后 26 份既有文件无缺失、无哈希变化，固定 ProgramFiles 及回执目录仍由 Administrators 所有，DACL 保持保护。新安装 EXE 的 SHA256 为 `38322e9aba2df26b92537db749740b791235f58df6c82f272f11cc836f8eca65`；安装文件与服务实际返回的 `windows-elements.js`、`fx-ui.css` 均与负责人已接受的源码字节一致。

已安装程序以非管理员启动环境、仅 System32 PATH，实际进入 Windows 后端，health/bootstrap 与资源读取通过，历史保持 7 条且恢复队列为 0，鉴权退出 exit 0。此轮未准备或执行任何环境计划；前后 28 项完整 User/System 环境原文和类型一致。脱敏证据见 `installed-20261019-130846.json`。源码页面鼠标接受和已安装服务资源一致性分别记录，不把二者写成已安装服务页的重复鼠标验收；干净 VM、多用户等边界沿用上文。

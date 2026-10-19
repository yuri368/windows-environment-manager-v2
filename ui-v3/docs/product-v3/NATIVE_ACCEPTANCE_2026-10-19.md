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

## 尚不能扩大声称通过的范围

本轮操作系统包含开发工具，尚不能证明干净系统无开发 runtime 的安装生命周期；多登录用户、使用另一个管理员凭据的 UAC、断电恢复、原生 IME/拖动/DPI 和完整 §18 门槛仍须对应实测。旧沙盒结果或这次成功取消不能替代这些证据。

ChatGPT Bridge 只读检查仍返回 `BROWSER_UNAVAILABLE` / `ECONNREFUSED 127.0.0.1:9222`，worker 未运行；本轮没有发送网页协作消息，也没有为启动 Bridge 写其 H 盘工作目录。既有 Astra 实施方案继续约束实现，子 Agent 审查不能冒充网页确认。

完整环境清单、启动 ticket、cookie/CSRF 和未裁剪服务状态只保留本地，不提交 Git。失败安装/备份证据不自动删除。

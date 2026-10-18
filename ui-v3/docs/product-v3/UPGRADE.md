# V3 升级备份与实例锁协议（protocol_version=1）

本组件实现 Astra D01 的升级前数据备份及持续持锁边界。它不替换程序文件、迁移数据库、运行安装器、调用 UAC 或访问 Registry。`upgrade.py`、`test_upgrade.py` 是本任务实现；产品 `__main__` 分派及 Inno 接线由各自 owner 持有。组件测试通过不能代替完整 D01 安装升级验收。

## 固定入口与 Python API

产品入口由主 Agent 在其他文件接入：

```python
if argv and argv[0] == "--upgrade-helper":
    from .upgrade import main as upgrade_main
    return upgrade_main(argv[1:])
```

源码独立入口是 `python -m envmanager_v3.upgrade`；打包入口是复制出的 `EnvManagerV3.exe --upgrade-helper`。都不构造 Environment/Service、不启动 HTTP、不输出启动 ticket。

```python
from envmanager_v3.upgrade import prepare_upgrade, verify_backup, UpgradeBlocked

with prepare_upgrade(data_dir, "3.1.0", backup_root=external_backup_root,
                     lease_seconds=1800) as session:
    receipt = session.check()   # 紧邻安装替换之前，再验证源数据及备份
    # 调用方在此完成程序文件替换，同时监测期限/异常。
    result = session.finish("installed")
```

`prepare_upgrade(data_dir, target_version, *, backup_root=None, lease_seconds=1800, allow_missing=False, cancellation_check=None, fault_injector=None)` 返回**仍持有生产 InstanceLock 的 UpgradeSession**；不返回放锁后的升级许可。必须跨整个替换过程保留该对象，所有退出路径都 `close()`。`finish("installed" | "cancelled")` 检查数据和备份、持久写入 `finish.json` 后放锁；异常也放锁并向调用方传播，不能声称成功。仅 `close()`／退出 context 不产生安装完成记录。`cancellation_check` 可在复制、哈希和 SQLite progress 中阻断；`fault_injector(stage, session)` 供确定性测试，业务方无需传入。

API 默认拒绝不存在的数据目录。`allow_missing=True` 用于首次安装：仅建立协调数据目录及 `instance.lock`，生成空备份，并明确记录 `source_data_existed=false`。已有用户内容不删除、不重写；锁文件的建立/初始化是唯一源目录协调写入。文件读取可能由操作系统更新访问时间，本组件不承诺 NTFS 元数据逐字节不变。

Python API 的期限由每次 `check`、复制及 SQLite progress 检查执行；它不会替调用方启动后台线程。独立安装器应使用下述常驻 helper，避免短命脚本放锁后才替换。

## Inno 可直接接入的 CLI 协议

以下 EXE 必须来自**新安装包完整 runtime 的临时副本**，副本位置在被替换的安装目录以外；不能运行待覆盖的旧 EXE。session 目录、备份目录与源数据目录必须互不包含；控制目录父级必须预先存在，session 子目录必须不存在且排他创建。

```text
EnvManagerV3.exe --upgrade-helper check-user
EnvManagerV3.exe --upgrade-helper hold --target-version 3.1.0 --session-dir <new-session-dir> --lease-seconds 1800 --prepare-timeout 120 --parent-pid <setup-pid> --require-unelevated
EnvManagerV3.exe --upgrade-helper status --session-dir <same-session-dir> --timeout 10
EnvManagerV3.exe --upgrade-helper finish --session-dir <same-session-dir> --outcome installed --timeout 10
```

`prepare` 是 `hold` 的同义命令，**同样前台常驻、持续持锁**。`--data-dir <explicit-local-path>` 和 `--backup-root <external-local-path>` 可选。所有路径拒绝 UNC、symlink/junction/reparse 路径；数据树内遇到 reparse 也阻断，不跨链接追读其他目录。

`check-user` 是无参数的同步身份预检，只调用现有 `require_unelevated()`；不解析数据目录、不创建 data/session/backup、不构造环境或 Service。成功 stdout 为 `{"protocol_version":1,"result":"passed"}` 且 exit 0；拒绝输出标准 `result=blocked`、`error_code=ORIGINAL_USER_REQUIRED`、原始错误说明及 exit 1，备份字段为 null。安装器必须在启动 hold 前，以**同一原用户执行方式**同步执行 check-user 并核对退出码；机器安装使用 `ExecAsOriginalUser` 等待退出。失败立即显示身份检查错误，不能继续等待不存在的 session。预检通过只允许继续启动 hold，不能授权替换；hold 仍必须带 `--require-unelevated` 再次检查。

该预检解决已观察到的安装器问题：Sandboxie FakeAdminRights 下 hold 在创建 control 前正确拒绝，但隐藏 stdout 的异步安装器只能等待 120 秒。此修复保留 FakeAdminRights/提升 token 的拒绝；不通过放宽身份规则解决等待。旧 `014347` 候选 EXE 不含 check-user，安装器接线必须配套包含新 CLI 的 EXE/runtime，不能仅重编译 ISS 后复用旧 runtime。

1. 安装器解压新 runtime 到替换范围外，先同步执行上述 check-user；通过后创建有正确 ACL 的 mailbox 父目录，然后以原始未提升用户身份**异步**启动 `hold`；不能等待 hold 结束才开始安装。
2. helper 在任何目录创建前处理 `--require-unelevated`，读取自身 TokenElevation；提升状态或查询失败返回 `ORIGINAL_USER_REQUIRED`，不能 fallback 管理员。显式 `--data-dir` 也不能绕过此标记。
3. 省略 `--data-dir` 时，由 helper 自身调用 `SHGetKnownFolderPath(FOLDERID_LocalAppData)` 加 `EnvManagerV3`；不从提升后 setup 的环境变量、`{localappdata}` 或管理员 HKCU 猜原用户目录。非 Windows 必须明确传目录。
4. helper 获取 `data_dir/instance.lock`（Windows 第 0 字节非阻塞独占，与当前生产 `__main__.InstanceLock` 相同）。有运行实例时直接 `INSTANCE_ACTIVE`。准备期间保持锁，未完成 execution／恢复待办阻断，不擅自对账或修改状态以通过升级。
5. `progress.json` 仅诊断准备阶段；`ready.json` 仅表示已形成备份并发现会话。其 `result=ready` 不是持久升级许可。安装器必须同步运行独立 `status`，取得 exit 0 及 `result=ready` 后才允许开始替换；替换期间保留 helper、监测 terminal／期限，并在受控替换边界重新 status。不能凭 PID 存在或旧 ready 文件判定锁有效。
6. 安装替换完成后才调用 `finish --outcome installed`。安装失败/取消用 `finish --outcome cancelled`，保留备份，安装器负责程序文件失败处理。finish exit 0 的 `result=finished` 表示协调会话确认结束；`outcome` 是调用方申报，不是本组件验证了所有安装文件。
7. 未发布 ready 前也能取消：向本次 session 写 `cancel.json`（内容不限，存在即取消；只拒绝、不会授权任何操作）。准备超时、用户取消或异常清理均可写该标记。标记可在长复制/哈希/SQLite progress 中消费。
8. 安装器传 `--parent-pid` 后，helper 打开该 Windows 进程的只读 SYNCHRONIZE 句柄；准备及持锁阶段检查进程对象，不靠可复用 PID。父进程退出或无法确认存活即阻断、放锁；不终止父进程。无法打开句柄直接拒绝启动。

准备期限默认 120 秒（`--prepare-timeout`），成功 ready 后不再使用该准备期限。绝对 lease 默认 1800 秒，最大 7200 秒，从准备开始计时，不能无限续租。helper 在循环及长操作的检查点强制期限；内核 I/O 本身不可由 Python 即时抢占。安装器必须在剩余期限内完成替换及 finish；临近期限应停止新增替换，不能把 lease 过期理解成升级成功。helper 被强杀、机器断电或 lease 到期会释放操作系统锁，旧 ready 仍不授权继续。组件不能使外部安装器的文件复制成为原子事务。

## 消息格式与失败语义

成功 ready／status 的 JSON（stdout 及 ready.json 使用相同核心字段）：

```json
{
  "protocol_version": 1,
  "result": "ready",
  "backup_id": "unique-hex-id",
  "backup_path": "C:\\Users\\original-user\\AppData\\Local\\EnvManagerV3.upgrade-backups\\unique-hex-id",
  "manifest_sha256": "sha256-of-manifest-json",
  "data_dir": "C:\\Users\\original-user\\AppData\\Local\\EnvManagerV3",
  "target_version": "3.1.0",
  "helper_pid": 1234,
  "source_data_existed": true,
  "lease_expires_at_unix": 1792400000.0
}
```

控制请求使用每次新 nonce 的 `<nonce>.request.json`，helper 回 `<nonce>.response.json`，核对 `backup_id`／`request_id` 并实时重新检查源内容及备份。status 客户端超时绝不退回 ready 文件；状态已结束返回 `SESSION_CLOSED` 或具体阻断原因。结束/失败写 `terminal.json`。stdout 均为 JSON，不含启动 ticket；执行错误返回 exit 1、`result=blocked`、`error_code`、`message`、`backup_id`／`backup_path`（不可获得时为 null）。参数语法错误沿用 argparse 的 stderr 和 exit 2，同样不允许安装。

典型阻断原因包括 `INSTANCE_ACTIVE`、`UNFINISHED_EXECUTION`、`SOURCE_CHANGED`、`BACKUP_CHANGED`、`BACKUP_FAILED`、`LEASE_EXPIRED`、`PREPARE_TIMEOUT`、`CANCELLED`、`PARENT_EXITED`、`PARENT_UNAVAILABLE`、`HELPER_UNRESPONSIVE` 和 `ORIGINAL_USER_REQUIRED`。磁盘满、权限、复制、SQLite、flush、发布失败都传播失败，不生成可用 ready。失败暂存可保留 `.incomplete-<id>/failure.json`；不能以该目录或孤立 manifest 放行。

## 备份内容、验证和恢复

默认备份在源数据目录的相邻 `<data-dir-name>.upgrade-backups/<id>/`，不递归备份自身。先对所有持久文件建立大小、SHA256、文件身份及时间快照，将文件复制至私有 `shadow/`，验证复制内容；仅对 shadow 中的数据库调用 SQLite backup API。数据库及 WAL **不在原目录用 SQLite 打开**，因此不会在源目录创建或更新 `-shm`。

`data/` 是完整恢复载荷，包括工作区 TOML、非法/未保存草稿、SQLite settings/history/executions/previews、独立 history 文件、system-jobs/recoveryfiles、未知普通数据文件及空目录。识别 SQLite magic／`.sqlite3`／`.sqlite`／`.db` 后生成独立完整数据库，包含 committed WAL，不把未提交 WAL 当成已提交数据。对应 `-wal`／`-shm`／`-journal` 不作为恢复载荷重复叠加；shadow 可保留原始 WAL 作为取证，**不用于直接恢复**。

精确排除并记入 manifest：目录名 `tmp`、`temp`、`.tmp`、`.temp`、`__pycache__`；以 `.lock`／`-shm` 结尾的文件。没有通配排除 history、drafts、recoveryfiles、system-jobs 或旧备份。数据库 schema 使用现有 Database 校验契约，记录每库 `user_version`、backup 方法、支持 schema 和目标产品版本；不执行任何迁移。`source_product_version` 是 helper 构建版本，不能冒充未读取到的历史安装版本。

载荷逐文件 flush，SQLite integrity_check，manifest flush，Windows 使用 write-through rename 发布（其他平台 fsync 目录）。manifest 列出恢复载荷的 SHA256／大小、目录树、源清单、schema/version、排除项和恢复说明；ready 另外绑定 manifest SHA256。`verify_backup(path, manifest_sha256)` 验证清单及全部载荷；新增/删除/篡改均拒绝。准备后再次比对源清单；helper 持锁期间及每个 status/finish 重查源和备份。检测到外改即永久阻断本会话，不以内容随后恢复为由放行。未遵守锁的外部进程在两个观察点间修改又完全恢复的情况不构成可证明的实时监控保证。

恢复须先停止所有实例，另存当前数据及残留 WAL，再整体恢复经验证的 `data/`，确保无旧 sidecar 叠加。组件不自动恢复、不删除用户现有数据、不自动清理备份。备份属于应用内容快照，不是含 ACL/ADS 的 NTFS 卷镜像。

## 每用户与机器安装边界

用户安装和机器安装均由原用户身份的 helper 读取/备份该用户应用数据。机器安装使用 `ExecAsOriginalUser`；若 setup 最初就以 RunAsAdmin 启动而无法降权，`--require-unelevated` 必须阻断。提升后的 status/finish 只访问显式 session 控制目录，不推断/打开管理员用户数据。

安装器 owner 负责临时 runtime／mailbox 父目录 ACL：原用户能创建自己的 session 且持续读写，其他普通用户不可写该 session，管理员可进行 status/finish。`mkdir(0700)` 不能代替 Windows 父 ACL 的设计及原生验证。无法访问时阻断，不能换到管理员数据目录重试。

单个 session 的实例锁仅保护一个明确数据目录，不能证明机器上所有其他登录用户都退出了共享安装路径的旧 EXE。机器范围替换/跨会话运行进程检查由安装器负责。其他用户数据不由提升安装器遍历或备份；各用户在自己首次运行、任何 schema 迁移前，必须以自己身份单独备份并持锁完成迁移。本组件提供 API，主启动/Database owner 的迁移接入和多用户验收仍需独立完成；不能声称一个用户的 ready 覆盖所有用户。

## 验证记录及仍待 VM 验收

```powershell
python -m unittest tests_v3.test_upgrade -v
```

测试仅使用临时持久目录／真实 SQLite 和 WAL、生产 InstanceLock、真实 helper 子进程、只读 token/known-folder/process-handle API，以及确定性 I/O 失败注入；不运行安装器、不触碰宿主 Registry／UAC。

2026-10-19 初次联合回归 75 项通过（含当时的 upgrade、主入口分派、Database、launch）。随后主 Agent 的 full backend 日志记录一次 helper 在旧 15 秒测试发现窗口内未 ready；该日志没有子进程阶段或堆栈，不能据此断言阻塞根因。独立复查该目标为 0.779 秒通过。现测试使用与生产准备预算对齐的有界窗口、文件输出、每 10 秒子进程 faulthandler 堆栈、准备阶段 progress，失败会保留/输出阶段和 stdout/stderr，不静默重试或跳过。

最终定稿 `python -m unittest tests_v3.test_upgrade -q`：**50 项全部通过，27.461 秒，无跳过**。原失败目标额外连续 5 次独立运行均通过（1.554 / 1.593 / 1.576 / 1.902 / 1.634 秒），总 8.259 秒；未复现历史启动超时，也未据此声称已证明历史阻塞根因。遵照主 Agent 要求未重跑全套后端测试。

后续 `check-user` 定点修复：3 项新增预检测试，以及既有 hold 身份拒绝、显式路径身份复核、真实 token 和产品入口分派测试，**共 7 项全部通过，0.442 秒**。真实源码产品 CLI 子进程通过验证；本轮未重跑全 suite，也未据此宣称新打包 EXE 或 Sandboxie 安装器验收完成。

仍待原生/干净 VM 验收，未宣称通过：

- 无开发 runtime 的新旧包升级：临时完整 runtime、Inno 每替换边界 live status、完成/失败/取消/准备超时/父进程退出，以及安装文件回滚与重启读历史。
- 用户安装及机器安装的原用户 token、ExecAsOriginalUser、RunAsAdmin 拒绝、session/runtime ACL、中文/空格路径、readonly/磁盘满和防病毒导致慢 I/O。
- 多登录用户共享程序文件的在用检测、每用户首次 schema 迁移前备份、其他用户现存草稿/历史/恢复文件保留。
- 断电/重启后的备份恢复演练及 NTFS 存储设备持久性；本机 flush/rename 测试不能替代断电实验。
- System ProgramData 受保护恢复回执的机器范围备份不属于当前每用户 data_dir 快照，安装器/恢复 owner 应另行保证保留；本组件不通过管理员身份冒充某用户读取该用户数据。

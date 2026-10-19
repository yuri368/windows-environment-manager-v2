# V3 Windows x64 打包与隔离启动检查

当前原生修复候选为 **`packaging_v3/output/20261019-105247-728`**，本次只在 C 盘副本构建，完整路径为 `C:\EnvManagerV3Acceptance\20261019-child-native\source\packaging_v3\output\20261019-105247-728`。修复 machine 安装器 ACL 参数 ABI、控制邮箱身份和升级备份长路径。两类安装包、独立 File 启动、升级 helper、frozen 长路径回归及源码/stage 一致性核对均通过；真实 machine 同版本重装已通过。验收边界及失败记录见 [原生验收报告](NATIVE_ACCEPTANCE_2026-10-19.md)。它仍是未签名候选，尚不代表全部生产门槛完成。

| 当前原生产物 | SHA256 |
| --- | --- |
| `dist/EnvManagerV3/EnvManagerV3.exe`，须整目录分发 | `4b11c2b840a6d7cac8cc3649ee178f4b30dec3ca3031393726a985a3950f4f79` |
| `EnvManagerV3-3.0.0-dev1-windows-x64.zip` | `653cabfffa4bb820ff0b9efe83f9b36f3fd273fb2395e92dfcb4d3e9eeb0feda` |
| `EnvManagerV3-3.0.0-dev1-setup-user-x64.exe` | `f68686525a316a1d978c738d68a758c12f719f50b65d974a8f949cf198881463` |
| `EnvManagerV3-3.0.0-dev1-setup-machine-x64.exe` | `62500aab0633cace6808075294442b69fb1a7d076c4ce0ec49c92c799168bd3d` |

## 较早候选记录

较早候选 **`packaging_v3/output/20261019-024952-878`** 包含新标签页 CSRF 恢复、恢复/历史恢复冻结预览、已处理恢复记录投影和窄屏配置路径换行修复。当时完整 build.ps1 已 exit 0；115 包文件、15 生产资源、30 许可证、16 V3 模块、源码与 stage 无漂移。真正的 frozen EXE File 检查、两次鉴权启动/退出和升级锁/备份 smoke 均通过。下面保留其原始哈希和当时范围，不代替本页最新候选。

| 当前产物 | SHA256 |
| --- | --- |
| `dist/EnvManagerV3/EnvManagerV3.exe`，须整目录分发 | `0738bfc8431f45485782ade445e969d5aa8fc187fe571dd3b60a5fe586336910` |
| `EnvManagerV3-3.0.0-dev1-windows-x64.zip` | `01356d64d85ad86e32df5829e898d042f94cd290779436ce814d100dea6e707f` |
| `EnvManagerV3-3.0.0-dev1-setup-user-x64.exe` | `fb212e43c48fc220295a0839ab216e54704220763fb23802897917fc4c79d5d7` |
| `EnvManagerV3-3.0.0-dev1-setup-machine-x64.exe` | `1f60ab96cbebf4654ec0e2cc6204492fc730eb37c2cdef295c9c5ed9cc06accb` |

源码输入：product-app.js `407289928c60fe1c00929d0dfbbd5400200afcf678919c4ed327501d6e1ff57a`，service.py `26c3948a80f62731c298c3a460c0d683ab380b3bc5548d0958c0ea389174450a`，upgrade.py `fe125e4b999fcaf1910e6d56d1404e6fc96e6b2ddf8362ce6220d06bd9c5ca7f`。详细证据见该目录 `build-result.json`、`bundle-verification.json`、`SHA256SUMS.txt`、`smoke/` 和 `upgrade-smoke/`。真实 DefaultBox 安装生命周期及其限制记录在 ACCEPTANCE.md；干净 Windows VM、System/UAC、机器 ACL 和多用户门槛仍未通过。

`023956-534` 在构建期间源码因恢复 UI 验收继续修改，最终 source-match 核对失败；保留为诊断产物，不能交付为当前候选。`024527-428` 已通过构建及沙盒安装生命周期，随后发现恢复名称混入配置模式选项；当前 `024952-878` 已移除这两个非法选项并重新完整构建通过核对。

## 构建

从仓库根目录执行，使用 PowerShell 7；预览源也是相同入口。Git 镜像的 `envmanager_v3/tests_v3/packaging_v3` 位于仓库根，网页位于 `ui-v3`。打包按 `ROOT/ui-v3/index.html` 是否存在选择 `ui-v3`，否则使用 ROOT；spec 始终读取本次 stage 的 `ui-v3`，不收集整个源目录。

```powershell
& .\packaging_v3\build.ps1 -Python 'H:\ProgramFiles\DevTools\Python\Python313\python.exe' -BundleOnly
```

准备官方便携编译器并完整构建：

```powershell
& .\packaging_v3\prepare-inno.ps1
& .\packaging_v3\build.ps1 -Python 'H:\ProgramFiles\DevTools\Python\Python313\python.exe'
# 只要其中一种安装器可加 -Installers User 或 -Installers Machine；默认 Both。
```

脚本将构建依赖安装到 `packaging_v3/.build-venv`，不安装到全局 Python。依赖只能来自 `https://pypi.org/simple`，`requirements-build.txt` 固定全部直接/间接依赖与 wheel SHA256，安装启用 `--require-hashes --only-binary=:all:`，不关闭 SSL 校验。固定运行工具为 Python **3.13.7 x64**、PyInstaller **6.22.3**、hooks **2026.8**、tomlkit **0.13.3**；业务代码的 Python 3.11 最低目标不等于本机实际构建版本，当前未验证 Python 3.11 构建。更换 Python 版本需重新审查和生成锁定依赖。

每次运行生成 `packaging_v3/output/<时间戳>/`，包含：

- `dist/EnvManagerV3/EnvManagerV3.exe` 及整个 onedir 目录，必须整目录分发。
- `EnvManagerV3-3.0.0-dev1-windows-x64.zip`、`SHA256SUMS.txt`、`bundle-files.sha256.json`、`build-result.json`。
- `build.log`、PyInstaller `work/` 警告和分析文件、`stage/runtime-manifest.json`、`smoke/` 与 `upgrade-smoke/` 证据和隔离数据。
- ISCC 可用且 smoke 通过时：`EnvManagerV3-3.0.0-dev1-setup-user-x64.exe`、`EnvManagerV3-3.0.0-dev1-setup-machine-x64.exe`、`inno-tool.json`（编译器版本和 SHA256）。脚本优先使用本目录的官方便携 Inno 6.7.3，也接受 `-IsccPath`，但其编译工具文件必须与 pin 一致。

任一启动测试、依赖/资源校验、打包或 smoke 失败，构建返回失败。若 EXE 已生成，仍保存压缩包、哈希和失败状态，便于诊断，不能据此宣布 smoke 通过。缺少 ISCC 时完整构建在 onedir 产出后明确失败；`-BundleOnly` 明确只要求 onedir。

同步构建源码到 Git 镜像时保留本目录脚本、spec/iss/manifest、锁定 JSON、requirements、原始 Node 许可证/官方校验清单和 Inno 许可证。`.build-venv`、`.pip-cache`、`.pyinstaller-cache`、`wheelhouse`、`tools`、`output` 和运行日志均为本地构建材料，不作为产品源码同步；便携编译器可由 `prepare-inno.ps1` 在镜像本地重新准备。本子任务不执行 Git 同步、提交或发布。

## Node 与许可证

优先从 `Get-Command node.exe` 定位的已安装 Node 复制；可用 `-NodePath '<绝对路径>\node.exe'` 指定。脚本不下载或安装 Node，不修改 PATH。

`runtime-lock.json` 固定：

- Node **v26.10.0 / win-x64**。
- `node.exe` SHA256：`cea6ac365f9bb9586dafd2084d996e092e7d3e7d07ab52d1f76bf53d1fca9bc4`。
- 来源为 [Node 官方 v26.10.0 SHA256 清单](https://nodejs.org/dist/v26.10.0/SHASUMS256.txt) 的 `win-x64/node.exe` 条目；首次构建已逐字节核对已安装 binary。
- 随 binary 安装的 `LICENSE` SHA256：`168f764dbef057a171bca4f00e11c4714e8e532e7ed63c0289bee35649b9c395`。
- [官方该 tag LICENSE](https://raw.githubusercontent.com/nodejs/node/v26.10.0/LICENSE) SHA256：`6b85ee1983d01fa2cdcdd5d1a3053c6221fae41d6a54e43b416bf35969fc4128`。

两份 LICENSE 的字节哈希因 CRLF/LF 不同而不同，通用换行解码后的文本已核对一致，包含 Node 的第三方许可全文。原始两份文件均保留，未通过换行转换冒充相同 binary hash。后续构建严格校验这两个 pin 和实际 `node --version`，版本或哈希不符即失败。

`_internal/licenses/` 随包收集 Python 完整 LICENSE、Node 全文/第三方通知、tomlkit MIT、PyInstaller 许可及 bootloader exception、Inno Setup 许可，以及当前构建依赖许可，`manifest.json` 记录许可文件的逐文件 SHA256。`_internal/runtime-manifest.json` 记录运行时、依赖版本、规则/网页/worker 和 Python 源快照哈希。未对 V3 做代码签名，不描述为已验证发行商。

Inno 6.7.3 从[官网所链接的官方 release](https://github.com/jrsoftware/issrc/releases/tag/is-6_7_3)下载。安装器 SHA256 固定为 `9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732`，已与官方 `.issig` 比较，且 Authenticode 验证为 `Valid`、发布者 `Pyrsys B.V.`。使用[官方便携模式](https://jrsoftware.org/ishelp/topic_technotes.htm) `/PORTABLE=1 /CURRENTUSER /VERYSILENT /NOICONS` 将编译器部署到 `packaging_v3/tools/inno-6.7.3`，未提权、不添加文件关联和工具卸载注册；官方该 tag 的 setup/isportable 源码副本与部署日志保留在 tools 下。`inno-tool-lock.json` 固定官方释放的 49 个根目录工具文件 SHA256，包含编译器、预处理器和安装引擎。ISCC.exe 自身版本资源为 `0.0.0.0`，不能用它冒充发行版本；报告的 6.7.3 来自官方已验签发行包，并记录原始 fileVersion。ISCC.exe SHA256 为 `0a8757031b33777e4c9cbffee40f11a5062b36d25cbe144c1db73b6102b80ad7`。

## 资源与独立性

`EnvManagerV3.spec` 只收集 `runtime-lock.json` 列出的 15 个生产 HTML/JS/CSS；`temp.html`、演示初始化 app.js、fixtures、tests、design-evidence、node_modules、V2 `envmanager`、PySide6 和用户数据不进入包。DemoPlan/config-format 保留为共享正式规则。worker 放到 `_internal/envmanager_v3/engine_worker.cjs`，Node 放到 `_internal/runtime/node.exe`，网页放到 `_internal/ui-v3/`，匹配 frozen `assets_directory()` 与 planner 的查找位置。

Python package 经正常 import 收集，包含 `service`、`workspace` 及 helper 等模块；不将 V2 复制进 V3。每次先暂存独立 V3 源文件/网页快照，再从暂存源码构建，避免前端/后端 agent 后续写入改变该候选包的输入。暂存副本不是新的代码维护位置。

EXE 构建后由 `prepare.py seal` 生成包根目录 `system-package.json`，协议 2，字段精确为 `protocol/package_id/version/entry/rules/files`；`files` 包含所有运行时文件的 SHA256，排除清单自身。`rules` 按 demo-plan.js、config-format.js、engine_worker.cjs 顺序给出逻辑名与包内路径。此接口已经与 IPC owner 对齐；清单本身不是信任凭据，native provider 还须核验固定 ProgramFilesX64 路径、owner、ACL、非 reparse 及受保护文件句柄。两种安装器使用相同包清单；只有机器安装负责受保护部署。`verify-bundle.py` 在最终构建结束核对资源、许可证、模块（包括 upgrade）、全包清单、产物哈希、三规则复合 hash、PE manifest、两类 smoke 及当前 Python/网页/worker/构建脚本与清单一致性；源码漂移时构建不能标最终完成。测试源码不进入运行包；Git 镜像的生产与测试源码同步由主 agent 负责，本轮从预览源构建。

默认 backend 仍为 `windows`；没有把发行入口改成演示模式。所有本任务执行的命令都显式使用 `--backend file`，默认 Windows 分支测试使用 mock。EXE 为 `asInvoker`，当前使用 console bootloader 保留命令行 JSON 和错误输出，不会默认申请管理员。

## 检查命令与边界

```powershell
# 只测启动参数、资源、锁和错误路径；默认 Windows adapter 完全 mock。
& .\packaging_v3\.build-venv\Scripts\python.exe -B -m unittest discover -s tests_v3 -p test_launch.py -v

# 对已有包重新执行隔离 smoke；证据目录必须不存在。
& .\packaging_v3\.build-venv\Scripts\python.exe -B .\packaging_v3\smoke.py --exe '<构建目录>\dist\EnvManagerV3\EnvManagerV3.exe' --evidence-dir '<packaging_v3 下新建证据目录>'

# 手动检查包配置，data-dir 指向独立目录。
& '<构建目录>\dist\EnvManagerV3\EnvManagerV3.exe' --backend file --data-dir '<隔离目录>' --no-browser --check
```

smoke 顺序：`--check` → 实际子进程 `--backend file --no-browser --port 0` → `/health` → ticket/cookie/CSRF 交换 → bootstrap 必须是 isolated → 正式 HTML/JS → 鉴权 quit → 再启动验证工作区身份。冻结包测试将子进程 PATH 缩到 Windows System32，证明不会靠开发机 PATH 的 Node/Python 掩盖漏包。启动 ticket 和 CSRF 不保存到日志；失败时保留 stderr、状态与 ID 差异。它不执行环境计划或 Registry adapter，不替代安装 VM、原生 UAC、真实 DPI 和完整浏览器操作验收。

新增 `smoke-upgrade.py` 由构建脚本对新 EXE 执行：用 file backend 建独立数据，加入 SQLite、原始非法 TOML 和恢复文件 fixture，启动 `--upgrade-helper hold --require-unelevated --parent-pid ...`；真实第二实例必须被锁阻止；校验 SQLite 备份和原始字节；模拟程序文件替换前后均须 fresh status；finish installed 后源数据哈希不变且产品可重开。读取备份 SQLite 使用 `mode=ro&immutable=1`，避免验证过程产生 WAL/SHM 改动备份。该脚本不执行 Setup，也不验证 UAC/安装 ACL。结果分别保存在 `upgrade-smoke/upgrade-smoke-result.json` 和 `build-result.json` 的 `upgradeValidation` 中。

## 安装器行为

`EnvManagerV3.iss` 使用 `PrivilegesRequired=lowest`，默认安装到 `{localappdata}\Programs\EnvManagerV3`，Start Menu 使用 `{userprograms}`，可选桌面快捷方式使用 `{userdesktop}`，均属于安装当前用户；不创建所有用户快捷方式。没有安装后自动启动入口。不修改 PATH/环境变量，不安装驱动、服务或计划任务，不清理用户数据。`CreateUninstallRegKey=yes` 保留正常的每用户卸载注册，安装后可通过系统“已安装的应用”或 `unins000.exe` 管理。开发期间只编译 V3 安装包，不在宿主运行它；这一执行约束不禁用产品将来的正常安装注册。常量与卸载项行为参见 [Inno 常量](https://jrsoftware.org/ishelp/topic_consts.htm) 和 [CreateUninstallRegKey](https://jrsoftware.org/ishelp/topic_setup_createuninstallregkey.htm)。

新增独立入口 `EnvManagerV3-machine.iss`，通过 `MachineInstall` 编译分支，机器安装要求管理员；固定目录 `{commonpf64}\EnvManagerV3`，拒绝命令行改目录。只部署程序和配置应用私有目录 ACL，不写环境变量，不执行 backend。机器安装 Start Menu/可选桌面快捷方式使用公共 shell 目录；主程序 manifest 保持 `asInvoker`。程序启动的数据目录仍为当次操作者 `%LOCALAPPDATA%\EnvManagerV3`，不会由安装器写入提升账户的用户数据目录。

PE 读取已观察到 Inno 6.7.3 生成的两种安装器外层 loader 均为 `asInvoker`，不能据此把机器配置判断为“不提权”；安装提权策略由 `PrivilegesRequired=admin/lowest` 控制，实际 UAC 仍需 VM 验证。参见 [Inno PrivilegesRequired 官方说明](https://jrsoftware.org/ishelp/topic_setup_privilegesrequired.htm)。验证报告记录观察到的 PE 值，主程序仍强制核对 `asInvoker`；不在宿主运行 setup 来验证 UAC。

机器安装器原生 API 将程序目录/全部已安装资源 owner 设为 Administrators，protected DACL 仅 Administrators/SYSTEM full、Authenticated Users read/execute；不改变顶层 Program Files 或 ProgramData ACL。初始化 `%ProgramData%\EnvManagerV3\system-receipts`，公共祖先 owner Administrators、protected DACL 仅 BA/SY full、AU `0x120089` read，与 IPC owner 的 `receipt_sddl` 一致；SID/operation 子目录由提升 helper 创建。最终回执路径为 `system-receipts/<operatorSID>/<job32hex>/receipt.json`，同目录保留 `progress.jsonl`。已有回执根 ACL 不匹配时拒绝，不能通过覆盖 ACL 信任旧内容；路径含 reparse point 时拒绝。应用文件 ACL 设置后读回比较，失败则安装报错；这些执行代码已编译验证，实际部署/ACL/重新安装/拒绝路径仍需安装 VM 验证。

普通卸载只移除已安装程序文件，保留 `%LOCALAPPDATA%\EnvManagerV3` 中配置、SQLite、历史、草稿和恢复回执；机器级 ProgramData 回执目录也不会被卸载清理。两种安装器使用独立 AppId 和卸载注册项。旧 `222928-330` 候选仍是禁止就地升级的历史包；下面的升级接线属于其后源码，必须重建才会包含。

### 升级接线（2026-10-19）

两种 ISS 都包含 `upgrade-install.iss`，在 `PrepareToInstall` 从新安装包提取完整新 runtime 到私有临时目录，用新 EXE 的 `--upgrade-helper`，不依赖旧 EXE 是否含 upgrade 模块。通过 `ExecAsOriginalUser` 启动 holder，`--require-unelevated` 检查 token，拒绝提升账户 fallback。数据目录由 holder 按调用者 Known Folder 解析；自定义数据目录可显式传安装器 `/DataDir=<绝对目录>`。首次安装也经同一协议，备份清单记录源数据原先是否存在。

holder 的 hold 持有真正的单实例锁，检查未完成执行，创建原始工作区/草稿/历史/恢复文件清单和 SQLite backup，发布 ready。安装器不能只凭 ready 文件放行：在进入安装、每个程序文件 `BeforeInstall`、部署完成时调用 status，通过新 nonce/backup ID 响应确认 holder 仍持锁。只有部署及机器文件 ACL 设置成功后才 finish installed；失败/取消 finish cancelled，不自动回放环境计划或恢复数据。保留备份和终态证据，人工恢复协议见 `UPGRADE.md`。

准备期限为 120 秒，绝对租期为 7200 秒；安装器传自己的 PID，holder 以只读 SYNCHRONIZE 句柄监控精确进程对象。准备期间取消/超时写 `cancel.json`，准备循环检查该标记；安装器退出或无法确认父进程时拒绝继续。租期/源数据/备份变化也会阻止后续覆盖。机器安装临时 runtime 归管理员且普通用户只读，控制父目录仅允许创建私有子目录；这些 ACL、跨账户 UAC、父进程句柄可读性以及 Setup 失败回滚仍须原生 VM 实测，不能以编译或 helper smoke 代替。

升级备份覆盖本次启动安装器的原操作者数据目录。机器安装涉及其他用户会话、其他用户数据目录和自定义目录的场景，仍须逐项 VM 验证，不能宣称所有用户均已备份或 D01 已完成。

机器安装另包含 `machine-in-use.iss`：在任何覆盖前递归收集已有 `{app}` 的全部普通文件（包括主 EXE、Node、Python DLL 和其他资源），拒绝 reparse，最多 1024 文件，向 Windows Restart Manager 一次注册完整列表。在开始安装、每个程序文件覆盖前以及部署结束时重新查询；有任意受影响进程/服务、需要重启、API/权限错误均拒绝继续，覆盖其他登录用户的共享资源占用，不按当前用户名或进程名过滤。保持 `CloseApplications=no`、`RestartApplications=no`，代码没有 RmShutdown/RmRestart 或强杀调用。查询依据见 [RmRegisterResources](https://learn.microsoft.com/en-us/windows/win32/api/restartmanager/nf-restartmanager-rmregisterresources) 与 [RmGetList](https://learn.microsoft.com/en-us/windows/win32/api/restartmanager/nf-restartmanager-rmgetlist)。这些 API 只在将来的机器 Setup 内执行，本任务宿主只编译；跨用户检测效果、检查与复制间的新进程竞争、失败回滚仍待多会话原生 VM 验证。Sandboxie 安装生命周期可提供隔离功能证据，不替代真实 UAC/管理员 ACL 和跨登录会话验收。

## 本轮构建证据

2026-10-18，Windows 11 build 26100，首轮 handle `8328`，输出 `packaging_v3/output/20261018-200226-566`：

- 14 项启动测试通过；EXE 已产出；frozen `--check` 成功；loopback health 和鉴权 bootstrap 已取得响应。
- 严格 smoke 在首次重启的空工作区身份检查失败。只读定位到初始空工作区通过 `update_config([])` 未触发 persist；问题已反馈主 agent，本子任务不修改 backend。
- 未找到本机原生 ISCC：搜索了 AppLinks、Program Files、Scoop、Local Programs 和 H 盘文件，未读取 Registry。未编译/运行安装器。
- 本首包不是最终前端包，也不是通过完整 smoke 的发行包。后续构建记录追加在此文档，具体 SHA256 和状态以每个输出目录为准。

后续进展：空工作区 identity 修复已由 workspace owner 落盘，14 项启动测试与连续两次 EXE/file smoke 已通过。官方便携 Inno 6.7.3 已准备并实际编译成功，不再存在缺失编译器的阻塞。`20261018-201322-708` 与 `20261018-202209-549` 均已产出安装包，但其后仍有后端更新；最新交付记录见下。

尚待：无开发 runtime 的干净 VM 安装/升级/卸载/数据保留验收；named pipe/helper、UAC 和原生 Windows 完整生产验收。旧包禁止就地升级；新源码已接升级 helper，待冻结重建和 VM 验收。此前 DefaultBox 测试不由本打包子任务执行或重新宣称通过。本任务没有在宿主执行 V3 安装器或 Windows backend。

### 22:29 候选（不作为最终交付）

`packaging_v3/output/20261018-222928-330`，handle `76513` 已 exit 0。EXE、ZIP、user/machine 双安装器均生成，14 项启动测试、两次 EXE/file smoke 通过；构建结束时当前源码与快照一致，115 个包文件、15 个静态资源、30 份许可文件及三规则联合 hash 均通过核对。IPC 三文件均匹配 owner 提供的冻结 SHA256。

其后主线发现真实子进程终止恢复缺陷：`write_intent/compensating` 中断后读回等于 before，若没有 durable verified rollback，不能标记 compensated。该候选不包含后续修复，必须等执行器 owner 修复并通过进程边界测试后重建。此处保留真实构建证据，不据此宣称恢复或完整产品验收通过。

本候选 SHA256 记录在其 `SHA256SUMS.txt`：

| 文件 | SHA256 |
| --- | --- |
| EnvManagerV3.exe（位于 dist/EnvManagerV3，须整目录分发） | `539de44a17f655ee9d0e5d2c81fc4510499ee0cd213a3ae91e39b26629978f7a` |
| EnvManagerV3-3.0.0-dev1-windows-x64.zip | `2ed9ad1b03caded3e51ff281a5ab38c7f46649becf9206712221a724843ac2b2` |
| EnvManagerV3-3.0.0-dev1-setup-user-x64.exe | `255916c107bebf3207ce98f6b3dd9c9ecbf0ac00ef8e530fdfe81331a7fec025` |
| EnvManagerV3-3.0.0-dev1-setup-machine-x64.exe | `4c2d4bb5804285664866c6daac62fcb272344a667f971bb6c14b145f2d054491` |

证据：`commands.log`、`build-result.json`、`bundle-verification.json`、`smoke/smoke-result.json`、`dist/EnvManagerV3/system-package.json` 与 `dist/EnvManagerV3/_internal/runtime-manifest.json`。未运行这两种安装器；原生安装 VM 验收仍待执行。

### 发布门槛记录

| 范围 | 本打包子任务实际状态 |
| --- | --- |
| PyInstaller onedir、Node/规则/许可证完整性 | 已构建并执行隔离启动；证据逐包记录 |
| user / machine 两种 Inno 候选 | 已实际编译；未在宿主执行 |
| 精确冻结源码、全包文件与产物 hash | 构建后核对；源码变动必须重建 |
| 已发现的真实子进程中断恢复缺陷 | `222928-330` 不含修复，不作为最后交付；等待修复后重建 |
| D01 干净 VM 安装、启动、快捷方式、卸载、数据保留 | 待原生 VM 验证；file smoke 不能替代 |
| D01 升级锁、SQLite/工作区备份及升级验收 | 新源码已完成 installer/helper 接线及源码 helper smoke；冻结包与实际安装升级 VM 仍待验证，D01 未通过；旧包阻止升级不算完成 |
| 真实 UAC、管理员回执 ACL 与 System Registry 提交 | 待隔离原生 VM 验收，不以编译/mock/普通权限 pipe 代替 |
| 宿主安装、宿主 Windows backend、宿主环境变量改写 | 本任务未执行 |

以上保留产品全部完成门槛；打包子任务的通过不等于产品 goal 完成。

### 交给隔离验收 owner 的命令

以下 Setup 命令仅在已由 MCP 确认的 DefaultBox 或干净 VM 内执行；本打包任务不执行它们，不创建并行沙盒安装。将 `$candidate` 改为最终记录的构建目录，示例目录 `014347-746` 在构建完成核验后才可用于验收。

```powershell
$candidate = 'H:\Yuri368\环境变量配置V3\packaging_v3\output\20261019-014347-746'
$testRoot = Join-Path $env:LOCALAPPDATA 'EnvManagerV3-isolated-acceptance'
New-Item -ItemType Directory -Path $testRoot -Force | Out-Null
$userSetup = Join-Path $candidate 'EnvManagerV3-3.0.0-dev1-setup-user-x64.exe'
$program = Join-Path $testRoot 'program'
$data = Join-Path $testRoot 'data'
$setupArgs = @('/CURRENTUSER', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART')
$setupArgs += '/DIR="' + $program + '"'
$setupArgs += '/DataDir="' + $data + '"'
$setupArgs += '/LOG="' + $testRoot + '\install.log"'
$setupProcess = Start-Process -FilePath $userSetup -ArgumentList $setupArgs -WindowStyle Hidden -Wait -PassThru
if ($setupProcess.ExitCode -ne 0) { throw "Setup failed: $($setupProcess.ExitCode); inspect install.log" }
# 所有产品运行显式 file backend。
& "$program\EnvManagerV3.exe" --backend file --data-dir $data --no-browser --check
# 退出全部产品实例后，再运行同一 setup /DataDir，验证升级备份、数据/身份/历史保留。
# 最后执行 $program\unins000.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART，核对 data 和相邻 data.upgrade-backups 保留。
```

机器安装的独立入口为同目录 `EnvManagerV3-3.0.0-dev1-setup-machine-x64.exe`，使用 `/ALLUSERS` 而非 `/CURRENTUSER`，不传 `/DIR`，固定 ProgramFilesX64 路径；可同样显式传 `/DataDir` 做隔离数据验证。必须从原用户启动，不能通过去掉 require-unelevated 绕过失败。模拟管理员 token 若导致协议拒绝，记录失败，不能把它等同于真实跨账户 UAC 已通过。多登录用户占用测试应在原生 VM 由另一个用户运行共享安装 EXE/Node，再确认机器 setup 拒绝且不关闭其进程；Sandboxie 单一登录身份不能替代该项。

### 2026-10-19 冻结源码候选（含机器在用检查）

输出 `packaging_v3/output/20261019-014347-746`，handle `56623`，**exit 0**。从本预览源平铺布局构建，非旧镜像。旧 `013811-969` 仅是加入机器在用检查前的迭代候选，不作为最后交付。

实测通过：14 项 launch；新冻结 EXE 的 `--check`、连续两次真实启动/health/bootstrap/鉴权退出及空工作区身份保持；PATH 移除开发 runtime；升级 helper SQLite/原始非法草稿/恢复文件备份、真实第二实例阻止、模拟替换期间持锁、finish 后数据哈希不变及产品重开。此次升级输出捕获已正确处理冻结 Python 的 Windows 本地编码，无先前读线程 UnicodeDecodeError。

user/machine 两安装器均由原生 ISCC 6.7.3 实际编译生成。`bundle-verification.json` 为 passed，`currentSourceMatchesStaged=true`、无漂移、无错误；核对 115 个包文件、15 个生产资源、30 份许可证，PYZ 包含 16 个 V3 package/module（包括 upgrade），主 EXE 为 asInvoker。`machine-in-use.iss` 已进入构建输入清单，其 SHA256 为 `91bb044e7b6329d2944c2bd9244401ad28ab43ebe5a8635553f15419cbd28514`；宿主没有执行其 Restart Manager API 或 Setup。

冻结输入核对：upgrade.py `f3ceeadc8cb2b0d7074496f02188f20779ef3b2812164865d79276134586ac58`；product-app.js `f53531a24c15a2a3e60207d9df1fc623bb7d32bc1188c19f743ca3f0cc419ce6`；service.py `c66fb7499fa6726fe5196aa81eae0fc63857587b99f41c88e689b04b8b11e8fd`；workspace.py `a80077e92bf16fd0e0d749b4ef7b828d6d104da4d464c251cfd3c5aa93500685`；__main__.py `dd5a14213928bc492199ce4b3b96bc2631d71ea9fdb63c0a0033dfbf36e608bf`。准确小写 SHA 以 runtime-manifest.json 为准；test_upgrade.py 与 test_upgrade_dispatch.py 的源哈希另记 `freeze-evidence.json`，测试文件未混入运行包。

| 产物 | SHA256 |
| --- | --- |
| dist/EnvManagerV3/EnvManagerV3.exe，须整目录分发 | `eb529a83158fb0486c755f18bb8cac27cb72abdd273ddce3b7783bf9559dbb85` |
| EnvManagerV3-3.0.0-dev1-windows-x64.zip | `0a3af858343dcd81f6755584b98612237dc9a94201f249eba4fbcdbfd55140e9` |
| EnvManagerV3-3.0.0-dev1-setup-user-x64.exe | `a4bce83582b7e309e15cef11c7dd6ed908620f57e9b6c76ca9f4b27ce97040df` |
| EnvManagerV3-3.0.0-dev1-setup-machine-x64.exe | `d297d5157acfdb3ad37096963e3eedee4d94f9a2bd8e57e931b2f2c760a06daa` |

证据：本输出的 `console.log`、`commands.log`、`SHA256SUMS.txt`、`build-result.json`、`bundle-verification.json`、`freeze-evidence.json`、`smoke/smoke-result.json`、`upgrade-smoke/upgrade-smoke-result.json`、`stage/runtime-manifest.json`。恢复句柄状态为 `packaging_v3/output/active-build.json`。未执行 Sandboxie 安装或宿主安装；隔离安装生命周期由主 agent 后续单独执行，D01/真实 UAC/机器 ACL/多用户在用拒绝/断电恢复尚未验收，不将本候选称完整产品验收通过。

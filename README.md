# Windows 环境变量方案管理器 V2

V2 是面向 Windows 10/11 的全新实现，按“应用 → 方案 → 变量”管理 User 和 System 环境变量。配置文件只保存方案定义；选择草稿、成功应用值和执行恢复记录由独立状态库保存。

**[下载 Windows 安装包](https://github.com/yuri368/windows-environment-manager-v2/releases/latest/download/EnvironmentManagerV2-2.0.0-windows-x64-setup.exe)** · [版本与源码](https://github.com/yuri368/windows-environment-manager-v2/releases)

双击安装，完成后即可打开 GUI；程序内置 Python 和 Qt，无需安装开发环境。支持 Windows 10 1809 或更新版本 / Windows 11 x64。

![主界面：批量选择和 Windows 环境变量入口](docs/images/main-window.png)

## 运行环境

普通用户从 GitHub Release 下载 `EnvironmentManagerV2-2.0.0-windows-x64-setup.exe`，双击进入中文安装向导。程序内置 Python 和 Qt；安装后可从桌面或开始菜单打开 GUI，完成页默认勾选“立即启动”。使用正式界面时按标准 Windows UAC 提示确认管理员权限即可，无需运行命令或安装开发环境。

安装版的个人配置保存在 `%LOCALAPPDATA%\EnvironmentManagerV2\config\env.toml`，首次运行从内置示例创建。请在配置编辑器中把示例路径改成实际安装路径；安装和升级不改写 Windows 环境变量，也不会覆盖个人配置。下面的 Python 环境和启动脚本说明面向源码运行及开发者。

- Windows 10 1809 或更新版本 / Windows 11；当前安装包为 x64
- Python 3.11 或更新版本
- PySide6 6.11.2
- tomlkit 0.13.3

双击 `启动.cmd` 会在本目录创建 `.venv`，检查 Python 版本以及 PySide6、tomlkit 的导入和版本；缺少或版本不符时安装 `requirements.txt` 中的依赖。正式 GUI 启动会先检查管理员令牌，再创建 Qt 界面、服务和状态库：已有管理员权限就继续；普通权限时通过标准 UAC 提示，以同一 Python 解释器重新启动并保留命令行参数和当前目录。取消 UAC、提权启动失败，或重启进程仍未取得管理员权限时会显示错误并停止，不重复提权。重新启动只在正式主界面路径发生；`-Demo`、`-Check` 和 `-CheckAdmin` 不请求 UAC。启动 GUI 使用隐藏窗口。也可以在项目目录运行：

```powershell
.\启动.cmd
```

无管理员权限时，`启动.cmd -CheckAdmin` 可只读检测当前进程是否已提升；已提升返回退出码 `0`，未提升返回 `1`，不会触发 UAC。`启动.cmd -Check` 仅校验配置，不应用变量、不提权。两个命令也可通过 `launcher.ps1` 使用。

命令行入口为 `python -m envmanager`。在已激活的虚拟环境中可使用：

```powershell
python -m envmanager --check
python -m envmanager --check-admin
python -m envmanager --demo
python -m envmanager --demo --screenshot C:\Temp\envmanager-demo.png
python -m envmanager --config .\env.toml --state-dir .\local-state
```

`--screenshot` 仅能与 `--demo` 一起使用。演示模式使用临时状态和模拟存储，不读取或写入本机注册表。`--check` 校验配置并报告问题，不应用任何变量。`--check-admin` 只读检测当前进程的管理员权限；已提升返回退出码 `0`，未提升返回 `1`，不会请求 UAC。

启动器还支持 `-Demo`、`-Config <路径>`、`-StateDir <路径>`、`-Check` 和 `-CheckAdmin`：

```powershell
.\launcher.ps1 -Demo
.\launcher.ps1 -Config 'D:\配置\环境.toml'
.\launcher.ps1 -Demo -Config .\env.example.toml
.\启动.cmd -Check
.\启动.cmd -CheckAdmin
.\launcher.ps1 -Demo -Config .\env.example.toml -StateDir .\demo-state
```

传入相对的 `-Config` 或 `-StateDir` 时，启动器按调用时所在目录解析；正式 GUI 使用的 `-Config` 与 `-StateDir` 会随 UAC 重启保留。`-Check` 同步执行配置校验，退出码表示校验结果；`-CheckAdmin` 仅返回当前权限检测结果，不提权。

## 配置

`env.toml` 是本机个人配置，已加入 `.gitignore`，不随源码发布。首次正式启动时，如果默认配置不存在，会从 `env.example.toml` 创建一份 `env.toml`；请先在配置编辑器中按实际安装路径修改并保存，再预览应用。已有个人配置不会被覆盖，显式指定的 `--config` 路径也不会自动创建或替换。默认演示模式始终使用示例配置；`--check` 在默认配置尚未创建时只读校验示例，不创建文件。

源码运行默认读取项目目录中的 `env.toml`，安装版读取上文的个人配置目录。配置按 TOML 1.0 编写，采用 `[应用名.方案名]` 分组，每个变量键对应一条记录。应用表可用 `default_option` 指定默认方案；该字段是保留元数据名，值必须引用同一应用下实际存在的方案。未指定时兼容旧配置，使用该应用的第一个方案：

```toml
[Java]
default_option = "JDK17"

[Java.JDK17]
JAVA_HOME = { default_value = 'C:\Tools\Java\jdk-17', scope = "User", mode = "replace", editable = false }
Path = { default_value = '%JAVA_HOME%\bin', scope = "User", mode = "+append", editable = false }
```

支持 `replace`、`+append`、`-append` 和 `append` 四种操作。同一目标只有 `+append`/`-append` 时，系统按前置项、未匹配的现有项、后置项合成结果，不需要额外排序；若同一个忽略大小写匹配的列表项同时被要求前置和后置，整批操作会以冲突停止。同一目标包含 `append` 时，将它的相关输入合并为一个完整列表统一排序一次，确认后的顺序决定最终结果，方向要求不再约束最终位置。不同变量、不同作用域各自计算和排序。`replace` 输入相同则可合并，不同则冲突；`replace` 与追加操作指向同一目标也会冲突。排序和确认本身不会写入 Windows。

配置和运行状态分离：统一选择器可以选择默认值、上次成功应用值、候选值或允许编辑时的自定义值。每次正式启动时，每个应用使用最近一次成功应用记录中的方案、来源和选定输入初始化；没有历史时使用默认方案及各变量的 `default_value`。启动后用户修改的选择保持在运行中，点击应用不会覆盖这些选择；成功应用才更新成功偏好。候选值偏好按 `label` 与 `value` 保存并在加载时核验，避免候选项重排后选错值。配置源改变但历史输入仍合法时保留历史输入并提示；历史输入不再合法时回退到默认值。只选择或编辑不会改写 TOML。

### 配置编辑器

在主窗口右上角打开“配置 → 可视化编辑”。左侧依次选择应用、方案和变量；应用使用扁平索引，变量名与用户/系统作用域分别显示。应用概览摘要行右侧的方案选择器列出“全部方案”和当前应用的所有方案。选择具体方案会进入该方案概览并同步左侧方案；选“全部方案”回到当前应用的聚合概览，不改变左侧当前方案上下文，因此新增、删除和设默认方案仍作用于左侧当前方案。即使只有一个方案也显示选择器；表单编辑页不另加重复选择器。搜索覆盖全部应用、方案和变量，匹配结果替代变量表并可直接定位，应用与方案分区仍保留。应用、方案和变量各自的标题行直接并列新增与删除入口；方案选择器下方另有“设为默认方案”。动作按钮统一使用简短文字，提示中保留完整对象说明。`default_value`、`scope`、`mode`、`editable` 和候选字段遵循本文件所述的 TOML 配置规范；同名 User/System 双作用域记录分别编辑，配置仍以一组双作用域记录保存。

选择一个方案后，点击“设为默认方案”更新草稿，再保存配置即可生效。主窗口左侧勾选多个应用，每行可直接选择方案；底部“预览所选应用”统一预览这些应用当前选择的全部变量。点击应用名称只切换详情，勾选状态会保留。参考值默认折叠，点击变量右侧箭头可查看默认值、上次应用值和 Windows 实际状态；无效输入在清单和底部提示，并可用“定位输入”直接修正。

主窗口右上角的“环境变量”按钮直接打开 Windows 原生环境变量设置，便于查看用户级与系统级变量。该入口在演示界面中也会打开本机真实设置；打开动作本身不修改变量。在 Windows 设置中修改内容后，回到管理器点击“刷新”，后续执行仍会重新检查实际状态和冲突。

表单编辑后点击“更新此变量”进入共享草稿；表单尚未更新、草稿尚未保存与文件已同步分别显示。切换应用、方案、变量或搜索结果时，未更新的输入由导航守卫保护。可以撤销、重做，查看 TOML 差异，并先校验再明确保存。删除最后一条变量记录时会自动清理变空的方案及应用；配置至少保留一条有效变量记录，因此空配置不能保存。编辑器仅修改 TOML，不写入 Windows 环境变量，也不清除 SQLite 状态。外部程序在编辑过程中修改了配置文件时，编辑器会阻止覆盖并要求重新载入。

“配置 → 编辑原始 TOML”可直接打开源码页进行 TOML 编辑。可视化编辑器的设计边界和数据流见 [TOML 编辑器架构说明](docs/toml-editor-architecture.md)。

界面采用暖灰浅色工作区、墨色文字和少量绿色强调。主窗口、配置编辑器与执行对话框共用字体、图标和控件样式；源码与保存差异支持着色。布局、模块约定与演示截图见 [桌面界面设计](docs/ui-design.md)。

成功应用后，状态库记录此次参与应用的选定输入作为上次应用值，而不是追加合并后的完整 Windows 值。可同时选择多个应用的方案批量应用；按作用域和不区分大小写的变量名聚合目标，每个目标最多写入一次。整个批次共用一份预览和一次确认，只有所有目标验证成功后才一并提交涉及应用的成功偏好；取消或失败不更新成功偏好。执行前展示预览；执行中核对实际状态、逐项验证写入，并在部分失败时按保守策略尝试恢复本次已写入内容。

注册表状态保留“变量不存在”“存在且为空”“存在且非空”的区别，并保留原始字符串和注册表类型。配置中的环境变量引用按原始文本处理，不由管理器展开。`System` 写入需要管理员权限，不会自动降级到 `User`。

方案切换、撤销和历史恢复会先检查外部改动及共享变量。无法可靠判断列表项归属或外部合并结果时，管理器停止并报告冲突；共享恢复无法安全识别时，批量操作也会以冲突停止。共享的追加操作可以协作；破坏其他激活方案的替换操作会被阻止。

恢复或停用会按安全规则处理注册表和激活状态，但不会抹掉各应用最近一次成功应用的方案、来源和选定输入偏好。当前撤销实现仅在实际内容、存在状态和类型与应用结果完全一致，且目标未被其他激活应用共享时恢复原始结构。历史恢复仅允许恢复最近一次状态提交，之后有新操作或外部修改时会阻止恢复。复杂的交错修改、共享列表差异合并遵循 AGENTS.md 附录 B，保留数据并报告无法安全恢复；界面激活标记只表示最近一次成功应用记录。

## 状态与隐私

正式运行状态默认保存在：

```text
%LOCALAPPDATA%\EnvironmentManagerV2\<配置文件路径的 SHA-256>\state.sqlite3
```

配置路径哈希用于隔离不同配置文件的选择、成功应用和恢复状态。可用 `--state-dir` 指定其他状态目录。演示运行使用临时目录和模拟存储。状态库和配置都可能含有敏感值；不要将真实令牌放入示例、提交或日志，并妥善限制本机文件访问。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m envmanager --check
.\.venv\Scripts\python.exe -m envmanager --demo
```

`--check` 只做配置校验。演示模式验证界面和模拟流程，不代表真实注册表写入已验证。真实写入前始终需要用户确认；真实 System 变量写入需要管理员权限。

自动化测试使用内存存储模拟多变量失败、外部变更与崩溃；Windows 适配测试仅在 `HKCU\Software\EnvironmentManagerV2Tests\<随机标识>` 下创建可删除的隔离键，结束后清理，不写入实际的用户或系统环境变量。界面测试使用 Qt 离屏模式，验证来源选择、输入校验、排序、预览取消与显式配置保存。

源码按职责分为 `config.py`（TOML 校验）、`selection.py`（选择与草稿）、`engine.py`（纯计算）、`store.py`（Windows/模拟适配）、`persistence.py`（SQLite 状态与执行日志）、`service.py`（计划、执行与恢复）、`ui.py`（桌面界面）。启动入口是 `envmanager/__main__.py`。

## 迁移

V2 使用独立配置模型和状态格式。本目录中的 `env.toml` 是当前项目配置文件；没有自动迁移旧项目中的配置或自定义值。需要迁移时，应先人工审阅源配置，确认变量、作用域和操作语义后再录入。旧版 `append` 只补充缺少项的语义与 V2 的移动及去重规则不同，不能直接视为等价。

## 发布与许可证

本项目采用 **GPL-3.0-only**，完整条款见 [LICENSE](LICENSE)。本软件不提供担保。第三方依赖保留其各自许可证。

面向使用者发布带运行时的 Windows x64 安装包；同时运行 `python tools/build_release.py` 生成对应源码 ZIP 和 SHA-256 校验文件。源码打包仅使用明确列出的源码、测试、文档、示例和启动脚本，排除个人配置、运行状态、虚拟环境和本地 QA 输出。构建和发布步骤见 [GitHub 发布说明](docs/github-release.md)，第三方声明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。安装包不要求用户安装 Python，只有源码运行需要开发环境。

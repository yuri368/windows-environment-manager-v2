# Windows 环境变量方案管理器 V3

**把环境变量按应用和方案整理好，先看清改动，再确认执行。**

Windows 10 / 11 x64 · 中文界面 · 用户与系统变量 · TOML 配置 · GPL-3.0-only

[**下载 V3.0.0 正式版**](https://github.com/yuri368/windows-environment-manager-v2/releases/tag/v3.0.0) · [快速上手](#快速上手) · [示例配置](env.example.toml) · [反馈问题](https://github.com/yuri368/windows-environment-manager-v2/issues)

重装系统后需要重新配置开发环境，电脑里同时保留多个 JDK、Python 或 Maven 版本，或者 `Path` 越来越长、难以维护时，可以把每个应用需要的变量保存为方案，再按需选择和应用。

例如，Java 的 JDK 17 和 JDK 21 各自定义 `JAVA_HOME`、`Path`；Python、Node.js 等应用拥有自己的方案。多个应用可以分别选择方案，再一起预览。本工具管理环境变量，不负责安装软件或自动发现软件路径。

## 下载与安装

安装包包含 Python 和 Node.js 运行时。**普通使用无需安装开发环境，也无需运行命令行。** 启动后，程序会在系统默认浏览器中打开本机界面。

| 版本 | 适用情况 | 下载 |
| --- | --- | --- |
| 用户安装版 | 通常选择这一项，安装到当前用户目录 | [setup-user-x64.exe](https://github.com/yuri368/windows-environment-manager-v2/releases/download/v3.0.0/EnvManagerV3-3.0.0-setup-user-x64.exe) |
| 机器安装版 | 安装到 Program Files，安装时需要管理员确认 | [setup-machine-x64.exe](https://github.com/yuri368/windows-environment-manager-v2/releases/download/v3.0.0/EnvManagerV3-3.0.0-setup-machine-x64.exe) |
| 便携版 | 解压后运行，保留完整目录 | [windows-x64.zip](https://github.com/yuri368/windows-environment-manager-v2/releases/download/v3.0.0/EnvManagerV3-3.0.0-windows-x64.zip) |
| 校验文件 | 核对下载文件的 SHA256 | [SHA256SUMS.txt](https://github.com/yuri368/windows-environment-manager-v2/releases/download/v3.0.0/SHA256SUMS.txt) |

直接双击安装器即可；机器安装器会自行申请提权，**不要预先右键选择“以管理员身份运行”**，否则升级检查可能因无法确认原始普通用户身份而拒绝继续。

安装后从开始菜单打开 **Environment Manager V3**。便携版运行解压目录中的 `EnvManagerV3.exe`，不要只复制单个 EXE。正式版未作代码签名。

## 快速上手

### 1. 导入配置

首次进入空工作区时，通过页面的 **“导入配置”** 加载 TOML。可以先参考 [示例配置](env.example.toml)，将其中的路径改成自己电脑上的实际位置。

导入后，程序使用受管工作副本；后续保存配置不会覆盖最初导入的源文件。安装 V3 不会自动导入或迁移 V2 的配置与历史。

### 2. 准备应用和方案

在工作台选择应用、方案，以及变量的值来源。配置编辑器按 **应用 → 方案 → 变量** 组织内容，可以先创建名称，再补充下级内容与属性。

配置定义、当前选定值和 Windows 当前环境分别保存。**选择方案、修改配置、保存配置或查看预览，都不会直接应用环境变量。**

### 3. 预览、审阅，再执行

点击 **“预览并应用”**，核对每个目标的作用域、当前值和预计结果。点击 **“审阅此计划”** 后，再点击 **“执行已审阅计划”** 才会执行该版本的修改。

如果相关环境值发生变化，需要重新审阅。系统变量写入按需请求 UAC；平时浏览界面和编辑配置不要求整个程序一直以管理员身份运行。执行后，已打开的终端或其他程序可能需要重新启动才能读取新环境。

## 界面与主要功能

| 页面或功能 | 可以做什么 |
| --- | --- |
| 工作台 | 按应用选择方案和值来源，查看当前匹配与前后对照，批量准备并预览修改 |
| 配置编辑器 | 新增应用、方案、变量和候选值，校验并保存或导出 TOML；命名草稿可以保留后继续填写 |
| Windows 环境 | 分别查看用户和系统环境，以元素列表或完整字符串编辑原始值，提交后先生成计划 |
| 元素编辑器 | 展示重复项、空元素和删除标记；支持整行纵向拖动、按钮及键盘排序，移动反馈与两栏对照同步更新 |
| 历史与恢复 | 查看执行记录，预览符合条件的历史恢复，处理执行中断后的恢复待办 |
| 单实例与退出 | 再次启动时重开已有实例；顶部“退出”在保留草稿后关闭后台服务 |

列表编辑保留元素身份，区分不存在的变量、存在但为空的变量和空列表元素。删除标记中的幽灵行用于显示和调整删除项的位置，不会作为有效元素写入结果。

撤销配置、恢复历史和执行失败后的补偿有各自的检查条件。遇到外部修改、来源不明确或无法证明状态连续的情况，程序会保留当前状态并要求重新核对，不保证任意旧操作都能自动恢复。

## 配置示例

下面为 Java 准备两个方案。路径仅为示例，使用前请换成真实目录。

```toml
[Java]
default_option = "JDK17"

[Java.JDK17]
JAVA_HOME = { default_value = 'C:\Tools\Java\jdk-17', scope = "User", mode = "replace", editable = true }
Path = { default_value = '%JAVA_HOME%\bin', scope = "User", mode = "+append", editable = false }

[Java.JDK21]
JAVA_HOME = { default_value = 'C:\Tools\Java\jdk-21', scope = "User", mode = "replace", editable = true }
Path = { default_value = '%JAVA_HOME%\bin', scope = "User", mode = "+append", editable = false }
```

`replace` 替换目标值，`+append` 将列表项放到前面，`append` 放到末尾，`-append` 用于合并后确认顺序。`scope` 明确指定 `User` 或 `System`。

需要作为完整字符串处理的变量可设置 `special = true`，只允许 `replace`；同一作用域、同名目标在各方案中的特殊属性必须一致。更多字段与约束见 [完整示例](env.example.toml)、[配置规范](AGENTS.md) 和 [V3 运行说明](ui-v3/docs/product-v3/RUNNING.md)。

## 数据、备份与升级

V3 默认数据目录：

```text
%LOCALAPPDATA%\EnvManagerV3
```

| 数据 | 保存的内容 |
| --- | --- |
| 受管 TOML | 应用、方案、变量及默认值等配置定义 |
| 工作输入与草稿 | 当前选定方案、值来源、自定义输入，以及未完成的配置和名称草稿 |
| 执行与恢复记录 | 冻结计划、执行日志、历史及恢复待办；系统执行还保留受保护回执 |

备份前先点击顶部 **“退出”**，再复制整个数据目录；如果存在 SQLite WAL 文件，也需要一起保留。单独复制 TOML 可以复用方案定义，但不能带走全部工作状态和历史。系统执行回执另存于受保护的机器目录，不包含在用户数据目录中；复制数据不会让旧恢复授权在另一台机器自动生效。

安装、升级和卸载不自动应用环境方案；卸载保留用户数据。V2 与 V3 使用独立的实现、安装和数据目录，V2 的操作方法与下载见 [V2 文档](docs/README_V2.md)。

## 隔离试用

想先体验完整流程而不修改 Windows 环境，可以在便携版解压目录打开 PowerShell：

```powershell
.\EnvManagerV3.exe --backend file --data-dir .\v3-isolated
```

此模式将环境值保存到指定目录中的 `environment.json`，配置与历史也保留在该目录。它使用文件环境，不能用来判断本机 Windows 环境是否已经配置正确。

## 常见问题

**为什么在浏览器里打开？**

V3 使用本地服务和浏览器界面，服务只监听本机地址。配置、历史和环境读写由本地程序处理；使用本工具不需要注册账号。

**关掉标签页就退出了吗？**

没有。关闭标签页会保留后台服务。再次启动可以重开页面；要完全关闭程序，请点击顶部 **“退出”**。

**为什么历史还在，却不能直接恢复？**

历史保存不等于恢复条件一直有效。重启、监听连续性中断或外部修改后，即使当前文本看起来相同，旧历史也可能无法直接恢复。可以核对历史目标值，再从当前快照创建新的编辑计划。

**能自动恢复重装系统前的软件环境吗？**

可以复用已备份的方案定义，但仍需检查软件文件、盘符和路径。它不安装软件、不同步多个 Windows 系统，也不保证旧历史可以在新系统继续执行。

**目前验证到什么范围？**

V3.0.0 完成了跨平台后端与 UI 回归、固定运行时构建，以及 Windows 11 差分系统中的安装升级、真实 User/System 提交与还原和人工交互验收。干净 Windows 虚拟机安装生命周期尚未验证；多用户、另一管理员凭据和真实断电实验暂缓。详细范围见 [发布审查](ui-v3/docs/product-v3/RELEASE_3_0_0.md) 与 [验收记录](ui-v3/docs/product-v3/RELEASE_READINESS_2026-10-19.md)。

## 从源码运行

开发环境使用 Python 3.13 和 Node.js。安装依赖后，先以文件环境运行：

```powershell
python -m pip install -r requirements-v3.txt
python -m envmanager_v3 --backend file --data-dir .\local-state\v3-isolated
```

也可使用根目录的 `隔离运行V3.cmd`。`启动V3.cmd` 使用真实 Windows 后端；环境修改仍需在页面中完成计划审阅与执行确认。

- [运行与数据说明](ui-v3/docs/product-v3/RUNNING.md)
- [构建、包校验与版本化证据](ui-v3/docs/product-v3/BUILD.md)
- [数据库说明](ui-v3/docs/product-v3/DATABASE.md)
- [升级与备份实现](ui-v3/docs/product-v3/UPGRADE.md)
- [V2 文档与原生 Qt 界面说明](docs/README_V2.md)

本项目采用 [GPL-3.0-only](LICENSE) 开源许可证。第三方许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)，发布包内另附所含运行时与依赖的许可证。

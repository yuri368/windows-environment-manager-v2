# V3 产品运行与数据

本目录说明独立 V3 产品。`temp.html` 保留已接受的浏览器演示；正式入口是 `index.html`，由 `envmanager_v3` 本地服务加载。V2 的 `envmanager/` 不作为 V3 运行依赖。

## 从候选包运行

当前候选包和 SHA256 见 [BUILD.md](BUILD.md)。使用用户安装包可选择安装位置；便携 ZIP 解压后保留完整目录，运行其中的 `EnvManagerV3.exe`。包内已包含固定 Python/Node 运行时，正常启动会打开系统默认浏览器，不需要另开开发服务器。默认使用 Windows 后端，配置与历史位于下述用户数据目录。

要使用同一候选包进行隔离试用，在解压目录打开 PowerShell：

```powershell
.\EnvManagerV3.exe --backend file --data-dir .\v3-isolated
```

此模式将环境值保存到该数据目录的 `environment.json`。首次没有配置时，通过页面的“导入配置”加载合法 TOML；安装器不自动导入或迁移 V2 数据。

## 从源码运行

在项目根目录，使用 Python 3.13 和 Node.js，并安装 `requirements-v3.txt`：

```powershell
python -m pip install -r requirements-v3.txt
python -m envmanager_v3 --backend file --data-dir .\local-state\v3-isolated
```

这个命令启动完整界面和持久文件环境，不会修改 Windows 环境。也可双击根目录的 `隔离运行V3.cmd`。配置、输入和历史会保留在指定数据目录，重新启动时继续使用。

正式 Windows 模式启动命令为 `python -m envmanager_v3 --backend windows`；根目录 `启动V3.cmd` 使用该模式。它会读取当前 Windows 环境，只有对冻结计划完成审阅并再次确认执行，才尝试修改目标环境。开发验收始终显式指定 `--backend file`。

本地网页只绑定 IPv4 loopback。启动器生成一次性票据，浏览器交换会话后移除 URL 中的票据。服务重启会使旧会话失效，需要打开新启动 URL；不要公开或复制带票据的地址。

## 三类独立数据

- 受管 TOML：导入文件的工作副本。保存配置不会执行环境计划，也不会覆盖最初导入的文件。
- 工作输入：选定方案、变量来源、自定义原文、未提交的命名草稿。自动保存到 SQLite；它们与配置默认值分别保存。
- 环境与执行记录：File 模式的 `environment.json` 或 Windows 原始值，以及 SQLite 中冻结计划、执行日志、历史和恢复记录。

默认数据目录为 `%LOCALAPPDATA%\EnvManagerV3`。`--data-dir` 可指定独立本地目录。安装/卸载程序文件与数据目录分开；备份数据请先退出实例，再复制整个目录。不要只复制 SQLite 主文件而遗漏仍存在的 WAL。

## 常用流程

导入 TOML 后，在工作台选择方案和值来源。点击“预览并应用”生成服务端计划；“审阅此计划”不写环境，再次点击“执行已审阅计划”才执行该版本。若相关目标变化，预览会更新，必须重新审阅。

配置编辑器可先命名应用、方案和变量，再补充下级内容与属性。新增变量输入与变量条目并列；未提交草稿可以在重启后继续。只有通过完整格式校验的配置才能保存/导出。

Windows 环境页直接编辑原始值。普通变量保留重复项、空元素与顺序；特殊变量作为完整字符串。元素 ID 来自服务器会话，提交后仍先生成计划。原文、存在但为空、以及删除变量是不同意图。

撤销配置按实际历史贡献计算，保护其他配置仍依赖的值。遇到重复项或连续性不可证明时会阻止自动删除；重启不会重新赋予旧贡献自动删除权。历史恢复是单独的整值恢复计划，不等同于撤销配置。

发生中断或部分写入时，保留原操作 ID 并进入恢复待办。重新启动只对账，不自动重发写入。恢复原值或保留当前值都需要新的预览、审阅和确认。

## 当前验收边界

构建方法与每个包的证据见 [BUILD.md](BUILD.md)，数据库版本与迁移备份见 [DATABASE.md](DATABASE.md)，当前通过项与缺口见 [ACCEPTANCE.md](ACCEPTANCE.md)。产品完整验收以架构方案第 17 节矩阵及实际测试证据为准；File 模式、mock helper、Sandboxie 模拟管理员均不能代替真实 Windows UAC、干净虚拟机安装和原生 DPI 验收。

# GitHub 发布

面向普通使用者发布带 Python 与 Qt 运行时的 Windows x64 安装包，同时提供 GPL-3.0-only 对应源码 ZIP。安装包双击后进入中文向导，创建开始菜单和可选桌面快捷方式，完成页默认勾选“立即启动”。用户不需要安装 Python 或运行命令。根目录 `LICENSE` 和 `pyproject.toml` 中的许可证声明应一同保留。

## 本地验证

在项目目录运行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m envmanager --check --config .\env.example.toml
.\演示.cmd
.\.venv\Scripts\python.exe tools\build_release.py
```

构建安装包需要 Windows x64、项目依赖、`requirements-build.txt` 中的 PyInstaller 和 Inno Setup 6.7 或更新版本：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe tools\build_windows.py --iscc 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
```

`--iscc` 可以指定实际编译器路径。构建脚本使用隔离的 DLL 搜索路径生成无控制台窗口的独立 EXE，并在不含 Python 的 PATH 和临时目录中校验配置、启动演示 GUI；通过后才将完整运行时目录编译为安装包。演示验证不写本机环境变量。第三方许可和来源见 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)。

当前安装包支持 Windows 10 1809 或更新版本和 Windows 11 x64，与 [Qt 6.11 支持平台](https://doc.qt.io/qt-6.11/supported-platforms.html) 一致。

发布文件位于 `dist`：`EnvironmentManagerV2-2.0.0-windows-x64-setup.exe`、对应源码 ZIP 和两份 SHA-256 文件。源码 ZIP 使用明确的文件清单，不包含 `env.toml`、SQLite 历史库、配置备份、`.venv` 或本地 QA 截图。README 中的公开截图由示例配置和模拟存储生成。

## 上传仓库

在 GitHub 创建空仓库后，在项目目录运行：

```powershell
git init
git add .
git status --short
git diff --cached --stat
git commit -m "Release environment manager v2"
git branch -M main
git remote add origin <你的仓库地址>
git push -u origin main
```

确认暂存清单只有发布所需文件。`.gitignore` 会排除个人配置和本地运行数据，但不会撤销已经跟踪的文件；如果先前提交过个人配置，应先单独检查仓库历史。

随后创建 `v2.0.0` 标签和 GitHub Release，附上安装包、对应源码 ZIP 和 SHA-256 文件。在 Release 描述开头指明普通用户下载 `windows-x64-setup.exe`；源码 ZIP 用于审阅、修改与重新构建。

## 下载后的首次运行

双击安装包，完成中文向导即可打开 GUI。后续使用桌面或开始菜单快捷方式启动。正式界面通过标准 UAC 请求管理员权限；不需要打开终端。默认安装目录为 `%LOCALAPPDATA%\Programs\EnvironmentManagerV2`。

安装版个人配置保存在 `%LOCALAPPDATA%\EnvironmentManagerV2\config\env.toml`，缺失时从内置示例创建；运行历史仍保存在独立 SQLite 目录。安装、升级和卸载不覆盖或删除个人配置及历史。示例中的安装路径需要按使用者实际环境修改，管理器不会自动扫描或安装开发工具。只有完成预览并确认后才应用环境变量。

双击 `演示.cmd` 可以使用示例配置体验界面和模拟执行，不申请管理员权限、不创建个人配置、不写入环境变量。界面上的“环境变量”按钮是独立入口，点击后打开本机真实 Windows 设置。

源码使用者仍可按 README 中的 Python 和启动脚本步骤运行；安装包使用者无需这些步骤。

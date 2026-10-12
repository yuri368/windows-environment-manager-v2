# 持续集成测试

`.github/workflows/tests.yml` 在 push、pull request 和手动触发时，以 `ubuntu-latest`、`windows-latest` 两个平台运行 Python 3.13。安装来源统一为 `requirements.txt`，目前固定 `PySide6==6.11.2` 与 `tomlkit==0.13.3`。工作流先创建项目内 `.venv`，使 Windows 启动器回归使用的 `.venv/Scripts/python.exe`、`pythonw.exe` 与主测试环境一致。

## 执行范围

两个平台都执行完整发现命令，不维护筛选后的测试白名单：

```text
python -m unittest discover -s tests -v
```

范围包含核心配置/元素算法、匹配/共享/撤销/切换、事务/历史/回滚、vNext T01～T31 和 Qt 界面回归；新增 `tests/test_*.py` 自动进入发现。实际总数、失败与跳过以每次运行结果为准，不把旧版本测试数量写成固定验收门槛。GUI 使用 `QT_QPA_PLATFORM=offscreen`，UTF-8 模式保证中文日志与跨平台输出一致。Ubuntu 安装 OpenGL/EGL、键盘、字体运行库与 Noto CJK 字体；这里使用预编译的 Qt wheel，无需编译 Qt 或启动 X11/Xvfb。

| 测试类别 | Ubuntu | Windows | 边界 |
|---|---|---|---|
| 纯算法、MemoryStore 服务/事务、Qt offscreen | 运行 | 运行 | 临时状态目录，不修改主机环境变量 |
| `test_windows_store.py` | 按原始 skip 原因跳过 | 运行 | 仅 `HKCU\Software\EnvironmentManagerV2Tests\<UUID>\User` / `System`；逻辑 System 也映射 HKCU 测试键 |
| `test_privileges.py` 原生 ctypes 部分 | 按原始 skip 原因跳过 | 使用模拟 Windows API 运行 | 不发起 UAC，不启动提权程序 |
| `test_launcher.py` | 按原始 skip 原因跳过 | 运行 Windows PowerShell 5.1 回归 | GUI Start-Process 被测试函数替代；启动参数探针为临时惰性模块 |
| `test_launch_visibility.py` | 按原始 skip 原因跳过 | 独立 `pythonw.exe` / `windows` Qt 进程运行 | 只改变本测试创建的 HWND，检查原生可见性，无 UAC/环境变量写入 |
| `test_manual_windows_acceptance.py` | 运行 | 运行 | 只测试参数与 MemoryStore/原生 API 模拟边界，不运行人工验收控制器 |

真实注册表测试不得扩展到 `HKCU\Environment`、真实 `Path` 或 HKLM；新的测试必须继续使用 UUID 测试子树。真实 UAC 交互、真实 User/System Environment 写入、安装目录启动、安装/卸载与物理屏幕高 DPI 验收由人工 Windows 验收负责，不能以 CI 通过代替。工作流不调用 `tools/manual_windows_acceptance.py` 的命令行入口，不构建安装包，不发布 Release。

## 结果与复现

每个平台生成独立的 `test-results-<runner>-python-3.13` Actions artifact，保存安装日志、Python/Qt/平台与依赖版本、完整 verbose unittest 日志、退出码和 `summary.md`，保留 14 天。Job summary 显示命令、运行平台、完整 suite 统计、每条跳过测试的原始原因及人工验收边界。失败后仍执行摘要与日志上传；若依赖安装失败或测试尚未开始，摘要明确显示未完成，不报告测试通过。测试进程的非零退出码直接使 job 失败，矩阵两端独立完成。

本地 Windows 对应命令：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:QT_QPA_PLATFORM = 'offscreen'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

本地 Linux 对应命令（先安装工作流列出的系统运行库）：

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
QT_QPA_PLATFORM=offscreen PYTHONUTF8=1 PYTHONIOENCODING=utf-8 \
  .venv/bin/python -m unittest discover -s tests -v
```

测试使用 GitHub 托管 runner 与 Python 3.13 系列当前补丁版本；运行时日志保存确切版本。更新依赖时同时检查两平台结果，不能通过删测试或扩大 skip 范围消除失败。

## 官方依赖参考

本工作流采用官方支持的 [checkout v6](https://github.com/actions/checkout/tree/v6)、[setup-python v6](https://github.com/actions/setup-python/tree/v6) 与 [upload-artifact v4](https://github.com/actions/upload-artifact/tree/v4)。checkout/setup-python 使用 Node 24，需要 runner 至少 v2.327.1；GitHub 托管 runner 负责运行环境更新。Python 缓存以固定 `requirements.txt` 为键；checkout 不持久化凭据，工作流仅申请 `contents: read`。

Qt 系统库对应 [Qt 6.11 Linux 要求](https://doc.qt.io/qt-6.11/linux-requirements.html)，Ubuntu 包名称可在官方索引核对，例如 [libopengl0](https://packages.ubuntu.com/noble/libopengl0)、[libxkbcommon0](https://packages.ubuntu.com/noble/libxkbcommon0)、[libfontconfig1](https://packages.ubuntu.com/noble/libfontconfig1)。摘要采用 GitHub 官方的 [GITHUB_STEP_SUMMARY](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-commands#adding-a-job-summary) 文件协议。

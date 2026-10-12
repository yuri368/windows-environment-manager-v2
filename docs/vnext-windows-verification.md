# 2.1.0 Windows 安装与真实注册表验收

## 验收环境与交付物

2026-10-12，在 Windows 11 IoT Enterprise LTSC x64（10.0.26100）验证 vNext 2.1.0。源码使用 Python 3.13.7、PySide6 6.11.2、PyInstaller 6.22.3 和 Inno Setup 6.7.3 构建。

交付物为 `EnvironmentManagerV2-2.1.0-windows-x64-setup.exe`、`windows-environment-manager-v2-2.1.0-source.zip` 及各自的 SHA-256 文件。安装包大小为 33,658,944 字节，SHA-256 为 `8c092d327d4ec8f2431996cab1e294a2e0ec68562da2b4976698d2779c989a32`。

本轮在本机生成交付物，没有创建 GitHub Release、发布标签或合并 PR。

## 安装与个人数据

- 升级前备份原 2.0.1 安装目录及全部个人配置、历史文件，并按文件 SHA-256 核对备份。
- 2.1.0 安装器返回 0；当前用户卸载登记的版本为 2.1.0，安装目录为 `%LOCALAPPDATA%\Programs\EnvironmentManagerV2`。
- 安装后的 EXE 与构建目录中的 EXE SHA-256 相同。
- 从安装目录启动 `--check` 和 `--demo --screenshot`，PATH 仅含 Windows 目录，没有 Python，均成功退出。演示窗口的原生可见性检查由程序执行。
- 默认开始菜单快捷方式存在，指向安装目录中的 EXE。
- 安装版对已有个人配置执行只读 `--check`，退出码为 0。
- 5 个已有个人文件在安装、演示启动和正式 UAC 验收前后的哈希完全一致。正式验收显式使用 QA 配置和独立状态目录。

本地安装日志与数据保全证据位于 `artifacts/vnext-delivery-2.1.0/`，个人备份位于其中 `backup-2.0.1/`。该目录被 Git 忽略，不进入对应源码 ZIP。

## 真实 HKCU/HKLM 验收

本轮人工同意 UAC 后，在实际 `HKCU\Environment` 与 `HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment` 中使用同一 GUID 的 8 个临时变量名。两作用域全部 16 个目标在写入前均确认不存在。没有修改 Path 或其他既有环境变量。

通过 `WindowsStore`、`EnvironmentService` 和独立 SQLite 状态库实际执行 24 个事务：20 个成功事务，以及 4 个预期注入失败后完整回滚的事务。

| 实际操作 | User / System 结果 |
| --- | --- |
| 同名变量分别应用和默认整理 | 作用域隔离，原始存在状态和值正确 |
| 普通 replace 部分撤销 | `A;C` 撤销配置 `A;B` 后为 `C` |
| 关闭三项写入整理后撤销 | `" A ;B;; C "` 撤销 `B` 后严格为 `" A ;; C "` |
| 创建唯一空值，再主动撤销 | 先存在且值为 `""`，撤销后键不存在 |
| 特殊值原样应用与整键撤销 | 分号、空格、制表符原样写入，撤销删除整个键 |
| 历史恢复 | 原始顺序、空段、重复项、空白和 REG_EXPAND_SZ 类型无损恢复 |
| 第二个真实写入完成后注入失败 | 恢复先前原始文本、类型、唯一空值与原不存在状态 |
| 整理已有无效格式并保留类型 | `A;A; B ;;\t;` 整理为 `A;B`，保留 REG_EXPAND_SZ |

事务通知接口实际调用，记录中的 `notification_error` 均为空。测试结束后确认临时环境变量全部清理，残留项为 0。

`tools/manual_windows_acceptance.py` 是需明确批准的独立人工验收工具，不由 unittest discovery 执行真实写入。它要求管理员令牌、完整 GUID、绝对安装 EXE 路径以及新建或空的验收目录。每次修改和清理都重新核对已确认的原始状态与类型；外部或未知状态保留并记录，不盲目删除。

使用该工具时，先从人工同意 UAC 的管理员 Python 进程执行：

```text
python tools/manual_windows_acceptance.py --work-dir <绝对验收目录> --exe <安装EXE绝对路径> --test-id <完整GUID>
```

工具产生 `ready_for_gui.json` 后，从原来的非管理员进程启动其中的 EXE，并传入文件指定的 `--config` 与 `--state-dir`。这使安装版自身请求 UAC。工具验证窗口后关闭该验收窗口，并记录最终结果。GUI 等待默认上限 300 秒，可显式设置为 1–900 秒。

## 正式安装版 UAC 与窗口

从非管理员控制器、安装目录和不含 Python 的 PATH 启动正式 EXE。安装版自身完成管理员重新启动；确认 EXE 路径、进程创建时间与真实 TokenElevation 后，原生 `IsWindowVisible=true`，窗口未最小化，主窗口标题正确。

仅向这个已核实的验收窗口发送 WM_CLOSE，正式程序和控制器的退出码均为 0。首次原生正式窗口截图受到高 DPI 坐标虚拟化影响，只保存了部分内容；它证明窗口绘制，不能用来确认完整布局。

验收工具已修正为在 PMv2 线程上下文中读取物理像素尺寸并完成拍摄，随后恢复先前 DPI 上下文；相关纯边界测试及真实线程上下文进入/恢复检查通过。补拍所需的额外 UAC 被取消（Windows 错误 1223），补拍进程没有启动，因此完整高 DPI 正式窗口截图仍待人工补拍。安装版演示完整截图已检查，布局可读。

本地真实注册表与 UAC 结果见 `artifacts/vnext-delivery-2.1.0/real-windows/result.json`；安装版演示截图为 `installed-demo.png`。这些本地证据不包含在对应源码 ZIP 中。2.1.1 已补充正式安装版 200% 缩放完整主窗口截图与工作区修复验证；下拉和共享弹窗仍待补验，最新结果见 [Issue #5 验证记录](issue-5-verification.md)。

## 验证边界

核心规则 T01–T31、五种撤销范围、共享分组和最终预览的自动化证据见 [vNext 验收映射](vnext-verification.md)。以上实际注册表验收补充生产 Windows 适配、权限、事务和安装启动的证据。

尚未执行全新 Windows 10/11 机器安装、交互安装向导完成页的自动启动、卸载后的个人数据保留，以及 Explorer/第三方程序或新登录会话接收新环境的人工验证。当前真实通知接口成功不代表每个外部程序都已更新自身环境。

Sandboxie 默认箱已有数据，指定目录副本与当前活动服务的配置关联尚未确认，本轮没有修改或清理该箱。沙盒中的虚拟 HKLM 也不能替代上述实际 HKLM 验收。

from __future__ import annotations

import argparse
import ctypes
import os
import sys
import tempfile
from pathlib import Path

from .config import load_config
from .models import RegistryState
from .persistence import StateRepository, default_state_dir
from .privileges import ensure_administrator, is_administrator, report_startup_error
from .service import EnvironmentService
from .store import MemoryStore, WindowsStore

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def window_is_visible(window):
    if not window.isVisible():
        return False
    if sys.platform == "win32":
        from ctypes import wintypes
        check = ctypes.WinDLL("user32", use_last_error=True).IsWindowVisible
        check.argtypes = [wintypes.HWND]
        check.restype = wintypes.BOOL
        return bool(check(int(window.winId())))
    return True


def resolve_config_path(requested=None, *, demo=False, readonly=False):
    if requested is not None:
        return Path(requested)
    if getattr(sys, "frozen", False):
        local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        personal = local / "EnvironmentManagerV2" / "config" / "env.toml"
    else:
        personal = PROJECT_ROOT / "env.toml"
    example = PROJECT_ROOT / "env.example.toml"
    if demo:
        return example
    if personal.exists():
        return personal
    if readonly:
        return example
    # Validate before creating the initial personal file; never replace an
    # existing file, including one created concurrently by another launch.
    load_config(example)
    personal.parent.mkdir(parents=True, exist_ok=True)
    try:
        with personal.open("xb") as output:
            output.write(example.read_bytes())
    except FileExistsError:
        pass
    return personal


def build_service(config_path, demo=False, state_dir=None):
    config_path = Path(config_path).resolve()
    config = load_config(config_path)
    temporary = None
    if demo:
        temporary = tempfile.TemporaryDirectory(prefix="environment-manager-v2-demo-")
        directory = Path(temporary.name)
        store = MemoryStore([
            RegistryState("Path", "User", True, r"C:\Windows\System32;C:\Tools\Common\bin", 2),
            RegistryState("Path", "System", True, r"C:\Windows;C:\Windows\System32", 2),
            RegistryState("MY_LIST", "User", True, "A;B;B;C", 1),
        ])
    else:
        directory = Path(state_dir).resolve() if state_dir else default_state_dir(config_path)
        store = WindowsStore()
    try:
        service = EnvironmentService(config, store, StateRepository(directory))
    except Exception:
        if temporary:
            temporary.cleanup()
        raise
    return service, temporary


def main(argv=None):
    launch_argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description="Windows 环境变量方案管理器 V2")
    parser.add_argument("--config", type=Path, help="UTF-8 TOML 配置路径（默认 env.toml；演示使用 env.example.toml）")
    parser.add_argument("--state-dir", type=Path, help="独立状态目录（演示模式始终使用临时目录）")
    parser.add_argument("--demo", action="store_true", help="模拟注册表与临时状态")
    parser.add_argument("--check", action="store_true", help="只校验配置，不启动界面或读取注册表")
    parser.add_argument("--check-admin", action="store_true", help="仅检测当前进程管理员权限（0=有权限，1=无权限），不提权")
    parser.add_argument("--_elevated", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--screenshot", type=Path, help="演示截图输出路径（保存后退出）")
    args = parser.parse_args(launch_argv)
    if args.screenshot and not args.demo:
        parser.error("--screenshot 必须与 --demo 一起使用")
    if args.screenshot and args.check:
        parser.error("--screenshot 不能与 --check 一起使用")
    if args.check_admin:
        administrator = is_administrator()
        print("当前进程具有管理员权限" if administrator else "当前进程没有管理员权限")
        return 0 if administrator else 1
    if args.check:
        try:
            config = load_config(resolve_config_path(args.config, demo=args.demo, readonly=True))
            options = sum(len(v) for v in config.applications.values())
            records = sum(len(specs) for v in config.applications.values() for specs in v.values())
            print(f"配置有效：{len(config.apps())} 个应用，{options} 个方案，{records} 条变量记录")
            return 0
        except Exception as exc:
            print(f"配置校验失败：{exc}", file=sys.stderr)
            return 1
    if not args.demo:
        try:
            result = ensure_administrator(launch_argv, elevated=args._elevated)
        except Exception as exc:
            report_startup_error(str(exc))
            return 1
        if result is not None:
            return result
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication, QMessageBox
        from .ui import MainWindow
    except ImportError as exc:
        recovery = ("界面组件加载失败，请重新安装完整的 Windows 安装包。" if getattr(sys, "frozen", False)
                    else "缺少界面依赖，请双击启动.cmd 或运行 .venv\\Scripts\\python.exe -m pip install -r requirements.txt")
        report_startup_error(f"{recovery}\n{exc}")
        return 1
    app = QApplication.instance() or QApplication([sys.argv[0]])
    from .icons import window_icon
    app.setWindowIcon(window_icon())
    service, temporary = None, None
    exit_code = 0
    try:
        args.config = resolve_config_path(args.config, demo=args.demo)
        service, temporary = build_service(args.config, args.demo, args.state_dir)
        window = MainWindow(service, args.config.resolve(), args.demo)
        window.show()
        if args.screenshot:
            def save_screenshot():
                nonlocal exit_code
                try:
                    if not window_is_visible(window):
                        raise RuntimeError("主窗口未显示，截图验证失败")
                    output = args.screenshot.resolve()
                    output.parent.mkdir(parents=True, exist_ok=True)
                    if not window.grab().save(str(output)):
                        raise OSError("截图保存失败")
                    print(f"演示截图已保存：{output}")
                except Exception as exc:
                    print(f"截图失败：{exc}", file=sys.stderr)
                    exit_code = 1
                app.quit()
            QTimer.singleShot(600, save_screenshot)
        exit_code = app.exec() or exit_code
    except Exception as exc:
        message = f"无法启动：{exc}"
        if sys.stderr:
            print(message, file=sys.stderr)
        QMessageBox.critical(None, "环境变量方案管理器 V2", message)
        exit_code = 1
    finally:
        if temporary:
            temporary.cleanup()
    return exit_code

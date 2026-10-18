"""Launch the accepted V3 interface with its local, authenticated product runtime."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import webbrowser

from .environment import FileEnvironment, WindowsEnvironment
from .http_server import create_server
from .service import Service


def assets_directory():
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return root / "ui-v3" if (root / "ui-v3" / "demo-plan.js").is_file() else root


class InstanceLock:
    """One coordinator per data directory; locking never deletes prior journals."""
    def __init__(self, path):
        self.path, self.file = Path(path), None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.file = self.path.open("a+b")
            # Reading byte zero before acquiring the Windows range lock can
            # itself fail when another instance owns it. Inspect file size
            # without touching the locked byte, and close on every failure.
            if self.path.stat().st_size == 0:
                self.file.write(b"0")
                self.file.flush()
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if self.file:
                self.file.close()
            raise RuntimeError("此数据目录已有 V3 实例运行，请返回原页面。") from error
        return self

    def __exit__(self, *_):
        if self.file:
            self.file.close()


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "--system-helper":
        from .system_helper import main as helper_main
        return helper_main(argv[1:])
    parser = argparse.ArgumentParser(description="环境变量方案管理器 V3", allow_abbrev=False)
    parser.add_argument("--backend", choices=("windows", "file"), default="windows")
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("LOCALAPPDATA", Path.home())) / "EnvManagerV3")
    parser.add_argument("--environment-file", type=Path)
    parser.add_argument("--assets", type=Path, default=assets_directory())
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--check", action="store_true", help="Validate runtime/configuration without serving or writing the environment")
    args = parser.parse_args(argv)
    data_dir = args.data_dir.resolve()
    if str(data_dir).startswith("\\\\"):
        parser.error("数据目录必须是本地目录。")
    if args.backend == "windows" and os.name != "nt":
        parser.error("Windows 后端只支持 Windows。隔离开发使用 --backend file。")
    try:
        with InstanceLock(data_dir / "instance.lock"):
            environment = WindowsEnvironment() if args.backend == "windows" else FileEnvironment(args.environment_file or data_dir / "environment.json")
            service = Service(data_dir, environment=environment, assets=args.assets,
                              monitor_environment=args.backend == "windows" and not args.check)
            try:
                if args.check:
                    print(json.dumps({"status": "ok", "engineHash": service.planner.engine_hash,
                                      "configuration": service.workspace(service.work.data["id"])}, ensure_ascii=True))
                    return 0
                if not (args.assets / "index.html").is_file():
                    raise RuntimeError("正式界面 index.html 缺失；不会自动回退到演示页面。")
                server = create_server(service, args.assets, args.port)
                try:
                    print(json.dumps({"status": "listening", "url": server.launch_url, "backend": args.backend}, ensure_ascii=True), flush=True)
                    if not args.no_browser:
                        webbrowser.open(server.launch_url)
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass
                finally:
                    server.server_close()
            finally:
                service.close()
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

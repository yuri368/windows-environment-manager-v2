"""Open Windows settings without interpreting shell commands or changing data."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path
import subprocess
import sys


def system_directory() -> Path:
    if sys.platform != "win32":
        raise OSError("此入口仅支持 Windows。")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_directory = kernel32.GetSystemDirectoryW
    get_directory.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    get_directory.restype = wintypes.UINT
    buffer = ctypes.create_unicode_buffer(32768)
    length = get_directory(buffer, len(buffer))
    if not length or length >= len(buffer):
        raise OSError("无法获取 Windows 系统目录。")
    return Path(buffer.value)


def open_environment_settings() -> None:
    directory = system_directory()
    executable = directory / "rundll32.exe"
    control_panel = directory / "sysdm.cpl"
    if not executable.is_file() or not control_panel.is_file():
        raise OSError("找不到 Windows 环境变量设置组件。")
    subprocess.Popen(
        [str(executable), "sysdm.cpl,EditEnvironmentVariables"],
        cwd=str(directory), shell=False,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

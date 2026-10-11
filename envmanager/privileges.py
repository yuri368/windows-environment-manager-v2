"""Windows startup privileges; never read or modify environment variables."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import subprocess
import sys


ELEVATED_ARGUMENT = "--_elevated"
ERROR_CANCELLED = 1223


class ElevationError(RuntimeError):
    pass


class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG),
        ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


def is_administrator() -> bool:
    """Check the effective Windows token, not the user's account name."""
    if os.name != "nt":
        return False
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    check = shell32.IsUserAnAdmin
    check.argtypes = []
    check.restype = wintypes.BOOL
    return bool(check())


def _windows_error(action: str) -> ElevationError:
    error = ctypes.get_last_error()
    if error == ERROR_CANCELLED:
        return ElevationError("已取消管理员权限请求，正式界面未启动。")
    return ElevationError(f"{action}（Windows 错误 {error}：{ctypes.FormatError(error).strip()}）")


def relaunch_as_administrator(argv: list[str]) -> int:
    """Use the standard UAC prompt, wait, and propagate the child exit code.

    ShellExecuteEx receives a Windows command line, not a PowerShell script.
    list2cmdline therefore preserves Unicode paths, quotes and trailing slashes.
    The same working directory preserves relative configuration/state paths.
    """
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    execute = shell32.ShellExecuteExW
    execute.argtypes = [ctypes.POINTER(_ShellExecuteInfo)]
    execute.restype = wintypes.BOOL
    wait = kernel32.WaitForSingleObject
    wait.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    wait.restype = wintypes.DWORD
    get_exit = kernel32.GetExitCodeProcess
    get_exit.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    get_exit.restype = wintypes.BOOL
    close = kernel32.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    initialize = ole32.CoInitializeEx
    initialize.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    initialize.restype = ctypes.c_long
    uninitialize = ole32.CoUninitialize
    uninitialize.argtypes = []
    uninitialize.restype = None

    info = _ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    # NOCLOSEPROCESS | NOASYNC | FLAG_NO_UI (security prompts still appear).
    info.fMask = 0x40 | 0x100 | 0x400
    info.lpVerb = "runas"
    info.lpFile = sys.executable
    prefix = [] if getattr(sys, "frozen", False) else ["-m", "envmanager"]
    info.lpParameters = subprocess.list2cmdline([*prefix, *argv, ELEVATED_ARGUMENT])
    info.lpDirectory = os.getcwd()
    # SW_HIDE is inherited by Qt's first ShowWindow call and hides the GUI
    # even after window.show(). The frozen executable/pythonw has no console.
    info.nShow = 1  # SW_SHOWNORMAL: show the relaunched application window.
    com_initialized = initialize(None, 0x2 | 0x4) >= 0
    try:
        if not execute(ctypes.byref(info)):
            raise _windows_error("无法请求管理员权限")
        if not info.hProcess:
            raise ElevationError("管理员启动未返回进程句柄，无法确认启动结果。")
        try:
            if wait(info.hProcess, 0xFFFFFFFF) != 0:
                raise _windows_error("无法等待管理员进程")
            code = wintypes.DWORD()
            if not get_exit(info.hProcess, ctypes.byref(code)):
                raise _windows_error("无法获取管理员进程退出结果")
            return code.value
        finally:
            close(info.hProcess)
    finally:
        if com_initialized:
            uninitialize()


def ensure_administrator(argv: list[str], *, elevated: bool = False) -> int | None:
    """None means continue this process; an int means exit without opening GUI."""
    if os.name != "nt":
        raise ElevationError("正式模式仅支持 Windows；其他平台请使用 --demo。")
    if is_administrator():
        return None
    if elevated:
        raise ElevationError("管理员重新启动后仍无管理员权限，已停止，避免重复请求 UAC。")
    return relaunch_as_administrator(argv)


def report_startup_error(message: str) -> None:
    """Console errors stay in the console; pythonw errors use a native dialog."""
    if sys.stderr is not None:
        print(f"启动失败：{message}", file=sys.stderr)
    elif os.name == "nt":
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        show = user32.MessageBoxW
        show.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
        show.restype = ctypes.c_int
        show(None, message, "环境变量方案管理器 V2", 0x10)

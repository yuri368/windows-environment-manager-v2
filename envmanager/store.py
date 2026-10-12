from __future__ import annotations

import ctypes
import os
from copy import deepcopy

from .models import PermissionDenied, RegistryState, ValidationError, match_key

REG_SZ = 1
REG_EXPAND_SZ = 2
SYSTEM_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"


def validate_state(state: RegistryState) -> None:
    if state.scope not in {"User", "System"}:
        raise ValidationError("作用域必须为 User 或 System")
    if not state.name or "=" in state.name or "\0" in state.name:
        raise ValidationError("变量名不能为空或包含 =、NUL")
    if state.exists:
        if not isinstance(state.value, str) or "\0" in state.value:
            raise ValidationError("环境变量必须为不含 NUL 的原始字符串")
        if state.registry_type not in {REG_SZ, REG_EXPAND_SZ}:
            raise ValidationError("不支持的注册表类型，拒绝覆盖")
        if len(state.value.encode("utf-16-le")) // 2 > 32766:
            raise ValidationError("变量值超过 32766 个 UTF-16 单元")
    elif state.value is not None or state.registry_type is not None:
        raise ValidationError("不存在的变量不能带有值或类型")


class WindowsStore:
    """Read raw values in one explicit scope; never use a process environment block."""

    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("正式模式仅支持 Windows；其他平台请使用 --demo")
        import winreg
        self.registry = winreg

    def _location(self, scope):
        if scope == "User":
            return self.registry.HKEY_CURRENT_USER, "Environment"
        if scope == "System":
            return self.registry.HKEY_LOCAL_MACHINE, SYSTEM_KEY
        raise ValidationError("作用域必须为 User 或 System")

    def read(self, scope: str, name: str) -> RegistryState:
        validate_state(RegistryState(name, scope, False))
        root, path = self._location(scope)
        try:
            with self.registry.OpenKey(root, path, 0, self.registry.KEY_READ) as key:
                value, kind = self.registry.QueryValueEx(key, name)
        except FileNotFoundError:
            return RegistryState(name, scope, False)
        state = RegistryState(name, scope, True, value, kind)
        validate_state(state)
        return state

    def list_values(self, scope: str) -> tuple[RegistryState, ...]:
        """Enumerate raw values from exactly one registry scope, read-only."""
        root, path = self._location(scope)
        try:
            key = self.registry.OpenKey(root, path, 0, self.registry.KEY_READ)
        except FileNotFoundError:
            return ()
        with key:
            value_count = self.registry.QueryInfoKey(key)[1]
            states = []
            for index in range(value_count):
                name, value, kind = self.registry.EnumValue(key, index)
                state = RegistryState(name, scope, True, value, kind)
                validate_state(state)
                states.append(state)
        return tuple(states)

    def check_permission(self, scope: str) -> None:
        root, path = self._location(scope)
        try:
            with self.registry.OpenKey(root, path, 0, self.registry.KEY_SET_VALUE):
                pass
        except FileNotFoundError:
            # Creating a missing User Environment key is done only during execution.
            parent = "" if scope == "User" else path.rsplit("\\", 1)[0]
            try:
                with self.registry.OpenKey(root, parent, 0, self.registry.KEY_CREATE_SUB_KEY):
                    pass
            except OSError as exc:
                raise PermissionDenied(f"没有 {scope} 作用域的写入权限") from exc
        except OSError as exc:
            suffix = "，请以管理员身份启动" if scope == "System" else ""
            raise PermissionDenied(f"没有 {scope} 作用域的写入权限{suffix}") from exc

    def write(self, state: RegistryState) -> None:
        validate_state(state)
        if not state.exists:
            raise ValidationError("删除必须显式调用 delete")
        root, path = self._location(state.scope)
        with self.registry.CreateKeyEx(root, path, 0, self.registry.KEY_SET_VALUE) as key:
            self.registry.SetValueEx(key, state.name, 0, state.registry_type, state.value)

    def delete(self, scope: str, name: str) -> None:
        validate_state(RegistryState(name, scope, False))
        root, path = self._location(scope)
        try:
            with self.registry.OpenKey(root, path, 0, self.registry.KEY_SET_VALUE) as key:
                self.registry.DeleteValue(key, name)
        except FileNotFoundError:
            pass

    def notify(self) -> None:
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        function = user32.SendMessageTimeoutW
        function.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                             wintypes.LPARAM, wintypes.UINT, wintypes.UINT,
                             ctypes.POINTER(ctypes.c_size_t)]
        function.restype = wintypes.LPARAM
        message = ctypes.create_unicode_buffer("Environment")
        result = ctypes.c_size_t()
        if not function(0xFFFF, 0x001A, 0, ctypes.addressof(message), 0x0002,
                        3000, ctypes.byref(result)):
            raise OSError(ctypes.get_last_error(), "环境变更通知失败或超时")


class MemoryStore:
    """Isolated demo/test store with the same raw-state contract as Windows."""

    def __init__(self, states=()):
        self.values = {(s.scope, match_key(s.name)): deepcopy(s) for s in states}
        self.writes = []
        self.notifications = 0
        self.denied_scopes = set()

    def read(self, scope, name):
        if scope not in {"User", "System"}:
            raise ValidationError("作用域必须为 User 或 System")
        state = self.values.get((scope, match_key(name)))
        if state is None:
            return RegistryState(name, scope, False)
        return RegistryState(name, scope, state.exists, state.value, state.registry_type)

    def list_values(self, scope):
        """List only explicitly seeded demo/test data; never consult the host."""
        if scope not in {"User", "System"}:
            raise ValidationError("作用域必须为 User 或 System")
        states = (deepcopy(state) for (saved_scope, _), state in self.values.items()
                  if saved_scope == scope and state.exists)
        return tuple(sorted(states, key=lambda state: (match_key(state.name), state.name)))

    def check_permission(self, scope):
        if scope not in {"User", "System"}:
            raise ValidationError("未知作用域")
        if scope in self.denied_scopes:
            raise PermissionDenied(f"没有 {scope} 作用域的写入权限")

    def write(self, state):
        validate_state(state)
        self.check_permission(state.scope)
        if not state.exists:
            raise ValidationError("删除必须显式调用 delete")
        self.values[(state.scope, match_key(state.name))] = deepcopy(state)
        self.writes.append(state)

    def delete(self, scope, name):
        self.check_permission(scope)
        self.values.pop((scope, match_key(name)), None)
        self.writes.append(RegistryState(name, scope, False))

    def notify(self):
        self.notifications += 1

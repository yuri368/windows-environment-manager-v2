from __future__ import annotations

import os
import unittest
import uuid
from unittest.mock import patch

from envmanager.models import RegistryState, ValidationError
from envmanager.store import WindowsStore


@unittest.skipUnless(os.name == "nt", "Windows 隔离注册表测试")
class WindowsStoreTests(unittest.TestCase):
    """Production registry code exercised under disposable Software keys only."""

    def setUp(self):
        import winreg
        self.registry = winreg
        self.path = "Software\\EnvironmentManagerV2Tests\\" + uuid.uuid4().hex
        root_path = self.path

        class IsolatedStore(WindowsStore):
            def _location(inner, scope):
                if scope not in {"User", "System"}:
                    raise ValidationError("未知作用域")
                return winreg.HKEY_CURRENT_USER, root_path + "\\" + scope

        self.store = IsolatedStore()
        for scope in ("User", "System"):
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, self.path + "\\" + scope):
                pass

    def tearDown(self):
        # Keys are exactly named and verified; no environment keys are touched.
        if not self.path.startswith("Software\\EnvironmentManagerV2Tests\\"):
            raise AssertionError("测试注册表路径越界")
        for scope in ("User", "System"):
            self.registry.DeleteKey(self.registry.HKEY_CURRENT_USER, self.path + "\\" + scope)
        self.registry.DeleteKey(self.registry.HKEY_CURRENT_USER, self.path)
        try:
            self.registry.DeleteKey(self.registry.HKEY_CURRENT_USER, "Software\\EnvironmentManagerV2Tests")
        except OSError:
            pass  # Another concurrent test may still own a child.

    def test_raw_empty_expand_type_and_explicit_delete(self):
        self.assertFalse(self.store.read("User", "TEST_HOME").exists)
        self.store.check_permission("User")
        empty = RegistryState("TEST_HOME", "User", True, "", 1)
        self.store.write(empty)
        self.assertTrue(self.store.read("User", "TEST_HOME").same_content(empty))
        raw = RegistryState("TEST_HOME", "User", True, "%UNEXPANDED_REFERENCE%\\bin; ;X;X", 2)
        self.store.write(raw)
        self.assertTrue(self.store.read("User", "test_home").same_content(raw))
        self.store.delete("User", "TEST_HOME")
        self.assertFalse(self.store.read("User", "TEST_HOME").exists)

    def test_scope_isolation_and_unsupported_type_rejected(self):
        user = RegistryState("Path", "User", True, "U", 1)
        system = RegistryState("Path", "System", True, "S", 2)
        self.store.write(user)
        self.store.write(system)
        self.assertTrue(self.store.read("User", "Path").same_content(user))
        self.assertTrue(self.store.read("System", "Path").same_content(system))
        with self.assertRaises(ValidationError):
            self.store.write(RegistryState("Path", "User", True, "bad", 4))
        self.assertTrue(self.store.read("User", "Path").same_content(user))

    def test_notification_uses_bounded_broadcast_with_environment_pointer(self):
        import ctypes
        from types import SimpleNamespace

        class Send:
            def __call__(inner, hwnd, message, wparam, lparam, flags, timeout, result):
                self.assertEqual((hwnd, message, wparam), (0xFFFF, 0x001A, 0))
                self.assertEqual(ctypes.wstring_at(lparam), "Environment")
                self.assertEqual((flags, timeout), (0x0002, 3000))
                return 1

        with patch("ctypes.WinDLL", return_value=SimpleNamespace(SendMessageTimeoutW=Send())):
            self.store.notify()

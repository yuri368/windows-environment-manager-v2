"""Only temporary files and an injected fake Registry are exercised here.

Run: python -B -m unittest discover -s tests_v3 -p test_environment.py -v
No test constructs WindowsEnvironment without an explicit fake winreg.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from envmanager_v3.environment import (
    FileEnvironment, WindowsEnvironment, V3EnvironmentError,
    canonical_name, REG_SZ, REG_EXPAND_SZ, MAX_UTF16_UNITS,
)


class ErrorAssertions:
    def error(self, code, operation, *args, **kwargs):
        with self.assertRaises(V3EnvironmentError) as caught:
            operation(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        json.dumps(caught.exception.to_dict(), ensure_ascii=True, allow_nan=False)
        return caught.exception


class CanonicalTests(ErrorAssertions, unittest.TestCase):
    def test_v2_nonexpanding_upper_rule(self):
        for original, expected in (("Path", "PATH"), ("ß", "ß"), ("ss", "SS"),
                                   ("straße", "STRAßE"), ("ﬃ", "ﬃ"),
                                   ("ı", "I"), ("é", "É"), ("𐐨", "𐐨"),
                                   (" 系统名 ", " 系统名 "), ("\ud800", "\ud800")):
            with self.subTest(original=ascii(original)):
                self.assertEqual(canonical_name(original), expected)
        self.assertNotEqual(canonical_name("ß"), canonical_name("SS"))
        self.error("INVALID_NAME", canonical_name, None)


class FileTests(ErrorAssertions, unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "nested" / "environment.json"
        self.store = FileEnvironment(self.path)

    def test_empty_environment_has_no_examples_or_constructor_writes(self):
        self.assertFalse(self.path.exists())
        self.assertEqual(self.store.list(), {"items": []})
        missing = self.store.read("User", "Blank")
        self.assertEqual({key: missing[key] for key in ("scope", "name", "exists", "raw", "type")},
                         {"scope": "User", "name": "Blank", "exists": False,
                          "raw": None, "type": None})
        self.assertEqual(missing, self.store.read("User", "Blank"))
        self.assertFalse(self.path.exists())
        created = self.store.write("User", "Blank", "", expected=missing)
        self.assertTrue(created["exists"])
        self.assertEqual(created["raw"], "")
        self.assertEqual(created["type"], REG_SZ)
        self.assertNotEqual(missing["revision"], created["revision"])
        self.assertTrue(self.path.is_file())

    def test_restart_scopes_casing_special_text_and_json_snapshots(self):
        raw = "  %UNEXPANDED%;A;A;;\r\nB\n尾部\r😀\t  "
        first = self.store.write("User", "MiXeD", raw, REG_EXPAND_SZ)
        self.store.write("System", "MiXeD", "system", REG_SZ)
        self.store.write("User", "ß", "sharp")
        self.store.write("User", "SS", "double")
        self.store.write("User", " 原名 ", "原始值")
        with self.assertRaises(V3EnvironmentError) as invalid:
            self.store.write("User", "InvalidUnicode", "\ud800")
        self.assertEqual(invalid.exception.code, "INVALID_RAW")
        reopened = FileEnvironment(self.path)
        readback = reopened.read("User", "mixed")
        self.assertEqual(readback["raw"], raw)
        self.assertEqual(readback["name"], "MiXeD")
        self.assertEqual(readback["type"], REG_EXPAND_SZ)
        self.assertEqual(reopened.read("System", "MIXED")["raw"], "system")
        self.assertEqual(reopened.read("User", "ß")["raw"], "sharp")
        self.assertEqual(reopened.read("User", "ss")["raw"], "double")
        self.assertEqual(reopened.read("User", " 原名 ")["raw"], "原始值")
        self.assertEqual(len(reopened.list()["items"]), 5)
        self.assertEqual(len(reopened.list("User")["items"]), 4)
        self.assertNotEqual(first["revision"], readback["revision"])
        json.dumps(reopened.list(), allow_nan=False)

    def test_defaults_and_existing_type_are_preserved(self):
        self.assertEqual(self.store.write("User", "pAtH", "a")["type"], REG_EXPAND_SZ)
        self.assertEqual(self.store.write("User", "Other", "%TEMP%")["type"], REG_SZ)
        self.store.write("System", "Path", "b", REG_SZ)
        preserved = self.store.write("System", "PATH", "c")
        self.assertEqual(preserved["name"], "Path")
        self.assertEqual(preserved["type"], REG_SZ)
        self.error("TYPE_MISMATCH", self.store.write, "System", "Path", "d", REG_EXPAND_SZ)
        self.assertEqual(self.store.read("System", "Path")["raw"], "c")

    def test_raw_utf16_limit_and_no_trimming(self):
        for raw in ("", " \r\n\t ", ";;a;a;;", "x" * MAX_UTF16_UNITS,
                    "😀" * (MAX_UTF16_UNITS // 2)):
            with self.subTest(length=len(raw)):
                self.assertEqual(self.store.write("User", "Raw", raw)["raw"], raw)
        self.error("VALUE_TOO_LONG", self.store.write, "User", "Raw", "x" * 32767)
        self.error("VALUE_TOO_LONG", self.store.write, "User", "Raw", "😀" * 16384)
        self.error("INVALID_RAW", self.store.write, "User", "Raw", "before\0after")
        self.error("INVALID_RAW", self.store.write, "User", "Raw", None)

    def test_invalid_targets_and_types_never_create_file(self):
        for scope in ("user", "Machine", "", None, []):
            for operation, args in ((self.store.read, (scope, "X")),
                                    (self.store.list, (scope,)) if scope is not None else
                                    (self.store.check_permission, (scope,)),
                                    (self.store.write, (scope, "X", "x")),
                                    (self.store.delete, (scope, "X"))):
                self.error("INVALID_SCOPE", operation, *args)
        for name in ("", "=X", "A=B", "A\0B", None, 1):
            for operation, args in ((self.store.read, ("User", name)),
                                    (self.store.write, ("User", name, "x")),
                                    (self.store.delete, ("User", name))):
                self.error("INVALID_NAME", operation, *args)
        for value_type in ("REG_DWORD", "REG_MULTI_SZ", "reg_sz", 1, {}, False):
            self.error("INVALID_TYPE", self.store.write, "User", "X", "x", value_type)
        self.assertFalse(self.path.exists())

    def test_cas_conflicts_do_not_overwrite_or_delete(self):
        before = self.store.read("User", "X")
        written = self.store.write("User", "x", "one", expected=before)
        self.error("CONFLICT", self.store.write, "User", "X", "two", expected=before)
        self.error("CONFLICT", self.store.delete, "User", "X", expected=before)
        self.assertEqual(self.store.read("User", "X")["raw"], "one")
        self.assertFalse(self.store.delete("User", "X", expected=written)["exists"])
        self.error("CONFLICT", self.store.write, "User", "X", "two", expected=written)
        self.assertFalse(self.store.delete("User", "X")["exists"])
        self.error("INVALID_EXPECTED", self.store.write, "User", "X", "x", expected={})
        self.error("INVALID_EXPECTED", self.store.delete, "User", "X", expected="bad")
        other = self.store.read("System", "X")
        self.error("CONFLICT", self.store.write, "User", "X", "x", expected=other)

    def test_signature_type_and_observed_aba(self):
        original = self.store.write("User", "X", "a")
        self.assertEqual(self.store.write("User", "x", "a")["revision"], original["revision"])
        second = FileEnvironment(self.path)
        second.write("User", "X", "b")
        intermediate = self.store.read("User", "X")
        second.write("User", "X", "a")
        returned = self.store.read("User", "X")
        self.assertNotEqual(original["revision"], intermediate["revision"])
        self.assertNotEqual(original["revision"], returned["revision"])
        self.error("CONFLICT", self.store.write, "User", "X", "c", expected=original)
        # A change entirely between observations is intentionally undetectable.
        second.write("User", "X", "b")
        second.write("User", "X", "a")
        self.assertEqual(self.store.read("User", "X")["revision"], returned["revision"])
        second.delete("User", "X")
        second.write("User", "X", "a", REG_EXPAND_SZ)
        self.assertNotEqual(returned["revision"], self.store.read("User", "X")["revision"])

    def test_list_observes_absences_for_aba(self):
        original = self.store.write("User", "MiXeD", "a")
        other = FileEnvironment(self.path)
        other.delete("User", "mixed")
        self.assertEqual(self.store.list("User"), {"items": []})
        other.write("User", "MiXeD", "a")
        self.assertNotEqual(original["revision"], self.store.read("User", "mixed")["revision"])

    def test_atomic_fsync_replace_and_failed_save_leave_original(self):
        self.store.write("User", "X", "before")
        before_bytes = self.path.read_bytes()
        with mock.patch("envmanager_v3.environment.os.fsync", side_effect=OSError("fsync failed")):
            self.error("STORAGE_IO", self.store.write, "User", "X", "after")
        self.assertEqual(self.path.read_bytes(), before_bytes)
        with mock.patch("envmanager_v3.environment.os.replace", side_effect=PermissionError("locked")):
            self.error("PERMISSION_DENIED", self.store.delete, "User", "X")
        self.assertEqual(self.path.read_bytes(), before_bytes)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])
        events = []
        import os
        real_fsync, real_replace = os.fsync, os.replace
        def fsync(fd):
            events.append("fsync")
            real_fsync(fd)
        def replace(source, destination):
            events.append("replace")
            self.assertEqual(Path(source).parent, self.path.parent)
            self.assertEqual(Path(destination), self.path)
            json.loads(Path(source).read_text("utf-8"))
            real_replace(source, destination)
        with mock.patch("envmanager_v3.environment.os.fsync", side_effect=fsync), \
                mock.patch("envmanager_v3.environment.os.replace", side_effect=replace):
            self.store.write("User", "X", "after")
        self.assertEqual(events, ["fsync", "replace"])

    def test_corrupt_storage_is_not_silently_reinitialized(self):
        self.path.parent.mkdir(parents=True)
        for content in ("{bad", "null", "[]", '{"format":1,"values":0}',
                        '{"format":1,"values":{"User":[],"System":[]},"x":0}'):
            self.path.write_text(content, "utf-8")
            if '"x":0' in content:  # Additional top-level metadata is permitted.
                self.assertEqual(self.store.list(), {"items": []})
                continue
            self.error("INVALID_STORAGE", self.store.write, "User", "X", "x")
            self.assertEqual(self.path.read_text("utf-8"), content)
        state = {"format": 1, "values": {"User": [{"name": "x", "raw": "a", "type": REG_SZ},
                                                  {"name": "X", "raw": "b", "type": REG_SZ}],
                                         "System": []}}
        self.path.write_text(json.dumps(state), "utf-8")
        self.error("INVALID_STORAGE", self.store.list)

    def test_unsupported_types_are_visible_repr_safe_and_readonly(self):
        self.path.parent.mkdir(parents=True)
        state = {"format": 1, "values": {"User": [{"name": "Binary", "raw": ["\r\n", "汉"],
                                                  "type": 7}], "System": []}}
        self.path.write_text(json.dumps(state), "utf-8")
        snapshot = self.store.read("User", "binary")
        self.assertTrue(snapshot["exists"])
        self.assertFalse(snapshot["supported"])
        self.assertIsNone(snapshot["raw"])
        self.assertIn("\\r\\n", snapshot["display"])
        self.assertTrue(snapshot["display"].isascii())
        self.assertEqual(self.store.list()["items"], [snapshot])
        self.error("UNSUPPORTED_TYPE", self.store.write, "User", "binary", "overwrite")
        self.error("UNSUPPORTED_TYPE", self.store.delete, "User", "binary", expected=snapshot)
        self.store.write("System", "Other", "safe")
        self.assertEqual(json.loads(self.path.read_text("utf-8"))["values"]["User"],
                         state["values"]["User"])

    def test_multithread_cas_has_one_winner_across_instances(self):
        self.store.write("User", "X", "before")
        adapters = [FileEnvironment(self.path) for _ in range(8)]
        snapshots = [adapter.read("User", "X") for adapter in adapters]
        barrier = threading.Barrier(len(adapters))
        def race(pair):
            index, adapter = pair
            barrier.wait(timeout=10)
            try:
                return adapter.write("User", "X", str(index), expected=snapshots[index])["raw"]
            except V3EnvironmentError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(race, enumerate(adapters)))
        self.assertEqual(results.count("CONFLICT"), 7)
        self.assertEqual(len(self.store.list()["items"]), 1)

    def test_file_permission_and_notify_do_not_create_data_or_broadcast(self):
        with mock.patch("envmanager_v3.environment.WindowsEnvironment._broadcast") as broadcast:
            self.assertTrue(self.store.check_permission("System")["writable"])
            self.assertEqual(self.store.notify(), {"notified": False, "reason": "isolated_file_backend"})
            broadcast.assert_not_called()
        self.assertFalse(self.path.exists())


class FakeKey:
    def __init__(self, registry, scope, access):
        self.registry, self.scope, self.access = registry, scope, access
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.registry.calls.append(("close", self.scope))


class FakeWinreg:
    HKEY_CURRENT_USER = "FAKE_HKCU"
    HKEY_LOCAL_MACHINE = "FAKE_HKLM"
    KEY_QUERY_VALUE = 1
    KEY_SET_VALUE = 2
    REG_NONE = 0
    REG_SZ = 1
    REG_EXPAND_SZ = 2
    REG_BINARY = 3
    REG_DWORD = 4
    REG_MULTI_SZ = 7
    REG_QWORD = 11

    def __init__(self):
        self.rows = {"User": [], "System": []}
        self.calls = []
        self.denied = False
        self.enum_error = None

    def OpenKey(self, root, path, reserved, access):
        self.calls.append(("open", root, path, reserved, access))
        locations = {(self.HKEY_CURRENT_USER, "Environment"): "User",
                     (self.HKEY_LOCAL_MACHINE,
                      r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"): "System"}
        if (root, path) not in locations:
            raise AssertionError("Registry path outside authorized environment keys")
        if self.denied:
            raise PermissionError("fake access denied")
        return FakeKey(self, locations[(root, path)], access)

    def QueryValueEx(self, key, name):
        self.calls.append(("query", key.scope, name))
        for row in self.rows[key.scope]:
            if canonical_name(row[0]) == canonical_name(name):
                return row[1], row[2]
        raise FileNotFoundError(name)

    def EnumValue(self, key, index):
        self.calls.append(("enum", key.scope, index))
        if self.enum_error:
            raise self.enum_error
        if index >= len(self.rows[key.scope]):
            exc = OSError("no more items")
            exc.winerror = 259
            raise exc
        return self.rows[key.scope][index]

    def SetValueEx(self, key, name, reserved, native_type, raw):
        self.calls.append(("set", key.scope, name, reserved, native_type, raw))
        assert key.access & self.KEY_SET_VALUE
        for index, row in enumerate(self.rows[key.scope]):
            if canonical_name(row[0]) == canonical_name(name):
                self.rows[key.scope][index] = (row[0], raw, native_type)
                return
        self.rows[key.scope].append((name, raw, native_type))

    def DeleteValue(self, key, name):
        self.calls.append(("delete", key.scope, name))
        assert key.access & self.KEY_SET_VALUE
        for row in self.rows[key.scope]:
            if canonical_name(row[0]) == canonical_name(name):
                self.rows[key.scope].remove(row)
                return
        raise FileNotFoundError(name)


class WindowsTests(ErrorAssertions, unittest.TestCase):
    def setUp(self):
        self.registry = FakeWinreg()
        self.notifier = mock.Mock(return_value=True)
        self.store = WindowsEnvironment(self.registry, notifier=self.notifier)

    def test_constructor_is_inert_and_reads_are_confined_and_raw(self):
        self.assertEqual(self.registry.calls, [])
        raw = "  %SYSTEMROOT%;a;a;;\r\n 😀  "
        self.registry.rows["User"] = [("PaTh", raw, FakeWinreg.REG_EXPAND_SZ)]
        result = self.store.read("User", "pAtH")
        self.assertEqual(result["name"], "PaTh")
        self.assertEqual(result["raw"], raw)
        self.assertEqual(result["type"], REG_EXPAND_SZ)
        self.assertIn(("query", "User", "pAtH"), self.registry.calls)
        self.assertIn(("open", "FAKE_HKCU", "Environment", 0, FakeWinreg.KEY_QUERY_VALUE),
                      self.registry.calls)
        self.assertFalse(self.store.read("System", "Missing")["exists"])
        self.store.list()
        self.assertFalse(any(call[0] in ("set", "delete") for call in self.registry.calls))

    def test_write_preserves_original_input_name_and_existing_type(self):
        self.registry.rows["System"] = [("PaTh", "old", FakeWinreg.REG_SZ)]
        before = self.store.read("System", "path")
        raw = " %X%;;\r\n重复;重复; "
        result = self.store.write("System", "pAtH", raw, expected=before)
        self.assertEqual(result["name"], "PaTh")
        self.assertEqual(result["type"], REG_SZ)
        self.assertIn(("set", "System", "pAtH", 0, FakeWinreg.REG_SZ, raw), self.registry.calls)
        self.assertEqual(self.store.write("User", "Path", "x")["type"], REG_EXPAND_SZ)
        self.assertEqual(self.store.write("User", "Other", "")["type"], REG_SZ)
        self.assertEqual(self.store.write("User", "Percent", "%ROOT%", REG_EXPAND_SZ)["type"],
                         REG_EXPAND_SZ)
        self.error("TYPE_MISMATCH", self.store.write, "System", "Path", "x", REG_EXPAND_SZ)
        self.notifier.assert_not_called()  # Explicit batch notification is the service's job.

    def test_unicode_targets_and_missing_versus_empty(self):
        missing = self.store.read("User", "ß")
        blank = self.store.write("User", "ß", "", expected=missing)
        self.store.write("User", "ss", "double")
        self.assertEqual(self.store.read("User", "SS")["raw"], "double")
        self.assertEqual(self.store.read("User", "ß")["raw"], "")
        self.assertTrue(blank["exists"])
        self.assertIsNone(missing["raw"])
        self.assertEqual(len(self.store.list("User")["items"]), 2)

    def test_unsupported_types_readonly_and_repr_safe(self):
        self.registry.rows["User"] = [("Binary", b"\x00\r\n\xff", FakeWinreg.REG_BINARY),
                                      ("Multi", ["one", "\r\n汉"], FakeWinreg.REG_MULTI_SZ),
                                      ("Dword", 42, FakeWinreg.REG_DWORD),
                                      ("Unknown", b"x", 12345)]
        items = self.store.list("User")["items"]
        self.assertEqual(len(items), 4)
        self.assertEqual(items[0]["type"], "REG_BINARY")
        self.assertEqual(items[-1]["type"], "REG_TYPE_12345")
        for item in items:
            self.assertFalse(item["supported"])
            self.assertIsNone(item["raw"])
            self.assertTrue(item["display"].isascii())
            self.error("UNSUPPORTED_TYPE", self.store.write, "User", item["name"], "x")
            self.error("UNSUPPORTED_TYPE", self.store.delete, "User", item["name"])
        json.dumps(items, allow_nan=False)
        self.assertFalse(any(call[0] in ("set", "delete") for call in self.registry.calls))

    def test_permission_opens_only_no_set_delete_uac_or_creation(self):
        for scope in ("User", "System"):
            self.assertEqual(self.store.check_permission(scope), {"scope": scope, "writable": True})
        self.assertTrue(all(call[0] in ("open", "close") for call in self.registry.calls))
        for call in self.registry.calls:
            if call[0] == "open":
                self.assertEqual(call[-1], FakeWinreg.KEY_QUERY_VALUE | FakeWinreg.KEY_SET_VALUE)
        self.registry.denied = True
        self.error("PERMISSION_DENIED", self.store.check_permission, "System")
        self.error("PERMISSION_DENIED", self.store.read, "User", "X")

    def test_native_errors_are_not_treated_as_enumeration_end(self):
        self.registry.enum_error = PermissionError("fake enum failure")
        self.error("PERMISSION_DENIED", self.store.list, "User")
        self.registry.enum_error = OSError("fake broken I/O")
        self.error("REGISTRY_IO", self.store.list, "User")

    def test_cas_changes_type_external_changes_and_delete(self):
        before = self.store.write("User", "X", "a")
        self.registry.rows["User"] = [("X", "a", FakeWinreg.REG_EXPAND_SZ)]
        self.error("CONFLICT", self.store.write, "User", "X", "b", expected=before)
        self.error("CONFLICT", self.store.delete, "User", "X", expected=before)
        observed = self.store.read("User", "X")
        self.registry.rows["User"] = [("X", "changed", FakeWinreg.REG_EXPAND_SZ)]
        self.store.read("User", "X")
        self.registry.rows["User"] = [("X", "a", FakeWinreg.REG_EXPAND_SZ)]
        self.error("CONFLICT", self.store.delete, "User", "X", expected=observed)
        current = self.store.read("User", "X")
        missing = self.store.delete("User", "x", expected=current)
        self.assertFalse(missing["exists"])
        self.assertIn(("delete", "User", "x"), self.registry.calls)
        self.assertFalse(self.store.delete("User", "X")["exists"])

    def test_list_observes_deleted_values_and_retained_type(self):
        before = self.store.write("User", "X", "a")
        self.registry.rows["User"] = []
        self.assertEqual(self.store.list("User"), {"items": []})
        self.registry.rows["User"] = [("X", "a", FakeWinreg.REG_SZ)]
        self.assertNotEqual(before["revision"], self.store.read("User", "X")["revision"])

    def test_invalid_input_never_opens_registry(self):
        self.error("INVALID_NAME", self.store.write, "User", "A=B", "x")
        self.error("INVALID_SCOPE", self.store.read, "Machine", "X")
        self.error("INVALID_TYPE", self.store.write, "User", "X", "x", "REG_DWORD")
        self.error("VALUE_TOO_LONG", self.store.write, "User", "X", "😀" * 16384)
        self.assertEqual(self.registry.calls, [])

    def test_explicit_notification_success_and_failure_are_reported(self):
        self.assertEqual(self.store.notify(), {"notified": True})
        self.notifier.assert_called_once_with()
        self.notifier.return_value = False
        self.error("NOTIFY_FAILED", self.store.notify)
        self.notifier.side_effect = OSError("fake notification failure")
        self.error("NOTIFY_FAILED", self.store.notify)

    def test_ctypes_wm_settingchange_call_is_mocked_with_pointer_sized_signature(self):
        # _broadcast is actual production code; only its DLL factory is replaced.
        fake_send = mock.Mock(return_value=1)
        fake_user32 = mock.Mock(SendMessageTimeoutW=fake_send)
        store = WindowsEnvironment(self.registry)
        with mock.patch("envmanager_v3.environment.ctypes.WinDLL", create=True,
                        return_value=fake_user32) as load:
            self.assertEqual(store.notify(), {"notified": True})
        load.assert_called_once_with("user32", use_last_error=True)
        self.assertEqual(fake_send.call_args.args[:6], (0xFFFF, 0x001A, 0, "Environment", 2, 5000))
        self.assertEqual(len(fake_send.argtypes), 7)
        fake_send.return_value = 0
        with mock.patch("envmanager_v3.environment.ctypes.WinDLL", create=True,
                        return_value=fake_user32), \
                mock.patch("envmanager_v3.environment.ctypes.get_last_error", create=True,
                           return_value=1460):
            error = self.error("NOTIFY_FAILED", store.notify)
            self.assertEqual(error.details["winerror"], 1460)


if __name__ == "__main__":
    unittest.main()

"""Mock-only native notification/lifecycle/preflight tests; no host Registry.

python -B -m unittest discover -s tests_v3 -p 'test_environment*.py' -v
"""

from __future__ import annotations

import ctypes
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from envmanager_v3.environment import WindowsEnvironment, V3EnvironmentError
from envmanager_v3.environment_events import NativeRegistryEvents, RegistryChangeListener
from envmanager_v3.database import Database, encode
from envmanager_v3.execution import Executor
from envmanager_v3.planning import Planner
from tests_v3.test_environment import FakeWinreg, FakeKey


class NotifyKey(FakeKey):
    def Close(self):
        if self.scope == self.registry.fail_close_scope:
            raise OSError("mock Registry key close failure")
        self.registry.calls.append(("close_watch", self.scope))


class NotifyRegistry(FakeWinreg):
    KEY_NOTIFY = 16

    def __init__(self):
        super().__init__()
        self.fail_scope = None
        self.fail_close_scope = None
        self.native = None
        self.during_write = None

    def OpenKey(self, root, path, reserved, access):
        key = super().OpenKey(root, path, reserved, access)
        if access == self.KEY_NOTIFY:
            if key.scope == self.fail_scope:
                raise PermissionError("mock watch open denied")
            return NotifyKey(self, key.scope, access)
        return key

    def SetValueEx(self, *args):
        super().SetValueEx(*args)
        if self.native:
            self.native.signal(args[0].scope)
        if self.during_write:
            self.during_write()

    def DeleteValue(self, *args):
        super().DeleteValue(*args)
        if self.native:
            self.native.signal(args[0].scope)


class FakeNative:
    """Manual-reset native events, wait-any, and truly one-shot registrations.

    Condition.wait is event driven (no sleeps/poll loop); all native failures
    can be injected without reaching ctypes or real winreg.
    """

    def __init__(self):
        self.condition = threading.Condition()
        self.events = {}
        self.watches = {}
        self.created = []
        self.closed = []
        self.arms = []
        self.waiting = threading.Event()
        self.wait_error = None
        self.fail_arm_scope = None
        self.fail_poll_scope = None
        self.fail_reset_scope = None
        self.fail_create_at = None
        self.fail_set = False
        self.fail_close = set()

    def create_event(self):
        with self.condition:
            handle = len(self.created) + 1
            if handle == self.fail_create_at:
                raise OSError("mock CreateEvent failure")
            self.created.append(handle)
            self.events[handle] = False
            return handle

    def set_event(self, handle):
        with self.condition:
            if self.fail_set:
                raise OSError("mock SetEvent failure")
            self.events[handle] = True
            self.condition.notify_all()

    def reset_event(self, handle):
        with self.condition:
            if (self.fail_reset_scope is not None
                    and self.watches.get(handle, {}).get("scope") == self.fail_reset_scope):
                raise OSError("mock ResetEvent failure")
            self.events[handle] = False

    def close_event(self, handle):
        with self.condition:
            if handle in self.fail_close:
                raise OSError("mock CloseHandle failure")
            if handle in self.closed:
                raise AssertionError("double close")
            self.closed.append(handle)
            del self.events[handle]

    def arm(self, key, event):
        with self.condition:
            if key.scope == self.fail_arm_scope:
                raise OSError("mock RegNotifyChangeKeyValue failure")
            previous = self.watches.get(event)
            if previous and previous["armed"]:
                raise AssertionError("rearmed before previous one-shot completed")
            if self.events[event]:
                raise AssertionError("reset must precede rearm")
            self.watches[event] = {"scope": key.scope, "armed": True}
            self.arms.append((key.scope, event, threading.get_ident()))

    def is_set(self, event):
        with self.condition:
            if (self.fail_poll_scope is not None
                    and self.watches.get(event, {}).get("scope") == self.fail_poll_scope):
                raise OSError("mock WaitForSingleObject failure")
            return self.events[event]

    def wait(self, handles):
        with self.condition:
            self.waiting.set()
            while True:
                if self.wait_error:
                    raise self.wait_error
                for index, handle in enumerate(handles):
                    if self.events[handle]:
                        return index
                self.condition.wait()

    def signal(self, scope):
        with self.condition:
            for handle, watch in self.watches.items():
                if watch["scope"] == scope and watch["armed"]:
                    watch["armed"] = False
                    self.events[handle] = True
            self.condition.notify_all()

    def fail_wait(self):
        with self.condition:
            self.wait_error = OSError("mock native wait failed")
            self.condition.notify_all()


class EventTests(unittest.TestCase):
    def setUp(self):
        self.registry = NotifyRegistry()
        self.native = FakeNative()
        self.store = WindowsEnvironment(self.registry, notifier=lambda: True)
        self.addCleanup(self.store.close)
        self.registry.rows["User"] = [("Path", "a;a;;%ROOT%\r\n", self.registry.REG_EXPAND_SZ),
                                      ("Other", "same", self.registry.REG_SZ)]
        self.registry.rows["System"] = [("Path", "system", self.registry.REG_SZ)]

    def start(self, **kwargs):
        status = self.store.start_changes(native=self.native, **kwargs)
        self.assertTrue(status["running"])
        self.assertTrue(self.native.waiting.wait(2), "worker did not enter native wait")
        self.store.take_events()

    def test_inert_constructor_explicit_start_minimal_permissions_and_cleanup(self):
        self.assertEqual(self.registry.calls, [])
        self.assertEqual(self.native.created, [])
        self.assertEqual(self.store.take_events(), [])
        self.assertTrue(self.store.close()["closed"])
        before = self.store.read("User", "path")
        status = self.store.start_changes(native=self.native)
        self.assertEqual(status["scopes"], {"User": True, "System": True})
        events = self.store.take_events()
        self.assertEqual({event["scope"] for event in events}, {"User", "System"})
        self.assertTrue(all(event["reason"] == "listener_started" for event in events))
        self.assertNotEqual(before["revision"], self.store.read("User", "path")["revision"])
        opens = [call for call in self.registry.calls if call[0] == "open"
                 and call[-1] == self.registry.KEY_NOTIFY]
        self.assertEqual(len(opens), 2)
        self.assertEqual(opens[0][1:3], ("FAKE_HKCU", WindowsEnvironment.USER_KEY))
        self.assertEqual(opens[1][1:3], ("FAKE_HKLM", WindowsEnvironment.SYSTEM_KEY))
        self.assertFalse(any(call[0] in ("set", "delete") for call in self.registry.calls))
        self.store.start_changes()  # Idempotent; uses the already injected backend.
        self.assertEqual(len(self.native.created), 3)  # stop + User + System
        self.assertTrue(self.store.close()["closed"])
        self.assertCountEqual(self.native.closed, self.native.created)
        self.assertEqual([call for call in self.registry.calls if call[0] == "close_watch"],
                         [("close_watch", "User"), ("close_watch", "System")])
        self.assertTrue(self.store.close()["closed"])
        self.assertEqual(len(self.native.closed), 3)

    def test_equal_raw_unknown_aba_invalidates_only_affected_scope_and_never_revives(self):
        self.start()
        before = self.store.read("User", "Path")
        other = self.store.read("User", "Other")
        absent = self.store.read("User", "Absent")
        system = self.store.read("System", "Path")
        # Both B and returning A may occur before any observer gets the lock.
        with self.store._lock:
            self.registry.rows["User"][0] = ("Path", "B", self.registry.REG_EXPAND_SZ)
            self.native.signal("User")
            self.registry.rows["User"][0] = ("Path", before["raw"], self.registry.REG_EXPAND_SZ)
            self.native.signal("User")
            after = self.store.read("User", "path")  # drains before worker
            events = self.store.take_events()
        self.assertEqual(after["raw"], before["raw"])
        self.assertNotEqual(after["revision"], before["revision"])
        self.assertNotEqual(other["revision"], self.store.read("User", "Other")["revision"])
        self.assertNotEqual(absent["revision"], self.store.read("User", "Absent")["revision"])
        self.assertEqual(system, self.store.read("System", "Path"))
        self.assertEqual(after, self.store.read("User", "PATH"))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["reason"], "key_changed")
        self.assertEqual(events[0]["continuity"], "unknown")
        self.assertEqual(len(self.native.arms), 3)
        json.dumps(events, allow_nan=False)

    def test_pending_signal_blocks_expected_write_and_delete_before_worker_runs(self):
        self.start()
        for operation in (lambda snapshot: self.store.write("User", "Path", "new", expected=snapshot),
                          lambda snapshot: self.store.delete("User", "Path", expected=snapshot)):
            with self.store._lock:
                expected = self.store.read("User", "Path")
                self.native.signal("User")
                with self.assertRaises(V3EnvironmentError) as caught:
                    operation(expected)
                self.assertEqual(caught.exception.code, "CONFLICT")
        self.assertFalse(any(call[0] in ("set", "delete") for call in self.registry.calls))

    def test_signal_during_preflight_read_is_drained_before_expected_comparison(self):
        self.start()
        before = self.store.read("User", "Path")
        query = self.registry.QueryValueEx

        def signalled_query(key, name):
            result = query(key, name)
            self.native.signal("User")
            return result

        with mock.patch.object(self.registry, "QueryValueEx", side_effect=signalled_query):
            with self.assertRaises(V3EnvironmentError) as caught:
                self.store.write("User", "Path", "bad", expected=before)
        self.assertEqual(caught.exception.code, "CONFLICT")
        self.assertFalse(any(call[0] == "set" for call in self.registry.calls))

    def test_list_builds_all_revisions_after_signals_during_later_scope_scan(self):
        self.start()
        before = self.store.read("User", "Path")
        enumerate_value = self.registry.EnumValue

        def late_signal(key, index):
            if key.scope == "System" and index == 0:
                self.native.signal("User")
            return enumerate_value(key, index)

        with mock.patch.object(self.registry, "EnumValue", side_effect=late_signal):
            items = self.store.list()["items"]
        path = next(item for item in items if item["scope"] == "User" and item["name"] == "Path")
        self.assertNotEqual(before["revision"], path["revision"])
        self.assertEqual(path, self.store.read("User", "Path"))

    def test_repeated_one_shot_rearm_does_not_reregister_pending_wait(self):
        self.start()
        before = self.store.read("User", "Path")
        for index in range(6):
            with self.store._lock:
                self.native.signal("User")
                after = self.store.read("User", "Path")
                self.assertNotEqual(before["revision"], after["revision"])
                self.store.take_events()
                self.store.list("User")
            self.assertEqual(len(self.native.arms), index + 3)
            before = after

    def test_overlapping_external_non_target_change_is_not_consumed_as_known(self):
        self.start()
        self.registry.native = self.native
        user_other = self.store.read("User", "Other")
        system = self.store.read("System", "Path")
        before = self.store.read("User", "Path")
        # Another writer changes a non-target value inside our Set/readback
        # window. Comparing just the written target would falsely trust this.
        def external_write():
            self.registry.rows["User"][1] = ("Other", "external", self.registry.REG_SZ)
            self.native.signal("User")
        self.registry.during_write = external_write
        after = self.store.write("User", "Path", "verified", expected=before)
        self.assertEqual(after["raw"], "verified")
        self.assertEqual(after, self.store.read("User", "Path"))
        self.assertEqual(system, self.store.read("System", "Path"))
        self.assertNotEqual(user_other["revision"], self.store.read("User", "Other")["revision"])
        event = self.store.take_events()[0]
        self.assertEqual(event["continuity"], "unknown")
        self.assertIn("write_overlap", event["reasons"])

    def test_two_frozen_user_targets_commit_with_real_one_shot_signal_on_every_set(self):
        self.start()
        self.registry.native = self.native
        system = self.store.read("System", "Path")
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "state.sqlite3")
            self.addCleanup(db.close)
            executor = Executor(self.store, db)
            targets = []
            for name, raw in (("Path", "first"), ("Other", "second")):
                before = self.store.read("User", name)
                targets.append({"scope": "User", "name": name, "beforeSnapshot": before,
                    "members": [name], "plan": {
                        "targetKey": {"scope": "User", "name": name},
                        "before": {k: before[k] for k in ("exists", "raw", "type")},
                        "baseRevision": before["revision"],
                        "after": {"exists": True, "raw": raw, "type": before["type"]},
                        "operations": [{"type": "replace"}], "valid": True, "issues": [],
                        "mode": "replace", "special": False, "audit": {"fixture": "frozen"}}})
            receipt = {"planId": "native-user-batch", "version": 1, "targets": targets,
                       "dependencies": {"workspaceRevision": 1}}
            receipt["digest"] = hashlib.sha256(encode(receipt).encode()).hexdigest()
            db.preview(receipt)
            ids = {k: receipt[k] for k in ("planId", "version", "digest")}
            review = executor.review(ids)
            result = executor.execute({**ids, "reviewToken": review["reviewToken"],
                                       "idempotencyKey": "native-two-targets"})
            self.assertEqual(result["status"], "committed", result)
            self.assertEqual([row["status"] for row in result["targets"]], ["verified", "verified"])
            self.assertEqual(len([call for call in self.registry.calls if call[0] == "set"]), 2)
            self.assertEqual(len(db.history()), 1)
            db.close()
        self.assertEqual(system, self.store.read("System", "Path"))
        event = self.store.take_events()[0]
        self.assertEqual(event["continuity"], "known_write")
        self.assertCountEqual(event["targets"], ["PATH", "OTHER"])

    def test_trusted_audit_keeps_inactive_original_occurrence_deletable_after_native_signals(self):
        self.registry.rows["User"][0] = ("Path", "A;;X", self.registry.REG_EXPAND_SZ)
        self.start()
        self.registry.native = self.native
        planner = Planner()
        before = self.store.read("User", "Path")
        other = self.store.read("User", "Other")
        options = {"target": {"scope": "User", "name": "Path"}, "mode": "+append",
                   "snapshot": before, "selectedRaw": "A"}
        baseline = planner.call("captureBaseline", options)
        state = planner.call("createRemovalState", args=[baseline])
        audit = {"before": "A;;X", "after": "Y;A;;X", "whole": False, "elements": [
            {"elementId": "new", "beforeIndex": None, "afterIndex": 0, "after": "Y",
             "added": True, "removed": False, "replaced": False, "moved": False},
            *[{"elementId": f"o:{i}", "beforeIndex": i, "afterIndex": i + 1,
               "before": value, "after": value, "added": False, "removed": False,
               "replaced": False, "moved": False} for i, value in enumerate(["A", "", "X"])]]}
        plan = planner.call("buildElementEdit", {"target": options["target"], "snapshot": before,
                                                "afterRaw": audit["after"], "audit": audit})
        after = self.store.write("User", "Path", audit["after"], expected=before)
        # This is the same known-write bridge used by inactive Workspace sessions.
        prior = planner.call("syncRemovalState", {"baseline": baseline, "state": state,
            "snapshot": {**plan["before"], "revision": plan["baseRevision"]}})
        advanced = planner.advance_trusted_removal_state(baseline=baseline, state=prior,
                                                        plan=plan, snapshot=after)
        self.assertTrue(advanced["entries"][0]["eligible"])
        self.assertEqual(advanced["entries"][0]["currentIndex"], 1)
        # A second same-scope target write must not destroy the first target's
        # new snapshot, or the inactive session would lose its trusted mapping.
        self.store.write("User", "Other", "known-other", expected=other)
        current = self.store.read("User", "Path")
        self.assertEqual(current, after)
        synced = planner.call("syncRemovalState", {"baseline": baseline, "state": advanced,
                                                   "snapshot": current})
        removal = planner.call("build", {**options, "snapshot": current, "selectedRaw": "B",
                                         "baseline": baseline, "removalState": synced})
        self.assertTrue(synced["entries"][0]["eligible"])
        self.assertEqual(removal["after"]["raw"], "B;Y;;X")
        self.assertTrue(any(op["type"] == "remove" for op in removal["operations"]))

    def test_verified_programmatic_write_without_unknown_signal_is_target_local(self):
        self.start()
        other = self.store.read("User", "Other")
        system = self.store.read("System", "Path")
        before = self.store.read("User", "Path")
        after = self.store.write("User", "Path", "known", expected=before)
        self.assertNotEqual(after["revision"], before["revision"])
        self.assertEqual(other, self.store.read("User", "Other"))
        self.assertEqual(system, self.store.read("System", "Path"))
        events = self.store.take_events()
        self.assertEqual(events[0]["continuity"], "known_write")

    def test_known_delete_signal_preserves_frozen_other_target_and_same_value_write(self):
        self.start()
        self.registry.native = self.native
        path = self.store.read("User", "Path")
        other = self.store.read("User", "Other")
        absent = self.store.delete("User", "path", expected=path)
        self.assertFalse(absent["exists"])
        self.assertEqual(other, self.store.read("User", "Other"))
        same = self.store.write("User", "Other", other["raw"], expected=other)
        self.assertEqual(other, same)
        self.assertEqual(absent, self.store.read("User", "path"))
        self.assertEqual(self.store.take_events()[0]["continuity"], "known_write")

    def test_non_target_type_or_name_or_set_changes_make_local_fence_unknown(self):
        for variant in ("type", "name", "add", "delete"):
            with self.subTest(variant=variant):
                # Reuse the live listener, but capture a fresh expected snapshot
                # after each deliberately unknown preceding transition.
                self.registry.rows["User"] = [("Path", "A", self.registry.REG_EXPAND_SZ),
                                              ("Other", "same", self.registry.REG_SZ)]
                if self.store._changes is None:
                    self.start()
                    self.registry.native = self.native
                before = self.store.read("User", "Path")
                other = self.store.read("User", "Other")
                self.store.take_events()
                def overlap():
                    if variant == "type":
                        self.registry.rows["User"][1] = ("Other", "same", self.registry.REG_EXPAND_SZ)
                    elif variant == "name":
                        self.registry.rows["User"][1] = ("oTHER", "same", self.registry.REG_SZ)
                    elif variant == "add":
                        self.registry.rows["User"].append(("New", "outside", self.registry.REG_SZ))
                    else:
                        self.registry.rows["User"].pop(1)
                    self.native.signal("User")
                self.registry.during_write = overlap
                self.store.write("User", "Path", "known-target", expected=before)
                events = self.store.take_events()
                self.assertEqual(events[0]["continuity"], "unknown")
                self.assertIn("write_overlap", events[0]["reasons"])
                self.assertNotEqual(other["revision"], self.store.read("User", "Other")["revision"])

    def test_failed_write_and_failed_readback_never_consume_signal_as_known(self):
        self.start()
        self.registry.native = self.native
        before = self.store.read("User", "Path")
        def fail_after_set():
            raise OSError("mock setter changed value then failed")
        self.registry.during_write = fail_after_set
        with self.assertRaises(V3EnvironmentError):
            self.store.write("User", "Path", "uncertain", expected=before)
        self.assertEqual(self.store.take_events()[0]["continuity"], "unknown")
        self.registry.during_write = None
        before = self.store.read("User", "Path")
        self.registry.during_write = lambda: setattr(self.registry, "enum_error", OSError("mock readback failure"))
        with self.assertRaises(V3EnvironmentError):
            self.store.write("User", "Path", "written", expected=before)
        self.registry.enum_error = None
        event = self.store.take_events()[0]
        self.assertEqual(event["continuity"], "unknown")
        self.assertIn("write_readback_failed", event["reasons"])

    def test_rearm_failure_during_known_write_still_revokes_and_later_signal_is_unknown(self):
        self.start()
        self.registry.native = self.native
        before = self.store.read("User", "Path")
        other = self.store.read("User", "Other")
        self.registry.during_write = lambda: setattr(self.native, "fail_arm_scope", "User")
        self.store.write("User", "Path", "known", expected=before)
        event = self.store.take_events()[0]
        self.assertEqual(event["continuity"], "unknown")
        self.assertIn("listener_interrupted", event["reasons"])
        self.assertNotEqual(other["revision"], self.store.read("User", "Other")["revision"])
        self.assertFalse(self.store.changes_status()["scopes"]["User"])

    def test_event_after_completed_known_write_is_not_credited_to_that_write(self):
        self.start()
        self.registry.native = self.native
        before = self.store.read("User", "Path")
        after = self.store.write("User", "Path", "known", expected=before)
        self.assertEqual(self.store.take_events()[0]["continuity"], "known_write")
        with self.store._lock:
            self.native.signal("User")
            self.assertNotEqual(after["revision"], self.store.read("User", "Path")["revision"])
        self.assertEqual(self.store.take_events()[0]["continuity"], "unknown")

    def test_partial_start_failure_preserves_other_scope_and_cleans_partial_handles(self):
        self.native.fail_arm_scope = "User"
        status = self.store.start_changes(native=self.native)
        self.assertEqual(status["scopes"], {"User": False, "System": True})
        self.assertIn("User", status["errors"])
        events = self.store.take_events()
        self.assertEqual(events[0]["reason"], "listener_start_failed")
        system = self.store.read("System", "Path")
        self.store.invalidate_continuity("User", reason="unavailable")
        self.assertEqual(system, self.store.read("System", "Path"))
        self.store.close()
        self.assertCountEqual(self.native.created, self.native.closed)

    def test_missing_or_denied_key_never_created_or_polled_for_reconnect(self):
        self.registry.fail_scope = "User"
        status = self.store.start_changes(native=self.native)
        self.assertFalse(status["scopes"]["User"])
        self.assertTrue(status["scopes"]["System"])
        self.assertEqual(len(self.native.arms), 1)
        self.assertFalse(any(call[0] in ("set", "delete") for call in self.registry.calls))
        self.assertEqual(len([c for c in self.registry.calls if c[0] == "open"]), 2)

    def test_event_creation_failure_closes_already_acquired_key(self):
        self.native.fail_create_at = 2
        status = self.store.start_changes(native=self.native)
        self.assertFalse(status["running"])
        self.store.close()
        self.assertEqual(self.native.closed, [1])
        self.assertCountEqual([call[1] for call in self.registry.calls if call[0] == "close_watch"],
                              ["User", "System"])

    def test_native_library_failure_is_reported_without_fallback_registry_access(self):
        # Instantiate listener directly to exercise lazy DLL load failure with
        # the same fake registry; never construct a default real winreg adapter.
        emitted = []
        listener = RegistryChangeListener(self.registry,
                    {s: self.store._location(s) for s in ("User", "System")},
                    self.store._lock, lambda *args: emitted.append(args))
        with mock.patch("envmanager_v3.environment_events.NativeRegistryEvents",
                        side_effect=OSError("mock DLL load failure")):
            status = listener.start()
            listener.close()
        self.assertFalse(status["running"])
        self.assertEqual({event[0] for event in emitted}, {"User", "System"})
        self.assertEqual(self.registry.calls, [])

    def test_thread_creation_failure_does_not_open_any_native_resources(self):
        with mock.patch("envmanager_v3.environment_events.threading.Thread.start",
                        side_effect=RuntimeError("mock thread unavailable")):
            status = self.store.start_changes(native=self.native)
        self.assertFalse(status["running"])
        self.assertTrue(status["closed"])
        self.assertEqual(self.registry.calls, [])
        self.assertEqual(self.native.created, [])
        self.assertTrue(all(event["reason"] == "listener_start_failed"
                            for event in self.store.take_events()))

    def test_rearm_reset_and_poll_failures_revoke_only_affected_scope(self):
        for attribute in ("fail_arm_scope", "fail_reset_scope", "fail_poll_scope"):
            with self.subTest(attribute=attribute):
                native = FakeNative()
                self.native = native
                self.start()
                before = self.store.read("User", "Path")
                system = self.store.read("System", "Path")
                with self.store._lock:
                    setattr(native, attribute, "User")
                    native.signal("User")
                    events = self.store.take_events()
                self.assertEqual(events[0]["reason"], "listener_interrupted")
                self.assertFalse(self.store.changes_status()["scopes"]["User"])
                self.assertNotEqual(before["revision"], self.store.read("User", "Path")["revision"])
                self.assertEqual(system, self.store.read("System", "Path"))
                self.store.close()
                self.assertCountEqual(native.created, native.closed)

    def test_wait_failure_revokes_scopes_and_restart_keeps_gap_until_consumed(self):
        self.start()
        before = self.store.read("User", "Path")
        worker = self.store._changes._thread
        self.native.fail_wait()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(self.store.changes_status()["running"])
        self.assertNotEqual(before["revision"], self.store.read("User", "Path")["revision"])
        self.store.close()
        self.native = FakeNative()
        self.store.start_changes(native=self.native)
        events = self.store.take_events()
        self.assertTrue(all("listener_interrupted" in event["reasons"] for event in events))
        self.assertTrue(all("listener_started" in event["reasons"] for event in events))

    def test_stop_and_resume_or_explicit_power_gap_changes_equal_value_revision(self):
        self.start()
        before = self.store.read("User", "Path")
        self.store.close()
        stopped = self.store.read("User", "Path")
        self.assertNotEqual(before["revision"], stopped["revision"])
        self.native = FakeNative()
        self.store.start_changes(native=self.native)
        restarted = self.store.read("User", "Path")
        self.assertNotEqual(stopped["revision"], restarted["revision"])
        self.store.invalidate_continuity(reason="power_resume")
        self.assertNotEqual(restarted["revision"], self.store.read("User", "Path")["revision"])
        self.assertTrue(all("power_resume" in event["reasons"] for event in self.store.take_events()))

    def test_worker_wakes_service_without_adapter_lock_and_callback_failure_preserves_events(self):
        callback_ran = threading.Event()
        lock_free = []

        def callback():
            # A different thread can acquire the adapter lock while this callback
            # runs; an RLock acquired by the callback itself would prove nothing.
            acquired = []
            def probe():
                ok = self.store._lock.acquire(timeout=1)
                acquired.append(ok)
                if ok:
                    self.store._lock.release()
            thread = threading.Thread(target=probe)
            thread.start()
            thread.join(2)
            lock_free.append(acquired == [True])
            callback_ran.set()
            raise RuntimeError("mock callback failure")

        self.start(on_change=callback)
        self.assertTrue(callback_ran.wait(2))
        callback_ran.clear()
        self.native.signal("User")
        self.assertTrue(callback_ran.wait(2))
        self.assertTrue(all(lock_free))
        # Callback failures cannot consume or transform unknown continuity.
        events = self.store.take_events()
        self.assertEqual(events[0]["scope"], "User")
        self.assertEqual(events[0]["reason"], "key_changed")

    def test_failed_stop_does_not_join_or_close_handles_under_native_wait(self):
        self.start()
        self.native.fail_set = True
        status = self.store.close()
        self.assertFalse(status["closed"])
        self.assertFalse(status["running"])
        self.assertEqual(self.native.closed, [])
        self.assertTrue(all(event["reason"] == "listener_stop_failed"
                            for event in self.store.take_events()))
        # Explicit recovery retries the same stop handle, not a replacement.
        self.native.fail_set = False
        self.assertTrue(self.store.close()["closed"])
        self.assertCountEqual(self.native.created, self.native.closed)

    def test_foreground_drain_and_power_gap_still_wake_service(self):
        wake = threading.Event()
        self.start(on_change=wake.set)
        wake.clear()
        with self.store._lock:
            self.native.signal("User")
            # Consume the native event while the worker is held outside its
            # lock. The control wake must survive this foreground drain.
            self.store.read("User", "Path")
        self.assertTrue(wake.wait(2))

        wake.clear()
        self.store.invalidate_continuity("User", reason="power_resume")
        self.assertTrue(wake.wait(2))

    def test_failed_key_cleanup_retains_key_and_event_for_explicit_close_retry(self):
        self.start()
        self.registry.fail_close_scope = "User"
        status = self.store.close()
        self.assertFalse(status["closed"])
        user_event = next(handle for handle, watch in self.native.watches.items()
                          if watch["scope"] == "User")
        self.assertNotIn(user_event, self.native.closed)
        self.assertIn("User", status["errors"])
        self.assertFalse(self.store.start_changes()["closed"])
        self.registry.fail_close_scope = None
        self.assertTrue(self.store.close()["closed"])
        self.assertCountEqual(self.native.created, self.native.closed)

    def test_failed_event_cleanup_keeps_resource_reference_and_reports_gap(self):
        self.start()
        self.native.fail_close = set(self.native.created)
        status = self.store.close()
        self.assertFalse(status["closed"])
        self.assertEqual(self.native.closed, [])
        self.assertTrue(all("listener_cleanup_failed" in event["reasons"]
                            for event in self.store.take_events()))
        self.native.fail_close.clear()
        self.assertTrue(self.store.close()["closed"])
        self.assertCountEqual(self.native.created, self.native.closed)

    def test_callback_can_request_shutdown_without_self_join_or_lifecycle_deadlock(self):
        closed = threading.Event()
        results = []

        def close_from_worker():
            results.append(self.store.close())
            closed.set()

        self.store.start_changes(native=self.native, on_change=close_from_worker)
        self.assertTrue(closed.wait(2))
        worker = self.store._changes._thread
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(self.store.close()["closed"])
        self.assertCountEqual(self.native.created, self.native.closed)
        self.assertTrue(results)

    def test_event_coalescing_is_scope_bounded_and_validated(self):
        for _ in range(100):
            self.store.invalidate_continuity("User", reason="resume")
        events = self.store.take_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["sequence"], 100)
        self.assertEqual(events[0]["reasons"], ["resume"])
        self.assertEqual(self.store.take_events(), [])
        for operation in (lambda: self.store.invalidate_continuity("Host"),
                          lambda: self.store.invalidate_continuity(reason=""),
                          lambda: self.store.start_changes(on_change=3, native=self.native),
                          lambda: self.store.start_changes()):
            with self.assertRaises(V3EnvironmentError):
                operation()
        self.assertEqual(self.registry.calls, [])

    def test_cursor_wait_is_bounded_read_only_and_does_not_consume_event_queue(self):
        cursor = self.store.change_cursor()
        before_calls = list(self.registry.calls)
        unchanged = self.store.wait_for_change(cursor, timeout=0)
        self.assertFalse(unchanged["changed"])
        self.assertFalse(unchanged["reset"])
        self.assertEqual(unchanged["events"], [])
        self.assertEqual(before_calls, self.registry.calls)
        self.store.invalidate_continuity("User", reason="sleep_gap")
        first = self.store.wait_for_change(cursor, timeout=0)
        self.assertTrue(first["changed"])
        self.assertEqual(first["events"][0]["reason"], "sleep_gap")
        self.assertEqual(first, self.store.wait_for_change(cursor, timeout=0))
        self.assertEqual(len(self.store.take_events()), 1)
        self.assertEqual(first, self.store.wait_for_change(cursor, timeout=0))
        self.assertFalse(self.store.wait_for_change(first["cursor"], timeout=0)["changed"])
        self.assertEqual(before_calls, self.registry.calls)

    def test_cursor_waiters_wake_on_gap_without_polling_or_callback(self):
        cursor = self.store.change_cursor()
        started = threading.Barrier(3)
        results = []
        def wait():
            started.wait(2)
            results.append(self.store.wait_for_change(cursor, timeout=2))
        threads = [threading.Thread(target=wait) for _ in range(2)]
        for thread in threads:
            thread.start()
        started.wait(2)
        self.store.invalidate_continuity("System", reason="resume")
        for thread in threads:
            thread.join(3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), 2)
        self.assertTrue(all(result["changed"] for result in results))
        self.assertTrue(all(result["events"][0]["scope"] == "System" for result in results))
        self.assertEqual(self.registry.calls, [])

    def test_cursor_retains_unknown_gap_across_queue_drain_and_later_known_write(self):
        self.start()
        self.registry.native = self.native
        cursor = self.store.change_cursor()
        self.store.invalidate_continuity("User", reason="resume")
        self.store.take_events()
        before = self.store.read("User", "Path")
        self.store.write("User", "Path", "known-after-gap", expected=before)
        self.assertEqual(self.store.take_events()[0]["continuity"], "known_write")
        result = self.store.wait_for_change(cursor, timeout=0)
        self.assertEqual(result["events"][0]["continuity"], "unknown")
        self.assertIn("resume", result["events"][0]["reasons"])

    def test_initial_and_foreign_boot_cursor_reset_both_scopes_and_validate_inputs(self):
        for cursor in (None, "0" * 32 + ":5"):
            reset = self.store.wait_for_change(cursor, timeout=0)
            self.assertTrue(reset["reset"])
            self.assertTrue(reset["changed"])
            self.assertEqual({event["scope"] for event in reset["events"]}, {"User", "System"})
            self.assertTrue(all(event["continuity"] == "unknown" for event in reset["events"]))
        for cursor in (False, 4, b"bad", [], "bad", "0" * 32 + ":-1", self.store._epoch + ":99"):
            with self.subTest(cursor=cursor), self.assertRaises(V3EnvironmentError):
                self.store.wait_for_change(cursor, timeout=0)
        for timeout in (-1, 31, 10 ** 1000, float("inf"), float("nan"), True, "1"):
            with self.subTest(timeout=timeout), self.assertRaises(V3EnvironmentError):
                self.store.wait_for_change(self.store.change_cursor(), timeout=timeout)
        self.assertEqual(self.registry.calls, [])


class NativeCallTests(unittest.TestCase):
    def setUp(self):
        self.kernel = mock.Mock()
        self.advapi = mock.Mock()
        self.kernel.CreateEventW.return_value = 0x123456789
        for name in ("SetEvent", "ResetEvent", "CloseHandle"):
            getattr(self.kernel, name).return_value = 1
        self.kernel.WaitForSingleObject.return_value = 258
        self.kernel.WaitForMultipleObjects.return_value = 1
        self.advapi.RegNotifyChangeKeyValue.return_value = 0
        self.dlls = mock.patch("envmanager_v3.environment_events.ctypes.WinDLL", create=True,
                              side_effect=[self.kernel, self.advapi])
        self.load = self.dlls.start()
        self.addCleanup(self.dlls.stop)
        self.backend = NativeRegistryEvents()

    def test_actual_ctypes_wrapper_signatures_handles_filters_and_wait(self):
        handle = self.backend.create_event()
        self.assertGreater(handle, 0xFFFFFFFF)
        self.assertFalse(self.backend.is_set(handle))
        self.kernel.WaitForSingleObject.return_value = 0
        self.assertTrue(self.backend.is_set(handle))
        self.backend.reset_event(handle)
        self.backend.arm(0x234567890, handle)
        self.assertEqual(self.advapi.RegNotifyChangeKeyValue.call_args.args,
                         (0x234567890, False, 0x1000000C, handle, True))
        self.assertEqual(ctypes.sizeof(self.advapi.RegNotifyChangeKeyValue.argtypes[0]),
                         ctypes.sizeof(ctypes.c_void_p))
        self.assertEqual(self.backend.wait([handle, handle + 1]), 1)
        args = self.kernel.WaitForMultipleObjects.call_args.args
        self.assertEqual((args[0], list(args[1]), args[2], args[3]),
                         (2, [handle, handle + 1], False, 0xFFFFFFFF))
        self.backend.set_event(handle)
        self.backend.close_event(handle)
        self.load.assert_has_calls([mock.call("kernel32", use_last_error=True),
                                   mock.call("advapi32", use_last_error=True)])

    def test_lstatus_and_win32_failure_results_use_correct_error_source(self):
        # Patch error factories too so this test remains runnable off Windows.
        with mock.patch("envmanager_v3.environment_events.ctypes.WinError", create=True,
                        side_effect=lambda code: OSError(f"native error {code}")), \
             mock.patch("envmanager_v3.environment_events.ctypes.get_last_error", create=True,
                        return_value=6):
            self.advapi.RegNotifyChangeKeyValue.return_value = 5
            with self.assertRaisesRegex(OSError, "native error 5"):
                self.backend.arm(1, 2)
            for name, operation, result in (
                    ("CreateEventW", self.backend.create_event, 0),
                    ("SetEvent", lambda: self.backend.set_event(2), 0),
                    ("ResetEvent", lambda: self.backend.reset_event(2), 0),
                    ("CloseHandle", lambda: self.backend.close_event(2), 0),
                    ("WaitForSingleObject", lambda: self.backend.is_set(2), 0xFFFFFFFF),
                    ("WaitForMultipleObjects", lambda: self.backend.wait([1, 2]), 0xFFFFFFFF)):
                with self.subTest(name=name):
                    getattr(self.kernel, name).return_value = result
                    with self.assertRaisesRegex(OSError, "native error 6"):
                        operation()


if __name__ == "__main__":
    unittest.main()

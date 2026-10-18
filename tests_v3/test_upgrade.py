"""Real temporary files, SQLite/WAL, production locks and helper processes only."""
from contextlib import closing, redirect_stdout, ExitStack
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlparse

from envmanager_v3.__main__ import InstanceLock
from envmanager_v3.database import Database
from envmanager_v3 import upgrade


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "用户 data"
        self.data.mkdir()
        self.db_path = self.data / "state.sqlite3"
        db = Database(self.db_path)
        db.set("workspace", {"draft": " A\r\n;;A; ", "id": "stable-id"})
        db.set("configDraft", {"invalid": "[unfinished", "baseline": "raw\r\n"})
        db.execution("e1", "p1", "request1", "committed", {"recovery": "do not lose"})
        db.append_history("h1", "e1", {"raw": "history\r\n"})
        db.close()
        (self.data / "instance.lock").write_bytes(b"0")
        for relative, content in {
            "workspaces/uuid/config.toml": b"# raw\r\nvalue = ' A;;A; '\r\n",
            "drafts/invalid.toml": b"[unfinished\r\n",
            "history/h.json": b'{"immutable":true}',
            "system-jobs/job/recovery.json": b'{"uncertain":true}',
            "recoveryfiles/receipt.bin": bytes(range(256)),
            "custom/unknown-extension.xyz": b"keep unknown files too",
        }.items():
            path = self.data / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        (self.data / "empty").mkdir()

    def prepare(self, **kwargs):
        session = upgrade.prepare_upgrade(self.data, "3.1.0", **kwargs)
        self.addCleanup(session.close)
        return session

    def assert_blocked(self, code, callable_, *args, **kwargs):
        with self.assertRaises(upgrade.UpgradeBlocked) as caught:
            callable_(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def data_bytes(self):
        return {p.relative_to(self.data).as_posix(): p.read_bytes() for p in self.data.rglob("*")
                if p.is_file() and p.name != "instance.lock" and not p.name.endswith("-shm")}

    def test_complete_backup_preserves_raw_configuration_drafts_history_recovery_and_empty_dirs(self):
        before = self.data_bytes()
        session = self.prepare()
        manifest = upgrade.verify_backup(session.backup_path, session.manifest_sha256)
        self.assertEqual(before, self.data_bytes())
        self.assertEqual(manifest["target_version"], "3.1.0")
        self.assertEqual(manifest["target_schema_supported"], 1)
        self.assertEqual(manifest["sqlite"]["state.sqlite3"]["user_version"], 1)
        for name, content in before.items():
            if name != "state.sqlite3":
                self.assertEqual((session.backup_path / "data" / name).read_bytes(), content)
        self.assertTrue((session.backup_path / "data/empty").is_dir())
        with closing(sqlite3.connect(session.backup_path / "data/state.sqlite3")) as db:
            settings = dict(db.execute("SELECT key,value FROM settings"))
            self.assertEqual(json.loads(settings["workspace"])["draft"], " A\r\n;;A; ")
            self.assertEqual(json.loads(settings["configDraft"])["invalid"], "[unfinished")
            self.assertEqual(db.execute("SELECT count(*) FROM history").fetchone()[0], 1)
        for relative, item in manifest["files"].items():
            self.assertEqual(hashlib.sha256((session.backup_path / "data" / relative).read_bytes()).hexdigest(), item["sha256"])

    def test_committed_live_wal_is_included_without_touching_source_or_opening_source_sqlite(self):
        db = Database(self.db_path)
        self.addCleanup(db.close)
        db.connection.execute("PRAGMA wal_autocheckpoint=0")
        db.set("live-wal-only", {"raw": "committed WAL"})
        self.assertGreater(Path(str(self.db_path) + "-wal").stat().st_size, 0)
        before = self.data_bytes()
        real_connect = sqlite3.connect
        connections = []

        def connect(path, *args, **kwargs):
            connections.append(str(path))
            return real_connect(path, *args, **kwargs)

        with patch.object(upgrade.sqlite3, "connect", side_effect=connect):
            session = self.prepare()
        self.assertEqual(before, self.data_bytes())
        for name in connections:
            if name == ":memory:":
                continue
            parsed = urlparse(name)
            opened = Path(unquote(parsed.path).lstrip("/") if os.name == "nt" else unquote(parsed.path)) if name.startswith("file:") else Path(name)
            self.assertFalse(opened.resolve().is_relative_to(self.data.resolve()), name)
        with closing(sqlite3.connect(session.backup_path / "data/state.sqlite3")) as copy:
            self.assertEqual(json.loads(copy.execute("SELECT value FROM settings WHERE key='live-wal-only'").fetchone()[0]), {"raw": "committed WAL"})
        self.assertFalse((session.backup_path / "data/state.sqlite3-wal").exists())
        self.assertIn("state.sqlite3-wal", upgrade.verify_backup(session.backup_path)["normalized_sqlite_sidecars"])

    def test_uncommitted_wal_data_is_not_in_backup(self):
        db = Database(self.db_path)
        try:
            db.connection.execute("BEGIN IMMEDIATE")
            db.connection.execute("INSERT INTO settings VALUES('not_committed','123')")
            session = self.prepare()
            with closing(sqlite3.connect(session.backup_path / "data/state.sqlite3")) as copy:
                self.assertIsNone(copy.execute("SELECT value FROM settings WHERE key='not_committed'").fetchone())
        finally:
            db.connection.execute("ROLLBACK")
            db.close()

    def test_production_instance_lock_blocks_prepare_before_any_backup(self):
        root = self.root / "backups"
        with InstanceLock(self.data / "instance.lock"):
            self.assert_blocked("INSTANCE_ACTIVE", upgrade.prepare_upgrade, self.data, "3.1", backup_root=root)
        self.assertFalse(root.exists())

    def test_backup_session_continues_to_block_real_production_lock_until_finish(self):
        session = self.prepare()
        with self.assertRaises(RuntimeError):
            with InstanceLock(self.data / "instance.lock"):
                self.fail("Lock was released after backup")
        reply = session.finish()
        self.assertEqual(reply["result"], "finished")
        self.assertEqual(reply["outcome"], "installed")
        self.assertTrue((session.backup_path / "finish.json").is_file())
        with InstanceLock(self.data / "instance.lock"):
            pass
        self.assert_blocked("SESSION_CLOSED", session.check)

    def test_two_upgrades_cannot_overlap(self):
        self.prepare()
        self.assert_blocked("INSTANCE_ACTIVE", upgrade.prepare_upgrade, self.data, "4.0")

    def test_cancel_keeps_backup_and_releases_lock(self):
        session = self.prepare()
        self.assertEqual(session.finish("cancelled")["outcome"], "cancelled")
        upgrade.verify_backup(session.backup_path)
        with InstanceLock(self.data / "instance.lock"):
            pass

    def test_context_exit_releases_lock_without_claiming_install_completed(self):
        with upgrade.prepare_upgrade(self.data, "3.1") as session:
            path = session.backup_path
        self.assertFalse((path / "finish.json").exists())
        with InstanceLock(self.data / "instance.lock"):
            pass

    def test_unfinished_execution_blocks_without_reconciling_or_migrating(self):
        db = Database(self.db_path)
        db.execution("pending", "p", "pending", "recovery_required", {"raw": "evidence"})
        db.close()
        before = self.data_bytes()
        self.assert_blocked("UNFINISHED_EXECUTION", self.prepare)
        self.assertEqual(before, self.data_bytes())
        self.assertFalse(list(self.root.glob("*.upgrade-backups/*/manifest.json")))
        with InstanceLock(self.data / "instance.lock"):
            pass

    def test_future_schema_and_corrupt_database_block_without_source_changes(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("PRAGMA user_version=99")
        before = self.data_bytes()
        self.assert_blocked("BACKUP_FAILED", self.prepare)
        self.assertEqual(before, self.data_bytes())
        self.db_path.write_bytes(b"not sqlite")
        self.assert_blocked("BACKUP_FAILED", self.prepare)
        self.assertEqual(self.db_path.read_bytes(), b"not sqlite")

    def test_legacy_schema_backup_does_not_migrate_user_version(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("PRAGMA user_version=0")
        session = self.prepare()
        self.assertEqual(upgrade.verify_backup(session.backup_path)["sqlite"]["state.sqlite3"]["user_version"], 0)
        with closing(sqlite3.connect(self.db_path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 0)

    def test_additional_sqlite_database_is_normalized_and_reported(self):
        other = self.data / "history/archive.db"
        with closing(sqlite3.connect(other)) as db:
            db.execute("CREATE TABLE archive(raw TEXT)")
            db.execute("INSERT INTO archive VALUES('keep')")
            db.commit()
        session = self.prepare()
        self.assertEqual(upgrade.verify_backup(session.backup_path)["sqlite"]["history/archive.db"]["user_version"], 0)

    def test_config_only_data_is_backed_up_and_missing_directory_is_not_created(self):
        self.db_path.unlink()
        session = self.prepare()
        self.assertEqual(upgrade.verify_backup(session.backup_path)["sqlite"], {})
        absent = self.root / "administrator-guessed-profile"
        self.assert_blocked("NO_DATA_DIRECTORY", upgrade.prepare_upgrade, absent, "3.1")
        self.assertFalse(absent.exists())

    def test_lock_and_explicit_temporary_directories_are_excluded_but_not_recovery(self):
        (self.data / "temp").mkdir()
        (self.data / "temp/junk").write_text("temporary")
        (self.data / "custom/cache.lock").write_bytes(b"lock")
        session = self.prepare()
        manifest = upgrade.verify_backup(session.backup_path)
        self.assertIn("temp/", manifest["excluded_transients"])
        self.assertIn("instance.lock", manifest["excluded_transients"])
        self.assertIn("custom/cache.lock", manifest["excluded_transients"])
        self.assertTrue((session.backup_path / "data/system-jobs/job/recovery.json").exists())
        self.assertFalse((session.backup_path / "data/temp").exists())

    def test_rejects_backup_and_control_inside_source_before_recursive_copy(self):
        self.assert_blocked("INVALID_PATH", self.prepare, backup_root=self.data / "backups")
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            result = upgrade.main(["hold", "--data-dir", str(self.data), "--target-version", "3.1",
                                   "--session-dir", str(self.data / "control")])
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(stdout.getvalue())["result"], "blocked")
        self.assertFalse((self.data / "control").exists())

    def test_symlink_or_junction_data_is_refused(self):
        path = self.data / "linked"
        if os.name == "nt":
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(path), str(self.root / "outside")],
                                    capture_output=True)
            if result.returncode:
                self.skipTest("Junction creation unavailable")
        else:
            path.symlink_to(self.root, target_is_directory=True)
        self.assert_blocked("REPARSE_POINT", self.prepare)

    def test_failure_injections_do_not_publish_success_or_modify_source_and_release_lock(self):
        for point in ("before_copy", "after_copy", "before_sqlite_backup", "after_sqlite_backup", "before_manifest", "before_publish"):
            with self.subTest(point=point):
                before = self.data_bytes()

                def fail(stage, session):
                    if stage == point:
                        raise OSError("injected disk-full/denied")

                self.assert_blocked("BACKUP_FAILED", self.prepare, fault_injector=fail)
                self.assertEqual(before, self.data_bytes())
                with InstanceLock(self.data / "instance.lock"):
                    pass
        root = self.data.with_name(self.data.name + ".upgrade-backups")
        self.assertFalse([p for p in root.iterdir() if not p.name.startswith(".incomplete-")])
        self.assertTrue(list(root.glob(".incomplete-*/failure.json")))

    def test_actual_copy_error_blocks(self):
        before = self.data_bytes()
        with patch.object(upgrade, "_copy", side_effect=OSError("disk full")):
            self.assert_blocked("BACKUP_FAILED", self.prepare)
        self.assertEqual(before, self.data_bytes())

    def test_manifest_fsync_failure_blocks_publication(self):
        original = upgrade._write_json

        def fail(path, value):
            if path.name == "manifest.json":
                raise OSError("fsync failed")
            return original(path, value)

        with patch.object(upgrade, "_write_json", side_effect=fail):
            self.assert_blocked("BACKUP_FAILED", self.prepare)
        self.assertFalse(list(self.root.glob("*.upgrade-backups/*/manifest.json")))

    def test_external_file_change_during_backup_is_refused(self):
        def mutate(stage, session):
            if stage == "after_copy":
                (self.data / "drafts/invalid.toml").write_bytes(b"externally changed")

        self.assert_blocked("SOURCE_CHANGED", self.prepare, fault_injector=mutate)
        self.assertEqual((self.data / "drafts/invalid.toml").read_bytes(), b"externally changed")

    def test_external_wal_commit_during_backup_is_refused(self):
        db = Database(self.db_path)
        try:
            db.set("wal", "before")

            def mutate(stage, session):
                if stage == "after_sqlite_backup":
                    db.set("wal", "after")

            self.assert_blocked("SOURCE_CHANGED", self.prepare, fault_injector=mutate)
            self.assertEqual(db.get("wal"), "after")
        finally:
            db.close()

    def test_external_change_after_ready_poison_session_even_if_file_is_restored(self):
        session = self.prepare()
        path = self.data / "drafts/invalid.toml"
        original = path.read_bytes()
        path.write_bytes(b"changed")
        self.assert_blocked("SOURCE_CHANGED", session.check)
        path.write_bytes(original)
        self.assert_blocked("SOURCE_CHANGED", session.finish)
        self.assertTrue(session.closed)
        self.assertFalse((session.backup_path / "finish.json").exists())

    def test_new_or_deleted_file_during_hold_blocks(self):
        for action in ("add", "delete"):
            with self.subTest(action=action):
                session = self.prepare()
                path = self.data / "new-file"
                if action == "add":
                    path.write_bytes(b"new")
                else:
                    path.unlink()
                self.assert_blocked("SOURCE_CHANGED", session.check)
                session.close()

    def test_backup_tamper_blocks_finish(self):
        session = self.prepare()
        (session.backup_path / "data/drafts/invalid.toml").write_bytes(b"corrupt")
        self.assert_blocked("BACKUP_CHANGED", session.finish)
        self.assertFalse((session.backup_path / "finish.json").exists())

    def test_manifest_tamper_and_added_backup_file_are_detected(self):
        session = self.prepare()
        (session.backup_path / "data/extra.lock").write_bytes(b"unexpected")
        self.assert_blocked("BACKUP_CHANGED", session.check)
        session.close()
        session = self.prepare()
        (session.backup_path / "manifest.json").write_text("{}")
        self.assert_blocked("BACKUP_CHANGED", session.check)

    def test_lease_limits_and_expiry_block_success(self):
        for lease in (0, -1, float("nan"), float("inf"), 7201):
            self.assert_blocked("INVALID_LEASE", self.prepare, lease_seconds=lease)
        session = self.prepare()
        session.deadline = time.monotonic() - 1
        self.assert_blocked("LEASE_EXPIRED", session.finish)
        with InstanceLock(self.data / "instance.lock"):
            pass

    def test_finish_record_failure_releases_lock_but_does_not_return_success(self):
        session = self.prepare()
        with patch.object(upgrade, "_write_json", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                session.finish()
        self.assertTrue(session.closed)
        self.assertFalse((session.backup_path / "finish.json").exists())

    def helper(self, lease=180, *, extra_args=()):
        control = self.root / ("session-" + str(time.time_ns()))
        # The old 15s discovery wait was shorter than the production 120s
        # preparation budget and discarded the child's phase/stdout/stderr.
        # File-backed output avoids a pipe backpressure deadlock; slow children
        # dump Python stacks every 10s so a future timeout identifies the wait.
        stdout_path, stderr_path = self.root / (control.name + ".stdout"), self.root / (control.name + ".stderr")
        bootstrap = "import faulthandler,runpy; faulthandler.dump_traceback_later(10,repeat=True); runpy.run_module('envmanager_v3.upgrade',run_name='__main__')"
        command = [sys.executable, "-c", bootstrap, "hold", "--data-dir", str(self.data),
                   "--target-version", "3.1.0", "--session-dir", str(control), "--lease-seconds", str(lease), *extra_args]
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = subprocess.Popen(command, cwd=Path(__file__).resolve().parents[1], stdout=stdout, stderr=stderr)

        def cleanup():
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)

        self.addCleanup(cleanup)
        started = time.monotonic()
        deadline = started + 125  # CLI prepare-timeout=120 plus process startup

        def diagnostics():
            phase = (control / "progress.json").read_text() if (control / "progress.json").exists() else "before mailbox creation"
            return (f"pid={process.pid} exit={process.poll()} elapsed={time.monotonic()-started:.3f}s\n"
                    f"phase={phase}\nstdout={stdout_path.read_text(errors='replace')}\n"
                    f"stderr/stacks={stderr_path.read_text(errors='replace')}")

        while time.monotonic() < deadline:
            if (control / "ready.json").exists():
                return process, control
            if process.poll() is not None:
                self.fail("Helper exited before ready: " + diagnostics())
            if (control / "terminal.json").exists():
                self.fail("Helper blocked before ready: " + diagnostics())
            time.sleep(0.02)
        process.kill()
        process.wait(timeout=10)
        self.fail("Helper exceeded production preparation budget: " + diagnostics())

    def test_real_helper_protocol_status_finish_and_replacement_lock(self):
        process, control = self.helper()
        ready = json.loads((control / "ready.json").read_text())
        self.assertEqual(ready["result"], "ready")
        self.assertNotIn("ticket", json.dumps(ready).lower())
        with self.assertRaises(RuntimeError):
            with InstanceLock(self.data / "instance.lock"):
                pass
        result = subprocess.run([sys.executable, "-m", "envmanager_v3.upgrade", "status", "--session-dir", str(control)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(json.loads(result.stdout)["backup_id"], ready["backup_id"])
        reply = upgrade.request_session(control, "finish", outcome="installed")
        self.assertEqual(reply["result"], "finished")
        self.assertEqual(process.wait(timeout=10), 0)
        with InstanceLock(self.data / "instance.lock"):
            pass
        self.assert_blocked("SESSION_CLOSED", upgrade.request_session, control)

    def test_killed_helper_cannot_be_authorized_by_stale_ready_json(self):
        process, control = self.helper()
        process.kill()
        process.wait(timeout=10)
        self.assertTrue((control / "ready.json").is_file())
        self.assert_blocked("HELPER_UNRESPONSIVE", upgrade.request_session, control, timeout=0.2)
        with InstanceLock(self.data / "instance.lock"):
            pass

    def test_helper_expires_without_installer_and_releases_lock(self):
        process, control = self.helper(lease=1.5)
        self.assertEqual(process.wait(timeout=10), 1)
        terminal = json.loads((control / "terminal.json").read_text())
        self.assertEqual(terminal["result"], "blocked")
        self.assertEqual(terminal["error_code"], "LEASE_EXPIRED")
        self.assert_blocked("LEASE_EXPIRED", upgrade.request_session, control)
        with InstanceLock(self.data / "instance.lock"):
            pass

    def test_helper_detects_external_change_after_ready_and_never_finishes_successfully(self):
        process, control = self.helper()
        (self.data / "drafts/invalid.toml").write_bytes(b"external edit")
        self.assertEqual(process.wait(timeout=10), 1)
        self.assert_blocked("SOURCE_CHANGED", upgrade.request_session, control)
        ready = json.loads((control / "ready.json").read_text())
        self.assertFalse((Path(ready["backup_path"]) / "finish.json").exists())

    def test_failed_cli_prepare_reports_blocked_without_ready_or_ticket(self):
        control = self.root / "control"
        with InstanceLock(self.data / "instance.lock"):
            result = subprocess.run([sys.executable, "-m", "envmanager_v3.upgrade", "prepare",
                                     "--data-dir", str(self.data), "--target-version", "3.1",
                                     "--session-dir", str(control)], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 1)
        reply = json.loads(result.stdout)
        self.assertEqual(reply["error_code"], "INSTANCE_ACTIVE")
        self.assertEqual(reply["result"], "blocked")
        self.assertNotIn("ticket", result.stdout.lower())
        self.assertFalse((control / "ready.json").exists())

    def test_existing_control_directory_is_not_reused_or_modified(self):
        control = self.root / "existing"
        control.mkdir()
        (control / "keep").write_bytes(b"existing")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(upgrade.main(["hold", "--data-dir", str(self.data), "--target-version", "3.1",
                                           "--session-dir", str(control)]), 1)
        self.assertEqual(list(control.iterdir()), [control / "keep"])

    def test_allow_missing_is_explicit_and_records_first_install_without_existing_data(self):
        data = self.root / "first-install"
        with upgrade.prepare_upgrade(data, "3.1", allow_missing=True) as session:
            self.assertFalse(session.receipt()["source_data_existed"])
            self.assertFalse(upgrade.verify_backup(session.backup_path)["source_data_existed"])
            self.assertEqual(list(data.iterdir()), [data / "instance.lock"])
            self.assertEqual(upgrade.verify_backup(session.backup_path)["files"], {})
            with self.assertRaises(RuntimeError):
                with InstanceLock(data / "instance.lock"):
                    pass

    @unittest.skipUnless(os.name == "nt", "Windows Known Folder API")
    def test_known_folder_ignores_localappdata_environment_spoof(self):
        actual = upgrade.default_data_directory()
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root / "admin-spoof"), "USERPROFILE": str(self.root / "another-spoof")}):
            self.assertEqual(upgrade.default_data_directory(), actual)
        self.assertEqual(actual.name, "EnvManagerV3")
        self.assertTrue(actual.parent.is_dir())

    def test_omitted_data_dir_cli_uses_resolver_not_admin_environment(self):
        data = self.root / "original-user-data"
        control = self.root / "default-control"
        output = io.StringIO()
        # Exercise CLI path selection and a genuine first-install backup/lock;
        # the foreground serve loop alone is replaced to return immediately.
        def serve(session, mailbox):
            self.assertEqual(session.data_dir, data)
            self.assertFalse(session.source_data_existed)
            self.assertEqual(session.check()["result"], "ready")
            return 0
        with patch.object(upgrade, "default_data_directory", return_value=data) as resolver, \
                patch.object(upgrade, "_serve", side_effect=serve), redirect_stdout(output):
            self.assertEqual(upgrade.main(["hold", "--target-version", "3.1", "--session-dir", str(control)]), 0)
        resolver.assert_called_once_with()

    def test_publish_rename_error_blocks_upgrade(self):
        original = upgrade._replace
        def replace(source, destination):
            if source.is_dir():
                raise OSError("write-through rename failed")
            return original(source, destination)
        with patch.object(upgrade, "_replace", side_effect=replace):
            self.assert_blocked("BACKUP_FAILED", self.prepare)
        with InstanceLock(self.data / "instance.lock"):
            pass

    def test_large_manifest_is_not_limited_like_control_messages(self):
        session = self.prepare()
        path = session.backup_path / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["provenance_note"] = "x" * (1024 * 1024 + 1)
        upgrade._write_json(path, manifest)
        upgrade.verify_backup(session.backup_path)
        self.assert_blocked("BACKUP_CHANGED", session.check)

    def test_elevated_cli_is_refused_before_any_directory_or_known_folder_resolution(self):
        for command in ("hold", "prepare"):
            control = self.root / (command + "-refused")
            output = io.StringIO()
            error = upgrade.UpgradeBlocked("ORIGINAL_USER_REQUIRED", "elevated test token")
            with patch.object(upgrade, "require_unelevated", side_effect=error), \
                    patch.object(upgrade, "default_data_directory") as resolver, \
                    patch.object(upgrade, "prepare_upgrade") as prepare, redirect_stdout(output):
                code = upgrade.main([command, "--target-version", "3.1", "--session-dir", str(control), "--require-unelevated"])
            self.assertEqual(code, 1)
            reply = json.loads(output.getvalue())
            self.assertEqual(reply["error_code"], "ORIGINAL_USER_REQUIRED")
            self.assertEqual(reply["result"], "blocked")
            self.assertIn("backup_id", reply)
            self.assertIn("backup_path", reply)
            self.assertFalse(control.exists())
            resolver.assert_not_called()
            prepare.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows token API")
    def test_real_current_token_can_be_inspected_without_uac_or_registry(self):
        try:
            upgrade.require_unelevated()
        except upgrade.UpgradeBlocked as error:
            self.assertEqual(error.code, "ORIGINAL_USER_REQUIRED")
            self.assertIn("administrator fallback", str(error))

    def test_explicit_cli_data_path_still_checks_unelevated_before_creating_mailbox(self):
        control = self.root / "checked-mailbox"
        events = []
        def validate():
            self.assertFalse(control.exists())
            events.append("identity")
        def serve(session, mailbox):
            self.assertEqual(events, ["identity"])
            self.assertEqual(session.check()["result"], "ready")
            return 0
        with patch.object(upgrade, "require_unelevated", side_effect=validate), \
                patch.object(upgrade, "_serve", side_effect=serve):
            self.assertEqual(upgrade.main(["hold", "--data-dir", str(self.data), "--target-version", "3.1",
                                           "--session-dir", str(control), "--require-unelevated"]), 0)

    def test_cli_explicit_first_install_holds_lock_and_finishes_through_real_helper(self):
        absent = self.root / "first-install-process"
        control = self.root / "first-install-mailbox"
        command = [sys.executable, "-m", "envmanager_v3.upgrade", "hold", "--data-dir", str(absent),
                   "--target-version", "3.1", "--session-dir", str(control)]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 10
            while not (control / "ready.json").exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertIsNone(process.poll())
            reply = upgrade.request_session(control)
            self.assertFalse(reply["source_data_existed"])
            with self.assertRaises(RuntimeError):
                with InstanceLock(absent / "instance.lock"):
                    pass
            self.assertEqual(upgrade.request_session(control, "finish", outcome="cancelled")["outcome"], "cancelled")
            self.assertEqual(process.wait(timeout=10), 0)
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)

    def test_invalid_finish_outcome_cannot_claim_success(self):
        process, control = self.helper()
        self.assert_blocked("INVALID_OUTCOME", upgrade.request_session, control, "finish", outcome="invented")
        self.assertEqual(process.wait(timeout=10), 1)

    def test_sqlite_backup_api_failure_blocks_without_modifying_database_or_wal(self):
        before = self.data_bytes()
        with patch.object(upgrade, "_sqlite_backup", side_effect=sqlite3.DatabaseError("injected I/O error")):
            self.assert_blocked("BACKUP_FAILED", self.prepare)
        self.assertEqual(before, self.data_bytes())

    def test_finish_client_timeout_cannot_fall_back_to_existing_ready(self):
        process, control = self.helper()
        process.kill()
        process.wait(timeout=10)
        self.assert_blocked("HELPER_UNRESPONSIVE", upgrade.request_session, control, "finish", outcome="installed", timeout=0.1)
        ready = json.loads((control / "ready.json").read_text())
        self.assertFalse((Path(ready["backup_path"]) / "finish.json").exists())

    def test_cancel_marker_is_consumed_during_pre_ready_copy(self):
        control = self.root / "pre-ready-cancel"
        original = upgrade._copy
        def copy(source, destination, check):
            (control / "cancel.json").write_text("{}")
            return original(source, destination, check)
        output = io.StringIO()
        before = self.data_bytes()
        with patch.object(upgrade, "_copy", side_effect=copy), redirect_stdout(output):
            result = upgrade.main(["hold", "--data-dir", str(self.data), "--target-version", "3.1", "--session-dir", str(control)])
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(output.getvalue())["error_code"], "CANCELLED")
        self.assertFalse((control / "ready.json").exists())
        self.assertEqual(json.loads((control / "progress.json").read_text())["stage"], "before_copy")
        self.assertEqual(before, self.data_bytes())
        with InstanceLock(self.data / "instance.lock"):
            pass

    def test_cancel_marker_stops_ready_helper_without_claiming_finish(self):
        process, control = self.helper()
        (control / "cancel.json").write_text("{}")
        self.assertEqual(process.wait(timeout=10), 1)
        self.assert_blocked("CANCELLED", upgrade.request_session, control)
        with InstanceLock(self.data / "instance.lock"):
            pass

    def test_prepare_timeout_blocks_before_ready_and_releases_lock(self):
        control = self.root / "prepare-timeout"
        output = io.StringIO()
        with redirect_stdout(output):
            result = upgrade.main(["hold", "--data-dir", str(self.data), "--target-version", "3.1",
                                   "--session-dir", str(control), "--prepare-timeout", "0.0000001"])
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(output.getvalue())["error_code"], "PREPARE_TIMEOUT")
        self.assertFalse((control / "ready.json").exists())
        with InstanceLock(self.data / "instance.lock"):
            pass

    @unittest.skipUnless(os.name == "nt", "Windows process handles")
    def test_parent_handle_detects_exit_during_preparation_and_releases_lock(self):
        parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        monitor = upgrade._ParentProcess(parent.pid)
        try:
            monitor.check()
            def terminate(stage, session):
                if stage == "before_copy":
                    parent.kill()
                    parent.wait(timeout=10)
            self.assert_blocked("PARENT_EXITED", self.prepare, cancellation_check=monitor.check, fault_injector=terminate)
            with InstanceLock(self.data / "instance.lock"):
                pass
        finally:
            monitor.close()
            if parent.poll() is None:
                parent.kill()
            parent.wait(timeout=10)

    @unittest.skipUnless(os.name == "nt", "Windows process handles")
    def test_real_helper_stops_when_installer_process_exits(self):
        parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            process, control = self.helper(extra_args=("--parent-pid", str(parent.pid)))
            self.assertEqual(upgrade.request_session(control)["result"], "ready")
            parent.kill()
            parent.wait(timeout=10)
            self.assertEqual(process.wait(timeout=10), 1)
            self.assert_blocked("PARENT_EXITED", upgrade.request_session, control)
            with InstanceLock(self.data / "instance.lock"):
                pass
        finally:
            if parent.poll() is None:
                parent.kill()
            parent.wait(timeout=10)


class CheckUserTests(unittest.TestCase):
    """Identity preflight must return before any data or control-state work."""

    def check_user(self, error=None):
        output = io.StringIO()
        with ExitStack() as stack:
            guard = stack.enter_context(patch.object(upgrade, "require_unelevated", side_effect=error))
            unused = [stack.enter_context(patch.object(upgrade, name)) for name in (
                "default_data_directory", "_local_path", "prepare_upgrade", "_ParentProcess",
                "request_session", "_write_json", "_serve")]
            unused.append(stack.enter_context(patch.object(Path, "mkdir")))
            unused.append(stack.enter_context(patch.object(sqlite3, "connect")))
            # Product dispatch imports these classes but must never construct them.
            from envmanager_v3 import __main__ as product
            unused.extend(stack.enter_context(patch.object(product, name))
                          for name in ("Service", "WindowsEnvironment", "FileEnvironment", "create_server"))
            with redirect_stdout(output):
                result = product.main(["--upgrade-helper", "check-user"])
            guard.assert_called_once_with()
            for operation in unused:
                operation.assert_not_called()
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        self.assertNotIn("ticket", output.getvalue().lower())
        return result, json.loads(output.getvalue())

    def test_allowed_identity_returns_passed_without_creating_state(self):
        result, reply = self.check_user()
        self.assertEqual(result, 0)
        self.assertEqual(reply, {"protocol_version": 1, "result": "passed"})

    def test_elevated_or_unreadable_token_returns_immediate_blocked_without_state(self):
        for message in ("elevated or FakeAdminRights token", "Cannot read TokenElevation."):
            with self.subTest(message=message):
                result, reply = self.check_user(upgrade.UpgradeBlocked("ORIGINAL_USER_REQUIRED", message))
                self.assertEqual(result, 1)
                self.assertEqual(reply["result"], "blocked")
                self.assertEqual(reply["error_code"], "ORIGINAL_USER_REQUIRED")
                self.assertEqual(reply["message"], message)
                self.assertIsNone(reply["backup_id"])
                self.assertIsNone(reply["backup_path"])

    @unittest.skipUnless(os.name == "nt", "Windows token API")
    def test_real_product_cli_reports_current_token_result(self):
        try:
            upgrade.require_unelevated()
            expected = 0
        except upgrade.UpgradeBlocked:
            expected = 1
        process = subprocess.run(
            [sys.executable, "-m", "envmanager_v3", "--upgrade-helper", "check-user"],
            cwd=Path(__file__).resolve().parents[1], capture_output=True,
            text=True, encoding="utf-8", timeout=15)
        self.assertEqual(process.returncode, expected, process.stdout + process.stderr)
        self.assertEqual(process.stderr, "")
        reply = json.loads(process.stdout)
        self.assertEqual(reply["result"], "passed" if expected == 0 else "blocked")
        if expected:
            self.assertEqual(reply["error_code"], "ORIGINAL_USER_REQUIRED")
        self.assertNotIn("ticket", process.stdout.lower())


if __name__ == "__main__":
    unittest.main()

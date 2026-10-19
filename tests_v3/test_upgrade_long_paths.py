"""Windows backup I/O beyond MAX_PATH, without changing system policy."""
from contextlib import closing
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from envmanager_v3.__main__ import InstanceLock
from envmanager_v3 import upgrade


@unittest.skipUnless(os.name == "nt", "Windows extended-length paths")
class UpgradeLongPathTests(unittest.TestCase):
    def setUp(self):
        # Cleanup must traverse the same long names even with global policy off.
        self.temp = tempfile.TemporaryDirectory(dir=upgrade._io_path(tempfile.gettempdir()))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name.removeprefix("\\\\?\\")).resolve()
        self.relative = "workspaces/4610d63fa6f646a694dfba4e5564d583/backups/" + (
            "config.toml.2829a7050112ff3c015eb13bd0ece023ea68dab75ba0d109e084e15306f36db2.dhmum0et.bak")
        # Pad the COMMON parent so both source and backup grow. A short default
        # TEMP must still produce a long published payload, not only staging.
        padding = 240 - len(str(self.root / "d" / self.relative))
        if padding > 0:
            self.root = self.root / ("p" * max(1, padding - 1))
        self.data = self.root / "d"
        self.directory = self.data / "workspaces" / "4610d63fa6f646a694dfba4e5564d583" / "backups"
        self.directory.mkdir(parents=True)
        self.raw = b"# original raw bytes\r\n[unfinished\r\n"
        (self.data / self.relative).write_bytes(self.raw)
        self.assertLess(len(str(self.data / self.relative)), 260)
        self.backups = self.root / "operator-data.upgrade-backups"
        self.assertGreater(len(str(self.backups / ("0" * 32) / "data" / self.relative)), 260)

    def prepare(self):
        session = upgrade.prepare_upgrade(self.data, "3.0.0.dev1", backup_root=self.backups)
        self.addCleanup(session.close)
        return session

    def test_long_shadow_and_payload_preserve_raw_bytes_and_logical_receipts(self):
        session = self.prepare()
        shadow = self.backups / (".incomplete-" + session.backup_id) / "shadow" / self.relative
        payload = session.backup_path / "data" / self.relative
        self.assertGreater(len(str(shadow)), 260)
        self.assertGreater(len(str(payload)), 260)
        self.assertEqual(upgrade._io_path(payload).read_bytes(), self.raw)
        self.assertEqual((self.data / self.relative).read_bytes(), self.raw)
        manifest = upgrade.verify_backup(session.backup_path, session.manifest_sha256)
        self.assertEqual(manifest["source_data_dir"], str(self.data))
        self.assertEqual(manifest["files"][self.relative]["sha256"], hashlib.sha256(self.raw).hexdigest())
        self.assertNotIn("\\\\?\\", session.receipt()["backup_path"])
        self.assertEqual(session.check()["result"], "ready")
        with self.assertRaises(RuntimeError):
            with InstanceLock(self.data / "instance.lock"):
                self.fail("Upgrade lost the production instance lock")
        self.assertEqual(session.finish()["outcome"], "installed")
        with InstanceLock(self.data / "instance.lock"):
            pass

    def test_long_sqlite_uri_preserves_escaped_filename_and_committed_wal(self):
        name = "archive # % 用户." + "x" * 68 + ".db"
        database = self.directory / name
        self.assertLess(len(str(database)), 260)
        connection = sqlite3.connect(database)
        self.addCleanup(connection.close)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("CREATE TABLE fixture(raw TEXT)")
        connection.execute("INSERT INTO fixture VALUES(?)", (" A;;A;\r\n用户",))
        connection.commit()
        wal = Path(str(database) + "-wal")
        before = (database.read_bytes(), wal.read_bytes())
        session = self.prepare()
        relative = database.relative_to(self.data).as_posix()
        copied = session.backup_path / "data" / relative
        self.assertGreater(len(str(copied)), 260)
        with closing(sqlite3.connect(upgrade._sqlite_uri(copied, readonly=True) + "&immutable=1", uri=True)) as backup:
            self.assertEqual(backup.execute("SELECT raw FROM fixture").fetchone()[0], " A;;A;\r\n用户")
            with self.assertRaises(sqlite3.OperationalError):
                backup.execute("INSERT INTO fixture VALUES('forbidden')")
        self.assertEqual((database.read_bytes(), wal.read_bytes()), before)
        self.assertFalse(upgrade._io_path(Path(str(copied) + "-wal")).exists())
        self.assertEqual(session.finish()["result"], "finished")

    def test_long_backup_tamper_still_blocks_finish_and_releases_lock(self):
        session = self.prepare()
        copied = session.backup_path / "data" / self.relative
        upgrade._io_path(copied).write_bytes(b"tampered")
        with self.assertRaises(upgrade.UpgradeBlocked) as caught:
            session.finish()
        self.assertEqual(caught.exception.code, "BACKUP_CHANGED")
        self.assertFalse((session.backup_path / "finish.json").exists())
        self.assertEqual((self.data / self.relative).read_bytes(), self.raw)
        with InstanceLock(self.data / "instance.lock"):
            pass


if __name__ == "__main__":
    unittest.main()

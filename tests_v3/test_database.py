from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from envmanager_v3.database import Database, DatabaseSchemaError, SCHEMA_VERSION


# The actual pre-versioning schema, independent of the implementation constants.
LEGACY_SQL = """
CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE previews(
  id TEXT PRIMARY KEY, version INTEGER NOT NULL, digest TEXT NOT NULL,
  body TEXT NOT NULL, review_token TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE executions(
  id TEXT PRIMARY KEY, preview_id TEXT NOT NULL, request_key TEXT UNIQUE NOT NULL,
  status TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE history(
  id TEXT PRIMARY KEY, execution_id TEXT UNIQUE NOT NULL,
  body TEXT NOT NULL, created_at TEXT NOT NULL);
"""


class SchemaVersionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        # Also exercise URI escaping for the read-only preflight/backup reader.
        self.path = self.root / "状态 #1%.sqlite3"

    def seed_legacy(self, *, wal=False):
        connection = sqlite3.connect(self.path)
        if wal:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.executescript(LEGACY_SQL)
        connection.execute("INSERT INTO settings VALUES(?,?)", ('workspace', '" A\\r\\n; ;A"'))
        connection.execute("INSERT INTO previews VALUES(?,?,?,?,?,?,?)",
                           ('p1', 3, 'digest', '{"planId":"p1","version":3,"digest":"digest"}',
                            'reviewed', 'ready', 'preview-time'))
        connection.execute("INSERT INTO executions VALUES(?,?,?,?,?,?,?)",
                           ('e1', 'p1', 'request1', 'executing', '{"written":[]}', 'created', 'updated'))
        connection.execute("INSERT INTO history VALUES(?,?,?,?)",
                           ('h1', 'old-e', '{"after":" A; ;A"}', 'history-time'))
        connection.commit()
        return connection

    def files(self, *, include_shm=True):
        return {path.name: path.read_bytes() for path in self.root.iterdir()
                if path.is_file() and (include_shm or not path.name.endswith('-shm'))}

    def snapshot(self, connection):
        return tuple(connection.iterdump())

    def test_new_database_stamp_and_state_persist_on_reopen(self):
        db = Database(self.path)
        self.assertEqual(SCHEMA_VERSION, 1)
        self.assertEqual(db.connection.execute('PRAGMA user_version').fetchone()[0], 1)
        self.assertEqual(db.connection.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
        self.assertEqual(db.connection.execute('PRAGMA synchronous').fetchone()[0], 2)
        self.assertEqual(db.connection.execute('PRAGMA foreign_keys').fetchone()[0], 1)
        self.assertIsNone(db.backup_path)
        db.set('raw', ' A\r\n; ;A')
        db.close()
        db = Database(self.path)
        try:
            self.assertEqual(db.get('raw'), ' A\r\n; ;A')
            self.assertEqual(db.connection.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertIsNone(db.backup_path)
            self.assertEqual(list(self.root.glob('*.bak')), [])
        finally:
            db.close()

    def test_legacy_backup_and_all_records_survive_upgrade_and_reopen(self):
        with closing(self.seed_legacy()) as legacy:
            original = self.snapshot(legacy)
        db = Database(self.path)
        backup_path = db.backup_path
        try:
            self.assertIsNotNone(backup_path)
            self.assertEqual(self.snapshot(db.connection), original)
            with closing(sqlite3.connect(backup_path)) as backup:
                self.assertEqual(backup.execute('PRAGMA user_version').fetchone()[0], 0)
                self.assertEqual(self.snapshot(backup), original)
                self.assertEqual(backup.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        finally:
            db.close()
        backup_bytes = backup_path.read_bytes()
        db = Database(self.path)
        try:
            self.assertEqual(db.connection.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertEqual(db.get('workspace'), ' A\r\n; ;A')
            self.assertEqual(db.read_preview('p1')['review_token'], 'reviewed')
            self.assertEqual(db.find_execution('request1')['status'], 'executing')
            self.assertEqual(db.unfinished()[0]['id'], 'e1')
            self.assertEqual(db.history()[0]['after'], ' A; ;A')
            self.assertEqual(self.snapshot(db.connection), original)
            self.assertIsNone(db.backup_path)
        finally:
            db.close()
        self.assertEqual(list(self.root.glob('*.bak')), [backup_path])
        self.assertEqual(backup_path.read_bytes(), backup_bytes)

    def test_backup_includes_committed_uncheckpointed_wal(self):
        with closing(self.seed_legacy(wal=True)) as legacy:
            self.assertTrue(Path(str(self.path) + '-wal').exists())
            original = self.snapshot(legacy)
            db = Database(self.path)
            try:
                with closing(sqlite3.connect(db.backup_path)) as backup:
                    self.assertEqual(self.snapshot(backup), original)
                    self.assertEqual(backup.execute('PRAGMA user_version').fetchone()[0], 0)
                self.assertEqual(self.snapshot(db.connection), original)
            finally:
                db.close()

    def test_future_database_rejected_without_any_file_change(self):
        for version in (2, 99):
            with self.subTest(version=version):
                with closing(sqlite3.connect(self.path)) as future:
                    future.execute('CREATE TABLE IF NOT EXISTS future_data(value BLOB)')
                    future.execute('INSERT INTO future_data VALUES(?)', (b'keep me',))
                    future.execute(f'PRAGMA user_version={version}')
                    future.commit()
                before = self.files()
                with self.assertRaisesRegex(DatabaseSchemaError, f'version {version}.*newer'):
                    Database(self.path)
                self.assertEqual(self.files(), before)

    def test_future_version_in_live_wal_is_rejected_without_mutation(self):
        with closing(sqlite3.connect(self.path)) as future:
            future.execute('PRAGMA journal_mode=WAL')
            future.execute('PRAGMA wal_autocheckpoint=0')
            future.execute('PRAGMA user_version=2')
            before = self.files(include_shm=False)
            names = set(self.files())
            with self.assertRaisesRegex(DatabaseSchemaError, 'version 2.*newer'):
                Database(self.path)
            # SQLite's read-only reader can update transient -shm read marks.
            # The database/WAL bytes and the set of files must remain unchanged.
            self.assertEqual(self.files(include_shm=False), before)
            self.assertEqual(set(self.files()), names)
            self.assertEqual(future.execute('PRAGMA user_version').fetchone()[0], 2)

    def test_malformed_legacy_and_v1_are_rejected_without_repair(self):
        malformed = (
            LEGACY_SQL.replace('CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);', ''),
            LEGACY_SQL.replace('value TEXT NOT NULL', 'value TEXT'),
            LEGACY_SQL.replace('body TEXT NOT NULL, review_token TEXT,', 'body TEXT NOT NULL,'),
            LEGACY_SQL.replace('request_key TEXT UNIQUE NOT NULL', 'request_key TEXT NOT NULL'),
            LEGACY_SQL.replace('version INTEGER NOT NULL', 'version TEXT NOT NULL'),
            LEGACY_SQL + 'CREATE TABLE unknown(value TEXT);',
            LEGACY_SQL + 'CREATE TRIGGER surprise AFTER INSERT ON settings BEGIN DELETE FROM history; END;',
        )
        for version in (0, 1):
            for index, sql in enumerate(malformed):
                with self.subTest(version=version, malformed=index):
                    path = self.root / f'malformed-{version}-{index}.sqlite3'
                    with closing(sqlite3.connect(path)) as legacy:
                        legacy.executescript(sql)
                        legacy.execute(f'PRAGMA user_version={version}')
                    before = self.files()
                    with self.assertRaises(DatabaseSchemaError):
                        Database(path)
                    self.assertEqual(self.files(), before)

    def test_corrupt_file_is_not_overwritten(self):
        self.path.write_bytes(b'not a SQLite database')
        before = self.files()
        with self.assertRaises(sqlite3.DatabaseError):
            Database(self.path)
        self.assertEqual(self.files(), before)

    def test_backup_failure_prevents_upgrade(self):
        with closing(self.seed_legacy()):
            pass
        before = self.files()
        with patch.object(Database, '_backup_legacy', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                Database(self.path)
        self.assertEqual(self.files(), before)

    def test_failed_backup_copy_removes_partial_file_and_keeps_legacy(self):
        with closing(self.seed_legacy()):
            pass
        original_connect = sqlite3.connect

        class FailedBackupConnection(sqlite3.Connection):
            def backup(self, target, **kwargs):
                target.execute('CREATE TABLE partial(value TEXT)')
                raise sqlite3.OperationalError('injected backup failure')

        def connect(database, *args, **kwargs):
            if kwargs.get('uri'):
                kwargs['factory'] = FailedBackupConnection
            return original_connect(database, *args, **kwargs)

        before = self.files()
        with patch('envmanager_v3.database.sqlite3.connect', side_effect=connect):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'injected backup'):
                Database(self.path)
        self.assertEqual(self.files(), before)

    def test_existing_empty_file_or_empty_sqlite_database_can_initialize(self):
        self.path.touch()
        for empty in ('zero-byte', 'sqlite'):
            with self.subTest(empty=empty):
                if empty == 'sqlite':
                    self.path.unlink()
                    with closing(sqlite3.connect(self.path)) as connection:
                        connection.execute('VACUUM')
                db = Database(self.path)
                try:
                    self.assertEqual(db.connection.execute('PRAGMA user_version').fetchone()[0], 1)
                    self.assertIsNone(db.backup_path)
                finally:
                    db.close()

    def fail_statement(self, statement):
        original_connect = sqlite3.connect
        target = self.path

        class InterruptedConnection(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if sql == statement:
                    raise sqlite3.OperationalError('injected migration failure')
                return super().execute(sql, parameters)

        def connect(database, *args, **kwargs):
            if database == target:
                kwargs['factory'] = InterruptedConnection
            return original_connect(database, *args, **kwargs)

        return patch('envmanager_v3.database.sqlite3.connect', side_effect=connect)

    def test_failed_new_database_creation_rolls_back_ddl_and_stamp(self):
        with self.fail_statement('PRAGMA user_version=1'):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'injected'):
                Database(self.path)
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT name FROM sqlite_schema').fetchall(), [])
        db = Database(self.path)
        db.close()

    def test_failed_legacy_commit_rolls_back_stamp_and_retains_backup(self):
        with closing(self.seed_legacy()) as legacy:
            original = self.snapshot(legacy)
        with self.fail_statement('COMMIT'):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'injected'):
                Database(self.path)
        backups = list(self.root.glob('*.bak'))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(self.path)) as legacy:
            self.assertEqual(legacy.execute('PRAGMA user_version').fetchone()[0], 0)
            self.assertEqual(self.snapshot(legacy), original)
            self.assertEqual(legacy.execute('PRAGMA journal_mode').fetchone()[0], 'delete')
        with closing(sqlite3.connect(backups[0])) as backup:
            self.assertEqual(backup.execute('PRAGMA user_version').fetchone()[0], 0)
            self.assertEqual(self.snapshot(backup), original)
        backup_bytes = backups[0].read_bytes()
        db = Database(self.path)
        try:
            self.assertNotEqual(db.backup_path, backups[0])
            self.assertEqual(self.snapshot(db.connection), original)
        finally:
            db.close()
        self.assertEqual(backups[0].read_bytes(), backup_bytes)


class DurableStateTests(unittest.TestCase):
    def test_state_and_inflight_journal_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            db = Database(path)
            db.set("workspace", {"selected": " A\r\n; ;A"})
            db.execution("e1", "p1", "request1", "executing", {"before": "A", "written": []})
            db.close()
            db = Database(path)
            self.assertEqual(db.get("workspace")["selected"], " A\r\n; ;A")
            self.assertEqual(db.unfinished()[0]["id"], "e1")
            self.assertEqual(db.find_execution("request1")["status"], "executing")
            db.close()

    def test_transaction_rolls_back_selection_and_history_together(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "state.sqlite3")
            with self.assertRaises(RuntimeError):
                with db.transaction() as connection:
                    db.set("saved", "new", connection=connection)
                    db.append_history("h1", "e1", {"after": "new"}, connection=connection)
                    raise RuntimeError("interrupted before commit")
            self.assertIsNone(db.get("saved"))
            self.assertEqual(db.history(), [])
            db.close()

    def test_preview_version_replaces_and_revokes_review_token(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "state.sqlite3")
            db.preview({"planId": "p", "version": 1, "digest": "a"}, review_token="reviewed")
            db.preview({"planId": "p", "version": 2, "digest": "b"})
            row = db.read_preview("p")
            self.assertEqual(row["version"], 2)
            self.assertIsNone(row["review_token"])
            db.close()


if __name__ == "__main__":
    unittest.main()

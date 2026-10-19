"""Durable V3 workspace, immutable plan receipts and write-ahead execution journal."""
from __future__ import annotations

from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
from datetime import datetime, timezone


SCHEMA_VERSION = 1
_SCHEMA = (
    "CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    """CREATE TABLE previews(
        id TEXT PRIMARY KEY, version INTEGER NOT NULL, digest TEXT NOT NULL,
        body TEXT NOT NULL, review_token TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE executions(
        id TEXT PRIMARY KEY, preview_id TEXT NOT NULL, request_key TEXT UNIQUE NOT NULL,
        status TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE history(
        id TEXT PRIMARY KEY, execution_id TEXT UNIQUE NOT NULL,
        body TEXT NOT NULL, created_at TEXT NOT NULL)""",
)


class DatabaseSchemaError(RuntimeError):
    """The database cannot be opened safely by this product version."""


def _schema_shape(connection):
    """Compare storage contracts, not CREATE SQL formatting or autoindex names."""
    shape = {}
    for kind, name in connection.execute(
            "SELECT type,name FROM sqlite_schema WHERE type != 'index'"):
        if name.startswith("sqlite_"):
            continue
        if kind != "table":
            raise DatabaseSchemaError(f"Unexpected database {kind}: {name}")
        # Identifiers come from the database, so quote even unexpected names.
        quoted = '"' + name.replace('"', '""') + '"'
        columns = tuple(tuple(row) for row in connection.execute(f"PRAGMA table_xinfo({quoted})"))
        unique = []
        for index in connection.execute(f"PRAGMA index_list({quoted})"):
            if index[2]:
                index_name = '"' + index[1].replace('"', '""') + '"'
                keys = tuple(tuple(row)[1:] for row in connection.execute(
                    f"PRAGMA index_xinfo({index_name})") if row[5])
                unique.append((index[3], index[4], keys))
        foreign_keys = tuple(tuple(row) for row in connection.execute(f"PRAGMA foreign_key_list({quoted})"))
        shape[name] = (columns, tuple(sorted(unique)), foreign_keys)
    return shape


def _inspect_schema(connection) -> tuple[int, bool]:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise DatabaseSchemaError(
            f"Database schema version {version} is newer than supported version {SCHEMA_VERSION}.")
    if version not in (0, SCHEMA_VERSION):
        raise DatabaseSchemaError(f"Unsupported database schema version {version}.")
    shape = _schema_shape(connection)
    if not shape and version == 0:
        return version, False
    with closing(sqlite3.connect(":memory:")) as expected:
        for statement in _SCHEMA:
            expected.execute(statement)
        if shape != _schema_shape(expected):
            raise DatabaseSchemaError(
                f"Database schema version {version} does not match the V3 four-table schema; no repair attempted.")
    return version, True


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.backup_path: Path | None = None
        # Inspect existing files read-only BEFORE opening a writable connection,
        # changing journal mode, creating tables, or making a migration backup.
        if self.path.exists() and self.path.stat().st_size:
            with closing(sqlite3.connect(self._readonly_uri(), uri=True)) as existing:
                _inspect_schema(existing)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        try:
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.execute("PRAGMA foreign_keys=ON")
            with self.transaction():
                # Serialize initializers and recheck after acquiring the write lock.
                version, has_schema = _inspect_schema(self.connection)
                if version == 0:
                    if has_schema:
                        self.backup_path = self._backup_legacy()
                    else:
                        for statement in _SCHEMA:
                            self.connection.execute(statement)
                    # DDL and the stamp commit together; executescript would
                    # implicitly commit and break this guarantee.
                    self.connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self.connection.execute("PRAGMA journal_mode=WAL")
        except BaseException:
            self.connection.close()
            raise

    def _readonly_uri(self) -> str:
        # Do not use immutable=1: it would ignore committed data in a live WAL.
        return self.path.resolve().as_uri() + "?mode=ro"

    def _backup_legacy(self) -> Path:
        # BEGIN IMMEDIATE holds out other writers. A separate reader can back up
        # the same committed state (including WAL); backing up the writer's own
        # active transaction would wait on itself.
        descriptor, name = tempfile.mkstemp(
            prefix=self.path.name + ".schema-v0-", suffix=".bak", dir=self.path.parent)
        os.close(descriptor)
        backup_path = Path(name)
        try:
            with closing(sqlite3.connect(self._readonly_uri(), uri=True)) as source:
                with closing(sqlite3.connect(backup_path)) as backup:
                    backup.execute("PRAGMA synchronous=FULL")
                    source.backup(backup)
                    if backup.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                        raise DatabaseSchemaError("Legacy database backup failed its integrity check.")
        except BaseException:
            backup_path.unlink(missing_ok=True)
            raise
        return backup_path

    @contextmanager
    def transaction(self):
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield self.connection
                self.connection.execute("COMMIT")
            except BaseException:
                self.connection.execute("ROLLBACK")
                raise

    def get(self, key: str, default=None):
        with self.lock:
            row = self.connection.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value, *, connection=None):
        with self.lock:
            (connection or self.connection).execute(
                "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, encode(value)))

    def preview(self, receipt: dict, *, review_token=None, status="ready"):
        with self.transaction() as connection:
            connection.execute("""INSERT INTO previews VALUES(?,?,?,?,?,?,?)
              ON CONFLICT(id) DO UPDATE SET version=excluded.version,digest=excluded.digest,
              body=excluded.body,review_token=excluded.review_token,status=excluded.status""",
                (receipt["planId"], receipt["version"], receipt["digest"], encode(receipt),
                 review_token, status, now()))

    def read_preview(self, plan_id: str):
        with self.lock:
            row = self.connection.execute("SELECT * FROM previews WHERE id=?", (plan_id,)).fetchone()
        if not row:
            return None
        return {**dict(row), "body": json.loads(row["body"])}

    def execution(self, execution_id: str, plan_id: str, request_key: str, status: str, body: dict):
        stamp = now()
        with self.transaction() as connection:
            connection.execute("""INSERT INTO executions VALUES(?,?,?,?,?,?,?)
              ON CONFLICT(id) DO UPDATE SET status=excluded.status,body=excluded.body,updated_at=excluded.updated_at""",
                (execution_id, plan_id, request_key, status, encode(body), stamp, stamp))

    def find_execution(self, request_key: str):
        with self.lock:
            row = self.connection.execute("SELECT * FROM executions WHERE request_key=?", (request_key,)).fetchone()
        return {**dict(row), "body": json.loads(row["body"])} if row else None

    def unfinished(self) -> list[dict]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT * FROM executions WHERE status NOT IN ('committed','compensated','cancelled','resolved_current')").fetchall()
        return [{**dict(row), "body": json.loads(row["body"])} for row in rows]

    def append_history(self, history_id: str, execution_id: str, entry: dict, *, connection=None):
        with self.lock:
            (connection or self.connection).execute("INSERT INTO history VALUES(?,?,?,?)",
                (history_id, execution_id, encode(entry), now()))

    def history(self) -> list[dict]:
        with self.lock:
            rows = self.connection.execute("SELECT body,created_at FROM history ORDER BY created_at DESC,id DESC").fetchall()
        return [{**json.loads(row[0]), "when": row[1]} for row in rows]

    def close(self):
        with self.lock:
            self.connection.close()

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

from .models import ConflictError


def default_state_dir(config_path: Path) -> Path:
    identity = hashlib.sha256(str(config_path.resolve()).casefold().encode("utf-8")).hexdigest()[:20]
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share")))
    return base / "EnvironmentManagerV2" / identity


class StateRepository:
    """SQLite stores successful inputs and a write-ahead execution journal separately."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "state.sqlite3"
        with self._connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS snapshot (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
            initial = {"revision": 0, "last_values": {}, "active": {}, "orders": {},
                       "successful_applications": {}}
            connection.execute("INSERT OR IGNORE INTO snapshot VALUES (1, ?)", (self._json(initial),))
        self.snapshot()  # Reject malformed state instead of silently resetting it.

    @staticmethod
    def _json(value):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.execute("PRAGMA synchronous=FULL")
            with connection:
                yield connection
        finally:
            connection.close()

    def snapshot(self):
        with self._connect() as connection:
            snapshot = json.loads(connection.execute("SELECT data FROM snapshot WHERE id=1").fetchone()[0])
        if (not isinstance(snapshot, dict) or type(snapshot.get("revision")) is not int
                or snapshot["revision"] < 0
                or any(not isinstance(snapshot.get(k), dict) for k in ("last_values", "active", "orders"))):
            raise ValueError("持久化状态结构无效，请保留状态文件并检查")
        snapshot.setdefault("successful_applications", {})
        if not isinstance(snapshot["successful_applications"], dict):
            raise ValueError("成功选择记录结构无效，请保留状态文件并检查")
        for saved in snapshot["successful_applications"].values():
            if (not isinstance(saved, dict) or not isinstance(saved.get("option"), str)
                    or not isinstance(saved.get("selections"), dict)):
                raise ValueError("成功选择记录结构无效，请保留状态文件并检查")
        return snapshot

    def save_operation(self, operation):
        with self._connect() as connection:
            connection.execute("INSERT INTO operations VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                               (operation["id"], self._json(operation)))

    def operation(self, operation_id):
        with self._connect() as connection:
            row = connection.execute("SELECT data FROM operations WHERE id=?", (operation_id,)).fetchone()
        if row is None:
            raise ValueError("找不到该执行记录")
        return json.loads(row[0])

    def history(self):
        with self._connect() as connection:
            values = [json.loads(row[0]) for row in connection.execute("SELECT data FROM operations")]
        return sorted(values, key=lambda item: item["created_at"], reverse=True)

    def pending(self):
        return [op for op in self.history() if op["status"] in
                {"prepared", "writing", "rolling_back", "rollback_incomplete", "state_commit_failed"}]

    def commit(self, expected_revision, snapshot, operation, resolved_id=None):
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = json.loads(connection.execute("SELECT data FROM snapshot WHERE id=1").fetchone()[0])
            if current["revision"] != expected_revision:
                raise ConflictError("其他窗口已更新运行状态，请重新预览")
            target = deepcopy(snapshot)
            target["revision"] = expected_revision + 1
            operation["after_revision"] = target["revision"]
            connection.execute("UPDATE snapshot SET data=? WHERE id=1", (self._json(target),))
            connection.execute("UPDATE operations SET data=? WHERE id=?", (self._json(operation), operation["id"]))
            if resolved_id:
                row = connection.execute("SELECT data FROM operations WHERE id=?", (resolved_id,)).fetchone()
                if row:
                    resolved = json.loads(row[0])
                    resolved["resolved_by"] = operation["id"]
                    resolved["status"] = "recovered" if operation["action"] == "recover" else "restored"
                    connection.execute("UPDATE operations SET data=? WHERE id=?", (self._json(resolved), resolved_id))

    @contextmanager
    def execution_lock(self):
        # Serializes independent GUI processes while each journal write remains durable.
        path = self.directory / "execution.lock"
        handle = path.open("a+b")
        acquired = False
        try:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as exc:
                raise ConflictError("另一个窗口正在执行，请稍后重新预览") from exc
            yield
        finally:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

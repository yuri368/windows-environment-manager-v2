"""Editable TOML sessions; no registry or successful-application state mutations."""

from __future__ import annotations

import difflib
import os
import re
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

from .config import parse_config, save_config
from .config_document import ConfigurationDocument
from .models import ConfigError, ConflictError, VariableSpec


@contextmanager
def _save_lock(path: Path):
    """Serialize saves by editors of the same configuration, across processes."""
    lock_path = path.with_name(f".{path.name}.edit.lock")
    handle = lock_path.open("a+b")
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
            raise ConflictError("另一个配置编辑器正在保存，请稍后重试；草稿已保留") from exc
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


class ConfigEditingSession:
    """One source of truth for visual commands, raw edits, undo and explicit save."""

    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self._baseline = self.path.read_bytes()
        try:
            self._text = self._baseline.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ConfigError("配置文件必须是 UTF-8 编码") from exc
        self._history = [self._text]
        self._cursor = 0
        self.document: ConfigurationDocument | None = None
        self.error: str | None = None
        self.save_warning: str | None = None
        self._reparse()

    @property
    def text(self) -> str:
        return self._text

    @property
    def dirty(self) -> bool:
        return self._text.encode("utf-8") != self._baseline

    @property
    def can_undo(self) -> bool:
        return self._cursor > 0

    @property
    def can_redo(self) -> bool:
        return self._cursor + 1 < len(self._history)

    def _reparse(self):
        try:
            self.document = ConfigurationDocument(self._text)
        except (ConfigError, ValueError) as exc:
            self.document = None
            self.error = str(exc)
            return
        try:
            parse_config(self._text)
        except ConfigError as exc:
            self.error = str(exc)
        else:
            self.error = None

    def replace_text(self, text: str) -> None:
        if not isinstance(text, str):
            raise ConfigError("配置草稿必须是字符串")
        if text == self._text:
            return
        self._text = text
        self._history = self._history[:self._cursor + 1] + [text]
        self._cursor += 1
        self._reparse()

    def replace_source_text(self, displayed_text: str) -> None:
        """Apply a Qt source edit without normalizing untouched line separators."""
        if not isinstance(displayed_text, str):
            raise ConfigError("配置草稿必须是字符串")

        def lines(text):
            parts = re.split(r"(\r\n|\n|\r|\u2028|\u2029)", text)
            return [(parts[i], parts[i + 1] if i + 1 < len(parts) else "")
                    for i in range(0, len(parts), 2)
                    if parts[i] or i + 1 < len(parts)]

        old = lines(self._text)
        new = lines(displayed_text)
        endings = Counter(ending for _, ending in old if ending in {"\r\n", "\n"})
        default_ending = endings.most_common(1)[0][0] if endings else "\n"
        matcher = difflib.SequenceMatcher(a=[(body, bool(end)) for body, end in old],
                                        b=[(body, bool(end)) for body, end in new], autojunk=False)
        chunks = []
        for kind, i, j, x, y in matcher.get_opcodes():
            if kind == "equal":
                chunks.extend(body + end for body, end in old[i:j])
            else:
                for offset, (body, end) in enumerate(new[x:y]):
                    prior_end = old[i + offset][1] if kind == "replace" and i + offset < j else ""
                    chunks.append(body + ((prior_end or default_ending) if end else ""))
        self.replace_text("".join(chunks))

    def _command(self, name, *args, **kwargs):
        if self.document is None:
            raise ConfigError("原始 TOML 尚有错误，请先修正源码再使用可视化编辑")
        # Work on a separate document; a rejected command cannot damage history.
        document = ConfigurationDocument(self._text)
        getattr(document, name)(*args, **kwargs)
        self.replace_text(document.text)

    def put_record(self, spec: VariableSpec, original: VariableSpec | None = None):
        self._command("put_record", spec, original=original)

    def delete_record(self, spec: VariableSpec):
        self._command("delete_record", spec)

    def delete_group(self, app: str, option: str | None = None):
        self._command("delete_group", app, option)

    def set_default_option(self, app: str, option: str):
        self._command("set_default_option", app, option)

    def undo(self):
        if self.can_undo:
            self._cursor -= 1
            self._text = self._history[self._cursor]
            self._reparse()

    def redo(self):
        if self.can_redo:
            self._cursor += 1
            self._text = self._history[self._cursor]
            self._reparse()

    def validate(self):
        return parse_config(self._text)

    def diff(self) -> str:
        return "".join(difflib.unified_diff(
            self._baseline.decode("utf-8").splitlines(keepends=True),
            self._text.splitlines(keepends=True),
            fromfile=f"{self.path.name}（磁盘基线）", tofile=f"{self.path.name}（编辑草稿）"))

    def reload_from_disk(self):
        content = self.path.read_bytes()
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ConfigError("配置文件必须是 UTF-8 编码；原草稿已保留") from exc
        self._baseline, self._text = content, text
        self._history, self._cursor = [text], 0
        self._reparse()

    def save(self, service):
        self.save_warning = None
        config = self.validate()
        with _save_lock(self.path):
            try:
                actual = self.path.read_bytes()
            except OSError as exc:
                raise ConflictError("配置文件已移动、删除或无法读取；原草稿已保留") from exc
            if actual != self._baseline:
                raise ConflictError("配置文件已被其他程序修改；原草稿已保留，请重新加载")
            old_config = service.config
            old_selections = service.selections
            old_plans = dict(getattr(service, "_issued_plans", {}))
            prepared = False
            try:
                # Prepare the runtime first so a refresh failure leaves disk unchanged.
                service.reload(config)
                prepared = True
                if self.dirty:
                    save_config(self.path, self._text, expected_content=self._baseline)
            except Exception as exc:
                try:
                    persisted = self.path.read_bytes()
                except OSError:
                    persisted = None
                if prepared and persisted == self._text.encode("utf-8"):
                    # The replace may complete before a filesystem error reaches us.
                    self.save_warning = f"保存接口返回 {type(exc).__name__}，已核实磁盘配置与草稿完全一致"
                else:
                    service.config = old_config
                    service.selections = old_selections
                    if hasattr(service, "_issued_plans"):
                        service._issued_plans = old_plans
                    if prepared and persisted is None:
                        raise ConflictError("保存结果无法核实；草稿与原运行时状态已保留，请检查磁盘文件后重新加载") from exc
                    raise
            self._baseline = self._text.encode("utf-8")
            self._history, self._cursor = [self._text], 0
            self._reparse()
        return config

"""Format-preserving TOML document edits for the visual configuration editor.

This layer edits configuration text only. A fresh tomlkit document is used for
each command, so an invalid command cannot leave half of an edit in the draft.
"""

from __future__ import annotations

import tomllib

import tomlkit
from tomlkit.items import Array, Comment, InlineTable

from .config import parse_config
from .models import ConfigError, Configuration, VariableSpec, match_key


def _validated_text(text: str) -> Configuration | None:
    if not isinstance(text, str):
        raise ConfigError("配置草稿必须是字符串")
    try:
        tomlkit.parse(text)
        raw = tomllib.loads(text)
    except Exception as exc:
        raise ConfigError(f"TOML 语法错误：{exc}") from exc
    if not raw:
        return None  # Whitespace and comments are useful unsaved drafts.
    return parse_config(text)


def _string(value: str, *, prefer_literal: bool = False):
    if prefer_literal:
        try:
            return tomlkit.string(value, literal=True)
        except (ValueError, TypeError):
            pass
    return tomlkit.string(value)


def _inline(spec: VariableSpec) -> InlineTable:
    entry = tomlkit.inline_table()
    entry["default_value"] = _string(spec.default_value, prefer_literal=True)
    entry["scope"] = _string(spec.scope)
    entry["mode"] = _string(spec.mode)
    entry["editable"] = spec.editable
    if spec.special:
        entry["special"] = True
    if spec.choices:
        choices = tomlkit.array()
        for choice in spec.choices:
            row = tomlkit.inline_table()
            row["label"] = _string(choice.label)
            row["value"] = _string(choice.value, prefer_literal=True)
            choices.append(row)
        entry["choices"] = choices
    return entry


def _validated_spec(spec: VariableSpec) -> None:
    if not isinstance(spec, VariableSpec):
        raise ConfigError("变量记录必须是 VariableSpec")
    try:
        fragment = tomlkit.document()
        fragment.add(spec.app, tomlkit.table())
        fragment[spec.app].add(spec.option, tomlkit.table())
        fragment[spec.app][spec.option].add(spec.name, _inline(spec))
        parsed = parse_config(fragment.as_string()).variables(spec.app, spec.option)
    except ConfigError:
        raise
    except Exception as exc:
        raise ConfigError(f"无效变量记录：{exc}") from exc
    if len(parsed) != 1 or parsed[0] != spec:
        raise ConfigError("变量记录字段无效或类型不匹配")


def _key_for(group, name: str) -> str | None:
    wanted = match_key(name)
    return next((key for key in group if match_key(key) == wanted), None)


def _newline_like(source: str, rendered: str) -> str:
    # tomlkit retains existing trivia but emits LF for newly added nodes.
    if "\r\n" in source and "\n" not in source.replace("\r\n", ""):
        return rendered.replace("\r\n", "\n").replace("\n", "\r\n")
    return rendered


def _update_inline(entry: InlineTable, spec: VariableSpec, previous: VariableSpec) -> None:
    if previous.default_value != spec.default_value:
        entry["default_value"] = _string(spec.default_value, prefer_literal=True)
    if previous.scope != spec.scope:
        entry["scope"] = _string(spec.scope)
    if previous.mode != spec.mode:
        entry["mode"] = _string(spec.mode)
    if previous.editable != spec.editable:
        entry["editable"] = spec.editable
    if previous.special != spec.special:
        if spec.special:
            entry["special"] = True
        elif "special" in entry:
            del entry["special"]
    if previous.choices != spec.choices:
        if spec.choices:
            entry["choices"] = _inline(spec)["choices"]
        elif "choices" in entry:
            del entry["choices"]


def _rename_key_in_place(group, old_name: str, new_name: str) -> None:
    """Rename a key without moving its line or losing attached trivia.

    tomlkit has no public rename operation. Its pinned 0.13.3 container insert
    methods let us keep the parsed item's original position and comments.
    """
    container = group._value
    position, old_key = next(
        (index, key) for index, (key, _) in enumerate(container._body)
        if key is not None and key.key == old_name
    )
    new_key = tomlkit.key(new_name)
    new_key.sep = old_key.sep
    old_printed = old_key.as_string()
    new_key._original += old_printed[len(old_printed.rstrip()):]
    item = group.pop(old_name)
    if position < len(container._body):
        container._insert_at(position, new_key, item)
    else:
        container.append(new_key, item)


def _insert_comment_before(group, key_name: str, comment: Comment) -> None:
    container = group._value
    index = next(index for index, (key, _) in enumerate(container._body)
                 if key is not None and key.key == key_name)
    container._body.insert(index, (None, comment))
    for key, position in tuple(container._map.items()):
        if isinstance(position, tuple):
            container._map[key] = tuple(value + (value >= index) for value in position)
        elif position >= index:
            container._map[key] = position + 1


class ConfigurationDocument:
    def __init__(self, text: str):
        self._configuration = _validated_text(text)
        self._text = text

    @property
    def text(self) -> str:
        return self._text

    def groups(self) -> tuple[tuple[str, str], ...]:
        if self._configuration is None:
            return ()
        return tuple((app, option) for app, options in self._configuration.applications.items()
                     for option in options)

    def records(self, app: str, option: str) -> tuple[VariableSpec, ...]:
        if self._configuration is None:
            return ()
        return self._configuration.applications.get(app, {}).get(option, ())

    def default_option(self, app: str) -> str:
        if self._configuration is None or app not in self._configuration.applications:
            raise ConfigError("应用不存在")
        return self._configuration.default_option(app)

    def set_default_option(self, app: str, option: str) -> None:
        if not isinstance(app, str) or not isinstance(option, str):
            raise ConfigError("应用和方案名称必须是字符串")
        if (self._configuration is None or
                option not in self._configuration.applications.get(app, {})):
            raise ConfigError("要设为默认的应用或方案不存在")
        if self._configuration.default_options.get(app) == option:
            return
        draft = tomlkit.parse(self._text)
        draft[app]["default_option"] = _string(option)
        self._commit(draft)

    @staticmethod
    def _repair_defaults(draft) -> None:
        """Keep application metadata valid after removing or moving a record."""
        for app in tuple(draft):
            group = draft[app]
            options = [option for option in group if option != "default_option"]
            if not options:
                del draft[app]
            elif "default_option" in group and group["default_option"] not in options:
                group["default_option"] = _string(options[0])

    def _require_current(self, spec: VariableSpec) -> VariableSpec:
        for current in self.records(spec.app, spec.option):
            if current.key == spec.key:
                if current != spec:
                    raise ConfigError("原记录已变化，请重新读取后再编辑")
                return current
        raise ConfigError("原记录不存在，请重新读取后再编辑")

    def _commit(self, draft) -> None:
        self._repair_defaults(draft)
        rendered = _newline_like(self._text, draft.as_string())
        validated = _validated_text(rendered)
        self._text = rendered
        self._configuration = validated

    @staticmethod
    def _entry(group, name: str, scope: str) -> InlineTable:
        key = _key_for(group, name)
        if key is None:
            raise ConfigError("原变量键不存在")
        value = group[key]
        if isinstance(value, InlineTable):
            if value["scope"] == scope:
                return value
        elif isinstance(value, Array):
            for entry in value:
                if entry["scope"] == scope:
                    return entry
        raise ConfigError("原变量作用域不存在")

    @staticmethod
    def _remove(draft, spec: VariableSpec, *, clean_group: bool = True) -> InlineTable:
        group = draft[spec.app][spec.option]
        key = _key_for(group, spec.name)
        if key is None:
            raise ConfigError("原变量键不存在")
        value = group[key]
        if isinstance(value, InlineTable):
            if value["scope"] != spec.scope:
                raise ConfigError("原变量作用域不存在")
            removed = group.pop(key)
        else:
            removed = next((entry for entry in value if entry["scope"] == spec.scope), None)
            remaining = [entry for entry in value if entry["scope"] != spec.scope]
            if removed is None or len(remaining) != 1:
                raise ConfigError("原变量作用域不存在")
            group[key] = remaining[0]
        if clean_group and not group:
            del draft[spec.app][spec.option]
        return removed

    @staticmethod
    def _add(draft, spec: VariableSpec, entry: InlineTable | None = None,
             leading_comments: tuple[Comment, ...] = ()) -> None:
        entry = entry if entry is not None else _inline(spec)
        if spec.app not in draft:
            draft.add(spec.app, tomlkit.table())
        if spec.option not in draft[spec.app]:
            draft[spec.app].add(spec.option, tomlkit.table())
        group = draft[spec.app][spec.option]
        key = _key_for(group, spec.name)
        if key is None:
            for comment in leading_comments:
                group.add(comment)
            group.add(spec.name, entry)
            return
        current = group[key]
        if isinstance(current, InlineTable):
            if current["scope"] == spec.scope:
                raise ConfigError("同一方案已存在同名且同作用域的变量")
            both = tomlkit.array()
            both.append(current)
            both.append(entry)
            if entry.trivia.comment and not current.trivia.comment:
                both.trivia.comment_ws = entry.trivia.comment_ws
                both.trivia.comment = entry.trivia.comment
                both.trivia.trail = entry.trivia.trail
            group[key] = both
            if entry.trivia.comment and current.trivia.comment:
                leading_comments += (tomlkit.comment(entry.trivia.comment[1:].strip()),)
            for comment in leading_comments:
                _insert_comment_before(group, key, comment)
            return
        raise ConfigError("同一方案已包含双作用域变量，不能重复添加作用域")

    def put_record(self, spec: VariableSpec, original: VariableSpec | None = None) -> None:
        _validated_spec(spec)
        if original is not None:
            self._require_current(original)
            if spec == original:
                return  # Exact no-op, including original spelling and layout.
        for current in self.records(spec.app, spec.option):
            if current.scope == spec.scope and match_key(current.name) == match_key(spec.name):
                if original is None or current.key != original.key:
                    raise ConfigError("同一方案已存在同名且同作用域的变量")

        draft = tomlkit.parse(self._text)
        if (original is not None and original.app == spec.app and
                original.option == spec.option and
                (match_key(original.name) == match_key(spec.name) or
                 not any(match_key(current.name) == match_key(spec.name)
                         for current in self.records(spec.app, spec.option)))):
            group = draft[original.app][original.option]
            entry = self._entry(group, original.name, original.scope)
            _update_inline(entry, spec, original)
            if original.name != spec.name:
                key = _key_for(group, original.name)
                _rename_key_in_place(group, key, spec.name)
        else:
            moved_entry = None
            leading_comments: tuple[Comment, ...] = ()
            if original is not None:
                source_group = draft[original.app][original.option]
                if len(source_group) == 1:
                    leading_comments = tuple(item for key, item in source_group._value._body
                                             if key is None and isinstance(item, Comment))
                moved_entry = self._remove(draft, original)
                _update_inline(moved_entry, spec, original)
            self._add(draft, spec, moved_entry, leading_comments)
        self._commit(draft)

    def delete_record(self, spec: VariableSpec) -> None:
        self._require_current(spec)
        draft = tomlkit.parse(self._text)
        self._remove(draft, spec)
        self._commit(draft)

    def delete_group(self, app: str, option: str | None = None) -> None:
        if not isinstance(app, str) or (option is not None and not isinstance(option, str)):
            raise ConfigError("应用和方案名称必须是字符串")
        draft = tomlkit.parse(self._text)
        if app not in draft or (option is not None and (app, option) not in self.groups()):
            raise ConfigError("要删除的应用或方案不存在")
        if option is None:
            del draft[app]
        else:
            del draft[app][option]
        self._commit(draft)

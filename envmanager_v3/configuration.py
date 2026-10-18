"""Independent V3 configuration DTO/TOML conversion and single-file storage.

Only ConfigRepository performs I/O, at its explicitly supplied path and sibling
backups directory. No discovery, migration, environment access or V2 imports.
Locations in validation errors are DTO key/index paths, starting with ``apps``.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
import tomllib
from typing import Any


MISSING_REVISION = "missing"
_MODES = ("replace", "+append", "-append", "append")


class ConfigurationError(Exception):
    """Public error independent of the environment worker's exception types."""

    def __init__(self, code: str, message: str, *, errors: list[dict] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.errors = errors or []


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le", errors="surrogatepass")) // 2


def _match_name(name: str) -> str:
    # ConfigFormat.matchName tests JS string length, i.e. UTF-16 units. Even a
    # one-codepoint supplementary uppercase is not a one-unit replacement.
    return "".join(c.upper() if _utf16_length(c.upper()) == 1 else c for c in name)


def _choices(variable: dict) -> Any:
    choices = variable.get("choices", [])
    if not isinstance(choices, list):
        return choices
    return [
        {"label": f"候选值 {i + 1}", "value": choice}
        if isinstance(choice, str) else choice
        for i, choice in enumerate(choices)
    ]


def validate_config(apps: list[dict]) -> list[dict]:
    """Return all schema/business issues without mutating the supplied DTOs.

    Missing variable properties use the TOML defaults. Unknown DTO properties
    are presentation/session state and are never part of persistence.
    """
    errors: list[dict] = []

    def add(field: str, message: str, location: list) -> None:
        errors.append({"field": field, "message": message, "location": location})

    def string(value: Any, field: str, location: list, *, limit: bool = False) -> bool:
        if not isinstance(value, str):
            add(field, "必须是字符串", location)
            return False
        if "\0" in value:
            add(field, "不能包含 NUL 字符", location)
        if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
            add(field, "必须是有效的 Unicode 字符串", location)
        if limit and _utf16_length(value) > 32766:
            add(field, "不能超过 32766 个 UTF-16 单元", location)
        return True

    if not isinstance(apps, list):
        add("apps", "应用列表必须是数组", ["apps"])
        return errors
    app_names: set[str] = set()
    targets: dict[tuple[str, str], list[tuple[bool, list]]] = {}
    for ai, app in enumerate(apps):
        aloc = ["apps", ai]
        if not isinstance(app, dict):
            add("app", "应用必须是对象", aloc)
            continue
        name = app.get("name")
        if string(name, "name", aloc + ["name"]):
            if not name.strip():
                add("name", "应用名称不能为空", aloc + ["name"])
            if name in app_names:
                add("appName", "应用名称重复：" + name, aloc + ["name"])
            app_names.add(name)
        profiles = app.get("profiles")
        if not isinstance(profiles, list):
            add("profiles", "方案列表必须是数组", aloc + ["profiles"])
            continue
        if not profiles:
            add("profile", "应用必须包含方案", aloc + ["profiles"])
        default = app.get("default")
        if string(default, "default", aloc + ["default"]):
            if not any(isinstance(p, dict) and p.get("name") == default for p in profiles):
                add("default", "默认方案必须指向现有方案", aloc + ["default"])
        profile_names: set[str] = set()
        for pi, profile in enumerate(profiles):
            ploc = aloc + ["profiles", pi]
            if not isinstance(profile, dict):
                add("profile", "方案必须是对象", ploc)
                continue
            pname = profile.get("name")
            if string(pname, "profile", ploc + ["name"]):
                if not pname.strip() or pname == "default_option":
                    add("profile", "方案名称不能为空或使用保留名 default_option", ploc + ["name"])
                if pname in profile_names:
                    add("profileName", "方案名称重复：" + pname, ploc + ["name"])
                profile_names.add(pname)
            variables = profile.get("vars")
            if not isinstance(variables, list):
                add("vars", "变量列表必须是数组", ploc + ["vars"])
                continue
            if not variables:
                add("name", "方案必须包含变量", ploc + ["vars"])
            names: dict[tuple[str, str], list[list]] = {}
            for vi, variable in enumerate(variables):
                vloc = ploc + ["vars", vi]
                if not isinstance(variable, dict):
                    add("variable", "变量必须是对象", vloc)
                    continue
                vname = variable.get("name")
                name_ok = string(vname, "name", vloc + ["name"])
                if name_ok and (not vname or "=" in vname):
                    add("name", "变量名不能为空或包含 =、NUL", vloc + ["name"])
                scope = variable.get("scope", "User")
                mode = variable.get("mode", "replace")
                editable = variable.get("editable", True)
                special = variable.get("special", False)
                if scope not in ("User", "System"):
                    add("scope", "作用域必须是 User 或 System", vloc + ["scope"])
                if mode not in _MODES:
                    add("mode", "操作模式无效", vloc + ["mode"])
                if not isinstance(editable, bool):
                    add("editable", "editable 必须是布尔值", vloc + ["editable"])
                if not isinstance(special, bool):
                    add("special", "special 必须是布尔值", vloc + ["special"])
                if special and mode != "replace":
                    add("special", "特殊变量只允许 replace 模式", vloc + ["special"])
                string(variable.get("defaultValue", ""), "defaultValue", vloc + ["defaultValue"], limit=True)
                choices = _choices(variable)
                if not isinstance(choices, list):
                    add("choices", "候选列表必须是数组", vloc + ["choices"])
                else:
                    labels: set[str] = set()
                    values: set[str] = set()
                    for ci, choice in enumerate(choices):
                        cloc = vloc + ["choices", ci]
                        if not isinstance(choice, dict):
                            add("choices", "候选必须是字符串或 label/value 对象", cloc)
                            continue
                        label, value = choice.get("label"), choice.get("value")
                        if string(label, "choices", cloc + ["label"]):
                            if not label.strip():
                                add("choices", "候选的显示名称不能为空", cloc + ["label"])
                            if label in labels:
                                add("choices", "候选显示名称重复：" + label, cloc + ["label"])
                            labels.add(label)
                        if string(value, "choices", cloc + ["value"], limit=True):
                            if value in values:
                                add("choices", "候选的实际值与其他候选重复", cloc + ["value"])
                            values.add(value)
                if name_ok and scope in ("User", "System"):
                    key = (scope, _match_name(vname))
                    names.setdefault(key, []).append(vloc)
                    targets.setdefault(key, []).append((bool(special), vloc))
            for duplicates in names.values():
                if len(duplicates) > 1:
                    for vloc in duplicates:
                        add("scope", "同名变量只允许用户与系统各一条", vloc + ["scope"])
    for variants in targets.values():
        if len({special for special, _ in variants}) > 1:
            for _, vloc in variants:
                add("special", "同一作用域和变量名的 Special 属性须在各方案中一致", vloc + ["special"])
    return errors


def _validate(apps: list[dict]) -> None:
    errors = validate_config(apps)
    if errors:
        raise ConfigurationError("invalid_configuration", "\n".join(e["message"] for e in errors), errors=errors)


def _shape_error(message: str, location: list) -> ConfigurationError:
    return ConfigurationError("invalid_configuration", message, errors=[
        {"field": "structure", "message": message, "location": location}
    ])


def parse_config(text: str) -> list[dict]:
    """Parse TOML into configuration-only UI DTOs; accept a leading UTF-8 BOM."""
    if not isinstance(text, str):
        raise ConfigurationError("invalid_toml", "配置文本必须是字符串")
    try:
        document = tomllib.loads(text.removeprefix("\ufeff"))
    except (tomllib.TOMLDecodeError, ValueError) as exc:
        raise ConfigurationError("invalid_toml", str(exc)) from exc
    apps = []
    for app_name, app_table in document.items():
        aloc = ["apps", len(apps)]
        if not isinstance(app_table, dict):
            raise _shape_error("应用必须是 TOML 表", aloc)
        profiles = []
        for profile_name, profile_table in app_table.items():
            if profile_name == "default_option":
                continue
            ploc = aloc + ["profiles", len(profiles)]
            if not isinstance(profile_table, dict):
                raise _shape_error("方案必须是 TOML 表", ploc)
            variables = []
            for variable_name, records in profile_table.items():
                records = records if isinstance(records, list) else [records]
                if not records:
                    raise _shape_error("变量记录数组不能为空", ploc + ["vars"])
                for record in records:
                    vloc = ploc + ["vars", len(variables)]
                    if not isinstance(record, dict):
                        raise _shape_error("变量必须是内联表或内联表数组", vloc)
                    variable = {
                        "name": variable_name,
                        "scope": record.get("scope", "User"),
                        "mode": record.get("mode", "replace"),
                        "defaultValue": record.get("default_value", ""),
                        "editable": record.get("editable", True),
                        "special": record.get("special", False),
                        "choices": record.get("choices", []),
                    }
                    variable["choices"] = _choices(variable)
                    variables.append(variable)
            profiles.append({"name": profile_name, "vars": variables})
        apps.append({"name": app_name, "default": app_table.get(
            "default_option", profiles[0]["name"] if profiles else ""
        ), "profiles": profiles})
    _validate(apps)
    return apps


def _quote(value: str) -> str:
    # TOML basic strings, including DEL and raw CR/LF. Never use multiline
    # strings: TOML's leading-newline trimming would corrupt environment values.
    escapes = {"\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r", '"': '\\"', "\\": "\\\\"}
    return '"' + "".join(
        escapes.get(c, f"\\u{ord(c):04x}" if ord(c) < 32 or ord(c) == 127 else c)
        for c in value
    ) + '"'


def serialize_config(apps: list[dict]) -> str:
    """Serialize the allowlisted configuration properties, after full validation."""
    _validate(apps)

    def record(variable: dict) -> str:
        fields = [
            "default_value = " + _quote(variable.get("defaultValue", "")),
            "scope = " + _quote(variable.get("scope", "User")),
            "mode = " + _quote(variable.get("mode", "replace")),
            "editable = " + str(variable.get("editable", True)).lower(),
        ]
        if variable.get("special", False):
            fields.append("special = true")
        choices = _choices(variable)
        if choices:
            fields.append("choices = [" + ", ".join(
                "{ label = " + _quote(c["label"]) + ", value = " + _quote(c["value"]) + " }"
                for c in choices
            ) + "]")
        return "{ " + ", ".join(fields) + " }"

    lines = ["# V3 configuration; compatible with the V2 TOML file format.", ""]
    for app in apps:
        lines.extend(["[" + _quote(app["name"]) + "]", "default_option = " + _quote(app["default"]), ""])
        for profile in app["profiles"]:
            lines.append("[" + _quote(app["name"]) + "." + _quote(profile["name"]) + "]")
            groups: dict[str, list[dict]] = {}
            for variable in profile["vars"]:
                # Exact spelling keeps case-distinct User/System names intact;
                # identical names use the standard dual-scope array syntax.
                groups.setdefault(variable["name"], []).append(variable)
            for name, variables in groups.items():
                body = record(variables[0]) if len(variables) == 1 else "[ " + ", ".join(map(record, variables)) + " ]"
                lines.append(_quote(name) + " = " + body)
            lines.append("")
    return "\n".join(lines)


def _revision(data: bytes | None) -> str:
    return MISSING_REVISION if data is None else hashlib.sha256(data).hexdigest()


def _record_fields(variable: dict) -> dict:
    return {
        "default_value": variable.get("defaultValue", ""),
        "scope": variable.get("scope", "User"),
        "mode": variable.get("mode", "replace"),
        "editable": variable.get("editable", True),
        "special": variable.get("special", False),
        "choices": [{"label": c["label"], "value": c["value"]} for c in _choices(variable)],
    }


def _content(apps: list[dict]) -> dict:
    """Compare named configuration contents independently of TOML table order."""
    return {app["name"]: {
        "default": app["default"],
        "profiles": {profile["name"]: {
            (v["name"], v.get("scope", "User")): _record_fields(v)
            for v in profile["vars"]
        } for profile in app["profiles"]},
    } for app in apps}


def _update_document(text: str, apps: list[dict]) -> str:
    """Patch only changed configuration fields in an existing tomlkit document.

    Omitted defaults stay omitted. Existing table/key order, whitespace, scalar
    quoting, and comments survive unchanged fields. Candidate order is data;
    it is updated in place, retaining unchanged candidate items and their trivia.
    Named nodes that are deleted/renamed necessarily lose their own old text.
    """
    try:
        import tomlkit
    except ImportError as exc:
        raise ConfigurationError("dependency_missing", "保存已有配置需要 tomlkit==0.13.3") from exc

    # An independent parser validates both the incoming original and the result.
    parse_config(text)
    try:
        document = tomlkit.parse(text)
    except ValueError as exc:
        raise ConfigurationError("invalid_toml", str(exc)) from exc

    def item(value):
        if isinstance(value, str):
            return tomlkit.string(value)
        if isinstance(value, dict):
            table = tomlkit.inline_table()
            for key, val in value.items():
                table[key] = item(val)
            return table
        if isinstance(value, list):
            array = tomlkit.array()
            for val in value:
                array.append(item(val))
            return array
        return tomlkit.item(value)

    def choices_update(node, desired):
        # Reuse array and inline-table nodes, including per-item comments in
        # multiline arrays. Legacy string entries need conversion only if edited.
        while len(node) > len(desired):
            del node[-1]
        for i, choice in enumerate(desired):
            if i >= len(node):
                node.append(item(choice))
            elif isinstance(node[i], dict):
                for key in list(node[i]):
                    if key not in ("label", "value"):
                        del node[i][key]
                for key, value in choice.items():
                    if node[i].get(key) != value:
                        node[i][key] = item(value)
            elif {"label": f"候选值 {i + 1}", "value": node[i]} != choice:
                node[i] = item(choice)

    defaults = {"default_value": "", "scope": "User", "mode": "replace",
                "editable": True, "special": False, "choices": []}

    def record_update(node, desired):
        for key in list(node):
            if key not in defaults:
                del node[key]
        for key, value in desired.items():
            previous = node.get(key, defaults[key])
            if key == "choices":
                normalized = _choices({"choices": previous})
                clean = all(not isinstance(c, dict) or set(c) <= {"label", "value"} for c in previous)
                if normalized != value or not clean:
                    if key in node:
                        choices_update(node[key], value)
                    else:
                        node[key] = item(value)
            elif previous != value:
                node[key] = item(value)

    wanted_apps = {app["name"]: app for app in apps}
    for key in list(document):
        if key not in wanted_apps:
            del document[key]
    for name, app in wanted_apps.items():
        if name not in document:
            # New named subtrees have no original trivia to retain.
            document[name] = tomlkit.parse(serialize_config([app]))[name]
            continue
        table = document[name]
        old_default = next((key for key in table if key != "default_option"), "")
        if table.get("default_option", old_default) != app["default"]:
            table["default_option"] = item(app["default"])
        wanted_profiles = {p["name"]: p for p in app["profiles"]}
        for key in list(table):
            if key != "default_option" and key not in wanted_profiles:
                del table[key]
        # A previously implicit default can change after deleting its first
        # profile. Make it explicit only if the desired default needs it.
        implicit = next((key for key in table if key != "default_option"), "")
        if "default_option" not in table and implicit != app["default"]:
            table["default_option"] = item(app["default"])
        for pname, profile in wanted_profiles.items():
            if pname not in table:
                fresh = {"name": name, "default": pname, "profiles": [profile]}
                table[pname] = tomlkit.parse(serialize_config([fresh]))[name][pname]
                continue
            ptable = table[pname]
            groups: dict[str, list[dict]] = {}
            for variable in profile["vars"]:
                groups.setdefault(variable["name"], []).append(_record_fields(variable))
            for key in list(ptable):
                if key not in groups:
                    del ptable[key]
            for key, records in groups.items():
                if key not in ptable:
                    ptable[key] = item(records[0] if len(records) == 1 else records)
                    continue
                node = ptable[key]
                if isinstance(node, list):
                    # Keep array representation even when only one scope remains.
                    while len(node) > len(records):
                        del node[-1]
                    for i, desired in enumerate(records):
                        if i >= len(node):
                            node.append(item(desired))
                        else:
                            record_update(node[i], desired)
                elif len(records) == 1:
                    record_update(node, records[0])
                else:
                    expanded = tomlkit.array()
                    # Preserve the old inline table's spelling/spacing when it
                    # is still the same first scope, while adding the new scope.
                    record_update(node, records[0])
                    expanded.append(node)
                    for desired in records[1:]:
                        expanded.append(item(desired))
                    ptable[key] = expanded
    return tomlkit.dumps(document)


class ConfigRepository:
    """Explicit-path repository with raw-byte optimistic concurrency checks.

    Saves stage/fsync/parse the entire new file, fsync an exact-byte backup of
    an existing file, then recheck the revision immediately before os.replace.
    A revision check cannot lock out an unrelated writer that ignores this
    protocol in the last instruction-sized check/replace window.
    """

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path).absolute()

    def _read_bytes(self) -> bytes | None:
        try:
            return self.path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ConfigurationError("io_error", str(exc)) from exc

    def _check_revision(self, expected_revision: str) -> bytes | None:
        current = self._read_bytes()
        if _revision(current) != expected_revision:
            raise ConfigurationError("revision_conflict", "配置文件已被外部修改，请重新加载后再保存")
        return current

    def load(self) -> dict:
        data = self._read_bytes()
        if data is None:
            return {"apps": [], "revision": MISSING_REVISION, "text": "", "path": str(self.path)}
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ConfigurationError("invalid_encoding", "配置文件必须使用 UTF-8 编码") from exc
        return {"apps": parse_config(text), "revision": _revision(data), "text": text, "path": str(self.path)}

    def save(self, apps: list[dict], expected_revision: str) -> dict:
        # All validation and in-memory roundtripping precedes any directory or
        # file creation, including for first-time invalid saves.
        standard_text = serialize_config(apps)
        canonical = parse_config(standard_text)
        if serialize_config(canonical) != standard_text:
            raise ConfigurationError("roundtrip_error", "配置序列化回读不一致")
        original = self._check_revision(expected_revision)
        text = standard_text
        if original is not None:
            try:
                old_text = original.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise ConfigurationError("invalid_encoding", "配置文件必须使用 UTF-8 编码") from exc
            text = _update_document(old_text, canonical)
        parsed = parse_config(text)
        if _content(parsed) != _content(canonical):
            raise ConfigurationError("roundtrip_error", "增量保存的配置回读不一致")
        data = text.encode("utf-8")
        result = {"apps": parsed, "revision": _revision(data), "text": text, "path": str(self.path)}
        staged: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
            staged = Path(temp_name)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            staged_data = staged.read_bytes()
            if staged_data != data or parse_config(staged_data.decode("utf-8")) != parsed:
                raise ConfigurationError("roundtrip_error", "暂存配置回读不一致")
            self._check_revision(expected_revision)
            if original is not None:
                backup_dir = self.path.parent / "backups"
                backup_dir.mkdir(exist_ok=True)
                fd, backup_name = tempfile.mkstemp(
                    prefix=f"{self.path.name}.{_revision(original)}.", suffix=".bak", dir=backup_dir
                )
                with os.fdopen(fd, "wb") as backup:
                    backup.write(original)
                    backup.flush()
                    os.fsync(backup.fileno())
                if Path(backup_name).read_bytes() != original:
                    raise ConfigurationError("backup_error", "配置备份回读不一致")
            self._check_revision(expected_revision)
            os.replace(staged, self.path)
            staged = None
        except OSError as exc:
            raise ConfigurationError("io_error", str(exc)) from exc
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)
        return result

    def import_text(self, text: str, expected_revision: str) -> dict:
        """Import supplied text into this same file, under the normal save rules."""
        return self.save(parse_config(text), expected_revision)

"""Strict TOML configuration loading and explicit atomic saving."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
import tomllib

from .engine import validate_value
from .models import Choice, Configuration, ConfigError, ConflictError, ValidationError, VariableSpec, match_key


_REQUIRED = frozenset({"default_value", "scope", "mode", "editable"})
_OPTIONAL = frozenset({"choices"})


def _assignment_shapes(text: str) -> dict[tuple[str, str, str], tuple[str, int]]:
    """Record the first token of each variable RHS, which tomllib discards."""
    shapes: dict[tuple[str, str, str], tuple[str, int]] = {}
    section: tuple[str, str] | None = None
    multiline: str | None = None
    array_depth = 0
    inline_depth = 0

    def scan(line: str) -> None:
        nonlocal multiline, array_depth, inline_depth
        quote: str | None = None
        escaped = False
        index = 0
        while index < len(line):
            char = line[index]
            if multiline is not None:
                if line.startswith(multiline, index):
                    preceding = index - 1
                    backslashes = 0
                    while preceding >= 0 and line[preceding] == "\\":
                        backslashes += 1
                        preceding -= 1
                    if multiline == "'''" or backslashes % 2 == 0:
                        multiline = None
                        index += 3
                        continue
                index += 1
                continue
            if quote is not None:
                if char == quote and not escaped:
                    quote = None
                if quote == '"' and char == "\\" and not escaped:
                    escaped = True
                else:
                    escaped = False
                index += 1
                continue
            if char == "#":
                break
            if line.startswith('"""', index) or line.startswith("'''", index):
                multiline = line[index:index + 3]
                index += 3
                continue
            if char in ("'", '"'):
                quote = char
            elif char == "[":
                array_depth += 1
            elif char == "]":
                array_depth -= 1
            elif char == "{":
                inline_depth += 1
            elif char == "}":
                inline_depth -= 1
            index += 1

    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.lstrip()
        if multiline is None and (not stripped or stripped.startswith("#")):
            continue
        if multiline is None and array_depth == 0 and inline_depth == 0 and stripped.startswith("["):
            try:
                probe = tomllib.loads(line + "\n__location_probe__ = 1\n")
                route: list[str] = []
                node = probe
                while isinstance(node, dict) and "__location_probe__" not in node:
                    if len(node) != 1:
                        break
                    name, node = next(iter(node.items()))
                    route.append(name)
                section = tuple(route) if len(route) == 2 else None
            except tomllib.TOMLDecodeError:
                section = None
            continue
        if section is not None and multiline is None and array_depth == 0 and inline_depth == 0:
            quote: str | None = None
            escaped = False
            equal = -1
            for index, char in enumerate(line):
                if quote is not None:
                    if char == quote and not escaped:
                        quote = None
                    if quote == '"' and char == "\\" and not escaped:
                        escaped = True
                    else:
                        escaped = False
                elif char in ("'", '"'):
                    quote = char
                elif char == "=":
                    equal = index
                    break
            if equal >= 0:
                lhs = line[:equal].strip()
                try:
                    parsed_key = tomllib.loads(f"{lhs} = 0\n")
                except tomllib.TOMLDecodeError:
                    pass
                else:
                    if len(parsed_key) == 1:
                        name, value = next(iter(parsed_key.items()))
                        if value == 0:  # dotted keys are rejected by structural validation
                            rhs = line[equal + 1:].lstrip()
                            shapes[(*section, name)] = (rhs[:1], number)
        scan(line)
    return shapes


def _variable(app: str, option: str, name: str, data: object, location: str) -> VariableSpec:
    if not isinstance(data, dict):
        raise ConfigError(f"{location}: 变量记录必须是内联表")
    fields = set(data)
    if missing := _REQUIRED - fields:
        raise ConfigError(f"{location}: 缺少字段 {', '.join(sorted(missing))}")
    if unknown := fields - _REQUIRED - _OPTIONAL:
        raise ConfigError(f"{location}: 未知字段 {', '.join(sorted(unknown))}")
    default = data["default_value"]
    scope = data["scope"]
    mode = data["mode"]
    editable = data["editable"]
    if not isinstance(default, str):
        raise ConfigError(f"{location}.default_value: 必须是字符串")
    if scope not in ("User", "System") or not isinstance(scope, str):
        raise ConfigError(f"{location}.scope: 必须是 User 或 System")
    if mode not in ("replace", "+append", "-append", "append") or not isinstance(mode, str):
        raise ConfigError(f"{location}.mode: 无效操作模式")
    if type(editable) is not bool:
        raise ConfigError(f"{location}.editable: 必须是布尔值")
    try:
        validate_value(default, mode)
    except ValidationError as exc:
        raise ConfigError(f"{location}.default_value: {exc}") from exc

    choices: list[Choice] = []
    if "choices" in data:
        raw_choices = data["choices"]
        if not isinstance(raw_choices, list) or not raw_choices:
            raise ConfigError(f"{location}.choices: 必须是非空数组")
        labels: set[str] = set()
        values: set[str] = set()
        for index, raw in enumerate(raw_choices):
            place = f"{location}.choices[{index}]"
            if not isinstance(raw, dict) or set(raw) != {"label", "value"}:
                raise ConfigError(f"{place}: 必须只包含 label 和 value")
            label, value = raw["label"], raw["value"]
            if not isinstance(label, str) or not label.strip():
                raise ConfigError(f"{place}.label: 必须是非空显示名称")
            if not isinstance(value, str):
                raise ConfigError(f"{place}.value: 必须是字符串")
            try:
                validate_value(value, mode)
            except ValidationError as exc:
                raise ConfigError(f"{place}.value: {exc}") from exc
            if label in labels or value in values:
                raise ConfigError(f"{place}: 候选名称或值重复")
            labels.add(label)
            values.add(value)
            choices.append(Choice(label, value))
    return VariableSpec(app, option, name, scope, mode, editable, default, tuple(choices))


def parse_config(text: str) -> Configuration:
    if not isinstance(text, str):
        raise ConfigError("配置内容必须是字符串")
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"TOML 语法错误：{exc}") from exc
    shapes = _assignment_shapes(text)
    if not raw:
        raise ConfigError("配置中没有应用方案")
    applications: dict[str, dict[str, tuple[VariableSpec, ...]]] = {}
    default_options: dict[str, str] = {}
    for app, raw_options in raw.items():
        if not isinstance(raw_options, dict) or not raw_options:
            raise ConfigError(f"[{app}]: 应用必须包含方案表")
        options: dict[str, tuple[VariableSpec, ...]] = {}
        for option, raw_variables in raw_options.items():
            if option == "default_option":
                if not isinstance(raw_variables, str):
                    raise ConfigError(f"[{app}].default_option: 必须是字符串；default_option 是保留的应用元数据键，不能用作方案名称")
                default_options[app] = raw_variables
                continue
            if not isinstance(raw_variables, dict) or not raw_variables:
                raise ConfigError(f"[{app}.{option}]: 方案必须包含变量记录")
            specs: list[VariableSpec] = []
            names: set[str] = set()
            for name, raw_record in raw_variables.items():
                location = f"[{app}.{option}].{name}"
                if not isinstance(name, str) or not name or "=" in name or "\x00" in name:
                    raise ConfigError(f"{location}: 无效环境变量名")
                key = match_key(name)
                if key in names:
                    raise ConfigError(f"{location}: 同一方案中变量名重复（忽略大小写）")
                names.add(key)
                token, line = shapes.get((app, option, name), ("", "?"))
                if isinstance(raw_record, dict):
                    if token != "{":
                        raise ConfigError(f"{location}（第 {line} 行）: 单作用域记录必须使用内联表")
                    specs.append(_variable(app, option, name, raw_record, location))
                elif isinstance(raw_record, list):
                    if token != "[" or len(raw_record) != 2:
                        raise ConfigError(f"{location}（第 {line} 行）: 双作用域必须是两条内联表的数组")
                    pair = tuple(_variable(app, option, name, part, f"{location}[{index}]")
                                 for index, part in enumerate(raw_record))
                    if {part.scope for part in pair} != {"User", "System"}:
                        raise ConfigError(f"{location}: 双作用域必须各包含一条 User 与 System 记录")
                    specs.extend(pair)
                else:
                    raise ConfigError(f"{location}: 变量记录必须是内联表或双作用域数组")
            options[option] = tuple(specs)
        if not options:
            raise ConfigError(f"[{app}]: 应用必须包含方案表")
        if app in default_options and default_options[app] not in options:
            raise ConfigError(f"[{app}].default_option: 引用的默认方案 {default_options[app]!r} 不存在")
        applications[app] = options
    return Configuration(applications, hashlib.sha256(text.encode("utf-8")).hexdigest(), default_options)


def load_config(path: str | os.PathLike[str]) -> Configuration:
    try:
        text = Path(path).read_bytes().decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"无法读取 UTF-8 配置 {path}: {exc}") from exc
    return parse_config(text)


def save_config(path: str | os.PathLike[str], text: str, *, expected_content: bytes | None = None) -> Configuration:
    config = parse_config(text)
    destination = Path(path)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=destination.parent,
                                         prefix=f".{destination.name}.", suffix=".tmp", delete=False) as handle:
            temporary = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if expected_content is not None:
            try:
                actual = destination.read_bytes()
            except OSError as exc:
                raise ConflictError("配置文件已移动、删除或无法读取；请重新加载后再保存") from exc
            if actual != expected_content:
                raise ConflictError("配置文件已被其他程序修改；草稿已保留，请重新加载或另行保存")
        os.replace(temporary, destination)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return config

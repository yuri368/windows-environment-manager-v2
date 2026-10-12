"""Pure validation and calculation of environment-variable changes."""

from __future__ import annotations

from collections import Counter
import re
from typing import Sequence

from .elements import CleanupSettings, cleanup_items, cleanup_value, normalize_element
from .models import Calculation, RegistryState, ValidationError, VariableSpec, match_key

MODES = frozenset({"replace", "+append", "-append", "append"})
REG_SZ = 1
REG_EXPAND_SZ = 2
MAX_VALUE_UTF16_UNITS = 32766
_REFERENCE = re.compile(r"%[^%\x00]+%")


def validate_value(value: str, mode: str, special: bool = False) -> None:
    if not isinstance(mode, str) or mode not in MODES:
        raise ValidationError(f"不支持的操作模式：{mode!r}")
    if type(special) is not bool:
        raise ValidationError("special 必须是布尔值")
    if special and mode != "replace":
        raise ValidationError("特殊变量只允许 replace 模式")
    if not isinstance(value, str):
        raise ValidationError("值必须是字符串")
    if "\x00" in value:
        raise ValidationError("值不能包含 NUL 字符")
    if len(value.encode("utf-16-le")) // 2 > MAX_VALUE_UTF16_UNITS:
        raise ValidationError("值超过 Windows 环境变量长度限制")


def _result_state(spec: VariableSpec, existing: RegistryState, value: str) -> RegistryState:
    if len(value.encode("utf-16-le")) // 2 > MAX_VALUE_UTF16_UNITS:
        raise ValidationError("最终值超过 Windows 环境变量长度限制")
    registry_type = (existing.registry_type if existing.exists else
                     REG_EXPAND_SZ if _REFERENCE.search(value) else REG_SZ)
    return RegistryState(spec.name, spec.scope, True, value, registry_type)


def _validate_existing(spec: VariableSpec, existing: RegistryState) -> None:
    if existing.scope != spec.scope or match_key(existing.name) != match_key(spec.name):
        raise ValidationError("现有变量与配置的名称或作用域不一致")
    if existing.exists:
        if not isinstance(existing.value, str):
            raise ValidationError("现有变量的原始值必须是字符串")
        if type(existing.registry_type) is not int or existing.registry_type not in (REG_SZ, REG_EXPAND_SZ):
            raise ValidationError(f"不支持的注册表类型：{existing.registry_type!r}")
    elif existing.value is not None or existing.registry_type is not None:
        raise ValidationError("不存在的变量不能携带值或注册表类型")


def _confirmed(items: tuple[str, ...], confirmed_order: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(confirmed_order, tuple) or not all(isinstance(x, str) for x in confirmed_order):
        raise ValidationError("确认排序必须是字符串元组")
    if Counter(confirmed_order) != Counter(items):
        raise ValidationError("确认排序必须完整保留初始列表中的每一项及其重复次数")
    return confirmed_order


def calculate(spec: VariableSpec, selected_value: str, existing: RegistryState,
              confirmed_order: tuple[str, ...] | None = None,
              cleanup: CleanupSettings | None = None) -> Calculation:
    return calculate_batch(((spec, selected_value),), existing, confirmed_order, cleanup)


def calculate_batch(contributions: Sequence[tuple[VariableSpec, str]],
                    existing: RegistryState,
                    confirmed_order: tuple[str, ...] | None = None,
                    cleanup: CleanupSettings | None = None) -> Calculation:
    """Compose one target, applying cleanup only to this calculated write."""
    records = tuple(contributions)
    if not records:
        raise ValidationError("批量计算至少需要一条变量记录")
    settings = cleanup if cleanup is not None else CleanupSettings()
    if not isinstance(settings, CleanupSettings):
        raise ValidationError("自动整理设置类型无效")
    spec = records[0][0]
    _validate_existing(spec, existing)
    for record, value in records:
        if record.target != spec.target:
            raise ValidationError("批量计算的记录必须属于同一变量和作用域")
        if record.special != spec.special:
            raise ValidationError("同一目标的 special 定义必须一致")
        validate_value(value, record.mode, record.special)
    modes = {record.mode for record, _ in records}
    original = ((existing.value,) if existing.exists else ()) if spec.special else (
        tuple(existing.value.split(";")) if existing.exists and existing.value else ())
    if "replace" in modes:
        if modes != {"replace"}:
            raise ValidationError("同一变量的 replace 与追加操作冲突")
        if confirmed_order is not None:
            raise ValidationError("replace 模式不接受排序结果")
        values = {value if spec.special else cleanup_value(value, settings) for _, value in records}
        if len(values) != 1:
            raise ValidationError("同一变量的多个 replace 最终输入及顺序必须完全相同")
        value = next(iter(values))
        warnings = () if spec.special or value == records[0][1] else ("普通值已按高级设置自动整理",)
        items = () if spec.special else cleanup_items(records[0][1].split(";"), settings)
        duplicates = _duplicates(records[0][1].split(";")) if not spec.special and settings.deduplicate else ()
        return Calculation(_result_state(spec, existing, value), items=items,
                           deduplicated=duplicates, warnings=warnings, original_items=original)

    manual = "append" in modes
    if not manual and confirmed_order is not None:
        raise ValidationError("仅 append 模式接受排序结果")
    inputs: list[str] = []
    input_by_key: dict[str, str] = {}
    placement: dict[str, str] = {}
    directed_inputs: list[tuple[str, str]] = []
    for record, value in records:
        # The unique empty selected value does not append an empty slice.
        for raw in value.split(";") if value else ():
            key = normalize_element(raw)
            if key and not manual and key in placement and placement[key] != record.mode:
                raise ValidationError(f"同一列表项同时要求置前和置后：{key}")
            if key:
                placement.setdefault(key, record.mode)
                input_by_key.setdefault(key, raw)
            inputs.append(raw)
            directed_inputs.append((record.mode, raw))
    original_positions: dict[str, int] = {}
    for index, raw in enumerate(original):
        key = normalize_element(raw)
        if key and key in input_by_key:
            original_positions.setdefault(key, index)
    added = tuple(raw for raw in cleanup_items(inputs, settings)
                  if normalize_element(raw) and normalize_element(raw) not in original_positions)
    if manual:
        # Keep existing positions for the first input copy; newly introduced
        # elements go to the end before the one complete sorting confirmation.
        merged: list[str] = []
        used: set[str] = set()
        existing_counts: Counter[str] = Counter()
        selected_counts = Counter(normalize_element(raw) for raw in inputs)
        for raw in original:
            key = normalize_element(raw)
            if key in input_by_key:
                existing_counts[key] += 1
                if key not in used:
                    merged.extend(item for item in inputs if normalize_element(item) == key)
                    used.add(key)
                elif not settings.deduplicate and existing_counts[key] > selected_counts[key]:
                    merged.append(raw)
            else:
                merged.append(raw)
        merged.extend(raw for raw in inputs if not normalize_element(raw) or normalize_element(raw) not in used)
    else:
        untouched = [raw for raw in original if normalize_element(raw) not in input_by_key]
        prefix = [raw for mode, raw in directed_inputs if mode == "+append"]
        suffix = [raw for mode, raw in directed_inputs if mode == "-append"]
        if not settings.deduplicate:
            selected_counts = Counter(normalize_element(raw) for raw in inputs)
            for key in input_by_key:
                extras = [raw for raw in original if normalize_element(raw) == key][selected_counts[key]:]
                (prefix if placement[key] == "+append" else suffix).extend(extras)
        # Front insertion keeps the front copy; back insertion keeps the back
        # copy. Unselected existing duplicates retain their earliest copy.
        merged = list(cleanup_items(prefix, settings)) + untouched + list(
            cleanup_items(suffix, settings, prefer_last=True))
    items = cleanup_items(merged, settings)
    needs_order = manual and confirmed_order is None
    if manual and confirmed_order is not None:
        items = _confirmed(items, confirmed_order)
    moved = tuple(raw for index, raw in enumerate(items)
                  if normalize_element(raw) in original_positions
                  and original_positions[normalize_element(raw)] != index)
    duplicates = _duplicates(merged) if settings.deduplicate else ()
    # Include matched old copies removed during input placement in preview data.
    if settings.deduplicate:
        duplicates = _duplicates(original) + _duplicates(inputs)
    warnings: list[str] = []
    if (tuple(merged) != cleanup_items(merged, settings)
            or tuple(inputs) != cleanup_items(inputs, settings) or duplicates):
        warnings.append("普通值已按高级设置自动整理（空白、空项或重复元素）")
    return Calculation(_result_state(spec, existing, ";".join(items)),
                       items=items, added=added, moved=moved,
                       deduplicated=duplicates, warnings=tuple(warnings),
                       needs_order=needs_order, original_items=original)


def _duplicates(items: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for raw in items:
        key = normalize_element(raw)
        if key and key in seen:
            duplicates.append(raw)
        elif key:
            seen.add(key)
    return tuple(duplicates)

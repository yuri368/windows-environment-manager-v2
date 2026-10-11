"""Pure validation and calculation of environment-variable changes."""

from __future__ import annotations

from collections import Counter
import re
from typing import Sequence

from .models import Calculation, RegistryState, ValidationError, VariableSpec, match_key


MODES = frozenset({"replace", "+append", "-append", "append"})
REG_SZ = 1
REG_EXPAND_SZ = 2
MAX_VALUE_UTF16_UNITS = 32766
_REFERENCE = re.compile(r"%[^%\x00]+%")


def validate_value(value: str, mode: str) -> None:
    if not isinstance(mode, str) or mode not in MODES:
        raise ValidationError(f"不支持的操作模式：{mode!r}")
    if not isinstance(value, str):
        raise ValidationError("值必须是字符串")
    if "\x00" in value:
        raise ValidationError("值不能包含 NUL 字符")
    if value and not value.strip():
        raise ValidationError("非空值不能只包含空白字符")
    if mode != "replace" and value:
        for index, item in enumerate(value.split(";"), start=1):
            if not item.strip():
                raise ValidationError(f"列表第 {index} 项为空或仅含空白")
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


def calculate(
    spec: VariableSpec,
    selected_value: str,
    existing: RegistryState,
    confirmed_order: tuple[str, ...] | None = None,
) -> Calculation:
    validate_value(selected_value, spec.mode)
    _validate_existing(spec, existing)

    if spec.mode == "replace":
        if confirmed_order is not None:
            raise ValidationError("replace 模式不接受排序结果")
        return Calculation(result=_result_state(spec, existing, selected_value))

    original = tuple(existing.value.split(";")) if existing.exists and existing.value else ()
    raw_input = selected_value.split(";") if selected_value else []
    inputs: list[str] = []
    seen: set[str] = set()
    input_duplicates: list[str] = []
    for item in raw_input:
        key = match_key(item)
        if key in seen:
            input_duplicates.append(item)
        else:
            inputs.append(item)
            seen.add(key)

    matching = {match_key(item) for item in inputs}
    untouched = tuple(item for item in original if match_key(item) not in matching)
    original_positions: dict[str, int] = {}
    deduplicated_existing: list[str] = []
    for index, item in enumerate(original):
        key = match_key(item)
        if key in matching:
            if key in original_positions:
                deduplicated_existing.append(item)
            else:
                original_positions[key] = index
    added = tuple(item for item in inputs if match_key(item) not in original_positions)
    deduplicated = tuple(deduplicated_existing + input_duplicates)

    if spec.mode == "+append":
        items = tuple(inputs) + untouched
    else:
        items = untouched + tuple(inputs)

    def moved_items(order: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(item for index, item in enumerate(order) if
                     match_key(item) in original_positions and
                     original_positions[match_key(item)] != index and
                     match_key(item) in matching)

    moved = moved_items(items)
    warnings: list[str] = []
    if any(not item.strip() for item in original):
        warnings.append("现有列表含空项或纯空白项，已原样保留未匹配内容")
    if input_duplicates:
        warnings.append("输入列表含重复项，仅保留首次出现项")
    if any(count > 1 for count in Counter(match_key(item) for item in original).values()):
        warnings.append("现有列表含重复项；未匹配的重复项已原样保留")

    if spec.mode == "append":
        if confirmed_order is None:
            return Calculation(
                result=_result_state(spec, existing, ";".join(items)),
                items=items, added=added, moved=moved, deduplicated=deduplicated,
                warnings=tuple(warnings), needs_order=True, original_items=original,
            )
        if not isinstance(confirmed_order, tuple) or not all(isinstance(x, str) for x in confirmed_order):
            raise ValidationError("确认排序必须是字符串元组")
        if Counter(confirmed_order) != Counter(items):
            raise ValidationError("确认排序必须完整保留初始列表中的每一项及其重复次数")
        items = confirmed_order
        moved = moved_items(items)
    elif confirmed_order is not None:
        raise ValidationError("仅 append 模式接受排序结果")

    return Calculation(
        result=_result_state(spec, existing, ";".join(items)),
        items=items, added=added, moved=moved, deduplicated=deduplicated,
        warnings=tuple(warnings), original_items=original,
    )


def calculate_batch(
    contributions: Sequence[tuple[VariableSpec, str]],
    existing: RegistryState,
    confirmed_order: tuple[str, ...] | None = None,
) -> Calculation:
    """Calculate one target once from every selected application's contribution."""
    records = tuple(contributions)
    if not records:
        raise ValidationError("批量计算至少需要一条变量记录")
    if len(records) == 1:
        spec, value = records[0]
        return calculate(spec, value, existing, confirmed_order)
    spec = records[0][0]
    _validate_existing(spec, existing)
    for record, value in records:
        if record.target != spec.target:
            raise ValidationError("批量计算的记录必须属于同一变量和作用域")
        validate_value(value, record.mode)
    modes = {record.mode for record, _ in records}
    if "replace" in modes:
        if modes != {"replace"}:
            raise ValidationError("同一变量的 replace 与追加操作冲突")
        values = {value for _, value in records}
        if len(values) != 1:
            raise ValidationError("同一变量的多个 replace 输入必须完全相同")
        if confirmed_order is not None:
            raise ValidationError("replace 模式不接受排序结果")
        return Calculation(result=_result_state(spec, existing, records[0][1]))

    manual = "append" in modes
    if not manual and confirmed_order is not None:
        raise ValidationError("仅 append 模式接受排序结果")
    original = tuple(existing.value.split(";")) if existing.exists and existing.value else ()
    inputs: list[str] = []
    input_by_key: dict[str, str] = {}
    placement: dict[str, str] = {}
    input_duplicates: list[str] = []
    for record, value in records:
        for item in value.split(";") if value else ():
            key = match_key(item)
            if not manual and key in placement and placement[key] != record.mode:
                raise ValidationError(f"同一列表项同时要求置前和置后：{item}")
            placement.setdefault(key, record.mode)
            if key in input_by_key:
                input_duplicates.append(item)
            else:
                inputs.append(item)
                input_by_key[key] = item

    original_positions: dict[str, int] = {}
    deduplicated_existing: list[str] = []
    merged_existing: list[str] = []
    untouched: list[str] = []
    for index, item in enumerate(original):
        key = match_key(item)
        if key not in input_by_key:
            untouched.append(item)
            merged_existing.append(item)
        elif key in original_positions:
            deduplicated_existing.append(item)
        else:
            original_positions[key] = index
            merged_existing.append(input_by_key[key])
    added = tuple(item for item in inputs if match_key(item) not in original_positions)
    if manual:
        items = tuple(merged_existing) + added
    else:
        prefix = tuple(item for item in inputs if placement[match_key(item)] == "+append")
        suffix = tuple(item for item in inputs if placement[match_key(item)] == "-append")
        items = prefix + tuple(untouched) + suffix

    warnings: list[str] = []
    if any(not item.strip() for item in original):
        warnings.append("现有列表含空项或纯空白项，已原样保留未匹配内容")
    if input_duplicates:
        warnings.append("输入列表含重复项，仅保留首次出现项")
    if any(count > 1 for count in Counter(match_key(item) for item in original).values()):
        warnings.append("现有列表含重复项；未匹配的重复项已原样保留")
    needs_order = manual and confirmed_order is None
    if manual and confirmed_order is not None:
        if not isinstance(confirmed_order, tuple) or not all(isinstance(x, str) for x in confirmed_order):
            raise ValidationError("确认排序必须是字符串元组")
        if Counter(confirmed_order) != Counter(items):
            raise ValidationError("确认排序必须完整保留初始列表中的每一项及其重复次数")
        items = confirmed_order
    moved = tuple(item for index, item in enumerate(items)
                  if match_key(item) in original_positions
                  and original_positions[match_key(item)] != index)
    return Calculation(
        result=_result_state(spec, existing, ";".join(items)),
        items=items, added=added, moved=moved,
        deduplicated=tuple(deduplicated_existing + input_duplicates),
        warnings=tuple(warnings), needs_order=needs_order, original_items=original,
    )

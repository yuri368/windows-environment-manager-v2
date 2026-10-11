"""Pure removal scope expansion, actual-match candidates and target composition.

All inputs are snapshots. This module never reads or writes Windows or history.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Iterable, Mapping, Sequence

from .engine import REG_EXPAND_SZ, REG_SZ, validate_value
from .elements import CleanupSettings, cleanup_value, effective_elements, normalize_element
from .matching import match_variable
from .models import (
    Calculation, Configuration, ConflictError, PlanChange, RegistryState, ValidationError,
    VariableSpec, match_key,
)

if TYPE_CHECKING:
    from .sharing import ConflictDecision, SharedConflict

TargetKey = tuple[str, str]


class RemovalScope(str, Enum):
    VARIABLE = "variable"
    SCHEME = "scheme"
    APPLICATION_ALL = "application_all"
    BATCH_CURRENT = "batch_current"
    BATCH_ALL = "batch_all"


@dataclass(frozen=True)
class RemovalRequest:
    scope: RemovalScope
    selected_apps: frozenset[str]
    selected_options: Mapping[str, str] = field(default_factory=dict)
    variable_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "scope", RemovalScope(self.scope))
        except ValueError as exc:
            raise ValidationError(f"不支持的撤销范围：{self.scope!r}") from exc
        object.__setattr__(self, "selected_apps", frozenset(self.selected_apps))
        object.__setattr__(self, "selected_options", MappingProxyType(dict(self.selected_options)))
        object.__setattr__(self, "variable_keys", tuple(self.variable_keys))


class RemovalKind(str, Enum):
    WHOLE_KEY = "whole_key"
    LIST_ELEMENT = "list_element"
    EMPTY_VALUE = "empty_value"


@dataclass(frozen=True)
class RemovalSource:
    app: str
    option: str
    spec_key: str
    mode: str
    selected_value: str = ""


@dataclass(frozen=True)
class RemovalAtom:
    target: TargetKey
    kind: RemovalKind
    element_key: str | None
    display_value: str
    sources: tuple[RemovalSource, ...]
    occurrence_count: int = 1

    @property
    def atom_id(self) -> str:
        """Stable identity independent of source order, spelling and display text."""
        return json.dumps([self.target[0], match_key(self.target[1]),
                           self.kind.value, self.element_key],
                          ensure_ascii=False, separators=(",", ":"))

    @property
    def duplicate_count(self) -> int:
        return max(0, self.occurrence_count - 1)


@dataclass(frozen=True)
class SkippedRemoval:
    source: RemovalSource
    target: TargetKey
    reason: str
    display_value: str


@dataclass(frozen=True)
class RemovalCandidates:
    requested_sources: tuple[RemovalSource, ...]
    atoms: tuple[RemovalAtom, ...]
    skipped: tuple[SkippedRemoval, ...]


def expand_sources(config: Configuration, request: RemovalRequest) -> tuple[VariableSpec, ...]:
    """Expand one of the five explicit ranges in configuration order."""
    apps = request.selected_apps
    if not apps or not apps.issubset(config.applications):
        raise ValidationError("撤销请求必须选择实际存在的应用")
    single = request.scope in (RemovalScope.VARIABLE, RemovalScope.SCHEME,
                               RemovalScope.APPLICATION_ALL)
    if single and len(apps) != 1:
        raise ValidationError("单变量、单方案和单应用撤销必须只选择一个应用")
    current = request.scope in (RemovalScope.VARIABLE, RemovalScope.SCHEME,
                                RemovalScope.BATCH_CURRENT)
    result: list[VariableSpec] = []
    for app in config.apps():
        if app not in apps:
            continue
        if current:
            option = request.selected_options.get(app)
            if option not in config.applications[app]:
                raise ValidationError(f"应用 {app} 的撤销方案不存在或未指定")
            options = (option,)
        else:
            options = config.options(app)
        for option in options:
            result.extend(config.variables(app, option))
    if request.scope == RemovalScope.VARIABLE:
        if len(request.variable_keys) != 1:
            raise ValidationError("单变量撤销必须指定一条变量记录")
        wanted = request.variable_keys[0]
        result = [spec for spec in result if spec.key == wanted]
        if len(result) != 1:
            raise ValidationError("指定变量不属于请求中的应用和方案")
    elif request.variable_keys:
        raise ValidationError("只有单变量撤销接受变量记录标识")
    return tuple(result)


def snapshot_state(states: Mapping[TargetKey, RegistryState], target: TargetKey) -> RegistryState:
    """Require an explicit snapshot; missing map entries are not absent keys."""
    try:
        state = states[target]
    except KeyError as exc:
        raise ValidationError(f"缺少目标的原始状态快照：{target}") from exc
    if (state.scope, match_key(state.name)) != target:
        raise ValidationError("目标状态快照的名称或作用域不一致")
    if state.exists:
        if not isinstance(state.value, str):
            raise ValidationError("存在的变量快照必须包含原始字符串")
        if type(state.registry_type) is not int or state.registry_type not in (REG_SZ, REG_EXPAND_SZ):
            raise ValidationError(f"不支持的注册表类型：{state.registry_type!r}")
    elif state.value is not None or state.registry_type is not None:
        raise ValidationError("不存在的变量快照不能包含值或类型")
    return state


def selected_input(spec: VariableSpec, selected_values: Mapping[str, str]) -> str:
    try:
        value = selected_values[spec.key]
    except KeyError as exc:
        raise ValidationError(f"缺少变量的当前选定值：{spec.key}") from exc
    validate_value(value, spec.mode, special=spec.special)
    return value


def input_elements(value: str) -> tuple[str, ...]:
    """Stable normalized, case-sensitive nonempty elements."""
    return effective_elements(value)


def build_removal_atoms(
    specs: Iterable[VariableSpec],
    states: Mapping[TargetKey, RegistryState],
    selected_values: Mapping[str, str],
) -> RemovalCandidates:
    """Keep actual ordinary elements, exact empty states or exact special values."""
    sources: list[RemovalSource] = []
    atoms: dict[str, RemovalAtom] = {}
    skipped: list[SkippedRemoval] = []
    seen_specs: set[str] = set()
    for spec in specs:
        if spec.key in seen_specs:
            continue
        seen_specs.add(spec.key)
        value = selected_input(spec, selected_values)
        state = snapshot_state(states, spec.target)
        source = RemovalSource(spec.app, spec.option, spec.key, spec.mode, value)
        sources.append(source)
        candidates: list[RemovalAtom] = []
        matched = match_variable(spec, value, state)
        if spec.special or not input_elements(value):
            required_value = value if spec.special else ""
            if not state.exists or state.value != required_value:
                skipped.append(SkippedRemoval(source, spec.target,
                               "key_missing" if not state.exists else "value_mismatch", value))
            else:
                candidates.append(RemovalAtom(spec.target, RemovalKind.WHOLE_KEY if spec.special
                                              else RemovalKind.EMPTY_VALUE,
                                              None, required_value, (source,)))
        else:
            elements = input_elements(value)
            if not elements:
                skipped.append(SkippedRemoval(source, spec.target, "empty_input", value))
            for element in elements:
                key = element
                if key in matched.matched_elements:
                    candidates.append(RemovalAtom(spec.target, RemovalKind.LIST_ELEMENT,
                                                  key, element, (source,), matched.occurrence_counts[key]))
                else:
                    skipped.append(SkippedRemoval(source, spec.target,
                                   "element_missing" if state.exists else "key_missing", element))
        for atom in candidates:
            existing = atoms.get(atom.atom_id)
            atoms[atom.atom_id] = (replace(existing, sources=existing.sources + atom.sources)
                                  if existing else atom)
    return RemovalCandidates(tuple(sources), tuple(atoms.values()), tuple(skipped))


def compose_removal(
    ordinary_atoms: Sequence[RemovalAtom],
    shared_conflicts: Sequence[SharedConflict],
    decisions: Mapping[str, ConflictDecision | str],
    states: Mapping[TargetKey, RegistryState],
    cleanup: CleanupSettings | None = None,
) -> tuple[PlanChange, ...]:
    """Compose ordinary removals and explicit shared REMOVE into one change/key.

    Unspecified conflicts default to KEEP. All metadata, including consumers of
    explicitly removed shared atoms, remains available in change contributions.
    """
    from .sharing import ConflictDecision

    conflict_ids = {conflict.atom.atom_id for conflict in shared_conflicts}
    if set(decisions) - conflict_ids:
        raise ValidationError("撤销决定包含不属于共享冲突的对象")
    resolved: dict[str, ConflictDecision] = {}
    for conflict in shared_conflicts:
        try:
            resolved[conflict.atom.atom_id] = ConflictDecision(
                decisions.get(conflict.atom.atom_id, ConflictDecision.KEEP))
        except ValueError as exc:
            raise ValidationError("共享对象决定必须是 KEEP 或 REMOVE") from exc
    removed = {atom.atom_id: atom for atom in ordinary_atoms}
    kept: dict[str, RemovalAtom] = {}
    conflicts_by_id = {conflict.atom.atom_id: conflict for conflict in shared_conflicts}
    if set(removed) & conflict_ids:
        raise ValidationError("同一个对象不能同时是普通撤销项和共享冲突项")
    for conflict in shared_conflicts:
        atom = conflict.atom
        if resolved[atom.atom_id] == ConflictDecision.REMOVE:
            removed[atom.atom_id] = atom
        else:
            kept[atom.atom_id] = atom
    by_target: dict[TargetKey, list[RemovalAtom]] = {}
    for atom in removed.values():
        by_target.setdefault(atom.target, []).append(atom)
    for atom in kept.values():
        by_target.setdefault(atom.target, [])
    changes: list[PlanChange] = []
    for target, atoms in by_target.items():
        before = snapshot_state(states, target)
        whole = any(atom.kind in (RemovalKind.WHOLE_KEY, RemovalKind.EMPTY_VALUE) for atom in atoms)
        retained = [atom for atom in kept.values() if atom.target == target]
        if whole and any(atom.kind == RemovalKind.LIST_ELEMENT for atom in retained):
            raise ConflictError(f"整键删除与列表元素保留互斥：{target}")
        if not before.exists:
            raise ValidationError("撤销对象对应的键已不存在，必须重新分析")
        for atom in atoms:
            if atom.kind in (RemovalKind.WHOLE_KEY, RemovalKind.EMPTY_VALUE):
                if before.value != atom.display_value:
                    raise ValidationError("完整变量已改变，必须重新分析撤销")
            elif atom.element_key not in effective_elements(before.value):
                raise ValidationError("待撤销元素已不存在，必须重新分析")
        original = before.value.split(";")
        uncleaned = before.value
        if whole:
            after = RegistryState(before.name, before.scope, False)
        else:
            remove_keys = {atom.element_key for atom in atoms}
            remaining = [x for x in original if normalize_element(x) not in remove_keys]
            uncleaned = ";".join(remaining)
            special_target = any(atom.kind == RemovalKind.WHOLE_KEY for atom in retained)
            if atoms and not effective_elements(uncleaned):
                after = RegistryState(before.name, before.scope, False)
            else:
                final = uncleaned if special_target else cleanup_value(uncleaned, cleanup)
                after = RegistryState(before.name, before.scope, True, final, before.registry_type)
        for atom in retained:
            if atom.kind == RemovalKind.LIST_ELEMENT and (not after.exists or
                    atom.element_key not in effective_elements(after.value)):
                raise ConflictError(f"撤销结果不能丢失保留的共享元素：{target}")
            if atom.kind in (RemovalKind.WHOLE_KEY, RemovalKind.EMPTY_VALUE) and not after.same_content(before):
                raise ConflictError(f"撤销结果不能改变保留的共享值状态：{target}")
        contributions: list[dict] = []
        for atom in atoms + retained:
            conflict = conflicts_by_id.get(atom.atom_id)
            contributions.append({
                "atom_id": atom.atom_id, "kind": atom.kind.value,
                "element_key": atom.element_key, "display_value": atom.display_value,
                "occurrence_count": atom.occurrence_count,
                "decision": resolved[atom.atom_id].value if conflict else "remove",
                "sources": [vars(source) for source in atom.sources],
                "consumers": [vars(consumer) for consumer in conflict.consumers] if conflict else [],
            })
        if not before.same_content(after):
            cleaned = after.exists and after.value != uncleaned
            warnings = ("已自动整理普通元素的空白、空项或重复项",) if cleaned else ()
            if cleaned:
                contributions.append({"kind": "cleanup", "before_value": uncleaned,
                                      "after_value": after.value, "decision": "cleanup"})
            calculation = Calculation(after, tuple((after.value or "").split(";")) if after.exists else (),
                                      warnings=warnings, original_items=tuple(original))
            changes.append(PlanChange(before, after, "remove", "", calculation=calculation,
                                      contributions=tuple(contributions)))
    return tuple(changes)

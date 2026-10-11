"""Pure cross-application protection of requirements actually present in Windows."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping

from .models import Configuration, RegistryState, ValidationError, VariableSpec, match_key
from .removal import (
    RemovalAtom, RemovalKind, TargetKey, input_elements, selected_input, snapshot_state,
)


class ConflictDecision(str, Enum):
    KEEP = "keep"
    REMOVE = "remove"


@dataclass(frozen=True)
class ProtectedConsumer:
    app: str
    option: str
    spec_key: str
    mode: str
    reason: str


@dataclass(frozen=True)
class SharedConflict:
    atom: RemovalAtom
    consumers: tuple[ProtectedConsumer, ...]
    default_decision: ConflictDecision = ConflictDecision.KEEP


@dataclass(frozen=True)
class SharingAnalysis:
    ordinary_atoms: tuple[RemovalAtom, ...]
    shared_conflicts: tuple[SharedConflict, ...]


def protected_specs(config: Configuration, selected_apps: Iterable[str]) -> tuple[VariableSpec, ...]:
    selected = frozenset(selected_apps)
    if not selected.issubset(config.applications):
        raise ValidationError("共享保护范围包含不存在的应用")
    return tuple(spec for app in config.apps() if app not in selected
                 for option in config.options(app) for spec in config.variables(app, option))


def find_protected_consumers(
    atom: RemovalAtom,
    specs: Iterable[VariableSpec],
    states: Mapping[TargetKey, RegistryState],
    selected_values: Mapping[str, str],
) -> tuple[ProtectedConsumer, ...]:
    """List every impacted record, including present elements of partial records."""
    state = snapshot_state(states, atom.target)
    if not state.exists:
        return ()
    actual_keys = {match_key(x) for x in state.value.split(";")} if state.value else set()
    consumers: dict[str, ProtectedConsumer] = {}
    for spec in specs:
        if spec.target != atom.target:
            continue
        selected = selected_input(spec, selected_values)
        reason: str | None = None
        if spec.mode == "replace":
            if state.value == selected:
                if atom.kind == RemovalKind.WHOLE_KEY:
                    reason = "整键删除会破坏当前完全匹配的 replace 完整值"
                elif atom.element_key in actual_keys:
                    reason = f"删除元素 {atom.display_value!r} 会破坏当前完全匹配的 replace 完整值"
        else:
            matched = tuple(x for x in input_elements(selected) if match_key(x) in actual_keys)
            if atom.kind == RemovalKind.WHOLE_KEY:
                if matched:
                    reason = "整键删除会移除已匹配的列表元素：" + ";".join(matched)
                elif not selected:
                    # Empty append still requires the key to exist; deleting it
                    # destroys that matched requirement although it has no atom.
                    reason = "整键删除会破坏空追加输入所要求的键存在状态"
            elif any(match_key(x) == atom.element_key for x in matched):
                reason = f"删除已匹配的列表元素：{atom.display_value}"
        if reason:
            consumers[spec.key] = ProtectedConsumer(spec.app, spec.option, spec.key, spec.mode, reason)
    return tuple(consumers.values())


def classify_conflicts(
    config: Configuration,
    selected_apps: Iterable[str],
    atoms: Iterable[RemovalAtom],
    states: Mapping[TargetKey, RegistryState],
    selected_values: Mapping[str, str],
) -> SharingAnalysis:
    """Protect exactly the complement of selected apps, across all options."""
    protected = protected_specs(config, selected_apps)
    ordinary: list[RemovalAtom] = []
    conflicts: list[SharedConflict] = []
    for atom in atoms:
        consumers = find_protected_consumers(atom, protected, states, selected_values)
        if consumers:
            conflicts.append(SharedConflict(atom, consumers))
        else:
            ordinary.append(atom)
    return SharingAnalysis(tuple(ordinary), tuple(conflicts))

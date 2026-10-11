"""Pure cross-application protection of requirements actually present in Windows."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping

from .models import Configuration, RegistryState, ValidationError, VariableSpec
from .elements import effective_elements
from .matching import match_variable
from .removal import (
    RemovalAtom, RemovalKind, TargetKey, selected_input, snapshot_state,
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
    consumers: dict[str, ProtectedConsumer] = {}
    for spec in specs:
        if spec.target != atom.target:
            continue
        selected = selected_input(spec, selected_values)
        matched = match_variable(spec, selected, state)
        reason: str | None = None
        if spec.special:
            if matched.complete_match:
                if atom.kind == RemovalKind.WHOLE_KEY:
                    reason = "整键删除会破坏当前匹配的特殊完整值"
        elif not effective_elements(selected):
            if matched.complete_match and atom.kind in (RemovalKind.EMPTY_VALUE, RemovalKind.WHOLE_KEY):
                reason = "删除键会破坏已匹配的键存在且为空状态"
        else:
            if atom.kind == RemovalKind.WHOLE_KEY:
                if matched.matched_elements:
                    reason = "整键删除会移除已匹配的元素：" + ";".join(matched.matched_elements)
            elif atom.kind == RemovalKind.LIST_ELEMENT and atom.element_key in matched.matched_elements:
                reason = f"删除 {spec.mode} 已匹配的元素：{atom.display_value}"
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

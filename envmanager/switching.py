"""Pure, net-state scheme switching over frozen Windows snapshots.

Old matches are removed virtually, then the existing application engine computes
the new scheme. Protection is checked against that final state, never against an
intermediate deletion. There are no registry, selection or history side effects.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Mapping, Sequence

from .detection import DetectionSnapshot
from .engine import calculate_batch
from .matching import DetectionError, SchemeStatus, match_scheme, match_variable
from .models import (Configuration, ConflictError, PlanChange, RegistryState,
                     ValidationError, VariableSpec, match_key)
from .removal import (RemovalAtom, RemovalKind, RemovalSource, SkippedRemoval,
                      TargetKey, build_removal_atoms, compose_removal,
                      input_elements, selected_input, snapshot_state)
from .sharing import ConflictDecision, ProtectedConsumer, SharedConflict, protected_specs


@dataclass(frozen=True)
class OldSchemeCandidates:
    complete: tuple[str, ...] = ()
    partial: tuple[str, ...] = ()
    errors: tuple[DetectionError, ...] = ()

    @property
    def suggested(self) -> str | None:
        return self.complete[0] if len(self.complete) == 1 else None

    @property
    def ambiguous(self) -> bool:
        return len(self.complete) > 1


class AmbiguousOldSchemeError(ConflictError):
    def __init__(self, candidates: OldSchemeCandidates, app: str | None = None):
        self.candidates = candidates
        self.app = app
        super().__init__("存在多个完全匹配的旧方案，请明确选择：" + "、".join(candidates.complete))


@dataclass(frozen=True)
class SwitchMetrics:
    changed_target_count: int = 0
    removed_whole_key_count: int = 0
    removed_element_count: int = 0
    added_element_count: int = 0
    kept_shared_count: int = 0
    skipped_count: int = 0


@dataclass(frozen=True)
class SwitchDraft:
    config_digest: str
    app: str
    new_option: str
    candidates: OldSchemeCandidates
    old_options: tuple[str, ...]
    old_atoms: tuple[RemovalAtom, ...]
    skipped: tuple[SkippedRemoval, ...]
    new_specs: tuple[VariableSpec, ...]
    protected: tuple[VariableSpec, ...]
    states: Mapping[TargetKey, RegistryState]
    selected_values: Mapping[str, str]
    ordinary_impacts: tuple[RemovalAtom, ...]
    shared_conflicts: tuple[SharedConflict, ...]
    changes: tuple[PlanChange, ...]
    warnings: tuple[str, ...] = ()
    orders: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("states", "selected_values", "orders"):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))

    @property
    def ordinary_atoms(self) -> tuple[RemovalAtom, ...]:
        """Same presentation contract as a removal draft."""
        return self.ordinary_impacts


@dataclass(frozen=True)
class SwitchCoreResult:
    changes: tuple[PlanChange, ...]
    old_options: tuple[str, ...]
    ordinary_impacts: tuple[RemovalAtom, ...]
    shared_conflicts: tuple[SharedConflict, ...]
    skipped: tuple[SkippedRemoval, ...]
    decisions: Mapping[str, ConflictDecision]
    warnings: tuple[str, ...]
    metrics: SwitchMetrics

    def __post_init__(self) -> None:
        object.__setattr__(self, "decisions", MappingProxyType(dict(self.decisions)))

    @property
    def needs_order(self) -> bool:
        return any(change.calculation and change.calculation.needs_order for change in self.changes)


@dataclass(frozen=True)
class BatchSwitchDraft(SwitchDraft):
    """A collective net transition; all selected applications share one state."""

    applications: tuple[tuple[str, str], ...] = ()
    candidates_by_app: Mapping[str, OldSchemeCandidates] = field(default_factory=dict)
    old_options_by_app: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__post_init__()
        for name in ("candidates_by_app", "old_options_by_app"):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))


def identify_old_options(
    config: Configuration, detection: DetectionSnapshot, app: str, new_option: str,
) -> OldSchemeCandidates:
    """Suggest exactly one complete old scheme; partial matches are never inferred."""
    _validate_destination(config, app, new_option)
    if detection.config_digest != config.digest:
        raise ValidationError("检测快照的配置已失效")
    complete: list[str] = []
    partial: list[str] = []
    errors: list[DetectionError] = []
    for option in config.options(app):
        if option == new_option:
            continue
        scheme = detection.schemes.get((app, option))
        if scheme is None:
            raise ValidationError("检测快照缺少应用方案")
        if scheme.status is SchemeStatus.COMPLETE:
            complete.append(option)
        elif scheme.status is SchemeStatus.PARTIAL:
            partial.append(option)
        errors.extend(scheme.errors)
    return OldSchemeCandidates(tuple(complete), tuple(partial), tuple(errors))


def _validate_destination(config: Configuration, app: str, option: str) -> None:
    if app not in config.applications or option not in config.applications[app]:
        raise ValidationError("切换的应用或新方案不存在")


def _states(states: Mapping) -> dict[TargetKey, RegistryState]:
    """Accept both the detection TargetKey dataclass and removal tuple keys."""
    result: dict[TargetKey, RegistryState] = {}
    for key, state in states.items():
        target = ((key.scope, key.normalized_name) if hasattr(key, "normalized_name") else tuple(key))
        target = target[0], match_key(target[1])
        if target in result and not result[target].same_content(state):
            raise ValidationError("同一目标的快照不一致")
        result[target] = state
        snapshot_state(result, target)
    return result


def _candidates_from_states(config, app, new_option, states, selected_values):
    complete: list[str] = []
    partial: list[str] = []
    for option in config.options(app):
        if option == new_option:
            continue
        matched = match_scheme(app, option, (
            match_variable(spec, selected_input(spec, selected_values), snapshot_state(states, spec.target))
            for spec in config.variables(app, option)))
        if matched.status is SchemeStatus.COMPLETE:
            complete.append(option)
        elif matched.status is SchemeStatus.PARTIAL:
            partial.append(option)
    return OldSchemeCandidates(tuple(complete), tuple(partial))


def _order_key(records: Sequence[VariableSpec]) -> str:
    spec = records[0]
    return (spec.key if len(records) == 1 else
            json.dumps(["batch-target", spec.scope, spec.target[1]], ensure_ascii=False))


def _compose(old_atoms, new_specs, states, selected_values, orders, kept_ids=frozenset(),
             kept_whole_targets=frozenset()):
    new_by_target: dict[TargetKey, list[VariableSpec]] = {}
    for spec in new_specs:
        new_by_target.setdefault(spec.target, []).append(spec)
    # A common list item remains in the virtual state, so adding the new scheme
    # does not manufacture a removal, duplicate, or shared-deletion conflict.
    retained_new_keys = {
        target: {match_key(item) for spec in specs if spec.mode != "replace"
                 for item in input_elements(selected_input(spec, selected_values))}
        for target, specs in new_by_target.items()
    }
    removed = tuple(atom for atom in old_atoms if atom.atom_id not in kept_ids and
                    atom.target not in kept_whole_targets and
                    not (atom.kind is RemovalKind.LIST_ELEMENT and
                         atom.element_key in retained_new_keys.get(atom.target, set())))
    virtual = dict(states)
    for change in compose_removal(removed, (), {}, states):
        virtual[(change.after.scope, match_key(change.after.name))] = change.after
    targets = tuple(dict.fromkeys([atom.target for atom in old_atoms] + list(new_by_target)))
    changes: list[PlanChange] = []
    warnings: list[str] = []
    valid_order_keys = {_order_key(specs) for specs in new_by_target.values()}
    if set(orders) - valid_order_keys:
        raise ValidationError("排序结果不属于新方案的目标")
    for target in targets:
        before = snapshot_state(states, target)
        existing = snapshot_state(virtual, target)
        specs = new_by_target.get(target)
        if not specs:
            changes.append(PlanChange(before, existing, "remove", ""))
            continue
        records = tuple((spec, selected_input(spec, selected_values)) for spec in specs)
        order_key = _order_key(specs)
        confirmed_order = orders.get(order_key)
        if confirmed_order is not None:
            initial = calculate_batch(records, existing)
            if initial.needs_order and isinstance(confirmed_order, tuple) and all(
                    isinstance(item, str) for item in confirmed_order):
                # An order reviewed after KEEP can contain old shared items that
                # the preliminary REMOVE composition omits. Project only these
                # explicitly removed items; unknown entries remain invalid.
                expected_keys = {match_key(item) for item in initial.items}
                removed_keys = {atom.element_key for atom in removed if atom.target == target
                                and atom.kind is RemovalKind.LIST_ELEMENT}
                removed_whole = any(atom.target == target and atom.kind is RemovalKind.WHOLE_KEY
                                    for atom in removed)
                if removed_whole and before.value:
                    removed_keys.update(match_key(item) for item in before.value.split(";"))
                projected = tuple(item for item in confirmed_order
                                  if match_key(item) in expected_keys or match_key(item) not in removed_keys)
                discarded = Counter(confirmed_order) - Counter(projected)
                original_items = Counter(before.value.split(";")) if before.exists and before.value else Counter()
                if discarded - original_items:
                    raise ValidationError("确认排序必须完整保留初始列表中的每一项及其重复次数")
                missing = Counter(initial.items) - Counter(projected)
                extra = Counter(projected) - Counter(initial.items)
                restored_keys = {atom.element_key for atom in old_atoms if atom.target == target
                                 and atom.atom_id in kept_ids and atom.kind is RemovalKind.LIST_ELEMENT}
                if target in kept_whole_targets and before.value:
                    restored_keys.update(match_key(item) for item in before.value.split(";"))
                if missing and not extra and all(match_key(item) in restored_keys for item in missing):
                    # KEEP changed the list since this order was reviewed. Ask
                    # for a fresh complete order instead of extending consent.
                    confirmed_order = None
                else:
                    confirmed_order = projected
        calculation = calculate_batch(records, existing, confirmed_order)
        after = calculation.result
        # The final transition writes a still-existing key once; virtual removal
        # must not accidentally change the original registry string type.
        if before.exists and after.exists and after.registry_type != before.registry_type:
            after = replace(after, registry_type=before.registry_type)
            calculation = replace(calculation, result=after)
        modes = {spec.mode for spec in specs}
        mode = ("append" if "append" in modes else next(iter(modes))
                if len(modes) == 1 else "batch_append")
        selected = records[0][1] if len(records) == 1 else "\n".join(value for _, value in records)
        changes.append(PlanChange(before, after, mode, selected, order_key, calculation))
        warnings.extend(calculation.warnings)
    return tuple(changes), tuple(dict.fromkeys(warnings))


def _net_removed(atom: RemovalAtom, after: RegistryState) -> bool:
    if atom.kind is RemovalKind.WHOLE_KEY:
        return not after.exists or after.value != atom.display_value
    return not after.exists or atom.element_key not in {
        match_key(item) for item in after.value.split(";") if after.value
    }


def _new_sources(new_specs, target, selected_values):
    return tuple(RemovalSource(spec.app, spec.option, spec.key, spec.mode,
                               selected_input(spec, selected_values))
                 for spec in new_specs if spec.target == target)


def _net_conflicts(protected, old_atoms, new_specs, states, selected_values, changes):
    after_by_target = {(change.after.scope, match_key(change.after.name)): change.after
                       for change in changes}
    atoms_by_id = {atom.atom_id: atom for atom in old_atoms}
    conflicts: dict[str, tuple[RemovalAtom, dict[str, ProtectedConsumer]]] = {}

    def add(atom, spec, reason):
        original = atoms_by_id.get(atom.atom_id)
        if original is not None:
            atom = original
        entry = conflicts.setdefault(atom.atom_id, (atom, {}))
        entry[1][spec.key] = ProtectedConsumer(spec.app, spec.option, spec.key, spec.mode, reason)

    for spec in protected:
        after = after_by_target.get(spec.target)
        if after is None:
            continue
        before = snapshot_state(states, spec.target)
        if not before.exists:
            continue
        value = selected_input(spec, selected_values)
        sources = _new_sources(new_specs, spec.target, selected_values)
        whole = RemovalAtom(spec.target, RemovalKind.WHOLE_KEY, None, before.value, sources)
        if spec.mode == "replace":
            if before.value == value and (not after.exists or after.value != value):
                add(whole, spec, "切换的最终状态会破坏当前完全匹配的 replace 完整值")
            continue
        actual = before.value.split(";") if before.value else ()
        actual_keys = {match_key(item) for item in actual}
        after_keys = {match_key(item) for item in after.value.split(";")} if after.exists and after.value else set()
        matched = tuple(item for item in input_elements(value) if match_key(item) in actual_keys)
        if not after.exists and (matched or not value):
            add(whole, spec, "切换的最终状态会删除当前已匹配需求所在的完整变量")
        else:
            for item in matched:
                key = match_key(item)
                if key not in after_keys:
                    atom = RemovalAtom(spec.target, RemovalKind.LIST_ELEMENT, key, item, sources,
                                       sum(match_key(x) == key for x in actual))
                    add(atom, spec, "切换的最终状态会移除已匹配的列表元素：" + item)
    return tuple(SharedConflict(atom, tuple(consumers.values())) for atom, consumers in conflicts.values())


def _resolve_old_options(config, app, new_option, frozen_states, frozen_values,
                         old_options, detection):
    _validate_destination(config, app, new_option)
    candidates = (identify_old_options(config, detection, app, new_option) if detection is not None
                  else _candidates_from_states(config, app, new_option, frozen_states, frozen_values))
    if detection is not None:
        actual_candidates = _candidates_from_states(config, app, new_option, frozen_states, frozen_values)
        if (candidates.complete, candidates.partial) != (actual_candidates.complete, actual_candidates.partial):
            raise ValidationError("检测快照与切换输入不一致，需要重新扫描")
        for option in config.options(app):
            for variable in detection.schemes[(app, option)].variables:
                if variable.selected_value != frozen_values.get(variable.spec_key):
                    raise ValidationError("检测快照的当前选定值已失效")
                state = frozen_states.get((variable.target.scope, variable.target.normalized_name))
                if variable.windows_state is None or state is None or not state.same_content(variable.windows_state):
                    raise ValidationError("检测快照的 Windows 状态已失效")
    if old_options is None:
        if candidates.ambiguous:
            raise AmbiguousOldSchemeError(candidates, app)
        chosen = (candidates.suggested,) if candidates.suggested is not None else ()
    else:
        if isinstance(old_options, str):
            raise ValidationError("旧方案必须是方案名称序列")
        chosen = tuple(dict.fromkeys(old_options))
        if any(option == new_option or option not in config.applications[app] for option in chosen):
            raise ValidationError("旧方案必须属于同一应用且不能是新方案")
        if any(option not in candidates.complete + candidates.partial for option in chosen):
            raise ValidationError("所选旧方案没有可识别的实际配置")
    return candidates, chosen


def prepare_switch(
    config: Configuration, app: str, new_option: str,
    states: Mapping, selected_values: Mapping[str, str], *,
    old_options: Sequence[str] | None = None,
    detection: DetectionSnapshot | None = None,
    orders: Mapping[str, tuple[str, ...]] | None = None,
) -> SwitchDraft:
    """Freeze one switch; infer only a unique completely matched old scheme."""
    frozen_states = _states(states)
    frozen_values = dict(selected_values)
    candidates, chosen = _resolve_old_options(config, app, new_option, frozen_states,
                                              frozen_values, old_options, detection)
    old_specs = tuple(spec for option in chosen for spec in config.variables(app, option))
    old = build_removal_atoms(old_specs, frozen_states, frozen_values)
    new_specs = tuple(config.variables(app, new_option))
    protected = protected_specs(config, (app,))
    initial_orders = dict(orders or {})
    changes, warnings = _compose(old.atoms, new_specs, frozen_states, frozen_values, initial_orders)
    conflicts = _net_conflicts(protected, old.atoms, new_specs, frozen_states, frozen_values, changes)
    after = {(change.after.scope, match_key(change.after.name)): change.after for change in changes}
    conflict_ids = {conflict.atom.atom_id for conflict in conflicts}
    whole_conflict_targets = {conflict.atom.target for conflict in conflicts
                              if conflict.atom.kind is RemovalKind.WHOLE_KEY}
    ordinary = tuple(atom for atom in old.atoms if _net_removed(atom, after[atom.target])
                     and atom.atom_id not in conflict_ids and atom.target not in whole_conflict_targets)
    return SwitchDraft(config.digest, app, new_option, candidates, chosen, old.atoms, old.skipped,
                       new_specs, protected, frozen_states, frozen_values, ordinary, conflicts,
                       changes, warnings, initial_orders)


def prepare_batch_switch(
    config: Configuration, app_options: Mapping[str, str], states: Mapping,
    selected_values: Mapping[str, str], *,
    old_options: Mapping[str, Sequence[str]] | None = None,
    detection: DetectionSnapshot | None = None,
    orders: Mapping[str, tuple[str, ...]] | None = None,
) -> BatchSwitchDraft:
    """Resolve old schemes first, then compose all new requirements together."""
    requested = dict(app_options)
    if not requested or set(requested) - set(config.apps()):
        raise ValidationError("请选择有效的应用进行方案切换")
    explicit = dict(old_options or {})
    if set(explicit) - set(requested):
        raise ValidationError("旧方案选择包含未参与批次的应用")
    applications = tuple((app, requested[app]) for app in config.apps() if app in requested)
    frozen_states, frozen_values = _states(states), dict(selected_values)
    candidates_by_app, old_by_app = {}, {}
    for app, option in applications:
        candidates_by_app[app], old_by_app[app] = _resolve_old_options(
            config, app, option, frozen_states, frozen_values, explicit.get(app), detection)
    old_specs = tuple(spec for app, _ in applications for option in old_by_app[app]
                      for spec in config.variables(app, option))
    old = build_removal_atoms(old_specs, frozen_states, frozen_values)
    new_specs = tuple(spec for app, option in applications for spec in config.variables(app, option))
    protected = protected_specs(config, requested)
    initial_orders = dict(orders or {})
    changes, warnings = _compose(old.atoms, new_specs, frozen_states, frozen_values, initial_orders)
    conflicts = _net_conflicts(protected, old.atoms, new_specs, frozen_states, frozen_values, changes)
    after = {(change.after.scope, match_key(change.after.name)): change.after for change in changes}
    conflict_ids = {conflict.atom.atom_id for conflict in conflicts}
    whole_targets = {conflict.atom.target for conflict in conflicts
                     if conflict.atom.kind is RemovalKind.WHOLE_KEY}
    ordinary = tuple(atom for atom in old.atoms if _net_removed(atom, after[atom.target])
                     and atom.atom_id not in conflict_ids and atom.target not in whole_targets)
    app, option = applications[0]
    return BatchSwitchDraft(config.digest, app, option, candidates_by_app[app], old_by_app[app],
                            old.atoms, old.skipped, new_specs, protected, frozen_states, frozen_values,
                            ordinary, conflicts, changes, warnings, initial_orders,
                            applications, candidates_by_app, old_by_app)


def _require_kept_conflicts(conflicts, decisions, states, selected_values, protected, changes):
    after = {(change.after.scope, match_key(change.after.name)): change.after for change in changes}
    by_key = {spec.key: spec for spec in protected}
    for conflict in conflicts:
        if decisions[conflict.atom.atom_id] is ConflictDecision.REMOVE:
            continue
        atom = conflict.atom
        result = after[atom.target]
        for consumer in conflict.consumers:
            spec = by_key[consumer.spec_key]
            if spec.mode == "replace":
                satisfied = result.exists and result.value == selected_input(spec, selected_values)
            elif atom.kind is RemovalKind.WHOLE_KEY:
                before = snapshot_state(states, atom.target)
                actual = {match_key(item) for item in before.value.split(";")} if before.value else set()
                needed = {match_key(item) for item in input_elements(selected_input(spec, selected_values))} & actual
                current = {match_key(item) for item in result.value.split(";")} if result.exists and result.value else set()
                satisfied = result.exists and needed.issubset(current)
            else:
                satisfied = result.exists and atom.element_key in {
                    match_key(item) for item in result.value.split(";") if result.value
                }
            if not satisfied:
                raise ConflictError(f"新方案与保留的共享要求无法同时满足：{result.scope} / {result.name}，"
                                    f"{consumer.app} / {consumer.option}（{atom.display_value}）")


def finalize_switch(
    draft: SwitchDraft, decisions: Mapping[str, ConflictDecision | str] | None = None, *,
    orders: Mapping[str, tuple[str, ...]] | None = None,
) -> SwitchCoreResult:
    """Apply shared decisions without silently dropping a KEEP requirement."""
    supplied = dict(decisions or {})
    conflict_ids = {conflict.atom.atom_id for conflict in draft.shared_conflicts}
    if set(supplied) - conflict_ids:
        raise ValidationError("切换决定包含不属于共享冲突的对象")
    try:
        resolved = {key: ConflictDecision(supplied.get(key, ConflictDecision.KEEP)) for key in conflict_ids}
    except ValueError as exc:
        raise ValidationError("共享对象决定必须是 KEEP 或 REMOVE") from exc
    kept = frozenset(key for key, decision in resolved.items() if decision is ConflictDecision.KEEP)
    kept_whole_targets = frozenset(conflict.atom.target for conflict in draft.shared_conflicts
                                  if conflict.atom.kind is RemovalKind.WHOLE_KEY
                                  and conflict.atom.atom_id in kept)
    if any(conflict.atom.kind is RemovalKind.LIST_ELEMENT and
           conflict.atom.target in kept_whole_targets and
           resolved[conflict.atom.atom_id] is ConflictDecision.REMOVE
           for conflict in draft.shared_conflicts):
        raise ConflictError("完整变量保留与同目标列表元素撤销互斥")
    changes, warnings = _compose(draft.old_atoms, draft.new_specs, draft.states, draft.selected_values,
                                draft.orders if orders is None else dict(orders), kept, kept_whole_targets)
    final_conflicts = _net_conflicts(draft.protected, draft.old_atoms, draft.new_specs, draft.states,
                                    draft.selected_values, changes)
    known = {conflict.atom.atom_id: {consumer.spec_key for consumer in conflict.consumers}
             for conflict in draft.shared_conflicts}
    if any(conflict.atom.atom_id not in known or
           not {consumer.spec_key for consumer in conflict.consumers}.issubset(known[conflict.atom.atom_id])
           for conflict in final_conflicts):
        raise ConflictError("最终排序或保留决定产生新的共享冲突，需要重新分析")
    _require_kept_conflicts(draft.shared_conflicts, resolved, draft.states, draft.selected_values,
                           draft.protected, changes)
    after = {(change.after.scope, match_key(change.after.name)): change.after for change in changes}
    removed = tuple(atom for atom in draft.old_atoms if _net_removed(atom, after[atom.target]))
    warnings = tuple(dict.fromkeys(draft.warnings + warnings + tuple(
        f"撤销共享内容将影响 {consumer.app} / {consumer.option}：{conflict.atom.display_value}"
        for conflict in draft.shared_conflicts if resolved[conflict.atom.atom_id] is ConflictDecision.REMOVE
        for consumer in conflict.consumers)))
    removed_elements = {atom.atom_id: atom for atom in removed if atom.kind is RemovalKind.LIST_ELEMENT}
    for conflict in draft.shared_conflicts:
        if (conflict.atom.kind is RemovalKind.LIST_ELEMENT and
                resolved[conflict.atom.atom_id] is ConflictDecision.REMOVE and
                _net_removed(conflict.atom, after[conflict.atom.target])):
            removed_elements[conflict.atom.atom_id] = conflict.atom
    metrics = SwitchMetrics(
        sum(change.changed for change in changes),
        sum(atom.kind is RemovalKind.WHOLE_KEY and not after[atom.target].exists for atom in removed),
        sum(atom.occurrence_count for atom in removed_elements.values()),
        sum(len(change.calculation.added) for change in changes if change.calculation),
        len(kept), len(draft.skipped),
    )
    return SwitchCoreResult(changes, draft.old_options, draft.ordinary_impacts, draft.shared_conflicts,
                            draft.skipped, resolved, warnings, metrics)

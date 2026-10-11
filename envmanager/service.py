from __future__ import annotations

import uuid
import json
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone

from .detection import (DetectionRequest, DetectionSnapshot, recompute_for_selection,
                        refresh_targets, scan_all)
from .engine import calculate_batch, validate_value
from .matching import TargetKey
from .models import (BatchSchemeSwitchDraft, ConflictError, ExecutionPlan, OperationResult, PlanChange,
                     RegistryState, RemovalDraft, SchemeSwitchDraft, ValidationError, match_key)
from .removal import (RemovalKind, RemovalRequest, RemovalScope, build_removal_atoms,
                      compose_removal, expand_sources)
from .selection import SelectionSession
from .sharing import ConflictDecision, classify_conflicts, protected_specs
from .switching import finalize_switch, prepare_batch_switch, prepare_switch


def _target(state):
    return state.scope, match_key(state.name)


def _effect(change):
    cal = change.calculation
    return {"before": change.before.to_dict(), "after": change.after.to_dict(),
            "mode": change.mode, "selected_value": change.selected_value,
            "record_key": change.record_key, "safe": True,
            "contributions": deepcopy(list(change.contributions)),
            "added": list(cal.added) if cal else [], "moved": list(cal.moved) if cal else [],
            "deduplicated": list(cal.deduplicated) if cal else [],
            "original_items": list(cal.original_items) if cal else [],
            "final_items": list(cal.items) if cal else []}


class EnvironmentService:
    def __init__(self, config, store, state_repository):
        self.config = config
        self.store = store
        self.state = state_repository
        snapshot = self.state.snapshot()
        self.selections = SelectionSession(snapshot["last_values"], self._saved_selections(snapshot))
        self._options = {}
        self._initialization_warnings = {}
        self._initialize_options(snapshot)
        self._issued_plans = {}
        self._option_revision = 0
        self._detection = None
        self._detection_generation = 0
        self._removal_drafts = {}
        self._removal_snapshots = {}
        self._switch_drafts = {}

    @staticmethod
    def _saved_selections(snapshot):
        return {key: value for saved in snapshot.get("successful_applications", {}).values()
                for key, value in saved["selections"].items()}

    def _initialize_options(self, snapshot):
        successes = snapshot.get("successful_applications", {})
        for app in self.config.apps():
            if app in self._options and self._options[app] in self.config.options(app):
                continue
            saved = successes.get(app)
            # Older V2 states retain the successful scheme in their active marker.
            preferred = saved["option"] if saved else snapshot["active"].get(app, {}).get("option")
            if preferred in self.config.options(app):
                self._options[app] = preferred
            else:
                self._options[app] = self.config.default_option(app)
                if preferred is not None:
                    self._initialization_warnings[app] = "上次成功方案已不存在，已初始化为默认方案"
        self._options = {app: option for app, option in self._options.items() if app in self.config.apps()}
        for app, option in self._options.items():
            for spec in self.config.variables(app, option):
                self.selections.get(spec)

    def selected_option(self, app):
        return self._options[app]

    def select_option(self, app, option):
        if option not in self.config.options(app):
            raise ValidationError(f"{app}：方案不存在：{option}")
        if self._options.get(app) != option:
            self._options[app] = option
            self._option_revision += 1
            self._initialization_warnings.pop(app, None)
            self._issued_plans.clear()
            self._removal_drafts.clear()
            self._removal_snapshots.clear()
            self._switch_drafts.clear()

    def initialization_warning(self, app):
        return self._initialization_warnings.get(app)

    def reload(self, config):
        selections = deepcopy(self.selections)
        selections.reconcile(spec for options in config.applications.values()
                             for specs in options.values() for spec in specs)
        snapshot = self.state.snapshot()
        selections.refresh_last(snapshot["last_values"])
        selections.refresh_success(self._saved_selections(snapshot))
        self.config = config
        self.selections = selections
        self._initialize_options(snapshot)
        self._option_revision += 1
        self._detection = None
        self._issued_plans.clear()
        self._removal_drafts.clear()
        self._removal_snapshots.clear()
        self._switch_drafts.clear()

    def history(self):
        return self.state.history()

    def pending(self):
        return self.state.pending()

    def _all_specs(self):
        return tuple(spec for app in self.config.apps() for option in self.config.options(app)
                     for spec in self.config.variables(app, option))

    def _selection_inputs(self):
        selections = self.selections.snapshot_many(self._all_specs())
        values = {key: selection["value"] for key, selection in selections.items()}
        frozen = tuple((key, selection["source"], selection["value"])
                       for key, selection in selections.items())
        return values, frozen

    def detection_snapshot(self, force=False):
        """Return one immutable generation; force refreshes current Windows data.

        Editing an input reuses the raw Windows snapshot and recomputes every
        affected scheme without another registry read. A configuration reload or
        explicit refresh performs one read per distinct registry target.
        """
        values, _ = self._selection_inputs()
        cache = self._detection
        if force or cache is None or cache.config_digest != self.config.digest:
            self._detection_generation += 1
            cache = scan_all(self.config, values, self.store,
                             generation=self._detection_generation,
                             selection_revision=self.selections.revision)
        elif cache.selection_revision != self.selections.revision:
            self._detection_generation += 1
            cache = recompute_for_selection(self.config, values, cache,
                       generation=self._detection_generation,
                       selection_revision=self.selections.revision)
        self._detection = cache
        return cache

    def scan_all(self):
        """Explicit read-only full refresh, suitable for startup/manual refresh."""
        return self.detection_snapshot(force=True)

    def prepare_detection_scan(self) -> DetectionRequest:
        """Freeze a full-scan request on the caller thread without registry I/O."""
        values, _ = self._selection_inputs()
        self._detection_generation += 1
        return DetectionRequest(self.config, values, self._detection_generation,
                                self.selections.revision)

    def run_detection_scan(self, request: DetectionRequest) -> DetectionSnapshot:
        """Perform only the slow, read-only portion of a prepared scan."""
        return scan_all(request.config, request.selection_values, self.store,
                        generation=request.generation,
                        selection_revision=request.selection_revision)

    def accept_detection_scan(self, snapshot: DetectionSnapshot) -> DetectionSnapshot:
        """Publish a worker result and reconcile edits made while it was running."""
        if snapshot.config_digest != self.config.digest:
            raise ConflictError("配置已变化，检测结果已失效，请重新刷新")
        self._detection_generation = max(self._detection_generation, snapshot.generation)
        if snapshot.selection_revision != self.selections.revision:
            values, _ = self._selection_inputs()
            self._detection_generation += 1
            snapshot = recompute_for_selection(
                self.config, values, snapshot,
                generation=self._detection_generation,
                selection_revision=self.selections.revision,
            )
        self._detection = snapshot
        return snapshot

    def _refresh_detection_after_operation(self, plan):
        if self._detection is None or self._detection.config_digest != self.config.digest:
            self._detection = None
            return
        values, _ = self._selection_inputs()
        targets = tuple(dict.fromkeys(TargetKey(*_target(change.after))
                                      for change in plan.changes
                                      if TargetKey(*_target(change.after)) in self._detection.targets
                                      or TargetKey(*_target(change.after)) in self._detection.read_errors))
        self._detection_generation += 1
        self._detection = refresh_targets(self.config, values, self.store, self._detection,
                            targets, generation=self._detection_generation,
                            selection_revision=self.selections.revision)

    def _validate_frozen_inputs(self, item, snapshot):
        if (snapshot["revision"] != item.state_revision
                or self.config.digest != item.config_digest):
            raise ConflictError("配置或持久化状态已改变，请重新预览")
        if (item.option_revision is not None and self._option_revision != item.option_revision
                or item.selection_revision is not None
                and self.selections.revision != item.selection_revision):
            raise ConflictError("选定方案、来源或输入已改变，请重新预览")
        options = getattr(item, "frozen_options", getattr(item, "selected_options", ()))
        if options and tuple(self._options.items()) != options:
            raise ConflictError("选定方案已改变，请重新预览")
        if item.frozen_selections and self._selection_inputs()[1] != item.frozen_selections:
            raise ConflictError("选定来源或输入已改变，请重新预览")

    def prepare_remove(self, request):
        """Read and freeze actual matched removal objects without any writes."""
        if self.pending():
            raise ConflictError("存在未完成执行，请先在历史记录中检查并恢复")
        if not isinstance(request, RemovalRequest):
            raise ValidationError("撤销请求类型无效")
        current = request.scope in {RemovalScope.VARIABLE, RemovalScope.SCHEME,
                                    RemovalScope.BATCH_CURRENT}
        options = dict(request.selected_options)
        if current:
            for app in request.selected_apps:
                if app in self._options:
                    options.setdefault(app, self.selected_option(app))
        request = RemovalRequest(request.scope, request.selected_apps, options, request.variable_keys)
        specs = expand_sources(self.config, request)
        snapshot = self.state.snapshot()
        values, frozen = self._selection_inputs()
        detection = self.detection_snapshot(force=True)
        states = {(target.scope, target.normalized_name): state
                  for target, state in detection.targets.items()}
        source_targets = {spec.target for spec in specs}
        relevant = tuple(specs) + tuple(spec for spec in protected_specs(self.config, request.selected_apps)
                                      if spec.target in source_targets)
        relevant_keys = {spec.key for spec in relevant}
        problems = [error for error in detection.errors if error.spec_key in relevant_keys]
        if problems:
            raise ValidationError("无法分析撤销或共享保护：" + "；".join(dict.fromkeys(
                f"{error.app} / {error.option}：{error.message}" for error in problems)))
        candidates = build_removal_atoms(specs, states, values)
        analysis = classify_conflicts(self.config, request.selected_apps, candidates.atoms, states, values)
        # Activation bookkeeping also depends on the remaining matched objects
        # in the recorded active scheme, so those keys join the frozen inputs.
        dependencies = set(source_targets)
        for app in request.selected_apps:
            active = snapshot["active"].get(app)
            if active and active["option"] in self.config.options(app):
                dependencies.update(spec.target for spec in self.config.variables(app, active["option"]))
        draft = RemovalDraft(uuid.uuid4().hex, request, candidates, analysis, self.config.digest,
                    snapshot["revision"], self._option_revision, self.selections.revision,
                    tuple(self._options.items()), frozen, tuple(values.items()),
                    tuple(state for target, state in states.items() if target in dependencies))
        self._removal_drafts[draft.id] = draft
        self._removal_snapshots[draft.id] = deepcopy(snapshot)
        return draft

    @staticmethod
    def _atom_metadata(atom):
        return {"atom_id": atom.atom_id, "target": list(atom.target), "kind": atom.kind.value,
                "element_key": atom.element_key, "display_value": atom.display_value,
                "occurrence_count": atom.occurrence_count,
                "sources": [vars(source) for source in atom.sources]}

    def _active_after_removal(self, draft, changes, snapshot, decisions):
        active = deepcopy(snapshot["active"])
        if not changes:
            return active
        states = {_target(state): state for state in draft.states}
        states.update({_target(change.after): change.after for change in changes})
        values = dict(draft.selected_values)
        covered = {(source.app, source.option) for source in draft.candidates.requested_sources}
        for app in draft.request.selected_apps:
            activation = active.get(app)
            if not activation or (app, activation["option"]) not in covered:
                continue
            if activation["option"] not in self.config.options(app):
                continue
            if any(decisions[conflict.atom.atom_id] == ConflictDecision.KEEP
                   and any(source.app == app and source.option == activation["option"]
                           for source in conflict.atom.sources)
                   for conflict in draft.shared_conflicts):
                continue
            specs = self.config.variables(app, activation["option"])
            if any(spec.target not in states for spec in specs):
                continue  # An unreadable dependency cannot prove complete removal.
            active_values = dict(values)
            for effect in activation["effects"]:
                if effect.get("record_key") in active_values:
                    active_values[effect["record_key"]] = effect["selected_value"]
            try:
                remaining = build_removal_atoms(specs, states, active_values)
            except ValidationError:
                continue  # An invalid legacy activation is insufficient proof.
            empty_requirements = any(spec.mode != "replace" and active_values[spec.key] == ""
                                     and states[spec.target].exists for spec in specs)
            if not remaining.atoms and not empty_requirements:
                active.pop(app)
        return active

    def finalize_remove(self, draft_id, decisions=None):
        """Compose one final change per key; unspecified shared objects stay."""
        draft = self._removal_drafts.get(draft_id)
        if draft is None:
            raise ConflictError("撤销草案已失效，请重新预览")
        snapshot = self.state.snapshot()
        self._validate_frozen_inputs(draft, snapshot)
        if self.pending():
            raise ConflictError("存在未完成执行，请先恢复")
        decisions = decisions or {}
        states = {_target(state): state for state in draft.states}
        changes = compose_removal(draft.ordinary_atoms, draft.shared_conflicts, decisions, states)
        resolved = {conflict.atom.atom_id: ConflictDecision(
                    decisions.get(conflict.atom.atom_id, ConflictDecision.KEEP))
                    for conflict in draft.shared_conflicts}
        removed = tuple(draft.ordinary_atoms) + tuple(conflict.atom for conflict in draft.shared_conflicts
                      if resolved[conflict.atom.atom_id] == ConflictDecision.REMOVE)
        metrics = {"removed_element_count": sum(atom.occurrence_count for atom in removed
                                                if atom.kind == RemovalKind.LIST_ELEMENT),
                   "removed_atom_count": len(removed),
                   "removed_key_count": sum(atom.kind == RemovalKind.WHOLE_KEY for atom in removed),
                   "kept_shared_count": sum(value == ConflictDecision.KEEP for value in resolved.values()),
                   "skipped_count": len(draft.skipped)}
        warnings = [f"{atom.target[0]} / {states[atom.target].name}：将删除整个环境变量"
                    for atom in removed if atom.kind == RemovalKind.WHOLE_KEY]
        for change in changes:
            self.store.check_permission(change.before.scope)
            if change.before.scope == "System":
                warnings.append(f"{change.before.name}：操作系统级变量，需要管理员权限")
        for conflict in draft.shared_conflicts:
            if resolved[conflict.atom.atom_id] == ConflictDecision.REMOVE:
                consumers = "、".join(f"{consumer.app} / {consumer.option}" for consumer in conflict.consumers)
                warnings.append(f"{states[conflict.atom.target].name} / {conflict.atom.display_value}：撤销将影响 {consumers}")
        metadata = {"request": {"scope": draft.request.scope.value,
                    "selected_apps": [app for app in self.config.apps() if app in draft.request.selected_apps],
                    "selected_options": dict(draft.request.selected_options),
                    "variable_keys": list(draft.request.variable_keys)},
                    "protected_applications": [
                        {"app": app, "options": list(self.config.options(app))}
                        for app in self.config.apps() if app not in draft.request.selected_apps],
                    "sources": [vars(source) for source in draft.candidates.requested_sources],
                    "atoms": [self._atom_metadata(atom) for atom in draft.candidates.atoms],
                    "ordinary_atoms": [atom.atom_id for atom in draft.ordinary_atoms],
                    "shared_conflicts": [{"atom_id": conflict.atom.atom_id,
                        "consumers": [vars(consumer) for consumer in conflict.consumers],
                        "decision": resolved[conflict.atom.atom_id].value}
                        for conflict in draft.shared_conflicts],
                    "decisions": {key: value.value for key, value in resolved.items()},
                    "skipped_items": [{"source": vars(item.source), "target": list(item.target),
                                       "reason": item.reason, "display_value": item.display_value}
                                      for item in draft.skipped],
                    "metrics": metrics}
        applications = tuple((app, draft.request.selected_options.get(app, "*"))
                             for app in self.config.apps() if app in draft.request.selected_apps)
        app, option = applications[0]
        return self._issue(ExecutionPlan(uuid.uuid4().hex, app, option, "remove", changes,
                    draft.config_digest, draft.state_revision, draft.selected_values,
                    warnings=tuple(dict.fromkeys(warnings)),
                    active_after=self._active_after_removal(draft, changes, snapshot, resolved),
                    applications=applications, option_revision=draft.option_revision,
                    selection_revision=draft.selection_revision,
                    frozen_options=draft.selected_options, frozen_selections=draft.frozen_selections,
                    dependencies=draft.states, metadata=metadata))

    def prepare_scheme_switch(self, app, new_option, old_options=None, orders=None):
        """Freeze a live old/new scheme transition before any Windows writes.

        A unique complete old match can be suggested without application
        history. Ambiguous candidates remain an explicit caller decision, and
        partial matches are used only when listed in old_options.
        """
        if self.pending():
            raise ConflictError("存在未完成执行，请先在历史记录中检查并恢复")
        if app not in self.config.applications or new_option not in self.config.options(app):
            raise ValidationError("切换的应用或新方案不存在")
        self.select_option(app, new_option)
        snapshot = self.state.snapshot()
        values, frozen = self._selection_inputs()
        detection = self.detection_snapshot(force=True)
        candidate_targets = {spec.target for option in self.config.options(app)
                             for spec in self.config.variables(app, option)}
        relevant = {spec.key for spec in self._all_specs()
                    if spec.app == app or spec.target in candidate_targets}
        errors = [error for error in detection.errors if error.spec_key in relevant]
        if errors:
            raise ValidationError("无法分析方案切换或共享保护：" + "；".join(dict.fromkeys(
                f"{error.app} / {error.option}：{error.message}" for error in errors)))
        core = prepare_switch(self.config, app, new_option, detection.targets, values,
                              old_options=old_options, detection=detection, orders=orders)
        dependencies = candidate_targets | {_target(change.before) for change in core.changes}
        draft = SchemeSwitchDraft(uuid.uuid4().hex, core, self.config.digest,
                    snapshot["revision"], self._option_revision, self.selections.revision,
                    tuple(self._options.items()), frozen,
                    tuple(state for target, state in core.states.items() if target in dependencies))
        self._switch_drafts[draft.id] = draft
        return draft

    def prepare_batch_scheme_switch(self, app_options, old_options=None, orders=None):
        """Freeze all selected applications against a single global Windows scan."""
        if self.pending():
            raise ConflictError("存在未完成执行，请先在历史记录中检查并恢复")
        requested = (dict(app_options) if isinstance(app_options, dict) else
                     {app: self.selected_option(app) for app in app_options})
        if not requested or set(requested) - set(self.config.apps()):
            raise ValidationError("请选择有效的应用进行方案切换")
        for app, option in requested.items():
            if option not in self.config.options(app):
                raise ValidationError("切换的应用或新方案不存在")
        for app, option in requested.items():
            self.select_option(app, option)
        snapshot = self.state.snapshot()
        values, frozen = self._selection_inputs()
        detection = self.detection_snapshot(force=True)
        candidate_targets = {spec.target for app in requested for option in self.config.options(app)
                             for spec in self.config.variables(app, option)}
        relevant = {spec.key for spec in self._all_specs()
                    if spec.app in requested or spec.target in candidate_targets}
        errors = [error for error in detection.errors if error.spec_key in relevant]
        if errors:
            raise ValidationError("无法分析方案切换或共享保护：" + "；".join(dict.fromkeys(
                f"{error.app} / {error.option}：{error.message}" for error in errors)))
        core = prepare_batch_switch(self.config, requested, detection.targets, values,
                                    old_options=old_options, detection=detection, orders=orders)
        dependencies = candidate_targets | {_target(change.before) for change in core.changes}
        draft = BatchSchemeSwitchDraft(uuid.uuid4().hex, core, self.config.digest,
                    snapshot["revision"], self._option_revision, self.selections.revision,
                    tuple(self._options.items()), frozen,
                    tuple(state for target, state in core.states.items() if target in dependencies))
        self._switch_drafts[draft.id] = draft
        return draft

    @staticmethod
    def _switch_atom_removed(atom, after):
        if atom.kind == RemovalKind.WHOLE_KEY:
            return not after.exists or after.value != atom.display_value
        keys = {match_key(item) for item in after.value.split(";")} if after.exists and after.value else set()
        return atom.element_key not in keys

    def finalize_scheme_switch(self, draft_id, decisions=None, orders=None):
        """Turn frozen net impacts and explicit decisions into a safe final plan."""
        return self._finalize_scheme_switch(draft_id, decisions, orders)

    def finalize_batch_scheme_switch(self, draft_id, decisions=None, orders=None):
        if not isinstance(self._switch_drafts.get(draft_id), BatchSchemeSwitchDraft):
            raise ConflictError("批次切换草案已失效，请重新预览")
        return self._finalize_scheme_switch(draft_id, decisions, orders)

    def _finalize_scheme_switch(self, draft_id, decisions=None, orders=None):
        draft = self._switch_drafts.get(draft_id)
        if draft is None:
            raise ConflictError("方案切换草案已失效，请重新预览")
        snapshot = self.state.snapshot()
        self._validate_frozen_inputs(draft, snapshot)
        if self.pending():
            raise ConflictError("存在未完成执行，请先恢复")
        try:
            result = finalize_switch(draft.core, decisions, orders=orders)
        except ConflictError as exc:
            if "新的共享冲突" in str(exc):
                # This draft never disclosed the new consumers; existing user
                # consent cannot authorize a newly discovered net impact.
                self._switch_drafts.pop(draft_id, None)
            raise
        values = draft.core.selected_values
        is_batch = isinstance(draft, BatchSchemeSwitchDraft)
        applications = draft.applications if is_batch else ((draft.app, draft.new_option),)
        old_by_app = draft.old_options_by_app if is_batch else {draft.app: draft.old_options}
        selections = {spec.key: self.selections.snapshot(spec) for spec in draft.core.new_specs}
        new_by_target = {}
        for spec in draft.core.new_specs:
            new_by_target.setdefault(spec.target, []).append(spec)
        plan_id = uuid.uuid4().hex
        changes, confirmed = [], []
        effects_by_app = {app: [] for app, _ in applications}
        warnings = list(result.warnings)
        for change in result.changes:
            target = _target(change.before)
            new_specs = new_by_target.get(target, ())
            contributions = tuple({"app": spec.app, "option": spec.option, "record_key": spec.key,
                                   "mode": spec.mode, "selected_value": values[spec.key]}
                                  for spec in new_specs)
            if not new_specs:
                contributions = tuple({"app": source.app, "option": source.option,
                                       "record_key": source.spec_key, "mode": "remove",
                                       "selected_value": source.selected_value,
                                       "atom_id": atom.atom_id}
                                      for atom in draft.core.old_atoms if atom.target == target
                                      for source in atom.sources)
            change = replace(change, contributions=contributions)
            changes.append(change)
            if change.changed:
                self.store.check_permission(change.before.scope)
                if change.before.scope == "System":
                    warnings.append(f"{change.before.name}：操作系统级变量，需要管理员权限")
                if not change.after.exists:
                    warnings.append(f"{change.before.scope} / {change.before.name}：将删除整个环境变量")
            if change.after.registry_type == 1 and "%" in (change.after.value or ""):
                warnings.append(f"{change.after.name}：保留 REG_SZ，百分号引用将按原始文本保存")
            for spec in new_specs:
                effect = _effect(replace(change, mode=spec.mode, selected_value=values[spec.key],
                                        record_key=spec.key))
                effect["group_id"] = plan_id
                effects_by_app[spec.app].append(effect)
                selection_warning = self.selections.get(spec).warning
                if selection_warning:
                    warnings.append(f"{spec.app} / {spec.name}：{selection_warning}")
                if change.calculation and not change.calculation.needs_order and change.mode == "append":
                    confirmed.append((spec.key, tuple(change.calculation.items)))
        for app, _ in applications:
            initialization = self.initialization_warning(app)
            if initialization:
                warnings.append(f"{app}：{initialization}")
        active_after = deepcopy(snapshot["active"])
        for app, option in applications:
            active_after[app] = {"option": option, "effects": effects_by_app[app]}
        after = {_target(change.after): change.after for change in changes}
        all_atoms = {atom.atom_id: atom for atom in draft.core.old_atoms}
        all_atoms.update({conflict.atom.atom_id: conflict.atom for conflict in result.shared_conflicts})
        removed_atoms = tuple(atom for atom in all_atoms.values()
                              if atom.target in after and self._switch_atom_removed(atom, after[atom.target]))
        ordinary = tuple(atom for atom in result.ordinary_impacts
                         if self._switch_atom_removed(atom, after[atom.target]))
        sources = [{"app": spec.app, "option": spec.option, "spec_key": spec.key,
                    "mode": spec.mode, "selected_value": values[spec.key]}
                   for app, _ in applications for option in old_by_app[app]
                   for spec in self.config.variables(app, option)]
        metadata = {"kind": "scheme_switch",
                    "request": {"scope": "scheme_switch", "selected_apps": [draft.app],
                                "selected_options": {draft.app: draft.new_option},
                                "old_options": list(draft.old_options)},
                    "protected_applications": [
                        {"app": app, "options": list(self.config.options(app))}
                        for app in self.config.apps() if app != draft.app],
                    "old_options": list(draft.old_options), "new_option": draft.new_option,
                    "candidates": {"complete": list(draft.candidates.complete),
                                   "partial": list(draft.candidates.partial)},
                    "sources": sources, "new_selections": deepcopy(selections),
                    "atoms": [self._atom_metadata(atom) for atom in all_atoms.values()],
                    "removed_atoms": [atom.atom_id for atom in removed_atoms],
                    "ordinary_atoms": [atom.atom_id for atom in ordinary],
                    "shared_conflicts": [{"atom_id": conflict.atom.atom_id,
                        "consumers": [vars(consumer) for consumer in conflict.consumers],
                        "decision": result.decisions[conflict.atom.atom_id].value}
                        for conflict in result.shared_conflicts],
                    "decisions": {key: value.value for key, value in result.decisions.items()},
                    "skipped_items": [{"source": vars(item.source), "target": list(item.target),
                                       "reason": item.reason, "display_value": item.display_value}
                                      for item in result.skipped],
                    "metrics": vars(result.metrics)}
        if is_batch:
            metadata.update({"kind": "batch_scheme_switch",
                "request": {"scope": "batch_scheme_switch", "selected_apps": [app for app, _ in applications],
                            "selected_options": dict(applications),
                            "old_options": {app: list(old_by_app[app]) for app, _ in applications}},
                "protected_applications": [{"app": app, "options": list(self.config.options(app))}
                                           for app in self.config.apps() if app not in old_by_app],
                "switches": [{"app": app, "old_options": list(old_by_app[app]), "new_option": option}
                             for app, option in applications],
                "candidates_by_app": {app: {"complete": list(candidate.complete),
                                           "partial": list(candidate.partial)}
                                      for app, candidate in draft.candidates_by_app.items()}})
            for key in ("old_options", "new_option", "candidates"):
                metadata.pop(key)
        return self._issue(ExecutionPlan(plan_id, draft.app, draft.new_option,
                    "switch" if any(old_by_app.values()) else "apply", tuple(changes),
                    draft.config_digest, draft.state_revision,
                    tuple((spec.key, values[spec.key]) for spec in draft.core.new_specs),
                    tuple(confirmed), tuple(dict.fromkeys(warnings)), active_after=active_after,
                    applications=applications, successful_selections=selections,
                    option_revision=draft.option_revision, selection_revision=draft.selection_revision,
                    frozen_options=draft.selected_options, frozen_selections=draft.frozen_selections,
                    dependencies=draft.dependencies, metadata=metadata))

    def _issue(self, plan):
        self._issued_plans[plan.id] = deepcopy(plan)
        return plan

    def _shared_apps(self, active, app, state):
        target = _target(state)
        return [name for name, activation in active.items() if name != app
                and any(_target(RegistryState.from_dict(e["after"])) == target
                        for e in activation["effects"])]

    def _undo(self, app, snapshot, joint_switches=()):
        activation = snapshot["active"].get(app)
        changes, conflicts = [], []
        if not activation:
            return changes, conflicts
        for effect in activation["effects"]:
            before = RegistryState.from_dict(effect["before"])
            expected = RegistryState.from_dict(effect["after"])
            current = self.store.read(expected.scope, expected.name)
            if before.same_content(expected):
                # A scheme that merely claimed an existing value has nothing to remove.
                continue
            if not effect.get("safe", False):
                conflicts.append(f"{expected.scope} / {expected.name}：应用期间曾发生外部变更，不能确定撤销范围")
                continue
            if not current.same_content(expected):
                conflicts.append(f"{expected.scope} / {expected.name}：与最近应用结果不同，保留当前值")
                continue
            shared = [name for name in self._shared_apps(snapshot["active"], app, expected)
                      if name not in joint_switches]
            if shared:
                conflicts.append(f"{expected.scope} / {expected.name}：还由 {', '.join(shared)} 管理，不能安全合并撤销")
                continue
            changes.append(PlanChange(current, before, "undo", effect["selected_value"], effect["record_key"]))
        return changes, conflicts

    def plan(self, app, option, orders=None):
        return self.plan_batch({app: option}, orders)

    def plan_batch(self, app_options, orders=None):
        """Resolve all selected applications into one write per registry target."""
        snapshot = self.state.snapshot()
        self.selections.refresh_last(snapshot["last_values"])
        self.selections.refresh_success(self._saved_selections(snapshot))
        requested = (dict(app_options) if isinstance(app_options, dict) else
                     {app: self.selected_option(app) for app in app_options})
        if not requested:
            raise ValidationError("请至少选择一个应用")
        if set(requested) - set(self.config.apps()):
            raise ValidationError("所选应用已不存在，请重新选择")
        applications = tuple((app, requested[app]) for app in self.config.apps() if app in requested)
        for app, option in applications:
            self.select_option(app, option)
        orders = orders or {}
        switching = {app for app, option in applications
                     if app in snapshot["active"] and snapshot["active"][app]["option"] != option}
        conflicts, warnings = [], []
        actuals, virtual, merged = {}, {}, {}

        def read(scope, name):
            target = scope, match_key(name)
            if target not in actuals:
                actuals[target] = self.store.read(scope, name)
            return actuals[target]

        for app, _ in applications:
            if app not in switching:
                continue
            undone, problems = self._undo(app, snapshot, switching)
            conflicts.extend(problems)
            for change in undone:
                target = _target(change.before)
                actuals.setdefault(target, change.before)
                if target in virtual and not virtual[target].same_content(change.after):
                    conflicts.append(f"{change.before.scope} / {change.before.name}：共享方案的原始状态不同，不能安全共同撤销")
                else:
                    virtual[target] = change.after
                    merged[target] = change
        if self.pending():
            conflicts.append("存在未完成执行，请先在历史记录中检查并恢复")
        values, confirmed, success_selections = [], [], {}
        groups, effects_by_app = {}, {}
        for app, option in applications:
            activation = snapshot["active"].get(app)
            effects_by_app[app] = {} if app in switching or not activation else {
                _target(RegistryState.from_dict(e["after"])): deepcopy(e) for e in activation["effects"]}
            if self.initialization_warning(app):
                warnings.append(f"{app}：{self.initialization_warning(app)}")
            for spec in self.config.variables(app, option):
                selection = self.selections.get(spec)
                validate_value(selection.value, spec.mode)
                groups.setdefault(spec.target, []).append((spec, selection.value))
                values.append((spec.key, selection.value))
                success_selections[spec.key] = self.selections.snapshot(spec)
                if selection.warning:
                    warnings.append(f"{app} / {spec.name}：{selection.warning}")
        batch_id = uuid.uuid4().hex
        for target, records in groups.items():
            spec = records[0][0]
            actual = read(spec.scope, spec.name)
            existing = virtual.get(target, actual)
            self.store.check_permission(spec.scope)
            contributions = tuple({"app": item.app, "option": item.option, "record_key": item.key,
                                   "mode": item.mode, "selected_value": value}
                                  for item, value in records)
            order_key = (spec.key if len(records) == 1 else
                         json.dumps(["batch-target", spec.scope, target[1]], ensure_ascii=False))
            modes = {item.mode for item, _ in records}
            mode = "append" if "append" in modes else next(iter(modes)) if len(modes) == 1 else "batch_append"
            selected = records[0][1] if len(records) == 1 else "\n".join(value for _, value in records)
            try:
                cal = calculate_batch(records, existing, orders.get(order_key))
            except ValidationError as exc:
                conflicts.append(f"{spec.scope} / {spec.name}：{exc}")
                merged[target] = PlanChange(actual, existing, mode, selected, order_key,
                                           contributions=contributions)
                continue
            change = PlanChange(actual, cal.result, mode, selected, order_key, cal, contributions)
            merged[target] = change
            for item, value in records:
                effects = effects_by_app[item.app]
                effect = _effect(PlanChange(existing, cal.result, item.mode, value, item.key,
                                           cal, contributions))
                effect["group_id"] = batch_id
                if target in effects:
                    old_effect = effects[target]
                    old_after = RegistryState.from_dict(old_effect["after"])
                    effect["before"] = old_effect["before"]
                    effect["safe"] = old_effect.get("safe", False) and actual.same_content(old_after)
                    if not effect["safe"]:
                        warnings.append(f"{item.app} / {item.name}：检测到外部变更，本次以实际值计算，后续自动撤销会被阻止")
                    effect["previous_inputs"] = old_effect.get("previous_inputs", []) + [old_effect["selected_value"]]
                effects[target] = effect
            shared = [name for name, activation in snapshot["active"].items() if name not in requested
                      and any(_target(RegistryState.from_dict(e["after"])) == target
                              for e in activation["effects"])]
            if shared:
                warnings.append(f"{spec.scope} / {spec.name}：与 {', '.join(shared)} 共享，请检查本次替换或移动的影响")
                if change.changed and "replace" in modes:
                    conflicts.append(f"{spec.scope} / {spec.name}：替换会影响其他激活应用，请先协调或停用共享方案")
                elif change.changed and any(e["mode"] == "replace"
                     for shared_app in shared for e in snapshot["active"][shared_app]["effects"]
                     if _target(RegistryState.from_dict(e["after"])) == spec.target):
                    conflicts.append(f"{spec.scope} / {spec.name}：其他激活方案要求完整替换值，不能同时追加")
            if spec.scope == "System":
                warnings.append(f"{spec.name}：操作系统级变量，需要管理员权限")
            if cal.result.registry_type == 1 and "%" in (cal.result.value or ""):
                warnings.append(f"{spec.name}：保留 REG_SZ，百分号引用将按原始文本保存")
            warnings.extend(cal.warnings)
            if mode == "append" and not cal.needs_order:
                confirmed.extend((item.key, tuple(cal.items)) for item, _ in records)
        active_after = deepcopy(snapshot["active"])
        for app, option in applications:
            active_after[app] = {"option": option, "effects": list(effects_by_app[app].values())}
        changes = tuple(merged.values())
        for change in changes:
            if change.changed:
                self.store.check_permission(change.before.scope)
        app, option = applications[0]
        return self._issue(ExecutionPlan(batch_id, app, option,
            "switch" if switching else "apply", changes, self.config.digest, snapshot["revision"],
            tuple(values), tuple(confirmed), tuple(dict.fromkeys(warnings)),
            tuple(dict.fromkeys(conflicts)), active_after,
            applications=applications, successful_selections=success_selections))

    def plan_deactivate(self, app):
        snapshot = self.state.snapshot()
        changes, conflicts = self._undo(app, snapshot)
        if self.pending():
            conflicts.append("存在未完成执行，请先恢复")
        active = deepcopy(snapshot["active"])
        activation = active.pop(app, None)
        if not activation:
            conflicts.append("该应用没有管理器记录的激活方案")
        for change in changes:
            self.store.check_permission(change.before.scope)
        return self._issue(ExecutionPlan(uuid.uuid4().hex, app, activation["option"] if activation else "",
             "deactivate", tuple(changes), self.config.digest, snapshot["revision"],
             conflicts=tuple(conflicts), active_after=active))

    def plan_restore(self, history_id):
        op = self.state.operation(history_id)
        snapshot = self.state.snapshot()
        conflicts, changes = [], []
        if op["status"] not in {"success", "no_change", "notification_failed"}:
            conflicts.append("该记录不是尚未恢复的成功执行")
        if op.get("after_revision") != snapshot["revision"]:
            conflicts.append("此记录之后还有已提交操作，不能安全覆盖后续状态")
        if self.pending():
            conflicts.append("存在未完成执行，请先恢复")
        for item in op["changes"]:
            expected, original = RegistryState.from_dict(item["after"]), RegistryState.from_dict(item["before"])
            current = self.store.read(expected.scope, expected.name)
            if not current.same_content(expected):
                conflicts.append(f"{expected.scope} / {expected.name}：已发生外部变更，不能恢复快照")
            changes.append(PlanChange(current, original, "restore", item["selected_value"], item["record_key"]))
            if not current.same_content(original):
                self.store.check_permission(current.scope)
        return self._issue(ExecutionPlan(uuid.uuid4().hex, op["app"], op["option"], "restore",
             tuple(changes), self.config.digest, snapshot["revision"], conflicts=tuple(conflicts),
             active_after=deepcopy(op["snapshot_before"]["active"]), history_id=history_id))

    def plan_recover(self, operation_id):
        op = self.state.operation(operation_id)
        snapshot = self.state.snapshot()
        changes, conflicts = [], []
        if operation_id not in {p["id"] for p in self.pending()}:
            conflicts.append("该记录不再是未完成执行")
        # The failed operation did not commit state; only its attempted registry writes can be undone.
        for index in op.get("attempted", []):
            item = op["changes"][index]
            original, expected = RegistryState.from_dict(item["before"]), RegistryState.from_dict(item["after"])
            current = self.store.read(original.scope, original.name)
            if current.same_content(original):
                changes.append(PlanChange(current, original, "recover", item["selected_value"], item["record_key"]))
            elif current.same_content(expected):
                self.store.check_permission(current.scope)
                changes.append(PlanChange(current, original, "recover", item["selected_value"], item["record_key"]))
            else:
                conflicts.append(f"{current.scope} / {current.name}：既不是原始值也不是本次目标，不能直接覆盖")
        return self._issue(ExecutionPlan(uuid.uuid4().hex, op["app"], op["option"], "recover",
              tuple(changes), self.config.digest, snapshot["revision"], conflicts=tuple(conflicts),
              active_after=deepcopy(snapshot["active"]), history_id=operation_id))

    def _put(self, state):
        if state.exists:
            self.store.write(state)
        else:
            self.store.delete(state.scope, state.name)

    def execute(self, plan):
        issued = self._issued_plans.get(plan.id)
        if issued is None or issued != plan:
            raise ConflictError("执行计划无效或已被修改，请重新预览")
        if not plan.can_execute:
            raise ConflictError("排序尚未确认或存在冲突，不能执行")
        with self.state.execution_lock():
            snapshot = self.state.snapshot()
            self._validate_frozen_inputs(plan, snapshot)
            if plan.action != "recover" and self.pending():
                raise ConflictError("存在未完成执行，请先恢复")
            for dependency in plan.dependencies:
                actual = self.store.read(dependency.scope, dependency.name)
                if not actual.same_content(dependency):
                    raise ConflictError(f"{actual.scope} / {actual.name} 的撤销或共享依赖已改变，请重新预览")
            for change in plan.changes:
                actual = self.store.read(change.before.scope, change.before.name)
                if not actual.same_content(change.before):
                    raise ConflictError(f"{actual.scope} / {actual.name} 已改变，请重新预览并确认")
                if change.changed:
                    self.store.check_permission(actual.scope)
            if plan.action in {"apply", "switch"}:
                for app, option in plan.applications or ((plan.app, plan.option),):
                    if self.selected_option(app) != option:
                        raise ConflictError("选定方案已改变，请重新预览")
                    for spec in self.config.variables(app, option):
                        if (dict(plan.selected_values).get(spec.key) != self.selections.get(spec).value
                                or plan.successful_selections.get(spec.key) != self.selections.snapshot(spec)):
                            raise ConflictError("选定来源或输入已改变，请重新预览")
            op = {"id": plan.id, "created_at": datetime.now(timezone.utc).isoformat(),
                  "app": plan.app, "option": plan.option, "action": plan.action,
                  "applications": [list(item) for item in plan.applications],
                  "successful_selections": deepcopy(plan.successful_selections),
                  "status": "prepared", "message": "执行记录已保存，尚未写入",
                  "changes": [_effect(c) for c in plan.changes], "attempted": [], "verified": [],
                  "snapshot_before": snapshot, "warnings": list(plan.warnings)}
            if plan.metadata:
                op["metadata"] = deepcopy(plan.metadata)
            if plan.frozen_selections:
                op["frozen_selections"] = [list(item) for item in plan.frozen_selections]
                op["frozen_options"] = [list(item) for item in plan.frozen_options]
                op["config_digest"] = plan.config_digest
                op["selection_revision"] = plan.selection_revision
                op["option_revision"] = plan.option_revision
            if plan.dependencies:
                op["dependencies"] = [state.to_dict() for state in plan.dependencies]
            self.state.save_operation(op)  # Failure here prevents every registry write.
            self._issued_plans.pop(plan.id, None)
            mutated = False
            notification_error = None
            try:
                for index, change in enumerate(plan.changes):
                    actual = self.store.read(change.before.scope, change.before.name)
                    if not actual.same_content(change.before):
                        raise ConflictError(f"{actual.scope} / {actual.name} 在执行中改变")
                    if change.changed:
                        op["attempted"].append(index)
                        op["status"] = "writing"
                        self.state.save_operation(op)
                        self._put(change.after)
                        mutated = True
                    if not self.store.read(change.after.scope, change.after.name).same_content(change.after):
                        raise RuntimeError(f"{change.after.scope} / {change.after.name} 写入验证失败")
                    op["verified"].append(index)
                    self.state.save_operation(op)
                for change in plan.changes:
                    if not self.store.read(change.after.scope, change.after.name).same_content(change.after):
                        raise ConflictError(f"{change.after.scope} / {change.after.name} 整体核验失败")
                if mutated:
                    try:
                        self.store.notify()
                    except Exception as exc:
                        notification_error = f"环境通知异常：{type(exc).__name__}"
                target_snapshot = deepcopy(snapshot)
                if plan.action == "restore":
                    target_snapshot = deepcopy(self.state.operation(plan.history_id)["snapshot_before"])
                    # Registry restoration changes activation, not the latest successful preferences.
                    for key in ("last_values", "successful_applications"):
                        target_snapshot[key] = deepcopy(snapshot.get(key, {}))
                else:
                    target_snapshot["active"] = deepcopy(plan.active_after)
                    if plan.action in {"apply", "switch"}:
                        target_snapshot["last_values"].update(dict(plan.selected_values))
                        target_snapshot["orders"].update({key: list(order) for key, order in plan.confirmed_orders})
                        successes = target_snapshot.setdefault("successful_applications", {})
                        for app, option in plan.applications or ((plan.app, plan.option),):
                            successes[app] = {
                                "option": option, "operation_id": plan.id, "applied_at": op["created_at"],
                                "selections": {spec.key: deepcopy(plan.successful_selections[spec.key])
                                               for spec in self.config.variables(app, option)}}
                op["status"] = "notification_failed" if notification_error else ("success" if mutated else "no_change")
                op["message"] = ("已写入并验证，环境通知异常" if notification_error else
                                  "全部变量已验证并提交" if mutated else "无需写入，全部变量验证通过并提交")
                op["notification_error"] = notification_error
                op["failure_stage"] = "state_commit"
                self.state.commit(snapshot["revision"], target_snapshot, op, plan.history_id)
            except Exception as exc:
                if op.get("failure_stage") == "state_commit":
                    # A commit can persist before an exception reaches its caller.
                    # Never roll registry values back while a successful state commit remains.
                    try:
                        durable_snapshot = self.state.snapshot()
                        durable_op = self.state.operation(plan.id)
                        expected_snapshot = deepcopy(target_snapshot)
                        expected_snapshot["revision"] = snapshot["revision"] + 1
                        committed = (durable_snapshot == expected_snapshot and
                                     durable_op.get("after_revision") == expected_snapshot["revision"] and
                                     durable_op["status"] == op["status"])
                        uncommitted = (durable_snapshot == snapshot and
                                       "after_revision" not in durable_op)
                    except Exception:
                        committed = uncommitted = False
                    if committed:
                        self.selections.refresh_last(durable_snapshot["last_values"])
                        self.selections.refresh_success(self._saved_selections(durable_snapshot))
                        self._refresh_detection_after_operation(plan)
                        return OperationResult(durable_op["status"], "执行与状态均已核实提交，提交接口返回异常",
                            plan.id, sum(c.changed for c in plan.changes),
                            errors=(f"提交返回异常：{type(exc).__name__}；已核实持久化结果",),
                            notification_error=notification_error, **self._result_metrics(plan))
                    if not uncommitted:
                        return OperationResult("state_commit_failed", "注册表已验证，状态提交结果无法核实；请保留状态文件并重新启动检查",
                            plan.id, sum(c.changed for c in plan.changes),
                            errors=(f"状态提交结果不确定：{type(exc).__name__}",),
                            notification_error=notification_error)
                # Attempted includes a write that raised after mutating its target.
                return self._rollback(plan, op, exc, mutated)
            committed_snapshot = self.state.snapshot()
            self.selections.refresh_last(committed_snapshot["last_values"])
            self.selections.refresh_success(self._saved_selections(committed_snapshot))
            self._refresh_detection_after_operation(plan)
            return OperationResult(op["status"], op["message"], plan.id,
                                   sum(c.changed for c in plan.changes), notification_error=notification_error,
                                   **self._result_metrics(plan))

    @staticmethod
    def _result_metrics(plan):
        metrics = plan.metadata.get("metrics", {})
        return {name: metrics.get(name, 0) for name in
                ("removed_element_count", "kept_shared_count", "skipped_count")}

    def _rollback(self, plan, op, failure, mutated):
        stage = "状态提交失败" if op.get("failure_stage") == "state_commit" else "执行失败"
        reason = str(failure) if isinstance(failure, ConflictError) else type(failure).__name__
        errors = [f"{stage}：{reason}"]
        op["status"] = "rolling_back"
        try:
            self.state.save_operation(op)
        except Exception:
            errors.append("无法更新执行日志")
        incomplete = []
        for index in reversed(op["attempted"]):
            change = plan.changes[index]
            try:
                actual = self.store.read(change.before.scope, change.before.name)
                if actual.same_content(change.before):
                    continue
                if not actual.same_content(change.after):
                    raise ConflictError("当前值与写入结果不同，拒绝覆盖后续修改")
                self._put(change.before)
                mutated = True
                if not self.store.read(change.before.scope, change.before.name).same_content(change.before):
                    raise RuntimeError("回滚验证失败")
            except Exception as exc:
                incomplete.append(f"{change.before.scope} / {change.before.name}：{type(exc).__name__}")
        notification_error = None
        if mutated or op["attempted"]:
            try:
                self.store.notify()
            except Exception as exc:
                notification_error = f"回滚环境通知异常：{type(exc).__name__}"
        status = "rollback_incomplete" if incomplete else "rolled_back"
        op.update(status=status, errors=errors + incomplete,
                  message=f"{stage}，回滚不完整" if incomplete else f"{stage}，已验证恢复原始状态",
                  notification_error=notification_error)
        try:
            self.state.save_operation(op)
        except Exception:
            status = "state_commit_failed"
            errors.append("状态日志提交失败；重新启动后检查未完成执行")
        self._refresh_detection_after_operation(plan)
        return OperationResult(status, op["message"], plan.id, errors=tuple(errors + incomplete),
                               notification_error=notification_error)

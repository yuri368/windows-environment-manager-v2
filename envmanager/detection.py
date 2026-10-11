"""Deduplicated all-scheme scans and immutable in-memory detection snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Iterable, Mapping

from .matching import (DetectionError, SchemeMatch, TargetKey, VariableMatch,
                       match_scheme, match_variable)
from .models import (Configuration, EnvironmentStore, RegistryState, ValidationError,
                     VariableSpec, match_key)
from .store import validate_state


@dataclass(frozen=True)
class DetectionSnapshot:
    generation: int
    scanned_at: datetime
    config_digest: str
    selection_revision: int
    targets: Mapping[TargetKey, RegistryState]
    schemes: Mapping[tuple[str, str], SchemeMatch]
    errors: tuple[DetectionError, ...] = ()
    read_errors: Mapping[TargetKey, DetectionError] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("targets", "schemes", "read_errors"):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))


@dataclass(frozen=True)
class DetectionRequest:
    """Main-thread capture for one read-only full scan.

    Capturing selections before the worker starts avoids reading mutable UI
    state from a background thread.  The service reconciles a completed
    snapshot with any edits made while the registry was being read.
    """

    config: Configuration
    selection_values: Mapping[str, str]
    generation: int
    selection_revision: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "selection_values",
                           MappingProxyType(dict(self.selection_values)))


def _specs(config: Configuration) -> dict[TargetKey, VariableSpec]:
    targets: dict[TargetKey, VariableSpec] = {}
    for app in config.apps():
        for option in config.options(app):
            for spec in config.variables(app, option):
                targets.setdefault(TargetKey.from_spec(spec), spec)
    return targets


def _read(store: EnvironmentStore, spec: VariableSpec) -> RegistryState:
    state = store.read(spec.scope, spec.name)
    validate_state(state)
    if TargetKey(state.scope, match_key(state.name)) != TargetKey.from_spec(spec):
        raise ValidationError("读取结果与请求的名称或作用域不一致")
    return state


def _snapshot(
    config: Configuration, selection_values: Mapping[str, str],
    states: Mapping[TargetKey, RegistryState], read_errors: Mapping[TargetKey, DetectionError],
    generation: int, selection_revision: int,
) -> DetectionSnapshot:
    schemes: dict[tuple[str, str], SchemeMatch] = {}
    errors: list[DetectionError] = []
    for app in config.apps():
        for option in config.options(app):
            variables: list[VariableMatch] = []
            for spec in config.variables(app, option):
                target = TargetKey.from_spec(spec)
                selected = selection_values.get(spec.key, spec.default_value)
                state = states.get(target)
                target_error = read_errors.get(target)
                if target_error is not None or state is None:
                    error = DetectionError("read", target_error.message if target_error else
                                           "只读快照缺少目标变量", target, spec.key, app, option)
                else:
                    try:
                        variables.append(match_variable(spec, selected, state))
                        continue
                    except (ValueError, TypeError, UnicodeError) as exc:
                        error = DetectionError("validation", str(exc), target, spec.key, app, option)
                variables.append(VariableMatch(spec.key, target, spec.mode, None, selected,
                                               windows_state=state, error=error))
            scheme = match_scheme(app, option, variables)
            schemes[(app, option)] = scheme
            errors.extend(scheme.errors)
    return DetectionSnapshot(generation, datetime.now(timezone.utc), config.digest,
                             selection_revision, states, schemes, tuple(errors), read_errors)


def scan_all(
    config: Configuration, selection_values: Mapping[str, str], store: EnvironmentStore,
    *, generation: int = 0, selection_revision: int = 0,
) -> DetectionSnapshot:
    """Read each distinct target once and independently match every scheme.

    Missing selection entries use their spec defaults; explicit empty selections
    are preserved. Read failures fan out to all referencing schemes, never to a
    fabricated missing RegistryState. This function never requests write access.
    """
    states: dict[TargetKey, RegistryState] = {}
    errors: dict[TargetKey, DetectionError] = {}
    for target, spec in _specs(config).items():
        try:
            states[target] = _read(store, spec)
        except Exception as exc:
            errors[target] = DetectionError("read", str(exc), target)
    return _snapshot(config, selection_values, states, errors, generation, selection_revision)


def recompute_for_selection(
    config: Configuration, selection_values: Mapping[str, str], snapshot: DetectionSnapshot,
    *, generation: int | None = None, selection_revision: int | None = None,
) -> DetectionSnapshot:
    """Re-evaluate selections against the same Windows snapshot without any I/O."""
    if config.digest != snapshot.config_digest:
        raise ValueError("配置已变更，需要重新执行全量检测")
    return _snapshot(config, selection_values, snapshot.targets, snapshot.read_errors,
                     snapshot.generation + 1 if generation is None else generation,
                     snapshot.selection_revision if selection_revision is None else selection_revision)


def refresh_targets(
    config: Configuration, selection_values: Mapping[str, str], store: EnvironmentStore,
    snapshot: DetectionSnapshot, targets: Iterable[TargetKey], *,
    generation: int | None = None, selection_revision: int | None = None,
) -> DetectionSnapshot:
    """Read changed targets once each and re-evaluate all their scheme consumers."""
    if config.digest != snapshot.config_digest:
        raise ValueError("配置已变更，需要重新执行全量检测")
    specs = _specs(config)
    requested = tuple(dict.fromkeys(targets))
    if any(target not in specs for target in requested):
        raise ValueError("刷新目标不属于当前配置")
    states = dict(snapshot.targets)
    errors = dict(snapshot.read_errors)
    for target in requested:
        states.pop(target, None)
        errors.pop(target, None)
        try:
            states[target] = _read(store, specs[target])
        except Exception as exc:
            errors[target] = DetectionError("read", str(exc), target)
    return _snapshot(config, selection_values, states, errors,
                     snapshot.generation + 1 if generation is None else generation,
                     snapshot.selection_revision if selection_revision is None else selection_revision)

"""Read-only matching of raw Windows values against selected scheme inputs."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Iterable, Mapping

from .engine import validate_value
from .models import RegistryState, ValidationError, VariableSpec, match_key
from .store import validate_state


@dataclass(frozen=True)
class TargetKey:
    scope: str
    normalized_name: str

    @classmethod
    def from_spec(cls, spec: VariableSpec) -> TargetKey:
        return cls(*spec.target)


class VariableMatchStatus(Enum):
    MATCH = "match"
    KEY_MISSING = "key_missing"
    VALUE_MISMATCH = "value_mismatch"
    ELEMENTS_PARTIAL = "elements_partial"


class SchemeStatus(Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNCONFIGURED = "unconfigured"


@dataclass(frozen=True)
class DetectionError:
    kind: str
    message: str
    target: TargetKey | None = None
    spec_key: str | None = None
    app: str | None = None
    option: str | None = None


@dataclass(frozen=True)
class VariableMatch:
    spec_key: str
    target: TargetKey
    mode: str
    status: VariableMatchStatus | None
    selected_value: str
    matched_elements: tuple[str, ...] = ()
    missing_elements: tuple[str, ...] = ()
    windows_state: RegistryState | None = None
    occurrence_counts: Mapping[str, int] = field(default_factory=dict)
    error: DetectionError | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "occurrence_counts",
                           MappingProxyType(dict(self.occurrence_counts)))

    @property
    def duplicate_counts(self) -> Mapping[str, int]:
        """Total Windows occurrences for requested elements occurring more than once."""
        return MappingProxyType({item: count for item, count in self.occurrence_counts.items()
                                 if count > 1})

    @property
    def available(self) -> bool:
        return self.error is None and self.status is not None


@dataclass(frozen=True)
class SchemeMatch:
    app: str
    option: str
    status: SchemeStatus | None
    matched_count: int
    total_count: int
    variables: tuple[VariableMatch, ...]
    errors: tuple[DetectionError, ...] = ()

    @property
    def available(self) -> bool:
        return self.status is not None and not self.errors


def match_variable(
    spec: VariableSpec, selected_value: str, windows_state: RegistryState,
) -> VariableMatch:
    """Match content without writing, expanding references, or normalizing text.

    Invalid selected inputs or unreadable registry data raise ValidationError;
    callers must represent these as detection errors rather than business states.
    Registry type is validated but does not participate in content comparison.
    """
    validate_value(selected_value, spec.mode)
    validate_state(windows_state)
    if windows_state.scope != spec.scope or match_key(windows_state.name) != match_key(spec.name):
        raise ValidationError("现有变量与配置的名称或作用域不一致")
    target = TargetKey.from_spec(spec)
    # An explicitly empty selection requires an existing, explicitly empty value.
    # Without this branch, the empty required-element set is vacuously satisfied
    # by every nonempty Windows value.
    if spec.mode != "replace" and selected_value == "":
        status = (VariableMatchStatus.KEY_MISSING if not windows_state.exists else
                  VariableMatchStatus.MATCH if windows_state.value == "" else
                  VariableMatchStatus.VALUE_MISMATCH)
        return VariableMatch(spec.key, target, spec.mode, status, selected_value,
                             windows_state=windows_state)

    if spec.mode == "replace":
        status = (VariableMatchStatus.KEY_MISSING if not windows_state.exists else
                  VariableMatchStatus.MATCH if windows_state.value == selected_value else
                  VariableMatchStatus.VALUE_MISMATCH)
        return VariableMatch(spec.key, target, spec.mode, status, selected_value,
                             windows_state=windows_state)

    # Retain the first original spelling of each selected equivalence class.
    originals: dict[str, str] = {}
    for item in selected_value.split(";") if selected_value else ():
        originals.setdefault(match_key(item), item)
    counts = (Counter(match_key(item) for item in windows_state.value.split(";"))
              if windows_state.exists and windows_state.value else Counter())
    matched = tuple(originals[key] for key in originals if counts[key])
    missing = tuple(originals[key] for key in originals if not counts[key])
    status = (VariableMatchStatus.KEY_MISSING if not windows_state.exists else
              VariableMatchStatus.ELEMENTS_PARTIAL if missing else VariableMatchStatus.MATCH)
    return VariableMatch(spec.key, target, spec.mode, status, selected_value, matched, missing,
                         windows_state, {originals[key]: counts[key] for key in originals})


def match_scheme(app: str, option: str, variables: Iterable[VariableMatch]) -> SchemeMatch:
    """Aggregate M/N; a failed variable makes this scheme unavailable."""
    results = tuple(variables)
    errors = tuple(result.error for result in results if result.error is not None)
    if not results:
        errors = (DetectionError("config", "方案至少需要一条变量记录", app=app, option=option),)
    if any(not result.available for result in results) and not errors:
        errors = (DetectionError("validation", "方案含不可用的变量检测结果", app=app, option=option),)
    matched = sum(result.status is VariableMatchStatus.MATCH for result in results)
    if errors:
        status = None
    elif matched == len(results):
        status = SchemeStatus.COMPLETE
    elif all(result.status is VariableMatchStatus.KEY_MISSING for result in results):
        status = SchemeStatus.UNCONFIGURED
    else:
        status = SchemeStatus.PARTIAL
    return SchemeMatch(app, option, status, matched, len(results), results, errors)

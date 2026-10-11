from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .removal import RemovalCandidates, RemovalRequest, SkippedRemoval
    from .sharing import SharedConflict, SharingAnalysis
    from .switching import BatchSwitchDraft, OldSchemeCandidates, SwitchDraft


class ConfigError(ValueError):
    pass


class ValidationError(ValueError):
    pass


class ConflictError(RuntimeError):
    pass


class PermissionDenied(RuntimeError):
    pass


def match_key(value: str) -> str:
    """Case insensitive matching without expanding one character into several."""
    return "".join(c.upper() if len(c.upper()) == 1 else c for c in value)


def record_key(app: str, option: str, name: str, scope: str) -> str:
    return json.dumps([app, option, match_key(name), scope], ensure_ascii=False)


@dataclass(frozen=True)
class Choice:
    label: str
    value: str


@dataclass(frozen=True)
class VariableSpec:
    app: str
    option: str
    name: str
    scope: str
    mode: str
    editable: bool
    default_value: str
    choices: tuple[Choice, ...] = ()

    @property
    def key(self) -> str:
        return record_key(self.app, self.option, self.name, self.scope)

    @property
    def target(self) -> tuple[str, str]:
        return self.scope, match_key(self.name)


@dataclass(frozen=True)
class Configuration:
    applications: dict[str, dict[str, tuple[VariableSpec, ...]]]
    digest: str
    default_options: dict[str, str] = field(default_factory=dict)

    def apps(self) -> tuple[str, ...]:
        return tuple(self.applications)

    def options(self, app: str) -> tuple[str, ...]:
        return tuple(self.applications[app])

    def variables(self, app: str, option: str) -> tuple[VariableSpec, ...]:
        return self.applications[app][option]

    def default_option(self, app: str) -> str:
        return self.default_options.get(app, next(iter(self.applications[app])))


@dataclass(frozen=True)
class RegistryState:
    name: str
    scope: str
    exists: bool
    value: str | None = None
    registry_type: int | None = None

    def same_content(self, other: RegistryState) -> bool:
        return (self.scope == other.scope and match_key(self.name) == match_key(other.name)
                and self.exists == other.exists and self.value == other.value
                and self.registry_type == other.registry_type)

    def to_dict(self) -> dict:
        return {"name": self.name, "scope": self.scope, "exists": self.exists,
                "value": self.value, "registry_type": self.registry_type}

    @classmethod
    def from_dict(cls, value: dict) -> RegistryState:
        return cls(**value)


@dataclass(frozen=True)
class Calculation:
    result: RegistryState
    items: tuple[str, ...] = ()
    added: tuple[str, ...] = ()
    moved: tuple[str, ...] = ()
    deduplicated: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    needs_order: bool = False
    original_items: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlanChange:
    before: RegistryState
    after: RegistryState
    mode: str
    selected_value: str
    record_key: str = ""
    calculation: Calculation | None = None
    contributions: tuple[dict, ...] = ()

    @property
    def changed(self) -> bool:
        return not self.before.same_content(self.after)


@dataclass(frozen=True)
class ExecutionPlan:
    id: str
    app: str
    option: str
    action: str
    changes: tuple[PlanChange, ...]
    config_digest: str
    state_revision: int
    selected_values: tuple[tuple[str, str], ...] = ()
    confirmed_orders: tuple[tuple[str, tuple[str, ...]], ...] = ()
    warnings: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    active_after: dict = field(default_factory=dict)
    history_id: str | None = None
    applications: tuple[tuple[str, str], ...] = ()
    successful_selections: dict[str, dict] = field(default_factory=dict)
    # Optional frozen inputs extend the original apply/restore plan contract.
    # Tuples keep removal dependencies independent of mutable UI selection data.
    option_revision: int | None = None
    selection_revision: int | None = None
    frozen_options: tuple[tuple[str, str], ...] = ()
    frozen_selections: tuple[tuple[str, str, str], ...] = ()
    dependencies: tuple[RegistryState, ...] = ()
    metadata: dict = field(default_factory=dict)

    @property
    def needs_order(self) -> bool:
        return any(c.calculation and c.calculation.needs_order for c in self.changes)

    @property
    def can_execute(self) -> bool:
        return not self.conflicts and not self.needs_order


@dataclass(frozen=True)
class OperationResult:
    status: str
    message: str
    operation_id: str
    changed_count: int = 0
    errors: tuple[str, ...] = ()
    notification_error: str | None = None
    removed_element_count: int = 0
    kept_shared_count: int = 0
    skipped_count: int = 0

    @property
    def success(self) -> bool:
        return self.status in {"success", "no_change", "notification_failed"}


@dataclass(frozen=True)
class RemovalDraft:
    """Read-only removal analysis awaiting per-conflict UI decisions."""

    id: str
    request: RemovalRequest
    candidates: RemovalCandidates
    analysis: SharingAnalysis
    config_digest: str
    state_revision: int
    option_revision: int
    selection_revision: int
    selected_options: tuple[tuple[str, str], ...]
    frozen_selections: tuple[tuple[str, str, str], ...]
    selected_values: tuple[tuple[str, str], ...]
    states: tuple[RegistryState, ...]

    @property
    def ordinary_atoms(self):
        return self.analysis.ordinary_atoms

    @property
    def shared_conflicts(self) -> tuple[SharedConflict, ...]:
        return self.analysis.shared_conflicts

    @property
    def skipped(self) -> tuple[SkippedRemoval, ...]:
        return self.candidates.skipped


@dataclass(frozen=True)
class SchemeSwitchDraft:
    """Service-owned frozen switch analysis with the common UI draft contract."""

    id: str
    core: SwitchDraft
    config_digest: str
    state_revision: int
    option_revision: int
    selection_revision: int
    selected_options: tuple[tuple[str, str], ...]
    frozen_selections: tuple[tuple[str, str, str], ...]
    dependencies: tuple[RegistryState, ...]

    @property
    def app(self) -> str:
        return self.core.app

    @property
    def new_option(self) -> str:
        return self.core.new_option

    @property
    def candidates(self) -> OldSchemeCandidates:
        return self.core.candidates

    @property
    def old_options(self) -> tuple[str, ...]:
        return self.core.old_options

    @property
    def ordinary_atoms(self):
        return self.core.ordinary_atoms

    @property
    def shared_conflicts(self) -> tuple[SharedConflict, ...]:
        return self.core.shared_conflicts

    @property
    def skipped(self) -> tuple[SkippedRemoval, ...]:
        return self.core.skipped

    @property
    def changes(self) -> tuple[PlanChange, ...]:
        return self.core.changes

    @property
    def needs_order(self) -> bool:
        return any(change.calculation and change.calculation.needs_order for change in self.changes)


@dataclass(frozen=True)
class BatchSchemeSwitchDraft(SchemeSwitchDraft):
    core: BatchSwitchDraft

    @property
    def applications(self) -> tuple[tuple[str, str], ...]:
        return self.core.applications

    @property
    def candidates_by_app(self):
        return self.core.candidates_by_app

    @property
    def old_options_by_app(self):
        return self.core.old_options_by_app


class EnvironmentStore(Protocol):
    def read(self, scope: str, name: str) -> RegistryState: ...
    def check_permission(self, scope: str) -> None: ...
    def write(self, state: RegistryState) -> None: ...
    def delete(self, scope: str, name: str) -> None: ...
    def notify(self) -> None: ...

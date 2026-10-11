"""Per-record, in-memory value selection and editable drafts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .engine import validate_value
from .models import ValidationError, VariableSpec


@dataclass
class Selection:
    source: str
    value: str
    custom_draft: str | None = None
    error: str | None = None
    warning: str | None = None


class SelectionSession:
    def __init__(self, last_values: dict[str, str],
                 successful_selections: dict[str, dict] | None = None):
        self._last_values = dict(last_values)
        self._successful_selections = {
            key: dict(snapshot) if isinstance(snapshot, dict) else snapshot
            for key, snapshot in (successful_selections or {}).items()
        }
        self._selections: dict[str, Selection] = {}
        self._specs: dict[str, VariableSpec] = {}

    def _last(self, spec: VariableSpec) -> tuple[str | None, str | None]:
        if spec.key in self._successful_selections:
            snapshot = self._successful_selections[spec.key]
            value = snapshot.get("value") if isinstance(snapshot, dict) else None
        elif spec.key in self._last_values:
            value = self._last_values[spec.key]
        else:
            return None, None
        try:
            validate_value(value, spec.mode)
        except ValidationError as exc:
            return None, f"上次应用值无效，已使用默认值：{exc}"
        return value, None

    def _initialize(self, spec: VariableSpec) -> Selection:
        last, warning = self._last(spec)
        if last is None:
            return Selection("default", spec.default_value, warning=warning)
        snapshot = self._successful_selections.get(spec.key)
        if not isinstance(snapshot, dict):
            return Selection("last", last, warning=warning)
        source = snapshot.get("source")
        if source == "default" and last == spec.default_value:
            return Selection("default", last)
        if source == "last":
            return Selection("last", last)
        if source == "custom" and spec.editable:
            return Selection("custom", last, custom_draft=last)
        if isinstance(source, str) and source.startswith("choice:"):
            for index, choice in enumerate(spec.choices):
                if (snapshot.get("choice_label") == choice.label and
                        snapshot.get("choice_value") == choice.value == last):
                    return Selection(f"choice:{index}", last)
        return Selection("last", last,
                         warning="上次成功选择的来源已变化，已保留其输入为上次应用值")

    def get(self, spec: VariableSpec) -> Selection:
        self._specs[spec.key] = spec
        if spec.key not in self._selections:
            self._selections[spec.key] = self._initialize(spec)
        return self._selections[spec.key]

    def snapshot(self, spec: VariableSpec) -> dict:
        """Capture a successful input with stable candidate identity."""
        selection = self.get(spec)
        validate_value(selection.value, spec.mode)
        snapshot = {"source": selection.source, "value": selection.value}
        if selection.source.startswith("choice:"):
            candidates = {f"choice:{index}": choice
                          for index, choice in enumerate(spec.choices)}
            choice = candidates.get(selection.source)
            if choice is None or choice.value != selection.value:
                raise ValidationError("当前候选来源与配置不一致")
            snapshot.update(choice_label=choice.label, choice_value=choice.value)
        return snapshot

    def refresh_success(self, snapshots: dict[str, dict]) -> None:
        """Refresh persisted inputs without replacing initialized running drafts."""
        self._successful_selections = {
            key: dict(snapshot) if isinstance(snapshot, dict) else snapshot
            for key, snapshot in snapshots.items()
        }

    def sources(self, spec: VariableSpec) -> tuple[tuple[str, str, str], ...]:
        current = self.get(spec)
        sources = [("default", "默认值", spec.default_value)]
        last, _ = self._last(spec)
        if last is not None:
            sources.append(("last", "上次应用值", last))
        sources.extend((f"choice:{index}", choice.label, choice.value)
                       for index, choice in enumerate(spec.choices))
        if spec.editable:
            sources.append(("custom", "自定义值", current.custom_draft
                            if current.custom_draft is not None else current.value))
        return tuple(sources)

    @staticmethod
    def _check(selection: Selection, spec: VariableSpec) -> None:
        try:
            validate_value(selection.value, spec.mode)
        except ValidationError as exc:
            selection.error = str(exc)
        else:
            selection.error = None

    def choose(self, spec: VariableSpec, source_id: str) -> Selection:
        selection = self.get(spec)
        available = {source: value for source, _, value in self.sources(spec)}
        if source_id not in available:
            raise ValidationError(f"来源不可用：{source_id}")
        if source_id == "custom" and selection.custom_draft is None:
            selection.custom_draft = selection.value
        selection.source = source_id
        selection.value = available[source_id]
        self._check(selection, spec)
        return selection

    def edit(self, spec: VariableSpec, value: str) -> Selection:
        selection = self.get(spec)
        if not isinstance(value, str):
            raise ValidationError("编辑内容必须是字符串")
        if value == selection.value:
            return selection
        if not spec.editable:
            raise ValidationError("此变量不允许自定义输入")
        selection.source = "custom"
        selection.value = value
        selection.custom_draft = value
        self._check(selection, spec)
        return selection

    def refresh_last(self, last_values: dict[str, str]) -> None:
        previous = self._last_values
        self._last_values = dict(last_values)
        for key, selection in self._selections.items():
            spec = self._specs[key]
            last, warning = self._last(spec)
            selection.warning = warning
            if selection.source == "last":
                if last is None:
                    selection.source = "default"
                    selection.value = spec.default_value
                    self._check(selection, spec)
                elif key in previous and selection.value == previous[key]:
                    selection.value = last
                    self._check(selection, spec)

    def reconcile(self, specs: Iterable[VariableSpec]) -> None:
        """Retain running drafts only for records with an identical definition."""
        incoming = {spec.key: spec for spec in specs}
        self._selections = {
            key: selection for key, selection in self._selections.items()
            if key in incoming and self._specs.get(key) == incoming[key]
        }
        self._specs = incoming

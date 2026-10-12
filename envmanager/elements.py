"""Pure ordinary-element semantics shared by detection and write calculation.

Matching always normalizes ASCII spaces/tabs and ignores empty or repeated
elements. Write cleanup is an independent, explicitly selectable operation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import ValidationError


@dataclass(frozen=True)
class CleanupSettings:
    trim_whitespace: bool = True
    remove_empty: bool = True
    deduplicate: bool = True

    def __post_init__(self) -> None:
        if any(type(value) is not bool for value in
               (self.trim_whitespace, self.remove_empty, self.deduplicate)):
            raise ValidationError("自动整理设置必须是布尔值")


def normalize_element(raw: str) -> str:
    return raw.strip(" \t")


def effective_elements(value: str) -> tuple[str, ...]:
    """Return stable, case-sensitive unique effective elements."""
    return tuple(dict.fromkeys(item for raw in value.split(";")
                               if (item := normalize_element(raw))))


def cleanup_items(items: Iterable[str], settings: CleanupSettings | None = None,
                  *, prefer_last: bool = False) -> tuple[str, ...]:
    settings = settings if settings is not None else CleanupSettings()
    raw_items = tuple(items)
    ordered = reversed(raw_items) if prefer_last else iter(raw_items)
    result: list[str] = []
    seen: set[str] = set()
    for raw in ordered:
        key = normalize_element(raw)
        if not key and settings.remove_empty:
            continue
        # Empty slices are formatting, not duplicate nonempty elements.
        if key and settings.deduplicate and key in seen:
            continue
        if key:
            seen.add(key)
        result.append(key if settings.trim_whitespace else raw)
    if prefer_last:
        result.reverse()
    return tuple(result)


def cleanup_value(value: str, settings: CleanupSettings | None = None) -> str:
    # Joining no effective items represents a present empty value during apply.
    return ";".join(cleanup_items(value.split(";"), settings))

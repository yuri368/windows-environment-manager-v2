# Product

<!-- impeccable:product-schema 1 -->

## Platform

Windows native desktop (PySide6 / Qt). This is an existing native application; the skill's web/mobile platform enum does not describe it.

## Users

Confirmed: the user manages environment configuration for several applications on Windows. Batch selection is the primary workflow; individual adjustment is secondary.

## Product Purpose

Manage application -> option -> variable configuration, select source and input, aggregate a multi-application batch, preview once, then apply with verification and safe rollback.

## Operating Context

Existing Python 3.13 / PySide6 application. TOML configuration defines defaults and choices. Per-user SQLite stores successful inputs and operations. The original project is reference only; no migration is requested.

## Capabilities and Constraints

Preserve the v1.3 behavior in AGENTS.md, exact raw strings, empty versus absent states, source selection, drafts, default options, batch conflicts, aggregate sorting, undo/redo, explicit TOML saving and external-change checks. The UI never computes registry operations itself.

## Brand Commitments

Confirmed: light warm-gray surfaces, ink text, restrained accents. The user requests both main workspace and configuration editor layout/interaction replacement and rejects an old-fashioned property-sheet appearance.

## Evidence on Hand

env.example.toml; implementation and behavioral tests; native demo screenshots in docs/images/main-window.png and docs/images/main-window-narrow.png.

## Product Principles

- Show the selected applications and options before application.
- Keep input source and raw value understandable.
- Preserve unfinished edits during navigation.
- Preview changes before persistent writes.
- Report conflicts and partial failures precisely.

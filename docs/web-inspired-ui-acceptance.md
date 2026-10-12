# Web inspired native UI acceptance

This record documents the native Qt UI capture harness and the boundaries of its evidence. It does not claim release or installer acceptance.

## Implementation scope

The application exposes three native workspaces: the scheme workbench, a read-only Windows environment inventory, and operation history. The Windows inventory enumerates all values from both User and System scopes through the store listing API, then supports name/value search, scope filtering, hidden sensitive values, and explicit full-value viewing. History lists actual service records and routes **Preview restore** through `plan_restore`, so the existing preview and current-state checks run before any restore confirmation.

The configuration editor navigation shows real scheme group headings and their variable children. “All schemes” filters the cross-scheme overview; the selected real scheme in navigation remains the operation context. Scheme dropdowns use a single delegate to paint each popup row, including selection, status, default/active markers, and text, as one full-row item.

The Windows captures below use the public synthetic fixture at 1366×768 logical pixels and native DPR 1 (100%):

- Workbench: [workbench-1366x768-windows-100pct.png](images/web-inspired/workbench-1366x768-windows-100pct.png)
- Editor with scheme groups: [editor-schemes-1366x768-windows-100pct.png](images/web-inspired/editor-schemes-1366x768-windows-100pct.png)

## Capture setup and browsing boundary

`tools/capture_web_inspired_ui.py` uses a public synthetic TOML fixture, a temporary TOML/state directory, and `MemoryStore`. The harness prepares five real service history entries by executing the Java, Python, Maven, Git, and VSCode fixture plans. Those setup executions account for eight in-memory writes, five notifications, and five history records. They are fixture setup, not UI browsing.

After setup, the harness snapshots the store writes, notifications, persistent state, and history. It browses the workbench, all three workspaces, scheme popups, and the configuration editor, then asserts that browsing added zero writes and zero notifications and left state/history unchanged. It also checks that the temporary fixture TOML and hashes of any project configuration files present are unchanged. The harness asserts these boundaries before writing its report.

All displayed values are synthetic public examples. The capture harness uses `MemoryStore` rather than the host Windows registry, and contains no personal configuration values or credentials. Windows captures were produced with `--platform windows`; separate 200% DPR captures are also available in the same image directory.

## Verification status

The related workspace and enumeration test set has **35 tests passed**. A separate interaction report at `docs/compact-ui-evidence/web-inspired-interaction-report.json` records actual `QTest` key events for the three filter controls and three workspace tabs, including focus traversal, Enter preview, Escape dismissal, and 1-, 2-, and 12-row layouts. It reports no failures, no environment writes or notifications, unchanged state revision, and unchanged temporary/project configuration hashes. The overall test suite and CI totals belong in the parent task's final verification record and are intentionally not inferred here.

The current theme's contrast check covers 20 primary, secondary, and muted text/token pairs against five regular surfaces. All 20 exceed 4.5:1; the minimum is **4.666:1** for secondary text on the canvas. Disabled text, status badges, focus indicators, and captured screen pixels are outside that calculation.

There are 48 captures under `docs/images/web-inspired/` and six capture reports under `docs/compact-ui-evidence/web-inspired-report-*.json`. Windows and offscreen batches cover actual DPR 1, 1.5 and 2, including scheme popups, both workspaces at 1024×768 and 1366×768, the inventory and history. Every Windows capture passes Qt visibility and native `IsWindowVisible`; capture assertions also check that sidebar counts and scheme selectors fit and primary actions remain visible. The local Windows desktop uses 200% system scaling, so `QT_SCALE_FACTOR=0.5/0.75/1` yields these actual DPR values. At DPR 2 some requested large windows are limited by the available desktop; each report retains actual logical and pixel dimensions. The interaction report is offscreen input/state evidence and does not claim installed-app acceptance.

## Native popup frame follow-up

The original popup captures above are superseded by the six native desktop crops in `docs/images/scheme-popups/`. Qt's list delegate styled the content while its outer popup container retained a system frame, shadow and menu spacers. Both scheme selectors now use a frameless, shadowless container with one rounded list border. The popup height contains complete rows and list padding only. It opens below the field with a 4px gap, moves left when necessary, and opens above when there is insufficient space below.

The updated capture harness keeps the disposable public-fixture window in front and maximizes it for the menu captures. It crops the actual desktop, including the selector and complete popup, rather than inferring native chrome from `QWidget.grab`. The report at `docs/compact-ui-evidence/scheme-popup-native-report.json` records DPR 1, 1.5 and 2, native visibility, shared list/container bounds, complete rows and unchanged browsing state/configuration. Fixture initialization still accounts for eight MemoryStore writes and five history entries; browsing adds none.

- Sidebar at 200%: [rail-scheme-desktop-200pct.png](images/scheme-popups/rail-scheme-desktop-200pct.png)
- Target selector at 200%: [target-scheme-desktop-200pct.png](images/scheme-popups/target-scheme-desktop-200pct.png)

The selector tests cover both popup bounds and content height, actual Down/Enter selection and Escape cancellation, a bottom-right popup opening above within the screen, long names, and 32-option scrolling. All eight selector tests passed on native Windows as well as offscreen. Windows' scroll animation temporarily hides the real popup; this selector disables that effect for its own synchronous opening and then restores the application preference, so sizing and position apply immediately.

The final local suite passed all **582 tests in 41.703s**. Its four Windows registry tests use disposable `HKCU/Software/EnvironmentManagerV2Tests/<UUID>` keys. An initial restricted run denied those temporary-key writes, so the final complete run used the normal process permissions. No host environment keys were written. The current PR records the matching Windows/Linux CI result separately.

# Compact density and direct creation — 2026-10-18

User feedback covers the candidate creation panel, repeated unfinished-creation prompts, sidebar instructions and the size of workbench controls. One authorized Astra analysis recommended reducing container spacing and control chrome while preserving readable text and full values; the main agent implemented and verified the changes.

The shared compact-ui.css overlay reduces navigation, search/filter controls, batch/context bars, application rows, footer, workbench comparison, Windows inventory and preview/history outer spacing. Application and scheme text can wrap. The workbench sidefoot is removed. No zoom, transforms for scaling, root-font reduction, new internal scrolling or changes to values/plans were introduced.

Candidate creation is a 36px empty row with 28px inputs at the reviewed desktop size, no outer card, duplicate labels or empty error space. Input height is automatic: a four-line value measured 85.33px with matching client/scroll heights. At 600px and 391px, two-line input measured 46.33px and required no internal scrolling. Narrow layouts retain accessible visible field labels.

Application/profile/variable/choice creation entries switch directly. Paused drafts remain in the existing editor session, matched to their original owners by object identity; returning through the corresponding add entry restores the text. Cancelling/submitting clears only that active draft. Restore/reset clears all drafts, including paused drafts. Model creation, full validation and TOML isolation continue through the existing paths.

Validation:

- Full npm test: all 33 scripts completed, exit code 0; output in full-test.log. Creation checks now 35/35, including direct switching, retained drafts, cancellation and restore; existing editor checks 14/14.
- Browser CSS viewport widths 1632, 600 and 391: candidate input content remains complete and editor/main page have no horizontal overflow. Workbench at 391px also had no page overflow.
- Desktop measured navigation 42px, sidebar 236px, sidehead 109.29px, batchbar 31.26px, context bar 22px, first application row 50px. Natural content can exceed the minimum heights.
- Browser confirmed choice/application drafts resume without conflict prompts. No captured console errors. Preview was opened and cancelled, without applying the plan. Screenshots cover workbench, configuration candidate, Windows, history, preview and narrow layouts.

This is browser/DOM evidence, not a claim of native Windows DPI or IME validation. Unrelated pre-existing phase2 shell/surface/HTML work is preserved and excluded from this task's staged diff.

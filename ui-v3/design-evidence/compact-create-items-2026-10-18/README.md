# Compact creation items — 2026-10-18

User feedback: creation fields should share the size and placement of existing list items. This follow-up supersedes the earlier padded creation panels.

- Application/profile naming fields use 32px rows, matching sidebar items. Existing variable details stay visible while either name field is open.
- Variable creation entry and input occupy the final slot of the existing variable strip. Both are 34px high; the gap after the last variable is 4px. Native content sizing expands the field as the name grows, bounded by available width with natural wrapping.
- Confirmation/cancellation retain accessible names, titles and keyboard behavior. Naming stages, draft isolation and final configuration validation are unchanged.

Validation: creation checks 34/34; configuration editor checks 14/14. Browser measurement at CSS viewport widths 1440, 600 and 391 confirmed 34px variable fields and no horizontal overflow of the main editor. Application/profile form height and sidebar item height measured 32px. The test tab reported no captured console errors. These are browser viewport checks, not native Windows DPI or IME testing.

Screenshots: variable-inline.png, application-inline.png, profile-inline.png, narrow-inline.png (600px), narrow-391.png. JSON check outputs are recorded beside this file. The repository already had unrelated phase2 shell/surface/history changes; they are excluded from this commit.

ChatGPT webpage collaboration remains unavailable because the Bridge browser endpoint was unavailable in the preceding attempt. No architectural approval or webpage report is claimed; this change follows the user's explicit UI feedback and preserves existing creation boundaries.

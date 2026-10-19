# Compact focus and comparison wording — 2026-10-18

The creation form used a 2px outline with a positive 2px offset, extending 4px beyond its box into the next row's 2px gap. Focus outlines now use a negative 2px offset for creation forms, their action buttons, and configuration navigation items. Application/profile naming rows remain 32px high; no extra spacing or layout expansion was introduced.

The workbench's ambiguous “查看对照” caption now reads “Windows 值对比”, with a title explaining the three displayed values and aria-controls pointing to the existing inspector. The existing click handler, plan calculation and source values are unchanged. The button allows wrapping its caption, keeping MAVEN_HOME on one line at the reviewed desktop size.

Browser verification at a CSS viewport of 1632×816:

- Application form bottom 224.67px; Java row top 226.67px. Form height 32px, outline offset −2px. Mouse hover over Java leaves the form's focus indicator within its own bounds.
- Profile form bottom 696.67px; first profile row top 698.67px. Form height 32px, outline offset −2px. The adjacent hover state remains separate.
- Tab to the form submit button retains a visible inset focus outline. ArrowDown from Java focuses Python with an inset outline.
- After collapsing the comparison, clicking Path's comparison button reopens the inspector, shows “Windows 值对照 · Path” and focuses inspectTitle. No captured console errors. Caption has no horizontal overflow; MAVEN_HOME measures one 22px line.

Targeted checks passed: config creation 34/34, config editor 14/14. JSON outputs and browser screenshots are stored here. No native Windows DPI or IME checks were performed for this CSS/text change.

The mirror checkout contains pre-existing phase2 shell, surface and HTML edits. Only the local focus selectors, comparison caption/wrapping, resource versions and this evidence are staged for this task.

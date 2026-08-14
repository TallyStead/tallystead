# Accessibility baseline

Tallystead targets WCAG 2.2 AA for core web flows. Accessibility is a release property, not a final visual pass.

Core screens must support keyboard-only use, clear visible focus, logical focus order, native or correctly labelled controls, descriptive validation, heading hierarchy, zoom/reflow, non-color status cues, and reduced-motion preferences. The persistent desktop sidebar becomes a labelled top-bar menu trigger on mobile; opening and closing it must not remove access to navigation or the user menu.

## Review matrix

| Flow | Keyboard | Labels/headings | Screen reader | 200% zoom/mobile | Error/status communication |
| --- | --- | --- | --- | --- | --- |
| Setup, sign-in, password and passkey | Required | Required | Required | Required | Required |
| Main navigation and profile/session controls | Required | Required | Required | Required | Required |
| Add/edit transaction and receipt | Required | Required | Required | Required | Required |
| Import/review/transfer/reimbursement | Required | Required | Required | Required | Required |
| Planner, reports, plans, and goals | Required | Required | Required | Required | Required |
| Settings, export/import, and destructive confirmation | Required | Required | Required | Required | Required |

Automated source and browser checks can catch missing names, invalid markup, and some contrast problems, but do not replace the keyboard and screen-reader checks in `RELEASE_CHECKLIST.md`. Findings that block a core flow are release blockers.

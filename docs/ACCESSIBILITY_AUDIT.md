# Accessibility Audit — Deployment Readiness

**Audit date:** 18 August 2026  
**Scope:** sign-in and password reset, dashboard navigation, tracking list and bulk receipt, document upload and review, repository retention queue, search results, notifications, and reports.

## Method

This pass combined static template inspection, Django template validation, role-based page smoke testing, and keyboard-oriented markup review. The inspection checked the global skip link and main landmark, visible focus styles, explicit button types, label/control associations, image alternatives, table headings, non-colour status text, and status announcements. The smoke test requested every representative page as administrator, records officer, and regular office user.

This is not a substitute for a person using NVDA, JAWS, VoiceOver, or another assistive technology on the university's actual workstations. That manual check remains a handover requirement.

## Results

| Area | Result | Evidence or action |
|---|---|---|
| Keyboard entry | Pass | `base.html` provides a skip link to the main content landmark; focus styles are present for links, receipt buttons, tabs, and custom multiselect controls. |
| Bulk receipt | Pass | Every selectable row has a unique accessible label containing its tracking number. Submission requires a separately labelled custody-confirmation checkbox. The single-record receipt path remains available. |
| OCR privacy controls | Pass | Language and external-provider consent use standard labelled form fields. Sensitive-document guidance is visible text, not a tooltip or colour-only indicator. |
| Retention status | Pass | “Due for records-officer review” and the date are written as text. Red colour is supplementary; the wording carries the meaning. |
| Extraction state | Pass | Pending and processing states use plain language, and the extraction note container has status semantics. |
| Search | Pass with follow-up | Result titles remain normal links. Click telemetry redirects to the same permission-checked document page. Relevance is shown as a number and label, not only a bar. |
| Reports | Pass with follow-up | Charts include textual tables and labels; browser Print-to-PDF remains available. A manual check is still needed for page breaks at the university's preferred paper size. |
| Images and icons | Pass in static scan | No template image was found without an `alt` attribute. Decorative receipt marks are hidden from assistive technology. |
| Role-based rendering | Pass | The page smoke test covered three roles and reported no template or permission-path exceptions. |

## Auto-submitting the report scope — a trade-off, recorded

The Reports office picker carries `data-auto-submit`, so changing it re-scopes
the page immediately. That is a deliberate trade-off, not an oversight:

**Cost.** Firefox fires `change` while a user arrow-keys through a closed
`<select>`, so a keyboard user moving down the list is submitted on the first
option they pass rather than the one they meant. Screen-reader users meet the
same thing.

**Mitigation.** The **Apply** button stays. It is the no-JS path and the
keyboard escape hatch: a keyboard user can open the list with Alt+Down, move
within the popup without firing `change`, commit with Enter, or ignore the
auto-submit entirely and press Apply. The button is never hidden, and the
listener uses `requestSubmit()` so the form's own validation and submit event
still run.

**What still needs a human.** Confirm on Windows/Firefox with NVDA that
arrow-keying the picker does not strand the user mid-choice, and that focus
lands somewhere sensible after the page reloads. If it does strand them, the
fix is to drop `data-auto-submit` — the Apply button alone is already complete
behaviour.

## Remaining manual handover checks

On one Windows office workstation, complete the create → route → bulk receive → complete → archive → search flow using only Tab, Shift+Tab, Enter, Space, and arrow keys. Repeat the sign-in, password-reset request, upload privacy choice, retention filter, and report print actions with NVDA enabled. Confirm that focus never disappears behind the fixed top bar, validation errors are announced near their fields, and the browser print preview does not split a table heading from its rows.

Record the browser, assistive-technology version, operator, date, and any blocker in the issue tracker. Treat a blocker in receipt confirmation, document download, or password recovery as a release blocker; cosmetic reading-order refinements can be scheduled separately when the task remains understandable and operable.

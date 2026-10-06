# DocTrack UI and system accuracy release audit

**ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE.**

This release includes the previously requested office layout, split dashboard
queues, restored Repository chart, chart interactions, clearer forms and
accessibility corrections. The additional audit checks the data behind those
screens, permissions, record transitions, account writes and linked lists.

## Confirmed corrections

| Problem | Correction |
| --- | --- |
| Tracking search could hide results when only deadline, office or valid stages mixed with an invalid stage were selected. | Results follow the effective validated filters. An unfiltered search still starts with its existing prompt. |
| Ownership filters incorrectly rejected creator-owned records in receiving queues. | Returning documents can appear in their originating office's incoming and received queues. Truly incompatible filter combinations remain rejected. |
| Searching by the current office's code could miss a record. | Tracking search checks the current office code as well as its name and the originating office. |
| Autocomplete suggested inactive documents excluded from results. | Suggestions use the same active-document boundary. |
| Repository Pending Filing could count Tracking's wider office-history cohort. | Its shortcut now counts the exact Repository folder it opens. Where that folder covers one originating office, the hint names that office. The adjacent Tracking queue continues to show all permitted pending records the office has handled. |
| Drafts could produce an empty memo, overdue percentages above 100%, or an empty all-office overdue chart. | Memo totals explicitly include drafts; circulation charts continue to exclude unsent drafts. The all-office overdue chart includes overdue drafts and its links retain matching stage filters. |
| Viewers and completed parallel recipients could see unusable receipt controls. | Both individual and bulk controls match service permissions, current batches and active lifecycle stages. |
| Completion before receipt could return 500. | Validation returns usable feedback, and the premature completion control is hidden. Valid completion after receipt remains available. |
| Attachment rejection could leave a draft or remark saved, or save an upload before forwarding failed. | Each compound database operation rolls back together. Successful operations and append-only history remain intact. |
| Bulk receipt errors could return to the wrong screen. | Success and error paths both respect the originating dashboard/list destination. |
| Administrators editing another office's draft saw recipient choices based on their own office. | The draft's original office determines excluded self-routing. Creator and originating office remain unchanged. New drafts still use the initiating office. |
| Retiring an office/type could block repository edits or clear a draft's saved type. | Existing records retain their saved classification choices. Other inactive choices stay unavailable, and new records still offer active choices. |
| Audit office filtering followed an actor's current office after a transfer. | It uses the event's immutable office snapshot, including when the actor is later deleted. Unknown legacy attribution is not guessed. |
| A stale profile request could undo a concurrent suspension, role/office change, password reset or forced password change. | Profile saves write only the personal fields in the profile form. Preference saves remain independent. |
| Malformed timeout values could break sign-in rendering. | The value is compared with configured windows without parsing an untrusted integer. Administrator idle logout remains 15 minutes; staff/viewer logout remains 30 minutes. |
| Closed mobile navigation remained keyboard reachable; opening it did not contain focus. | Closed navigation is hidden/inert; opening traps Tab/Shift+Tab and makes background content inert. Escape, close and backdrop restore focus. Desktop resize and the native idle-warning dialog remain usable. |
| A monthly Repository link pointed at a removed report section and ignored filter. | Removed the redundant false drilldown. Existing Repository navigation remains available. |

## Preserved boundaries

- Django, PostgreSQL, Bootstrap and HTMX remain in place; no schema changes.
- `visible_to` remains the security base. Filters narrow it; `SYSTEM_ADMIN`
  alone has cross-office administration/view-as-office privileges.
- Receipt queries still use the current routing batch. Custody, filing,
  numbering, signed downloads and append-only history keep their existing rules.
- Browser checks used existing demo roles and permitted read-only pages. No
  workflow, upload, filing or administrative mutation was submitted in the live
  browser. End-to-end seeded checks used a separate PostgreSQL test database and
  separate file storage, with an explicit database-name guard before seeding.

## Verification

| Check | Result |
| --- | --- |
| Full production-style Django regression suite and coverage | 6,731 passed, 2 expected skips; 82.41% coverage. |
| Background processing and queued extraction/security regressions | 26 passed. |
| JavaScript security and interaction suite | 156 passed. CI now runs every JavaScript regression file. |
| Template validation | 87 templates, no problems. |
| Ruff and Git whitespace checks | Passed. |
| Django system/deployment checks | No issues. |
| Model/migration consistency | No changes detected. |
| Collected production static assets | Successful. |
| Dependency audit | No known vulnerabilities found in release requirements or the installed environment (Django 5.2.17). |
| Isolated end-to-end workflow | 21 checks passed. |
| Isolated page smoke checks | 256 requests across four roles; no page errors. |
| Independent source review | Workflow/account, UI/security, and metrics/query reviews; confirmed findings corrected. |
| Browser inspection | Principal pages at desktop and 320px; all four roles, office Pending Filing count/link, staff form, viewer detail, mobile navigation focus and light/dark examples. |

The focused regression cases were run against separate test databases and the
confirmed defects were reproduced before their fixes. Exact dashboard query
guards remain: 54 reads overall and 56 with one office selected. The extra fixed
reads count the separate pending queues and drafts; there is still a guard
against queries growing with each displayed record.

The skips are the existing MED-admin fixture without incoming rows and the
background-enabled case, which was covered by the separate 26-test background
run. Two existing Django 6 deprecation warnings concern `CheckConstraint.check`;
the supported application remains on Django 5.

The screenshot folder is [verification/system-release](verification/system-release/).
Earlier layout evidence remains in its dated verification folders. Those files
document earlier local checkpoints; this release audit records the integration.

## Practical limits

Passing tests and sampled browser checks do not establish WCAG conformance or
guarantee that no bug exists. Manual NVDA/screen-reader, forced-colors, actual
400% browser zoom and physical print inspection were not performed in this audit.
External OCR services and remote storage were not exercised with production
credentials. Database rollback cannot undo an already-written storage object;
a later failure can leave an unreferenced file, which remains a pre-existing
storage-cleanup limitation.

Release gates are the exact branch/PR head's CI success before merging and the
resulting main commit's CI and remote SHA verification. Live user data is not
seeded or reset as part of release.

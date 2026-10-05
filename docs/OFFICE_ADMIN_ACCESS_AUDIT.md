# Office administrator access audit

Policy: an `ADMIN` manages accounts, account roles, record permissions and audit
activity for their assigned office. Only `SYSTEM_ADMIN` can change the office
view or manage university-wide settings. Administrative status must never widen
the office's document view.

## Data boundaries

| Area | Office administrator boundary | Enforcement |
| --- | --- | --- |
| Dashboard, memo, tracking reports and CSV | Tracking records originated by, currently held by or actually routed through their office. No office switching. | `apps/tracking/models.py`, `apps/tracking/services.py`, `apps/core/filters.py`, `apps/core/views.py` |
| Tracking detail, review, receipt, routing, remarks, completion, reopening and attachments | The same visibility boundary is checked on direct links; action permissions are checked again in services. A grant or old creator identity alone cannot expand this role's view. | `apps/tracking/models.py`, `apps/tracking/views.py`, `apps/tracking/services.py` |
| Repository, folders, reports, search, file downloads and extraction status | The document's owning office must be the administrator's office. Restricted documents still require uploader identity or an applicable grant. OVPA sharing and grants do not allow foreign-office documents. | `apps/documents/models.py`, `apps/documents/views.py`, `apps/search/services.py` |
| Repository pending-upload list | Only unfiled records originating in the administrator's office. Other completed work handled by their office remains in Tracking. | `apps/documents/views.py` |
| Tag suggestions and metadata-review suggestions | Only tags attached to active documents this administrator may view. Global tag usage does not determine their suggestion order. | `apps/documents/services.py`, `apps/documents/views.py`, `apps/search/services.py` |
| Accounts and account totals | Own office only; system-administrator, Django staff and superuser accounts are excluded. Submitted office and role values are validated. | `apps/accounts/models.py`, `apps/accounts/forms.py`, `apps/accounts/views.py`, `apps/core/views.py` |
| Record permission recipients | Own office and its eligible accounts only, enforced in both the form and grant service. | `apps/tracking/forms.py`, `apps/tracking/services.py` |
| System audit activity | Verified office snapshots and an authorized target are required. Account transfers cannot change the historical office. | `apps/core/utils.py`, `apps/core/views.py` |
| Tracking access audit | Access activity only for tracking records the administrator may view, including legitimate outsiders accessing those records. | `apps/core/views.py` |
| Notifications and unread counts | Own office, with linked tracking records and repository documents checked against visibility. Hidden notifications cannot be marked read. | `apps/core/models.py`, `apps/core/notifications.py`, `apps/core/views.py` |
| Global settings and raw Django admin | System administrator only. Includes offices, document types, tags, metadata rules, metadata fields, office hours and holidays. | `apps/core/mixins.py`, `apps/core/views.py`, `apps/core/admin_site.py`, `apps/accounts/views.py` |
| Browser print audit | Object targets must be visible tracking records or active repository documents. Unknown target types, invalid IDs and unauthorized objects are rejected. | `apps/core/views.py` |

An inter-office routing destination is a directory choice, not a permission to
open that office's documents. A record genuinely routed to or from the office
remains usable in Tracking. Filing it in the originating office's repository
does not grant the handling administrator access to that repository: the page
returns to the permitted tracking history and does not offer a forbidden
repository link.

Ordinary users' and viewers' existing sharing rules are preserved. This audit
tightens the administrative role's boundary rather than changing university
sharing policy for all roles.

## Role and historical-log safeguards

`User.is_system_admin` now depends on the explicit `SYSTEM_ADMIN` role. Accidental
`is_staff` or `is_superuser` flags on an `ADMIN` account do not enable cross-office
application views or the raw Django admin. New superusers receive the explicit
system-administrator role; conflicting roles are rejected.

Office administrators' direct record and file lookups use authorized querysets.
Foreign IDs return 404 without fetching the object into a page or revealing
whether that foreign ID exists.

New audit entries record server-supplied actor and target office IDs. Older rows
without a verified office snapshot remain available to system administrators.
They are excluded from an office administrator's system audit panel instead of
guessing from an actor's present office. No historical rows are rewritten. The
audit page explains this limitation.

Administrators without an assigned office receive empty document, tracking,
account and audit querysets. They cannot use a missing office as a global scope.

## Verification

`tests/test_office_admin_boundaries.py` checks lists, reports, exports, folders,
search, suggestions, direct object URLs, downloads, posted actions, permission
forms, service calls, notifications, audit transfers, no-office accounts and
mixed Django staff/superuser flags. Tests check positive own-office and incoming
workflows as well as denial of unrelated foreign records.

Existing permission, office administration, report agreement, filter agreement,
repository and notification tests provide regression coverage. The page smoke
script checks allowed pages and intentional global-setting refusals separately.

Verification results on 3 October 2026:

- The full run checked 6,323 cases: 6,319 passed, one was skipped and three
  assertions expected the previous, broader office-administrator permissions.
  Those assertions now require the narrower policy requested by the user.
- The final affected regression run passed 445 tests, including all three
  corrected expectations and the new direct-link, filing and anonymous-access
  checks. One test skipped an incoming queue with no matching fixture records.
- Full-run application coverage was 77.41%, above CI's 60% minimum.
- Page smoke checks passed 256 requests across system administrator, office
  administrator, user and viewer roles. All 21 workflow self-checks passed.
- All 125 chart interaction tests passed. Repository lint, 87 template checks,
  Django system/deployment checks, migration consistency and static collection
  passed. The full suite was not repeated after the focused corrections.

Local verification used Windows, Python 3.13 and Node 24; CI specifies Linux,
Python 3.12 and Node 22. This is local verification, not a completed remote CI run.

A read-only check of the current local database inspected all three active
office administrators: zero foreign repository documents, unrelated tracking
records or foreign administrable accounts were visible. Browser verification as
`med.head` showed only MED repository folders and MED account administration.

These controls are application-level Django query and object checks. This audit
does not claim PostgreSQL row-level security or verify remote production data,
storage-provider permissions or deployment state. No release was requested.

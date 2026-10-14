# DocTrack system audit, 14 October 2026

The audit reviewed account/session safety, tracking workflow and office stages,
dashboard/report/list/export counts, Search and Repository filters, filing,
extraction, deletion/recovery, background tasks, and rendered UI. Existing local
turnaround/filter work and unrelated settings edits were preserved.

## Confirmed problems corrected

| Area | Problem | Correction |
| --- | --- | --- |
| Password changes and recovery | A stale user instance could restore older roles, office assignments, activation or email details, and replace a newer administrator password reset. | Lock and refresh the account, revalidate credentials/token, write only the password, and keep the forced-change update atomic. |
| Forced password changes | Reusing the administrator's password cleared the required-change flag. | Require a different password while the flag is set. |
| Recovery completion | Django's default completion redirect used an unregistered, unnamespaced route. | Use the existing `accounts:password_reset_complete` route. |
| Exact searches | Incidental identifier mentions could displace the exact document before its 100% score was applied. | Prioritize exact identifiers inside the same scoped candidate set, retaining normal limits. |
| Search candidate ranking | NULL full-text ranks could precede stronger indexed matches. | Sort NULL ranks last. |
| Administrative metadata edits | Renamed offices, types and tags, or changed searchable-field settings, left document indexes stale. | Refresh only affected documents, preserving lock order and tag usage counters. Refresh additional documents filed during the edit after commit. |
| Background metadata review | The original upload session's empty placeholder hid completed extraction suggestions. | Prefer the newest persisted suggestion, retaining the session fallback. |
| Interrupted filing | Copies made before a later attachment failed could remain in storage after the database rollback. | Clean up only newly created repository copies and preserve original attachments and the original error. |
| Unsuccessful OCR retries | A failed provider could be reported as empty text, erasing earlier searchable text; known image metrics could also be reset to zero. | Preserve provider failure/skipped status and retain usable text and current file metrics on unsuccessful extraction. |
| Overlapping extraction | A full retry could omit a file added during extraction. | Discover additional files under the final lock and process them outside it, with a bounded retry safeguard. Explicit selections stay explicit. |
| Repeated extraction delivery | Append jobs could duplicate already included text. | Append each unique extracted part under the existing document lock. |

The regressions reproduced these cases before their corresponding fixes. The
live count-audit utility was also updated for the current grouped dashboard,
implicit own-office stages and paused processing clock. Its outdated global
status assumptions were corrected rather than changing working page numbers.

## Verification

- Complete final suite: 7,215 passed, 3 skipped, 2 existing Django deprecation
  warnings. Two skips need background tasks enabled; the remaining skip uses
  an office-admin fixture with no incoming rows.
- Background-enabled final suite: 58 passed, including both skipped schedule
  checks and the new extraction regressions.
- 46 new bug regressions passed together, covering stale accounts/credentials,
  normal recovery/session behavior, exact search and index changes, suggestions,
  storage rollback, extraction overlap, duplicate delivery and image metrics.
- Account/security/session regression: 62 passed.
- Search/filter regression: 47 passed.
- Document/filing/deletion/permission regression: 86 passed, 1 background-only
  schedule check skipped in that run.
- Live number reconciliation: 21,807 comparisons across 26 scopes, no failures.
  The checked integrity counters were also zero.
- End-to-end workflow: 21 passed. Turnaround audit: 45 passed.
- Page audit: 256 requests across four roles, including deliberate refusals
  for forbidden actions; all expected responses passed.
- Browser: 28 page/theme/viewport configurations and eight HTMX interactions
  passed. JavaScript suite: 172 passed.
- Package compatibility check passed. Installed-package vulnerability audit:
  72 packages, no known vulnerabilities reported by the audit service.
- Lint and all 97 templates passed. Django reported no configuration errors
  and no missing migrations. Production deployment checks also passed using
  temporary CI configuration, without deploying or contacting providers.

The final complete suite is recorded in
`system-check-2026-10-14-full-suite-final.txt`. An earlier run was intentionally
stopped after independent review identified the final image-metric and rename
race cases; its interrupted log is retained separately.

Temporary live audit records, sessions and request logs were rolled back.
OCR failure tests mocked provider responses. Live external OCR, cloud-storage
availability, email delivery and manual screen-reader operation were not
exercised. Storage rollback cleanup remains best effort if the storage provider
itself refuses deletion; that failure is logged and the original filing error
is retained.

The changes remain local on `codex/turnaround-and-filter-parity`.

# Filters and theme verification

Verified on 1 October 2026, using the configured local PostgreSQL database.

## Changes

- Repository: one search/apply row, visible type/year/month filters, optional
  tag/origin/retention controls, and removable applied-filter chips.
- Folder and advanced-search choices use the records available to the reader.
  Existing documents remain searchable under retired document types.
- Pending uploads offer completion-year/month and tracking-record filters.
  Filed-document filters are explicitly ignored with a notice in that queue.
- Changing office/year preserves other selections, including multiple tracking
  stages. Report record filters and resets retain the selected page size/year.
- Invalid office IDs, years, dates, and enum choices return clear validation or
  fallback notices, while valid independent filters remain applied.
- Reports use compact metric cards and an office selector. The annual year
  selector has reserved space for the value and native dropdown arrow.
- Light mode uses khaki page/card/input surfaces. Dark mode uses green surfaces,
  green actions, and lighter text. Stage colors retain their distinct meanings.
  Physical print output retains white paper.

## Evidence

| Verification | Result |
| --- | --- |
| Dashboard/report/repository/search count reconciliation | 13,013 comparisons; 26 office scopes; no mismatches |
| Filter results, options, invalid inputs, and form preservation | 782 checks across four roles; no mismatches |
| Application page rendering | 244 requests across four roles; no page errors |
| Browser layout and surfaces | 26 page types, both themes, 390/768/1440 px; no page overflow or full-white content panels |
| Shared text/button contrast | 20 role-color comparisons; all at least 4.5:1 |
| Code, templates, Django configuration, migration drift | Passed; 80 templates checked; no model changes |

The role checks cover system administrator, office administrator, regular user,
and viewer. The browser checks cover dashboard, tracking, repository, folders,
pending uploads, reports, search, notifications, accounts, administration,
creation forms, and record details. The year selector was also operated in the
browser to confirm that changing the year updates the annual chart.

These are checks against the local database and browser preview. They do not
establish the contents or configuration of a deployed environment. Full pytest
and release checks are reserved for the next push, as requested.

## Repeat the data checks

```powershell
.\.venv\Scripts\python.exe scripts\audit_counts.py
.\.venv\Scripts\python.exe scripts\audit_filters.py
```

Both scripts roll back sessions, request logs, and temporary database writes.

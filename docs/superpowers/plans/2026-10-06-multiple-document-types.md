# Multiple document types implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan in the current workspace. Preserve the existing uncommitted UI and MED naming changes.

**Goal:** Select multiple document types for one tracking record, retain them when filing, and show and find them consistently.

**Architecture:** Add a many-to-many selection alongside the existing main-type reference. The main type continues to control deadlines, retention, and the report's non-overlapping type breakdown. Existing single-type records and submissions remain valid. Multiple-type submissions explicitly identify a selected main type.

**Tech stack:** Existing Django 5, PostgreSQL, Bootstrap 5, HTMX, and dependency-free JavaScript.

**Scope assumption:** The screenshot shows record creation, so types classify one record and its attachments. Per-file classification is outside this change.

## Constraints and acceptance checks

- Keep office permissions, routing batches, attachments, record identities, and existing retention dates intact.
- Backfill existing type selections through additive data migrations. Keep the original reference for compatibility.
- Reject unknown or newly selected inactive types. Existing inactive selections remain editable.
- A main type must belong to the selection. One selected type becomes its own main type; an empty selection remains unspecified.
- Keep all types on draft edits, review, details, routing slips, repository filing, metadata edits, search indexes, and CSV output.
- Type filters match any selected type. Main-type report links explicitly request main-type matching so counts still equal their destinations.
- Reuse the accessible searchable checkbox control, with native multiple selection as its no-JavaScript fallback.
- Validate multiselect and main-type behavior, legacy submissions, rejected input, clearing selections, filing, permission boundaries, filtering, retention and SLA behavior, and migration preservation.

## Tasks

- [x] Add selections and preservation migrations for TrackingRecord and Document.
- [x] Add shared form validation and accessible selection UI to creation and repository metadata forms.
- [x] Carry selections through creation, draft edits, filing, metadata services, and indexing.
- [x] Display all selected types and update type filters and report drilldowns without double-counting records.
- [x] Run regression checks, browser checks at desktop and phone widths, and review the final changes.

## Verification results

- Complete regression suite: 6,763 passed, two existing skips, two existing Django deprecation warnings.
- Final display, lifecycle, review, metadata, and accessibility checks: 90 passed, including the new main-type display checks.
- JavaScript suite: 160 passed.
- 90 templates parse; changed Python files pass lint; Django reports no configuration issues, missing migrations, or model changes.
- Both additive migrations applied locally. Row counts and hashes prove all 280 tracking records and 256 documents retained their original parent data. Every existing main type was backfilled.
- Desktop and 320px phone interactions verified in both themes. Label focus, filtering, keyboard selection, removing choices, clearing choices, and main-type updates work. Phone removal and clearing controls have 44px targets.
- Separate code review findings corrected; details and screenshots are under `docs/verification/multiple-document-types/`.
- Additional display partial tested from a failing missing-template test to passing zero-, single-, and multiple-type cases. Final targeted checks verify its integration after the full suite.
- Changes remain in the current local feature branch. Earlier UI and MED naming changes are preserved.

# Tracking status lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Make receipt, work, and completion stages accurate for each office and consistent in filters, counts, screens, and exports.

**Architecture:** Derive a recipient stage from its current routing batch, confirmed receipt, and subsequent work attributed to that recipient. An outgoing or all-offices document summary is Pending Receipt while any current recipient owes receipt, otherwise Received or In Process. Retain the terminal draft/completion states, existing permissions, routing history, and administrator filing approval.

**Tech Stack:** Django 5, PostgreSQL, Bootstrap 5, HTMX, PowerShell.

**Spec:** User's 2026-10-13 lifecycle request and HR/MED parallel-recipient screenshots in this chat.

## Global Constraints

- ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE.
- Begin with `visible_to(user)`; office selection only narrows access. Only SYSTEM_ADMIN may select other offices.
- Scope steps and work to the current routing batch.
- Preserve existing global completion policy, superseded routing history, and filing approval.
- Preserve `actor_office` as an audit snapshot; attribute administrative work separately.
- Do not commit on main/develop or discard preexisting local changes.

## Review Focus

- Parallel recipients must have independent Pending Receipt, Received, and In Process stages.
- Remarks/files before receipt, routing instructions, reads, prints, and reopening must not imply recipient work.
- Another office's activity or historical routing batch must not advance a recipient's stage.
- Every count and stage link must select the same unique document set, including selected-office exports.
- Partial receipts must not clear reminders for recipients still awaiting receipt.

### Task 1: Central lifecycle and stage selector

**Files:** `apps/tracking/models.py`, `apps/tracking/services.py`, tracking migration, `tests/test_office_status_lifecycle.py`.

**Interfaces:** `TrackingRecordQuerySet.with_stage_for(office=None)` annotates `stage`; `display_status` and `get_display_status_display()` expose it. `annotate_stage(records, user, office=None)` bulk-loads stage for displayed instances. `filter_records()` uses the annotated stage when present.

- [x] Write regressions for partial receipt, independent recipient work, file-only work, pre-receipt activity, admin attribution, reendorsement, reopening, completion and filing.
- [x] Run focused tests and confirm the original defects fail.
- [x] Implement shared correlated receipt/work queries and matching model recalculation. Record nullable work-office attribution without changing actor audit data. Reopening gets its own event.
- [x] Update scope and status aggregation consumers; recalculate after files and independent starts. Preserve receipt lock ordering.
- [x] Verify focused regressions pass; provide an idempotent dry-run reconciliation command for existing derived status fields.

### Task 2: Presentation, workspace, reports and exports

**Files:** Tracking/Search views, core analytics/views/report_filters, existing stage-pill templates, filter/help copy, `tests/test_office_status_display.py`, `tests/test_office_stage_reports.py`.

- [x] Add tests for HR/MED opposite stages across detail, Tracking, Search, Dashboard, Reports and CSV with role/office guards.
- [x] Apply the shared annotation before filtering, grouping or pagination; display the same annotated stage.
- [x] Replace obsolete whole-document-stage copy with recipient and outgoing summary wording. Keep unique document totals.
- [x] Revise earlier partial-receipt expectations to the user's corrected lifecycle.
- [x] Verify counts equal their linked document sets and selected-office exports.

### Task 3: Receipt reminders and verification

**Files:** `apps/core/tasks.py`, receipt resolution in tracking services, notification regression tests, verification notes.

- [x] Pin partial-receipt reminder behavior with regressions; preserve reminders for outstanding recipients and cover all current recipients in alerts.
- [x] Run focused and full Django regression suites, static checks, and relevant JavaScript/browser checks.
- [x] Inspect the reported live record and reconcile only derived active status/custody bookkeeping after dry-run review.
- [x] Record exact test evidence, remaining manual checks, and no unrelated behavior changes.

Commits and publication remain outside this request; retain reviewable local changes.

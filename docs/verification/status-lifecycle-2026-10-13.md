# Tracking lifecycle correction: 13 October 2026

Subsequent independent recheck, additional reminder/navigation corrections and fresh evidence: [status-lifecycle-recheck-2026-10-13.md](status-lifecycle-recheck-2026-10-13.md).

The reported record, `UDM-OVPA-REC-2026-10-0044`, has HR's confirmed receipt and work while MED has not confirmed receipt. It now shows **HR: In Process**, **MED: Pending Receipt**, and **REC/all-offices summary: Pending Receipt**. Seven existing active records had stale aggregate stages; the preview and repair logs document the derived-field corrections. No routing history, attachments, user accounts, or repository documents were removed.

## Rules

| Situation | Stage |
| --- | --- |
| Unsent preparation | Draft |
| Incoming recipient has not confirmed the current endorsement | Pending Receipt |
| Incoming recipient confirmed, with no subsequent work for its office | Received |
| Incoming recipient has a later remark, attachment, or explicit start | In Process |
| Outgoing/all-offices summary, any current recipient still owes receipt | Pending Receipt |
| Outgoing/all-offices summary, everyone confirmed and no office has worked | Received |
| Outgoing/all-offices summary, everyone confirmed and an office has worked | In Process |
| Work explicitly completed, awaiting administrator filing approval | Completed - Pending Filing |
| Administrator approved the repository document | Completed |

Forwarding and returning create a new batch and repeat the receipt/work cycle. Old batches cannot advance the current stage. Receipt notes, routing instructions, reads, prints, and administrative reopening do not count as processing. The existing global completion policy is preserved: completion ends the record's work and receipt actions, including parallel recipients; reopening restores stages from current routing evidence. Filing approval and all access rules remain in place.

Tracking, Search, Dashboard, Reports, memo, CSV, detail, and print use one shared office-stage annotation. Charts count distinct documents; a university-wide chart counts a parallel route once. Each office's stage children are disjoint and add up to its direction total. Overdue is independent of stage. Multiple recipient offices may each owe receipt or work on the same overdue document, so office accountability is explicitly overlapping while the document total stays unique.

Receipt reminders name the outstanding current recipients. Confirming one recipient immediately removes that office from the existing sender reminder, preserving its read state and the reminder for everyone still awaiting receipt. The last receipt resolves it. New routing batches resolve obsolete reminders.

`RecordActivity.work_office` records the office advanced by an update without changing `actor_office`, the audit snapshot. Migration `0011_recipient_work_stage` retains old work using the receipt history as of the event's timestamp, prioritizing the actor's confirmed office, then the latest confirmed holder for administrative work. It excludes pre-receipt activity and historic reopening notes.

## Operations

Apply normal migrations before serving the updated source. `python manage.py reconcile_tracking_stages` previews cached active stage/custody corrections. Add `--apply` to save them, or `--record=<exact tracking number>` to limit the operation. It preserves terminal stages and refuses to silently turn an official record without current steps into a draft. It is idempotent; history and files stay unchanged.

The local migration was applied successfully. A preview identified seven stale stages, all changed only to Pending Receipt; confirmed custody remained intact. The exact reported slip was then verified in HR, MED, and REC perspectives.

## Verification

- Original lifecycle regression: 9 failures reproduced before the model/service fix.
- First integration: 48 passed, 9 expected cross-page/reminder failures before integration fixes.
- Focused integration: 155 passed; 7 failures were previous shared-stage expectations subsequently corrected.
- Browser navigation: all 19 desktop/mobile, light/dark, enhanced/native configurations passed against 39 genuine Django response states.
- JavaScript: 172 passed.
- Full backend run: 7,068 passed, 12 failed, 3 skipped. The failures exposed obsolete navigation/shared-stage assertions and dashboard query expectations; these were corrected. This initial full run was not clean and is not represented as a full-suite pass.
- Final affected backend regression: 685 passed, covering office lifecycle, migration attribution, counts, exports, permissions, concurrent receipts, completion, search and reports. Two existing Django constraint deprecation warnings remain.
- Detail and printed slip: 16 desktop/mobile and light/dark office configurations passed, including the unconfirmed recipient and the recipient already working.
- Live audit: 10,790 checks across 1,026 page/export requests and 15 role/office representatives passed with zero failures. Requests and audit fixtures were rolled back. Source: `status-lifecycle-live-audit.json`.
- After the final immediate-reminder correction: 106 receipt, concurrency, completion, reminder and dashboard/report agreement tests passed (`status-lifecycle-receipt-final.txt`). These overlap the 685-test run and are not an additional unique test total.
- Static analysis, whitespace checks, Django system checks and migration consistency checks passed. A final live recheck at 11:12 confirmed HR In Process, MED Pending Receipt and aggregate Pending Receipt, with zero assertion failures.

This change remains local; no commit or push was requested or performed. Manual screen-reader testing has not been performed.

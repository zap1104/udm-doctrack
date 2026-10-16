# External UDM Office Support: approved implementation plan

## Goal and guardrails

Implement the user's approved External workflow for other UDM offices without DocTrack accounts. Only fix what is needed and do not break anything else. Existing OVPA offices, accounts, tracking records, routes, numbering, timers and population totals remain unchanged. Preserve the four existing UI edits. Do not push or deploy.

## Deliverables

1. Separate ExternalOffice, ExternalTrackingRecord, ExternalRoutingStep, ExternalActivity, ExternalAttachment, ExternalActionRevision and number-sequence models in tracking. Directory entries and cases belong to an internal managing office; external offices never become account offices. One active recipient, repeated endorsements and returns, independent UDM-EXT numbering, original paper reference retained.
2. Office admins manage their own external entries, system admins retain wider scope. Staff can view authorized published cases and act for their own internal custody; viewers only read. Manual external actions retain actual and recording times. Internal confirmations use the actual user's office and server time.
3. New /tracking/external/ and /documents/external/ views, forms, detail/actions, search, filters, reports and exports. Same status labels and palettes. Show managing office, confirmed custodian and pending recipient independently.
4. Audited external corrections: reasons, old/new values, actor and office snapshots; protect internal facts, dependent chronology, recording times and ownership. Filed custody history is frozen, with annotations available. Lock writes and use expected revisions to reject stale edits and duplicate transitions.
5. Document.collection OVPA/EXTERNAL default OVPA, optional mutually exclusive external tracking relation, snapshot-bearing external-office associations and unified tracking-origin accessor. Shared private storage, upload/extraction, signed access and 30-day deletion recovery remain available to both collections. External document mutations require authorized admins.
6. Permission-first, explicit collection filtering on every legacy repository/search/dashboard/report/facet/related-document consumer; no globally filtered manager. Existing OVPA populations stay unchanged. Background indexing, extraction, backup and purge handle both collections.
7. Separate external timing: internal received/in-process custody, paused receipt waits, external custody/transit separately, overall actual lifecycle from earliest known movement. No invented missing times; use existing business calendar. Count distinct slips/documents and keep draft/pending filing separate from working stages. Internal office perspective uses office=; external counterpart filter uses external_office=; invalid filters never widen results.
8. Internal notifications and durable audit references for external cases. No notifications to external offices. Complete mixed-flow, permission, concurrency, chronology, filing/storage/extraction/deletion, population parity, URL/filter, responsive and full regression verification.

## Work allocation and interfaces

- Backend: tracking/external_models.py, external_services.py, external_analytics.py; import models at bottom of tracking/models.py. Tracking migrations owned by backend implementer.
- Tracking UI: external_forms.py, external_views.py, external_urls.py, tracking/external templates; include external URLs before integer record routes. Own tests and scoped external CSS. Do not change legacy workflow templates beyond navigation links.
- Repository integration: Document Collection enum, in_collection() queryset, external_tracking_record relation, ExternalDocumentOffice through model; external_services/forms/views/templates and documents migrations. Own legacy document population isolation, search integration and tracking-origin consumers. Coordinate edits to core/views.py with parent.
- Parent: notifications/audit integration, execution ledger, integration/review, complete tests, migration application, browser verification and final handoff.
- Stable tracking model endpoint names: from_office/from_external_office and to_office/to_external_office; managing_office on directory and case. Services use keyword user and expected_revision; mutations reload active actor and locked record. UI sends revision on every existing-record write.
- Stable tracking service functions: create_external_office, save_external_office, create_record, route_record, confirm_receipt, record_external_receipt, add_remark, add_attachment, record_external_work, complete_record, reopen_record, correct_step, annotate_record. Filing is documents.external_services.archive_external_record.
- Notification helpers gain external_record=None and resolve_for_external_record(); existing callers unchanged.

## Defaults and rulings

- Existing legacy rows are not converted; new collection field defaults OVPA. Source stays DTS/UPLOAD/SCAN, not a fourth acquisition type called external.
- Directory names are office-scoped; snapshots prevent later renames changing historical facts. Original references are not globally unique; duplicate warnings must not reveal another office's records.
- Paper history may record past external facts. Never fabricate or overwrite internal DocTrack confirmations; unknown dates remain notes/evidence rather than fabricated timings.
- Current in-place feature branch preserves uncommitted UI work. No new worktree is needed for this authorized checkout; no changes to main/develop.

## Verification

Use meaningful failing behavior tests before feature implementation. Verify on PostgreSQL, with process-only test settings. Run focused tests per stage, then full Django and JavaScript suites, lint/template checks, migration drift and PostgreSQL migration checks. Verify all four roles, light/dark and phone/tablet/desktop. Reports must record actual results and manual-testing limits.

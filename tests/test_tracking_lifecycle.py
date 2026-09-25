"""One document, draft to repository, checked at every step.

Status, the timestamps each step owns, the timeline entry it adds and the
audit row it writes. Drafts are created alongside it, one abandoned, to show
that a number is issued on send and an abandoned draft leaves no gap.
"""

from __future__ import annotations

import re

import pytest
from django.conf import settings

from apps.core.models import AuditLog
from apps.tracking.models import RecordActivity, Status, TrackingRecord
from apps.tracking.services import (
    approve_upload,
    complete_record,
    confirm_receipt,
    create_draft_record,
    mark_in_process,
    route_record,
)


def _events(record):
    return list(record.activities.order_by("created_at", "pk").values_list("event", flat=True))


def _audits(record):
    return list(
        AuditLog.objects.filter(target_id=str(record.pk)).order_by("pk").values_list("action", flat=True)
    )


def _number(office, sequence):
    return re.compile(
        rf"^{re.escape(settings.TRACKING_NUMBER_PREFIX)}-{office.code}-\d{{4}}-\d{{2}}-0*{sequence}$"
    )


@pytest.mark.django_db
def test_a_document_from_draft_to_repository(users, offices, memo_type):
    med, sup, admin = users["med"], users["sup"], users["admin"]

    def draft(subject):
        return create_draft_record(user=med, subject=subject, instructions="x", document_type=memo_type)

    # --- drafts: no number, one of them never sent -----------------------------
    record, abandoned, second = draft("The document"), draft("Never sent"), draft("Sent second")
    assert record.status == Status.DRAFT and not record.has_tracking_number
    assert record.display_tracking_number == "Not yet assigned"
    assert _events(record) == [RecordActivity.Event.CREATED]
    assert _audits(record) == [AuditLog.Action.CREATE]

    # --- sent: the number is issued now, first in the series ----------------------
    route_record(record, [offices["SUP"]], user=med)
    record.refresh_from_db()
    step = record.routing_steps.get()
    assert record.status == Status.PENDING_RECEIPT
    assert _number(offices["MED"], 1).match(record.tracking_number), record.tracking_number
    assert step.sent_at and step.received_at is None and record.first_received_at is None
    assert _events(record)[-1] == RecordActivity.Event.SENT
    assert _audits(record)[-1] == AuditLog.Action.ROUTE

    # The second draft sent gets the next number: the abandoned one took none.
    route_record(second, [offices["HR"]], user=med)
    second.refresh_from_db()
    assert _number(offices["MED"], 2).match(second.tracking_number), second.tracking_number

    # --- received --------------------------------------------------------------
    confirm_receipt(record, user=sup)
    record.refresh_from_db()
    step.refresh_from_db()
    assert record.status == Status.RECEIVED
    assert step.received_at and record.first_received_at == step.received_at
    assert record.current_office == offices["SUP"]
    assert _events(record)[-1] == RecordActivity.Event.RECEIVED
    assert _audits(record)[-1] == AuditLog.Action.RECEIVE

    # --- in process --------------------------------------------------------------
    mark_in_process(record, user=sup)
    record.refresh_from_db()
    assert record.status == Status.IN_PROCESS
    assert _audits(record)[-1] == AuditLog.Action.UPDATE

    # --- completed, waiting for approval ---------------------------------------------
    complete_record(record, user=sup)
    record.refresh_from_db()
    assert record.status == Status.COMPLETED_PENDING_UPLOAD
    assert record.completed_at and record.completed_at >= record.first_received_at
    assert _events(record)[-1] == RecordActivity.Event.COMPLETED
    assert _audits(record)[-1] == AuditLog.Action.COMPLETE
    assert TrackingRecord.objects.pending_filing().filter(pk=record.pk).exists()

    # --- approved into the repository ------------------------------------------------
    document = approve_upload(record, user=admin)
    record.refresh_from_db()
    assert record.status == Status.COMPLETED
    assert record.archived_document == document
    assert document.reference_number == record.tracking_number
    assert _events(record)[-1] == RecordActivity.Event.ARCHIVED
    assert AuditLog.Action.ARCHIVE in _audits(record)

    # The abandoned draft is still a draft, still unnumbered.
    abandoned.refresh_from_db()
    assert abandoned.status == Status.DRAFT and not abandoned.has_tracking_number

    # The history only ever grew: every step's entry is still there, in order.
    assert _events(record)[:3] == [
        RecordActivity.Event.CREATED, RecordActivity.Event.SENT, RecordActivity.Event.RECEIVED,
    ]

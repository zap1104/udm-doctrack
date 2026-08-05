"""No hidden history: every movement stays visible, and later steps never
overwrite earlier ones."""

from __future__ import annotations

import pytest

from apps.tracking.services import add_remark, confirm_receipt, create_draft_record, route_record


@pytest.mark.django_db
def test_forwarding_adds_a_step_and_keeps_the_old_one(users, offices, memo_type):
    record = create_draft_record(
        user=users["med"], subject="Endorsement for supplies", instructions="For action.",
        document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    first_step = record.routing_steps.order_by("sequence").first()
    first_received_at = first_step.received_at

    route_record(record, [offices["HR"]], user=users["sup"], action="FORWARD")

    record.refresh_from_db()
    first_step.refresh_from_db()

    assert record.routing_steps.count() == 2
    # The original receipt timestamp is untouched by the later forward.
    assert first_step.received_at == first_received_at
    assert record.status == "FORWARDED"


@pytest.mark.django_db
def test_remarks_accumulate_rather_than_replace(users, offices, memo_type):
    record = create_draft_record(
        user=users["med"], subject="Repair request", instructions="For action.", document_type=memo_type
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])

    add_remark(record, user=users["sup"], remark="Scheduled for inspection.")
    add_remark(record, user=users["sup"], remark="Inspection done, awaiting parts.")

    activities = list(record.activities.order_by("created_at").values_list("message", "detail"))
    # add_remark() stores a short summary in `message` and the actual remark
    # text in `detail` — the timeline template renders them as separate lines
    # (see templates/tracking/detail.html), so the test checks the same field.
    details = [detail for _message, detail in activities]
    assert any("Scheduled for inspection" in detail for detail in details)
    assert any("awaiting parts" in detail for detail in details)

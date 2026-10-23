"""Mixed current receipts are independent of the selected office's own stage."""
import pytest

from apps.tracking.services import (
    active_for,
    apply_scope,
    complete_record,
    confirm_receipt,
    create_draft_record,
    route_record,
)

pytestmark = pytest.mark.django_db


def partial_case(users, offices):
    record = create_draft_record(user=users["med"], subject="Mixed receipt audit", instructions="Review")
    route_record(record, [offices["SUP"], offices["HR"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    return record


@pytest.mark.parametrize("role", ["admin", "med_admin", "sup", "hr"])
def test_partial_receipt_explanation_counts_mixed_current_batch_for_every_scope(client, users, offices, role):
    partial_case(users, offices)
    client.force_login(users[role])
    response = client.get("/tracking/reports/", {"office": "all"} if role == "admin" else {})
    assert response.status_code == 200
    assert response.context["partial_receipts"] == 1
    assert b"some current recipients have confirmed receipt" in response.content


def test_legacy_receipt_queues_do_not_resurrect_completed_pending_filing(users, offices):
    record = partial_case(users, offices)
    complete_record(record, user=users["sup"])
    for scope, role in [("inbox", "hr"), ("sent", "med")]:
        visible = active_for(users[role])
        assert not apply_scope(visible, scope, users[role]).filter(pk=record.pk).exists()

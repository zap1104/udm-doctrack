"""A superseded receipt requires an actual unconfirmed step in an older batch."""

import pytest

from apps.tracking.services import confirm_receipt, create_draft_record, route_record


@pytest.mark.django_db
@pytest.mark.parametrize("who", ["med", "med_admin", "admin"])
def test_an_unsent_draft_is_not_a_stale_receipt(client, users, memo_type, who):
    draft = create_draft_record(
        user=users[who], subject="Not sent yet", instructions="Prepare the document",
        document_type=memo_type,
    )
    assert not draft.routing_steps.exists()
    client.force_login(users[who])

    response = client.get("/tracking/reports/")

    assert response.status_code == 200
    assert response.context["stale_receipts"] == 0


@pytest.mark.django_db
@pytest.mark.parametrize("who", ["med", "med_admin", "viewer", "admin"])
def test_only_superseded_receipts_remain_after_the_current_batch_is_received(
    client, users, offices, memo_type, who,
):
    record = create_draft_record(
        user=users["med"], subject="An earlier recipient never confirmed",
        instructions="Review and forward", document_type=memo_type,
    )
    route_record(record, [offices["SUP"], offices["HR"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    route_record(record, [offices["REC"]], user=users["sup"], action="FORWARD")
    create_draft_record(
        user=users["med"], subject="Another unsent draft", instructions="Prepare",
        document_type=memo_type,
    )
    client.force_login(users[who])

    waiting = client.get("/tracking/reports/")

    assert waiting.status_code == 200
    assert waiting.context["awaiting_receipt"] == 1
    assert waiting.context["stale_receipts"] == 0

    confirm_receipt(record, user=users["admin"])
    received = client.get("/tracking/reports/")

    assert received.status_code == 200
    assert received.context["awaiting_receipt"] == 0
    assert received.context["stale_receipts"] == 1

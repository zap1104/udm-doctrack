import pytest

from apps.tracking.models import RoutingStep, Status
from apps.tracking.services import complete_record, confirm_receipt, create_draft_record, route_record

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.parametrize(
        "office_files_url",
        ["/tracking/?owner=custody", "/search/?mode=tracking&owner=custody"],
        ids=["tracking", "search"],
    ),
]


def office_file_ids(client, url, user):
    client.force_verified_login(user)
    response = client.get(url)
    assert response.status_code == 200
    return [record.pk for record in response.context["page_obj"].object_list]


def test_office_files_include_each_confirmed_holder_in_the_current_batch(
    client, office_files_url, users, offices, memo_type
):
    record = create_draft_record(
        user=users["med"], subject="Parallel custody", instructions="For action.", document_type=memo_type,
    )
    assert office_file_ids(client, office_files_url, users["med"]) == [record.pk]

    route_record(record, [offices["SUP"], offices["HR"]], user=users["med"])
    assert office_file_ids(client, office_files_url, users["med"]) == [record.pk]
    assert office_file_ids(client, office_files_url, users["sup"]) == []

    confirm_receipt(record, user=users["sup"])
    assert office_file_ids(client, office_files_url, users["sup"]) == [record.pk]
    assert office_file_ids(client, office_files_url, users["hr"]) == []
    assert office_file_ids(client, office_files_url, users["med"]) == []

    confirm_receipt(record, user=users["hr"])
    assert office_file_ids(client, office_files_url, users["hr"]) == [record.pk]
    assert office_file_ids(client, office_files_url, users["sup"]) == [record.pk]

    route_record(record, [offices["MED"]], user=users["hr"], action=RoutingStep.Action.FORWARD)
    assert office_file_ids(client, office_files_url, users["sup"]) == []
    assert office_file_ids(client, office_files_url, users["hr"]) == [record.pk]
    assert office_file_ids(client, office_files_url, users["med"]) == []


def test_office_files_keep_single_recipient_records_pending_filing(
    client, office_files_url, users, offices, memo_type
):
    record = create_draft_record(
        user=users["med"], subject="Custody pending filing", instructions="For action.", document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    complete_record(record, user=users["sup"])
    record.refresh_from_db()

    assert record.status == Status.COMPLETED_PENDING_UPLOAD
    assert office_file_ids(client, office_files_url, users["sup"]) == [record.pk]
    assert office_file_ids(client, office_files_url, users["med"]) == []

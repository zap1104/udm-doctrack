"""Filing a completed record into Document Management is a privileged act.

The archive endpoint used to have no permission check at all — only the view
check every record page does — so anyone who could read a completed record
could push it into the repository under their own name, copying every
attachment and writing an ARCHIVED entry into the append-only history.
"""

from __future__ import annotations

import pytest

from apps.tracking.services import (
    complete_record,
    confirm_receipt,
    create_draft_record,
    grant_access,
    route_record,
)


@pytest.fixture
def completed_record(users, offices, memo_type):
    """MED raises it, SUP receives and completes it."""
    record = create_draft_record(
        user=users["med"], subject="Supply request for filing", instructions="For action.",
        document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    record.refresh_from_db()
    complete_record(record, user=users["sup"])
    record.refresh_from_db()
    return record


@pytest.mark.django_db
def test_read_only_viewer_cannot_archive(client, completed_record, users, offices):
    """A grant opens the record for reading. It must not open it for filing."""
    grant_access(completed_record, user=users["med"], office=offices["HR"], reason="fyi")
    assert completed_record.can_user_view(users["hr"]) is True
    assert completed_record.can_user_archive(users["hr"]) is False

    client.force_login(users["hr"])
    response = client.post(f"/tracking/{completed_record.pk}/archive/")

    assert response.status_code == 403
    completed_record.refresh_from_db()
    assert completed_record.is_archived is False


@pytest.mark.django_db
def test_the_office_that_completed_it_can_archive(client, completed_record, users):
    assert completed_record.can_user_archive(users["sup"]) is True

    client.force_login(users["sup"])
    response = client.post(f"/tracking/{completed_record.pk}/archive/")

    assert response.status_code == 302
    completed_record.refresh_from_db()
    assert completed_record.is_archived is True


@pytest.mark.django_db
def test_originating_office_can_archive(completed_record, users):
    assert completed_record.can_user_archive(users["med"]) is True


@pytest.mark.django_db
def test_records_staff_can_archive_anything(completed_record, users):
    assert completed_record.can_user_archive(users["admin"]) is True


@pytest.mark.django_db
def test_the_button_is_hidden_from_a_viewer_who_may_not_use_it(
    client, completed_record, users, offices
):
    """A 403 the user could have been spared is still a bug in the page."""
    grant_access(completed_record, user=users["med"], office=offices["HR"], reason="fyi")

    client.force_login(users["hr"])
    body = client.get(f"/tracking/{completed_record.pk}/").content.decode()
    assert "Archive into Document Management" not in body

    client.force_login(users["sup"])
    body = client.get(f"/tracking/{completed_record.pk}/").content.decode()
    assert "Archive into Document Management" in body

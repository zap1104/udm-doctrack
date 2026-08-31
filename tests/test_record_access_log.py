"""Who opened and who printed a document, inspectable.

Logging reads and prints is only half the requirement. A view-only account
leaves no other trace, so if the rows cannot be found and filtered they answer
nobody — a log that cannot be inspected does not satisfy the thing it was built
for. These rows are surfaced on the administration audit screen.
"""

from __future__ import annotations

import pytest
from django.conf import settings

from apps.tracking.models import RecordActivity
from apps.tracking.services import confirm_receipt, create_draft_record, route_record

AUDIT_URL = "/administration/audit-log/"


@pytest.fixture
def med_record(users, offices, memo_type):
    record = create_draft_record(
        user=users["med"], subject="Confidential MED memo", instructions="For action.",
        document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    record.refresh_from_db()
    return record


@pytest.fixture
def hr_record(users, offices, memo_type):
    record = create_draft_record(
        user=users["hr"], subject="Confidential HR memo", instructions="For action.",
        document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["hr"])
    record.refresh_from_db()
    return record


@pytest.mark.django_db
def test_opens_and_prints_are_listed_on_the_audit_screen(client, second_client, med_record, users):
    second_client.force_login(users["viewer"])
    second_client.get(med_record.get_absolute_url())
    second_client.get(f"/tracking/{med_record.pk}/slip/")

    client.force_login(users["admin"])
    body = client.get(AUDIT_URL).content.decode()

    assert "Record access" in body
    assert med_record.tracking_number in body
    assert "viewer" in body


@pytest.mark.django_db
def test_the_panel_can_be_filtered_by_record(client, second_client, med_record, hr_record, users):
    second_client.force_login(users["sup"])
    second_client.get(med_record.get_absolute_url())
    second_client.get(hr_record.get_absolute_url())

    client.force_login(users["admin"])
    # Asserted against the panel rather than the page: the audit panel above it
    # legitimately names both records, having logged their creation.
    entries = client.get(f"{AUDIT_URL}?record={med_record.tracking_number}").context[
        "access_entries"
    ]

    numbers = {entry.record.tracking_number for entry in entries}
    assert numbers == {med_record.tracking_number}


@pytest.mark.django_db
def test_the_panel_can_be_filtered_by_person(client, second_client, med_record, users):
    second_client.force_login(users["viewer"])
    second_client.get(med_record.get_absolute_url())
    second_client.force_login(users["sup"])
    second_client.get(med_record.get_absolute_url())

    client.force_login(users["admin"])
    entries = client.get(f"{AUDIT_URL}?who=viewer").context["access_entries"]

    actors = {entry.actor.username for entry in entries}
    assert actors == {"viewer"}


@pytest.mark.django_db
def test_prints_appear_every_time_and_opens_do_not(client, second_client, med_record, users):
    """The dedup rule, visible where somebody would actually check it."""
    second_client.force_login(users["sup"])
    for _ in range(3):
        second_client.get(med_record.get_absolute_url())
        second_client.get(f"/tracking/{med_record.pk}/slip/")

    client.force_login(users["admin"])
    entries = list(client.get(AUDIT_URL).context["access_entries"])

    views = [e for e in entries if e.event == RecordActivity.Event.VIEWED]
    prints = [e for e in entries if e.event == RecordActivity.Event.PRINTED]
    assert len(views) == 1, "repeat opens collapse inside the dedup window"
    assert len(prints) == 3, "every print is its own row"


@pytest.mark.django_db
def test_the_screen_says_the_open_count_is_deduplicated(client, users):
    """The number looks like a statistic and is not one, so the page says so."""
    client.force_login(users["admin"])
    body = client.get(AUDIT_URL).content.decode()

    assert str(settings.VIEW_LOG_DEDUP_MINUTES) in body
    assert "reading sessions rather than page loads" in body


# --- scoping ---------------------------------------------------------------
@pytest.mark.django_db
def test_an_office_admin_sees_only_their_own_offices_records(
    client, second_client, med_record, hr_record, users
):
    """Otherwise inspecting who read your office's documents hands you the
    reading history of every other office's documents too — the same hole the
    account screens had."""
    second_client.force_login(users["sup"])
    second_client.get(med_record.get_absolute_url())
    second_client.get(hr_record.get_absolute_url())

    client.force_login(users["med_admin"])
    entries = client.get(AUDIT_URL).context["access_entries"]

    numbers = {entry.record.tracking_number for entry in entries}
    assert med_record.tracking_number in numbers
    assert hr_record.tracking_number not in numbers


@pytest.mark.django_db
def test_an_office_admin_sees_only_their_own_staff_in_the_system_log(
    client, med_record, hr_record, users
):
    """Opening this screen to office administrators is what made the unscoped
    AuditLog queryset a leak — the same shape as the account-screen hole."""
    client.force_login(users["med_admin"])
    entries = client.get(AUDIT_URL).context["entries"]

    offices = {entry.actor.office_id for entry in entries if entry.actor}
    assert offices <= {users["med_admin"].office_id}


@pytest.mark.django_db
def test_a_system_admin_still_sees_the_whole_system_log(client, med_record, hr_record, users):
    client.force_login(users["admin"])
    entries = client.get(AUDIT_URL).context["entries"]

    offices = {entry.actor.office_id for entry in entries if entry.actor}
    assert len(offices) > 1


@pytest.mark.django_db
def test_an_office_sees_an_outsider_who_read_its_own_document(
    client, second_client, med_record, users, offices
):
    """The scope follows the record, not the reader — an outsider reading your
    document is the case the trail matters most for."""
    from apps.tracking.services import grant_access

    grant_access(med_record, user=users["med"], office=offices["HR"], reason="fyi")
    second_client.force_login(users["hr"])
    second_client.get(med_record.get_absolute_url())

    client.force_login(users["med_admin"])
    entries = client.get(AUDIT_URL).context["access_entries"]

    assert any(entry.actor.username == "hr" for entry in entries)


@pytest.mark.django_db
def test_a_viewer_cannot_open_the_audit_screen(client, users):
    client.force_login(users["viewer"])
    assert client.get(AUDIT_URL).status_code == 403

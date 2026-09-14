"""The completed-but-unapproved stage, and the way back out of it.

Completed records that were never filed used to fall between the modules:
Tracking listed active records only and Documents listed what had actually been
filed, so a record completed with "file it now" unticked appeared in neither.
Nothing linked to it; the only way back was a URL somebody had kept.

The fix then was a queue on the repository page. The fix now is that the record
never leaves Tracking in the first place — COMPLETED_PENDING_UPLOAD is an active
status, and approval is the act that moves the record to the repository. So the
queue has moved to the tracking page, and these tests moved with it.

Returning one to tracking is the other half: completing a record was a one-way
door, even though routing a completed record refuses with "Reopen it before
routing again" — advice for a reopen that had no way to happen.
"""

from __future__ import annotations

import pytest

from apps.documents.services import archive_tracking_record
from apps.tracking.models import Status, TrackingRecord
from apps.tracking.services import (
    active_for,
    complete_record,
    confirm_receipt,
    create_draft_record,
    grant_access,
    reopen_record,
    route_record,
)


@pytest.fixture
def completed_unfiled(users, offices, memo_type):
    """MED raises it, SUP receives and completes it — without filing it."""
    record = create_draft_record(
        user=users["med"], subject="Completed but never filed", instructions="For action.",
        document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    record.refresh_from_db()
    complete_record(record, user=users["sup"], note="Done.")
    record.refresh_from_db()
    return record


# --- the queue -------------------------------------------------------------
@pytest.mark.django_db
def test_the_record_stays_in_tracking_until_it_is_approved(completed_unfiled, users):
    """The inversion: it used to leave Tracking the moment it was completed, and
    so belonged to no module until somebody filed it."""
    from apps.documents.models import Document

    assert completed_unfiled.status == Status.COMPLETED_PENDING_UPLOAD
    assert completed_unfiled in active_for(users["sup"]), "still in Tracking"
    assert not Document.objects.filter(tracking_record=completed_unfiled).exists(), "not in the repository"
    assert completed_unfiled in TrackingRecord.objects.visible_to(users["sup"]).pending_filing()


@pytest.mark.django_db
def test_approving_it_takes_it_out_of_the_queue_and_out_of_tracking(completed_unfiled, users):
    archive_tracking_record(completed_unfiled, user=users["sup_admin"])
    completed_unfiled.refresh_from_db()

    assert completed_unfiled.status == Status.COMPLETED
    assert completed_unfiled not in TrackingRecord.objects.visible_to(users["sup"]).pending_filing()
    assert completed_unfiled not in active_for(users["sup"])


@pytest.mark.django_db
def test_an_active_record_is_never_in_the_queue(users, offices, memo_type):
    record = create_draft_record(
        user=users["med"], subject="Still moving", instructions="For action.",
        document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    record.refresh_from_db()

    assert record not in TrackingRecord.objects.visible_to(users["med"]).pending_filing()


@pytest.mark.django_db
def test_the_queue_appears_on_the_tracking_page(client, completed_unfiled, users):
    client.force_login(users["sup"])
    body = client.get("/tracking/").content.decode()

    assert "Completed - pending upload" in body
    assert completed_unfiled.tracking_number in body


@pytest.mark.django_db
def test_the_queue_is_no_longer_on_the_repository_page(client, completed_unfiled, users):
    """It moved. Leaving a copy behind would mean two places to file from, only
    one of which the status model now agrees with."""
    client.force_login(users["sup"])
    body = client.get("/documents/").content.decode()

    assert "Pending filing" not in body


@pytest.mark.django_db
def test_the_queue_respects_visibility(client, completed_unfiled, users):
    """HR had nothing to do with this record and must not see it queued."""
    client.force_login(users["hr"])
    body = client.get("/tracking/").content.decode()

    assert completed_unfiled.tracking_number not in body


@pytest.mark.django_db
def test_the_scope_link_shows_only_the_queue(client, completed_unfiled, users, offices, memo_type):
    """?scope=pending-upload is where "see them all" goes."""
    still_moving = create_draft_record(
        user=users["med"], subject="Not finished yet", instructions="For action.",
        document_type=memo_type,
    )
    route_record(still_moving, [offices["SUP"]], user=users["med"])

    client.force_login(users["sup"])
    body = client.get("/tracking/?scope=pending-upload").content.decode()

    assert completed_unfiled.tracking_number in body
    assert still_moving.tracking_number not in body


# --- returning to tracking -------------------------------------------------
@pytest.mark.django_db
def test_the_office_that_completed_it_can_return_it(completed_unfiled, users):
    assert completed_unfiled.can_user_reopen(users["sup"]) is True


@pytest.mark.django_db
def test_an_unconnected_office_cannot_return_it(completed_unfiled, users, offices):
    grant_access(completed_unfiled, user=users["med"], office=offices["HR"], reason="fyi")
    assert completed_unfiled.can_user_view(users["hr"]) is True
    assert completed_unfiled.can_user_reopen(users["hr"]) is False


@pytest.mark.django_db
def test_the_originating_office_cannot_pull_it_back(completed_unfiled, users):
    """MED raised it, but SUP did the work and finished it."""
    assert completed_unfiled.can_user_reopen(users["med"]) is False


@pytest.mark.django_db
def test_a_filed_record_can_no_longer_be_returned(completed_unfiled, users):
    archive_tracking_record(completed_unfiled, user=users["sup_admin"])
    completed_unfiled.refresh_from_db()

    assert completed_unfiled.can_user_reopen(users["sup"]) is False
    assert completed_unfiled.can_user_reopen(users["admin"]) is False


@pytest.mark.django_db
def test_returning_restores_the_real_state_and_keeps_the_history(completed_unfiled, users):
    before = completed_unfiled.activities.count()

    reopen_record(completed_unfiled, user=users["sup"], reason="Endorsement still missing")
    completed_unfiled.refresh_from_db()

    # SUP had confirmed receipt, so that is the state it goes back to.
    assert completed_unfiled.status == Status.RECEIVED
    assert completed_unfiled.completed_at is None
    assert completed_unfiled.is_archived is False
    # Back where it can be worked on, and nothing was erased on the way.
    assert completed_unfiled in active_for(users["sup"])
    assert completed_unfiled.activities.count() == before + 1
    assert completed_unfiled.completion_note == "Done."


@pytest.mark.django_db
def test_returning_needs_a_reason(client, completed_unfiled, users):
    client.force_login(users["sup"])
    client.post(f"/tracking/{completed_unfiled.pk}/reopen/", {"reason": ""})

    completed_unfiled.refresh_from_db()
    assert completed_unfiled.status == Status.COMPLETED_PENDING_UPLOAD, (
        "an empty reason must not reopen it"
    )


@pytest.mark.django_db
def test_returning_end_to_end(client, completed_unfiled, users):
    client.force_login(users["sup"])
    response = client.post(
        f"/tracking/{completed_unfiled.pk}/reopen/", {"reason": "Finished too early"}
    )

    assert response.status_code == 302
    completed_unfiled.refresh_from_db()
    assert completed_unfiled.status == Status.RECEIVED
    assert any(
        "Finished too early" in (a.detail or "") for a in completed_unfiled.activities.all()
    )


@pytest.mark.django_db
def test_a_returned_record_can_be_routed_again(completed_unfiled, users, offices):
    """The dead end this closes: a completed record refuses to be routed."""
    reopen_record(completed_unfiled, user=users["sup"], reason="Needs another office")
    completed_unfiled.refresh_from_db()

    route_record(completed_unfiled, [offices["HR"]], user=users["sup"], action="FORWARD")
    completed_unfiled.refresh_from_db()
    assert completed_unfiled.status == Status.PENDING_RECEIPT

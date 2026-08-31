"""Approving a completed record into the repository is a privileged act.

The endpoint used to have no permission check at all — only the view check every
record page does — so anyone who could read a completed record could push it
into the repository under their own name, copying every attachment and writing
an ARCHIVED entry into the append-only history.

It is narrower now than the fix that closed that hole. Approval is an
administrator's act: the point of the COMPLETED_PENDING_UPLOAD stage is that
somebody other than the office which declared the work done checks it before it
becomes a permanent repository record. The office that finished it can no longer
file its own work.
"""

from __future__ import annotations

import pytest

from apps.tracking.models import Status
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
def test_completing_does_not_file_it(completed_record):
    """Completion is a claim, not a filing. The two used to be one step."""
    assert completed_record.status == Status.COMPLETED_PENDING_UPLOAD
    assert completed_record.is_archived is False


@pytest.mark.django_db
def test_read_only_grant_does_not_allow_approval(client, completed_record, users, offices):
    """A grant opens the record for reading. It must not open it for filing."""
    grant_access(completed_record, user=users["med"], office=offices["HR"], reason="fyi")
    assert completed_record.can_user_view(users["hr"]) is True
    assert completed_record.can_user_approve_upload(users["hr"]) is False

    client.force_login(users["hr"])
    response = client.post(f"/tracking/{completed_record.pk}/archive/")

    assert response.status_code == 403
    completed_record.refresh_from_db()
    assert completed_record.is_archived is False


@pytest.mark.django_db
def test_an_ordinary_user_cannot_approve_the_work_their_office_finished(
    client, completed_record, users
):
    """Approval is an administrator's act. Not because self-approval is banned
    — an administrator may approve their own work, and that is recorded rather
    than refused — but because an ordinary user has no approval right at all."""
    assert completed_record.can_user_approve_upload(users["sup"]) is False

    client.force_login(users["sup"])
    response = client.post(f"/tracking/{completed_record.pk}/archive/")

    assert response.status_code == 403
    completed_record.refresh_from_db()
    assert completed_record.is_archived is False
    assert completed_record.status == Status.COMPLETED_PENDING_UPLOAD


@pytest.mark.django_db
def test_an_ordinary_user_in_the_originating_office_cannot_approve(completed_record, users):
    assert completed_record.can_user_approve_upload(users["med"]) is False


@pytest.mark.django_db
def test_the_office_administrator_can_approve_their_own_offices_document(
    client, completed_record, users
):
    """SUP holds it, so SUP's administrator is the one who signs it off."""
    assert completed_record.can_user_approve_upload(users["sup_admin"]) is True

    client.force_login(users["sup_admin"])
    response = client.post(f"/tracking/{completed_record.pk}/archive/")

    assert response.status_code == 302
    completed_record.refresh_from_db()
    assert completed_record.is_archived is True
    assert completed_record.status == Status.COMPLETED


@pytest.mark.django_db
def test_an_office_administrator_cannot_approve_another_offices_document(
    completed_record, users, offices
):
    """HR's administrator has no standing here even after being shown the record."""
    grant_access(completed_record, user=users["med"], office=offices["HR"], reason="fyi")
    hr_admin = users["hr"]
    hr_admin.role = hr_admin.Role.ADMIN
    hr_admin.save(update_fields=["role"])

    assert completed_record.can_user_view(hr_admin) is True
    assert completed_record.can_user_approve_upload(hr_admin) is False


@pytest.mark.django_db
def test_a_system_administrator_can_approve_anything(completed_record, users):
    assert completed_record.can_user_approve_upload(users["admin"]) is True


# --- self-approval is recorded, not refused --------------------------------
@pytest.mark.django_db
def test_an_administrator_may_approve_a_document_they_completed_themselves(
    users, offices, memo_type
):
    """A one- or two-person office has nobody else. Blocking this would
    deadlock the queue with no escalation path."""
    from apps.tracking.services import approve_upload

    admin = users["sup_admin"]
    record = create_draft_record(
        user=users["med"], subject="Small office, one person", instructions="For action.",
        document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=admin)
    record.refresh_from_db()
    complete_record(record, user=admin)
    record.refresh_from_db()

    assert record.can_user_approve_upload(admin) is True
    approve_upload(record, user=admin)
    record.refresh_from_db()

    assert record.status == Status.COMPLETED
    assert record.approved_by_id == admin.pk
    assert record.approved_at is not None


@pytest.mark.django_db
def test_a_self_approval_is_recorded_as_one_in_both_trails(users, offices, memo_type):
    """Allowed, but not written down as though somebody independent looked."""
    from apps.core.models import AuditLog
    from apps.tracking.services import approve_upload

    admin = users["sup_admin"]
    record = create_draft_record(
        user=users["med"], subject="Finished and filed by one person",
        instructions="For action.", document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=admin)
    record.refresh_from_db()
    complete_record(record, user=admin)
    record.refresh_from_db()
    approve_upload(record, user=admin)
    record.refresh_from_db()

    entry = record.activities.filter(event="ARCHIVED").first()
    assert "Self-approved" in entry.message

    audit = AuditLog.objects.filter(action=AuditLog.Action.ARCHIVE).first()
    assert "Self-approved" in audit.summary
    assert audit.extra["self_approved"] is True


@pytest.mark.django_db
def test_an_independent_approval_is_not_labelled_a_self_approval(completed_record, users):
    """SUP completed it; SUP's administrator is a different person."""
    from apps.core.models import AuditLog
    from apps.tracking.services import approve_upload

    approve_upload(completed_record, user=users["sup_admin"])
    completed_record.refresh_from_db()

    assert completed_record.approved_by_id == users["sup_admin"].pk
    assert completed_record.completed_by_id == users["sup"].pk

    entry = completed_record.activities.filter(event="ARCHIVED").first()
    assert "Self-approved" not in entry.message

    audit = AuditLog.objects.filter(action=AuditLog.Action.ARCHIVE).first()
    assert audit.extra["self_approved"] is False


@pytest.mark.django_db
def test_self_approvals_are_findable_without_reading_the_prose(users, offices, memo_type):
    """The point of storing approved_by: this has to be a query."""
    from django.db.models import F

    from apps.tracking.models import TrackingRecord
    from apps.tracking.services import approve_upload

    admin = users["sup_admin"]
    own = create_draft_record(
        user=users["med"], subject="Self", instructions="x", document_type=memo_type,
    )
    route_record(own, [offices["SUP"]], user=users["med"])
    confirm_receipt(own, user=admin)
    own.refresh_from_db()
    complete_record(own, user=admin)
    own.refresh_from_db()
    approve_upload(own, user=admin)

    other = create_draft_record(
        user=users["med"], subject="Checked", instructions="x", document_type=memo_type,
    )
    route_record(other, [offices["SUP"]], user=users["med"])
    confirm_receipt(other, user=users["sup"])
    other.refresh_from_db()
    complete_record(other, user=users["sup"])
    other.refresh_from_db()
    approve_upload(other, user=admin)

    self_approved = TrackingRecord.objects.filter(approved_by=F("completed_by"))
    assert own in self_approved
    assert other not in self_approved


@pytest.mark.django_db
def test_a_viewer_can_never_approve(completed_record, users):
    assert completed_record.can_user_approve_upload(users["viewer"]) is False


@pytest.mark.django_db
def test_approving_twice_does_not_file_it_twice(completed_record, users):
    """The second attempt must not write a second Document or a second entry."""
    from django.core.exceptions import PermissionDenied, ValidationError

    from apps.documents.models import Document
    from apps.tracking.services import approve_upload

    approve_upload(completed_record, user=users["sup_admin"])
    completed_record.refresh_from_db()

    # Refused as a permission now rather than as a state error: an approved
    # record is COMPLETED, and nobody may approve a COMPLETED record.
    with pytest.raises((PermissionDenied, ValidationError)):
        approve_upload(completed_record, user=users["sup_admin"])

    assert Document.objects.filter(tracking_record=completed_record).count() == 1
    assert completed_record.activities.filter(event="ARCHIVED").count() == 1


@pytest.mark.django_db
def test_the_button_is_hidden_from_a_reader_who_may_not_use_it(
    client, completed_record, users, offices
):
    """A 403 the user could have been spared is still a bug in the page."""
    grant_access(completed_record, user=users["med"], office=offices["HR"], reason="fyi")

    client.force_login(users["hr"])
    body = client.get(f"/tracking/{completed_record.pk}/").content.decode()
    assert "Approve into Document Repository" not in body

    client.force_login(users["sup_admin"])
    body = client.get(f"/tracking/{completed_record.pk}/").content.decode()
    assert "Approve into Document Repository" in body

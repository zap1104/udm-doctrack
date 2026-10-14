"""A failed filing must leave only the original tracking files in storage."""

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.documents.models import Document
from apps.documents.services import archive_tracking_record
from apps.tracking.models import Attachment, Status
from apps.tracking.services import complete_record, confirm_receipt, create_draft_record, route_record


@pytest.mark.django_db
def test_missing_later_attachment_does_not_leave_a_repository_copy(
    users, offices, settings, tmp_path,
):
    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_BACKGROUND_TASKS = False
    record = create_draft_record(user=users["med"], subject="Complete filing", instructions="Review")
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    complete_record(record, user=users["sup"])
    original = Attachment.objects.create(
        record=record,
        file=SimpleUploadedFile("readable.txt", b"Original tracking attachment contents."),
        original_name="readable.txt", uploaded_by=users["med"],
    )
    Attachment.objects.create(
        record=record, file="tracking/missing-secondary.txt",
        original_name="missing-secondary.txt", uploaded_by=users["med"],
    )
    stored_before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()}

    with pytest.raises(ValidationError, match="Filing stopped"):
        archive_tracking_record(record, user=users["sup_admin"])

    record.refresh_from_db()
    assert record.status == Status.COMPLETED_PENDING_UPLOAD
    assert not Document.objects.filter(tracking_record=record).exists()
    assert original.file.storage.exists(original.file.name)
    stored_after = {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()}
    assert stored_after == stored_before

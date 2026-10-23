"""A successful target mutation consumes saved files once, and only once."""
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection

from apps.core import upload_drafts
from apps.core.models import UploadDraft
from apps.core.upload_context import form_nonce_for, resolve_upload_context
from apps.documents.models import Document
from apps.tracking.models import Attachment, ExternalOffice, ExternalTrackingRecord, TrackingRecord
from apps.tracking.services import attach_files, create_draft_record, route_record

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def storage(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def finalize():
    assert hasattr(upload_drafts, "finalize_upload"), "Saved files need atomic workflow finalization"
    return upload_drafts.finalize_upload


def prepare(user, workflow="tracking-create", payloads=(b"Original bytes",), **kwargs):
    context = resolve_upload_context(user, workflow, **kwargs)
    draft = upload_drafts.open_draft(user=user, context=context, form_nonce=form_nonce_for(user, context))
    if payloads:
        upload_drafts.stage_files(draft.pk, [SimpleUploadedFile(f"file-{index}.txt", body, "text/plain")
            for index, body in enumerate(payloads)], user=user, expected_revision=draft.revision,
            client_upload_ids=[uuid4() for _ in payloads])
    draft.refresh_from_db()
    return draft, context


def finish(draft, context, user, data=None, **kwargs):
    return finalize()(draft.pk, user=user, context=context, cleaned_data=data or {
        "subject": "Recovered document", "instructions": "Please review"}, expected_revision=draft.revision, **kwargs)


def test_create_consumes_once_and_preserves_source_until_cleanup(users):
    finalize()
    draft, context = prepare(users["med"])
    staged = draft.files.get()
    result = finish(draft, context, users["med"])
    again = finish(draft, context, users["med"])
    assert result == again
    assert TrackingRecord.objects.count() == 1
    record = TrackingRecord.objects.get(pk=result.parent_id)
    attachment = record.attachments.get()
    assert result.parent_kind == "tracking" and result.redirect.endswith(f"/{record.pk}/review/")
    assert attachment.checksum == staged.checksum and attachment.file.name != staged.file.name
    with attachment.file.open("rb") as stream:
        assert stream.read() == b"Original bytes"
    assert staged.file.storage.exists(staged.file.name)
    draft.refresh_from_db()
    assert draft.state == "consumed" and draft.files.get().state == "consumed"
    with pytest.raises(ValidationError, match="already|different"):
        finish(draft, context, users["med"], {"subject": "Different submission", "instructions": "Please review"})


def test_later_permanent_storage_failure_rolls_back_target_not_saved_uploads(users):
    finalize()
    draft, context = prepare(users["med"], payloads=(b"One", b"Two"))
    original, calls = Attachment.save, []

    def fail_second(instance, *args, **kwargs):
        original(instance, *args, **kwargs)
        calls.append(instance.file.name)
        if len(calls) == 2:
            raise OSError("simulated storage/SQL boundary failure")

    with patch.object(Attachment, "save", fail_second), pytest.raises(OSError):
        finish(draft, context, users["med"])
    draft.refresh_from_db()
    assert draft.state == "open" and draft.files.filter(state="saved").count() == 2
    assert not TrackingRecord.objects.exists() and not Attachment.objects.exists()
    for staged in draft.files.all():
        assert staged.file.storage.exists(staged.file.name)
        assert all(not staged.file.storage.exists(key) for key in calls)


def test_staged_content_integrity_and_storage_identity_are_rechecked(users):
    finalize()
    draft, context = prepare(users["med"])
    staged = draft.files.get()
    with staged.file.storage.open(staged.file.name, "wb") as stream:
        stream.write(b"Changed outside the upload")
    with pytest.raises(ValidationError, match="changed|checksum"):
        finish(draft, context, users["med"])
    assert not TrackingRecord.objects.exists()


def test_no_file_draft_and_stale_current_version(users):
    finalize()
    draft, context = prepare(users["med"], payloads=())
    result = finish(draft, context, users["med"])
    record = TrackingRecord.objects.get(pk=result.parent_id)
    first = attach_files(record, [SimpleUploadedFile("original.txt", b"V1", "text/plain")], user=users["med"])[0]
    staged, version_context = prepare(users["med"], "tracking-version", payloads=(b"V2",),
        target_id=record.pk, group_id=first.file_group_id)
    with pytest.raises(ValidationError, match="changed|version"):
        finish(staged, version_context, users["med"], {"change_summary": "Signed copy"}, expected_version=2)
    assert record.attachments.count() == 1
    result = finish(staged, version_context, users["med"], {"change_summary": "Signed copy"}, expected_version=1)
    assert result.version_number == 2 and record.attachments.count() == 2


def test_dispatch_retry_returns_original_result_without_second_movement(users, offices):
    finalize()
    record = create_draft_record(user=users["med"], subject="Send request", instructions="Please review")
    draft, context = prepare(users["med"], "tracking-route", target_id=record.pk)
    data = {"offices": [offices["SUP"]], "instructions": "Please review", "action": "FORWARD", "due_at": None}
    result = finish(draft, context, users["med"], data)
    assert finish(draft, context, users["med"], data) == result
    assert record.routing_steps.count() == 1 and record.attachments.count() == 1


def test_permission_revocation_does_not_consume_saved_files(users, offices):
    finalize()
    record = create_draft_record(user=users["med"], subject="Custody change", instructions="Please review")
    draft, context = prepare(users["med"], "tracking-edit", target_id=record.pk)
    route_record(record, [offices["SUP"]], user=users["med"])
    with pytest.raises(PermissionDenied):
        finish(draft, context, users["med"])
    assert UploadDraft.objects.get(pk=draft.pk).state == "open"


def test_external_creation_uses_bound_office_and_separate_files(users, offices):
    finalize()
    outside = ExternalOffice.objects.create(name="External source", managing_office=offices["MED"])
    draft, context = prepare(users["med_admin"], "external-create")
    result = finish(draft, context, users["med_admin"], {"title": "External document", "from_external_office": outside})
    assert not TrackingRecord.objects.exists()
    record = ExternalTrackingRecord.objects.get(pk=result.parent_id)
    assert result.parent_kind == "external" and record.managing_office_id == offices["MED"].pk
    assert record.attachments.count() == 1


@pytest.mark.django_db(transaction=True)
def test_repository_extraction_starts_after_consumption_commit(users, settings):
    finalize()
    settings.ENABLE_BACKGROUND_TASKS = settings.ENABLE_SOURCE_UNIT_EXTRACTION = False
    draft, context = prepare(users["med"], "repository-upload")
    calls = []

    def extracted(document_id, **kwargs):
        assert not connection.in_atomic_block
        assert UploadDraft.objects.get(pk=draft.pk).state == "consumed"
        assert Document.objects.filter(pk=document_id).exists()
        assert kwargs["user_id"] == users["med"].pk
        calls.append(document_id)
        return {"status": "COMPLETE"}

    with patch("apps.documents.tasks.extract_document_task", side_effect=extracted):
        result = finish(draft, context, users["med"], {"source": "UPLOAD", "allow_external_ocr": False, "ocr_language": "auto"})
        assert calls == [result.parent_id]
        assert finish(draft, context, users["med"], {"source": "UPLOAD", "allow_external_ocr": False, "ocr_language": "auto"}) == result
        assert calls == [result.parent_id]
    assert Document.objects.count() == 1 and Document.objects.get().files.count() == 1


@pytest.mark.parametrize("background", [False, True])
def test_every_initial_supporting_file_defers_extraction_and_joins_the_job(users, settings, background):
    finalize()
    settings.ENABLE_BACKGROUND_TASKS = background
    settings.ENABLE_SOURCE_UNIT_EXTRACTION = False
    draft, context = prepare(users["med"], "repository-upload", payloads=(b"Primary", b"Supporting"))
    with patch("apps.documents.services.extract_document_text", side_effect=AssertionError("Provider ran before consumption committed")), \
         patch("apps.documents.services._enqueue_extraction") as queued, patch("apps.documents.services._extract_saved_upload"):
        result = finish(draft, context, users["med"], {"source": "UPLOAD", "allow_external_ocr": False})
    assert Document.objects.get(pk=result.parent_id).files.count() == 2
    queued.assert_called_once()
    assert queued.call_args.kwargs["file_ids"] is None, "The post-commit job must process every initial file"

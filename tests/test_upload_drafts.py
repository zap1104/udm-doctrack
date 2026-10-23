"""Saved uploads are private, bounded and independent of document creation."""
import importlib
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.core.models import Notification, UploadDraft
from apps.documents.models import Document
from apps.tracking.models import RecordActivity, TrackingNumberSequence, TrackingRecord

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def storage(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def api():
    assert importlib.util.find_spec("apps.core.upload_drafts"), "Uploads need a private staging service"
    return importlib.import_module("apps.core.upload_drafts")


def new_draft(user, workflow="tracking-create", **kwargs):
    from apps.core.upload_context import form_nonce_for, resolve_upload_context

    context = resolve_upload_context(user, workflow, **kwargs)
    return api().open_draft(user=user, context=context, form_nonce=form_nonce_for(user, context)), context


def upload(payload=b"saved bytes", name="proof.txt"):
    return SimpleUploadedFile(name, payload, "text/plain")


def stage(draft, user, files=None, ids=None):
    files = files if files is not None else [upload()]
    return api().stage_files(draft.pk, files, user=user, expected_revision=draft.revision,
                             client_upload_ids=ids or [uuid4() for _ in files])


def test_saved_upload_is_not_a_business_record(users):
    service = api()
    before = [model.objects.count() for model in (TrackingRecord, Document, RecordActivity, Notification, TrackingNumberSequence)]
    draft, context = new_draft(users["med"])
    result = stage(draft, users["med"])
    assert len(result.accepted_ids) == 1 and not result.errors
    saved = draft.files.get()
    with saved.file.open("rb") as stream:
        assert stream.read() == b"saved bytes"
    assert saved.original_name == "proof.txt" and saved.checksum and saved.storage_identity
    assert [model.objects.count() for model in (TrackingRecord, Document, RecordActivity, Notification, TrackingNumberSequence)] == before
    assert service.read_draft(draft.pk, user=users["med"], context=context).pk == draft.pk


def test_partial_valid_upload_survives_invalid_neighbor(users):
    api()
    draft, _ = new_draft(users["med"])
    result = stage(draft, users["med"], [upload(), upload(b"bad", "malware.exe")])
    assert len(result.accepted_ids) == len(result.errors) == 1
    assert draft.files.filter(state="saved").count() == 1
    assert "not accepted" in result.errors[0]["message"]


def test_provider_error_on_second_file_keeps_first_selection_recoverable(users, tmp_path):
    from botocore.exceptions import ClientError

    from apps.core.models import StagedUpload
    draft, _ = new_draft(users["med"])
    storage = StagedUpload._meta.get_field("file").storage
    original = storage.save
    calls = 0
    def failing_save(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "Simulated provider refusal"}}, "PutObject")
        return original(*args, **kwargs)
    with patch.object(storage, "save", failing_save):
        result = stage(draft, users["med"], [upload(name="keep.txt"), upload(name="retry.txt")])
    assert len(result.accepted_ids) == len(result.errors) == 1
    assert draft.files.get().original_name == "keep.txt"
    assert len([path for path in tmp_path.rglob("*") if path.is_file()]) == 1


def test_manifest_failure_after_staging_compensates_every_new_blob(users, tmp_path):
    draft, _ = new_draft(users["med"])
    with patch("apps.core.upload_drafts._touch", side_effect=RuntimeError("Manifest write failed")):
        with pytest.raises(RuntimeError, match="Manifest write failed"):
            stage(draft, users["med"], [upload(name="one.txt"), upload(name="two.txt")])
    assert not draft.files.exists()
    assert not [path for path in tmp_path.rglob("*") if path.is_file()]


def test_open_manifest_and_byte_limits(users, settings):
    service = api()
    settings.UPLOAD_DRAFT_MAX_OPEN = 1
    draft, _ = new_draft(users["med"])
    with pytest.raises(ValidationError, match="saved upload"):
        new_draft(users["med"])
    settings.UPLOAD_DRAFT_MAX_BYTES = 5
    result = stage(draft, users["med"], [upload(b"123456")])
    assert result.errors and not result.accepted_ids and not draft.files.exists()
    assert service.read_draft(draft.pk, user=users["med"], context=new_context(users["med"])).pk == draft.pk


def new_context(user):
    from apps.core.upload_context import resolve_upload_context
    return resolve_upload_context(user, "tracking-create")


def test_staged_and_new_files_share_the_submission_limit(users, settings):
    api()
    settings.MAX_UPLOAD_FILES = 1
    draft, _ = new_draft(users["med"])
    assert stage(draft, users["med"]).accepted_ids
    draft.refresh_from_db()
    assert stage(draft, users["med"], [upload(b"another")]).errors
    assert draft.files.filter(state="saved").count() == 1


def test_replayed_upload_does_not_duplicate_or_resurrect(users):
    service = api()
    draft, _ = new_draft(users["med"])
    identity = uuid4()
    first = stage(draft, users["med"], ids=[identity])
    # Network lost the response, so the client still has the starting revision.
    repeated = stage(draft, users["med"], ids=[identity])
    assert first.accepted_ids == repeated.accepted_ids and draft.files.count() == 1
    draft.refresh_from_db()
    service.discard_staged_file(draft.pk, first.accepted_ids[0], user=users["med"], expected_revision=draft.revision)
    draft.refresh_from_db()
    assert stage(draft, users["med"], ids=[identity]).errors
    assert not draft.files.filter(state="saved").exists()


def test_get_does_not_extend_expiry_but_successful_save_does(users):
    service = api()
    draft, context = new_draft(users["med"])
    old = timezone.now() + timedelta(minutes=1)
    UploadDraft.objects.filter(pk=draft.pk).update(expires_at=old)
    service.read_draft(draft.pk, user=users["med"], context=context)
    draft.refresh_from_db()
    assert draft.expires_at == old
    service.save_form_state(draft.pk, {"subject": ["Typed title"], "document_types": ["1", "2"]},
                            user=users["med"], expected_revision=draft.revision)
    draft.refresh_from_db()
    assert draft.expires_at > old and draft.form_state["document_types"] == ["1", "2"]


@pytest.mark.parametrize("role", ["sup", "med_admin", "admin"])
def test_another_user_including_administrators_cannot_read_stage(users, role):
    service = api()
    draft, context = new_draft(users["med"])
    stage(draft, users["med"])
    with pytest.raises(PermissionDenied):
        service.read_draft(draft.pk, user=users[role], context=context)


@pytest.mark.parametrize("change", ["suspended", "transferred", "viewer"])
def test_current_account_authority_is_required(users, offices, change):
    service = api()
    draft, context = new_draft(users["med"])
    changes = {"suspended": {"is_active": False}, "transferred": {"office_id": offices["SUP"].pk}, "viewer": {"role": "VIEWER"}}
    type(users["med"]).objects.filter(pk=users["med"].pk).update(**changes[change])
    with pytest.raises(PermissionDenied):
        service.read_draft(draft.pk, user=users["med"], context=context)


def test_tabs_and_signed_form_nonce_are_independent_and_idempotent(users):
    service = api()
    from apps.core.upload_context import form_nonce_for
    context = new_context(users["med"])
    nonce = form_nonce_for(users["med"], context)
    first = service.open_draft(user=users["med"], context=context, form_nonce=nonce)
    assert service.open_draft(user=users["med"], context=context, form_nonce=nonce).pk == first.pk
    assert new_draft(users["med"])[0].pk != first.pk
    with pytest.raises(PermissionDenied):
        service.open_draft(user=users["sup"], context=context, form_nonce=nonce)


def test_stale_revision_and_expired_draft_cannot_write(users):
    api()
    draft, _ = new_draft(users["med"])
    stage(draft, users["med"])
    with pytest.raises(ValidationError, match="changed"):
        stage(draft, users["med"], [upload(b"other")])
    UploadDraft.objects.filter(pk=draft.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
    draft.refresh_from_db()
    with pytest.raises(ValidationError, match="expired"):
        stage(draft, users["med"])

"""Real PostgreSQL races around saved-upload claims and version finalization."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections

from apps.core import upload_drafts
from apps.core.models import UploadDraft
from apps.core.upload_context import form_nonce_for, resolve_upload_context
from apps.tracking.models import TrackingRecord
from apps.tracking.services import attach_files, confirm_receipt, create_draft_record, route_record

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def storage(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def finalize():
    assert hasattr(upload_drafts, "finalize_upload"), "Saved files need atomic workflow finalization"
    return upload_drafts.finalize_upload


def stage(user, *, workflow="tracking-create", body=b"Saved file", **kwargs):
    context = resolve_upload_context(user, workflow, **kwargs)
    draft = upload_drafts.open_draft(user=user, context=context, form_nonce=form_nonce_for(user, context))
    upload_drafts.stage_files(draft.pk, [SimpleUploadedFile("file.txt", body, "text/plain")], user=user,
        expected_revision=0, client_upload_ids=[uuid4()])
    draft.refresh_from_db()
    return draft, context


def parallel(left, right):
    gate = Barrier(3)

    def run(function):
        close_old_connections()
        try:
            gate.wait(timeout=20)
            try:
                return function()
            except (ValidationError, PermissionDenied) as exc:
                return exc
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(run, left), pool.submit(run, right)
        gate.wait(timeout=20)
        return a.result(timeout=40), b.result(timeout=40)


def test_two_consumers_create_one_record_and_return_same_receipt(users):
    complete = finalize()
    draft, context = stage(users["med"])
    def consume():
        return complete(draft.pk, user=users["med"], context=context,
            cleaned_data={"subject": "One final record", "instructions": "Review carefully"}, expected_revision=draft.revision)
    left, right = parallel(consume, consume)
    assert left == right and not isinstance(left, Exception)
    assert TrackingRecord.objects.count() == 1 and TrackingRecord.objects.get().attachments.count() == 1


def test_two_saved_versions_from_same_start_have_one_winner(users, offices):
    complete = finalize()
    record = create_draft_record(user=users["sup"], subject="Version race", instructions="Review")
    route_record(record, [offices["MED"]], user=users["sup"])
    confirm_receipt(record, user=users["med"])
    first = attach_files(record, [SimpleUploadedFile("file.txt", b"V1", "text/plain")], user=users["med"])[0]
    starts = [stage(users[role], workflow="tracking-version", body=body, target_id=record.pk, group_id=first.file_group_id)
              for role, body in [("med", b"V2 first candidate"), ("med_admin", b"V2 other candidate")]]
    def consume(index, role):
        draft, context = starts[index]
        return complete(draft.pk, user=users[role], context=context, cleaned_data={"change_summary": "Signed copy"},
            expected_revision=draft.revision, expected_version=1)
    results = parallel(lambda: consume(0, "med"), lambda: consume(1, "med_admin"))
    assert sum(isinstance(result, ValidationError) for result in results) == 1
    assert list(record.attachments.order_by("version_number").values_list("version_number", flat=True)) == [1, 2]
    assert UploadDraft.objects.filter(state="consumed").count() == UploadDraft.objects.filter(state="open").count() == 1


def test_remove_racing_consume_cannot_lose_a_committed_file(users):
    complete = finalize()
    draft, context = stage(users["med"])
    source = draft.files.get()
    results = parallel(lambda: complete(draft.pk, user=users["med"], context=context,
        cleaned_data={"subject": "Removal race", "instructions": "Please review"}, expected_revision=draft.revision),
        lambda: upload_drafts.discard_staged_file(draft.pk, source.pk, user=users["med"], expected_revision=draft.revision))
    assert sum(isinstance(result, (ValidationError, PermissionDenied)) for result in results) == 1
    source.refresh_from_db()
    if TrackingRecord.objects.exists():
        saved = TrackingRecord.objects.get().attachments.get()
        with saved.file.open("rb") as stream:
            assert stream.read() == b"Saved file"
        assert source.state == "consumed"
    else:
        assert source.state == "removed" and UploadDraft.objects.get(pk=draft.pk).state == "open"


def test_simultaneous_new_tabs_cannot_exceed_manifest_quota(users, settings):
    finalize()
    settings.UPLOAD_DRAFT_MAX_OPEN = 1
    context = resolve_upload_context(users["med"], "tracking-create")
    nonces = [form_nonce_for(users["med"], context) for _ in range(2)]
    results = parallel(lambda: upload_drafts.open_draft(user=users["med"], context=context, form_nonce=nonces[0]),
        lambda: upload_drafts.open_draft(user=users["med"], context=context, form_nonce=nonces[1]))
    assert sum(isinstance(result, ValidationError) for result in results) == 1
    assert UploadDraft.objects.count() == 1

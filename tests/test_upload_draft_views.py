"""Ordinary multipart forms and signed previews must recover saved uploads."""
from uuid import uuid4

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.core.models import UploadDraft
from apps.core.upload_context import form_nonce_for, resolve_upload_context
from apps.core.utils import sign_download
from apps.documents.models import Document
from apps.tracking.models import ExternalOffice, ExternalTrackingRecord, TrackingRecord

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def storage(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def file(payload=b"Keep this upload", name="proof.txt"):
    return SimpleUploadedFile(name, payload, "text/plain")


def recovery_fields(response):
    form = response.context["form"]
    assert hasattr(form, "recovery"), "Upload forms need recoverable saved-file context"
    state = form.recovery
    return {"upload_form_nonce": state["nonce"], "upload_batch_id": state["batch_id"],
            "upload_draft": str(state.get("draft_id") or ""), "upload_revision": state.get("revision", 0)}


def creation_data(offices):
    return {"subject": "", "instructions": "Please review this document", "receiving_offices": [offices["SUP"].pk],
            "classification": "INTERNAL", "priority": "NORMAL", "deadline_choice": "none"}


def test_invalid_create_post_refresh_and_correction_keep_saved_file(client, users, offices):
    client.force_login(users["med"])
    initial = client.get("/tracking/new/")
    data = {**creation_data(offices), **recovery_fields(initial), "attachments": file()}
    invalid = client.post("/tracking/new/", data)
    assert invalid.status_code == 302 and "upload_draft=" in invalid.url
    assert not TrackingRecord.objects.exists()
    draft = UploadDraft.objects.get(owner=users["med"])
    assert draft.files.get().original_name == "proof.txt"
    restored = client.get(invalid.url)
    assert restored.status_code == 200 and b"proof.txt" in restored.content
    assert restored.context["form"].errors["subject"]
    again = client.get(invalid.url)
    assert UploadDraft.objects.count() == 1 and draft.files.count() == 1
    fixed = client.post(invalid.url, {**creation_data(offices), "subject": "Recovered document", **recovery_fields(again)})
    assert fixed.status_code == 302 and fixed.url.endswith("/review/")
    assert TrackingRecord.objects.get().attachments.get().checksum == draft.files.get().checksum


def test_invalid_repository_office_keeps_file_and_missing_checkbox_stays_unchecked(client, users, offices):
    client.force_login(users["med"])
    initial = client.get("/documents/upload/")
    data = {**recovery_fields(initial), "office": "invalid", "source": "UPLOAD", "ocr_language": "auto",
            "allow_external_ocr": "on", "file": file()}
    invalid = client.post("/documents/upload/", data)
    assert invalid.status_code == 302 and "upload_draft=" in invalid.url
    assert not Document.objects.exists() and UploadDraft.objects.get().files.count() == 1
    restored = client.get(invalid.url)
    assert restored.context["form"].errors["office"]
    fixed = client.post(invalid.url, {**recovery_fields(restored), "office": offices["MED"].pk,
                                   "source": "UPLOAD", "ocr_language": "auto"})
    assert fixed.status_code == 302 and fixed.url.endswith("/review/")
    assert not Document.objects.get().allow_external_ocr


def test_partial_invalid_native_batch_retains_valid_neighbor(client, users, offices):
    client.force_login(users["med"])
    initial = client.get("/tracking/new/")
    invalid = client.post("/tracking/new/", {**creation_data(offices), **recovery_fields(initial),
        "attachments": [file(), file(b"bad", "invalid.exe")]})
    assert invalid.status_code == 302
    draft = UploadDraft.objects.get()
    assert draft.files.get().original_name == "proof.txt"
    restored = client.get(invalid.url)
    assert b"not accepted" in restored.content and b"proof.txt" in restored.content


def test_external_creation_recovers_attachments_without_internal_data(client, users, offices):
    outside = ExternalOffice.objects.create(name="Outside office", managing_office=offices["MED"])
    client.force_login(users["med_admin"])
    initial = client.get("/tracking/external/new/")
    data = {**recovery_fields(initial), "title": "", "from_endpoint": f"external:{outside.pk}",
            "to_endpoint": f"internal:{offices['MED'].pk}", "attachments": file()}
    invalid = client.post("/tracking/external/new/", data)
    assert invalid.status_code == 302 and UploadDraft.objects.get().collection == "EXTERNAL"
    restored = client.get(invalid.url)
    fixed = client.post(invalid.url, {**recovery_fields(restored), "title": "External recovered document",
        "from_endpoint": f"external:{outside.pk}", "to_endpoint": f"internal:{offices['MED'].pk}"})
    assert fixed.status_code == 302 and ExternalTrackingRecord.objects.get().attachments.count() == 1
    assert not TrackingRecord.objects.exists() and not Document.objects.exists()


def test_staging_api_and_preview_tokens_remain_private(client, users):
    client.force_login(users["med"])
    context = resolve_upload_context(users["med"], "tracking-create")
    response = client.post("/uploads/drafts/", {"workflow": "tracking-create", "form_nonce": form_nonce_for(users["med"], context)})
    assert response.status_code == 201
    draft_id = response.json()["id"]
    saved = client.post(f"/uploads/drafts/{draft_id}/files/", {"file": file(), "expected_revision": 0,
        "client_upload_id": str(uuid4())})
    assert saved.status_code == 200 and len(saved.json()["accepted_ids"]) == 1
    saved_id = saved.json()["accepted_ids"][0]
    token = sign_download(f"file-version:preview:upload:{saved_id}:{users['med'].pk}")
    url = f"/files/preview/upload/{saved_id}/?token={token}"
    preview = client.get(url)
    assert preview.status_code == 200 and b"Keep this upload" in preview.content
    assert preview["Cache-Control"] == "private, no-store" and preview["X-Content-Type-Options"] == "nosniff"
    assert client.get(f"/files/download/upload/{saved_id}/?token={token}").status_code == 403
    client.force_login(users["admin"])
    assert client.get(url).status_code in {403, 404}
    client.force_login(users["med"])
    removed = client.post(f"/uploads/drafts/{draft_id}/files/{saved_id}/remove/", {"expected_revision": saved.json()["revision"]})
    assert removed.status_code == 200 and client.get(url).status_code == 410


def test_staging_api_refuses_csrf_and_repeated_scope(client, users):
    from tests.conftest import HttpsClient
    protected = HttpsClient(enforce_csrf_checks=True)
    protected.force_login(users["med"])
    assert protected.post("/uploads/drafts/", {"workflow": "tracking-create"}).status_code == 403
    client.force_login(users["admin"])
    response = client.post("/uploads/drafts/?office=1&office=2", {"workflow": "repository-upload"})
    assert response.status_code == 400 and not UploadDraft.objects.exists()


def test_duplicate_first_native_submission_returns_one_committed_result(client, users, offices):
    client.force_login(users["med"])
    initial = client.get("/tracking/new/")
    data = {**creation_data(offices), "subject": "One native submission", **recovery_fields(initial)}
    first = client.post("/tracking/new/", {**data, "attachments": file()})
    assert first.status_code == 302
    repeated = client.post("/tracking/new/", {**data, "attachments": file()})
    assert repeated.status_code == 302 and repeated.url == first.url
    assert TrackingRecord.objects.count() == UploadDraft.objects.count() == 1
    assert TrackingRecord.objects.get().attachments.count() == 1
    changed = client.post("/tracking/new/", {**data, "attachments": file(b"Different bytes")})
    assert changed.status_code in {400, 409} and TrackingRecord.objects.count() == 1


def test_system_admin_selected_filing_office_survives_invalid_fields(client, users, offices):
    client.force_login(users["admin"])
    initial = client.get("/documents/upload/")
    invalid = client.post("/documents/upload/", {**recovery_fields(initial), "file": file(), "office": offices["MED"].pk,
        "source": "not-a-source", "ocr_language": "auto"})
    assert invalid.status_code == 302 and "office=" in invalid.url
    restored = client.get(invalid.url)
    assert int(restored.context["form"]["office"].value()) == offices["MED"].pk
    fixed = client.post(invalid.url, {**recovery_fields(restored), "office": offices["MED"].pk,
        "source": "UPLOAD", "ocr_language": "auto"})
    assert fixed.status_code == 302 and Document.objects.get().office_id == offices["MED"].pk


def test_invalid_date_and_repeated_types_survive_refresh(client, users, offices, memo_type):
    from apps.core.models import DocumentType
    letter = DocumentType.objects.create(name="Letter", code="LETTER")
    client.force_login(users["med"])
    initial = client.get("/tracking/new/")
    invalid = client.post("/tracking/new/", {**creation_data(offices), **recovery_fields(initial), "subject": "Date correction",
        "deadline_choice": "date", "due_date": "broken", "attachments": file(),
        "document_types": [memo_type.pk, letter.pk], "document_type": memo_type.pk})
    assert invalid.status_code == 302
    restored = client.get(invalid.url)
    assert restored.context["form"].errors["due_date"]
    assert {int(value) for value in restored.context["form"]["document_types"].value()} == {memo_type.pk, letter.pk}
    assert UploadDraft.objects.get().files.count() == 1 and not TrackingRecord.objects.exists()


@pytest.mark.parametrize("action", ["remark", "route"])
def test_independent_record_action_forms_keep_their_own_saved_inputs(client, users, offices, action):
    from apps.tracking.services import confirm_receipt, create_draft_record, route_record
    record = create_draft_record(user=users["sup"], subject="Action recovery", instructions="Review carefully")
    route_record(record, [offices["MED"]], user=users["sup"])
    confirm_receipt(record, user=users["med"])
    client.force_login(users["med"])
    detail = client.get(record.get_absolute_url())
    form = detail.context[f"{action}_form"]
    state = form.recovery
    data = {"upload_form_nonce": state["nonce"], "upload_batch_id": state["batch_id"],
            "upload_revision": state["revision"], "attachments": file()}
    data.update({"remark": ""} if action == "remark" else {"offices": [999999], "instructions": "Recover me", "deadline_choice": "none", "action": "SEND"})
    invalid = client.post(f"/tracking/{record.pk}/{action}/", data)
    assert invalid.status_code == 302
    restored = client.get(invalid.url)
    bound = restored.context[f"{action}_form"]
    other = restored.context["route_form" if action == "remark" else "remark_form"]
    assert bound.is_bound and bound.errors and bound.recovery["files"]
    assert not other.is_bound and not record.attachments.exists()
    if action == "route":
        assert b'class="tab-pane fade show active" id="tab-route"' in restored.content, "Recovered errors must be visible without JavaScript"


def test_version_upload_form_keeps_file_after_missing_change_summary(client, users):
    from apps.tracking.services import attach_files, create_draft_record
    record = create_draft_record(user=users["med"], subject="Version recovery", instructions="Review carefully")
    first = attach_files(record, [file(b"V1")], user=users["med"])[0]
    client.force_login(users["med"])
    url = f"/tracking/{record.pk}/files/{first.file_group_id}/versions/"
    initial = client.get(url)
    invalid = client.post(url, {**recovery_fields(initial), f"version-{first.file_group_id}-file": file(b"V2"),
        f"version-{first.file_group_id}-expected_version": 1, f"version-{first.file_group_id}-change_summary": ""})
    assert invalid.status_code == 302
    restored = client.get(invalid.url)
    assert restored.context["form"].errors["change_summary"] and b"proof.txt" in restored.content
    assert record.attachments.count() == 1


def test_changed_staged_storage_identity_does_not_serve_another_backend(client, users):
    from apps.core.models import StagedUpload
    from apps.core.upload_drafts import open_draft, stage_files
    context = resolve_upload_context(users["med"], "tracking-create")
    draft = open_draft(user=users["med"], context=context, form_nonce=form_nonce_for(users["med"], context))
    stage_files(draft.pk, [file()], user=users["med"], expected_revision=0, client_upload_ids=[uuid4()])
    staged = draft.files.get()
    StagedUpload.objects.filter(pk=staged.pk).update(storage_identity="f" * 64)
    client.force_login(users["med"])
    token = sign_download(f"file-version:preview:upload:{staged.pk}:{users['med'].pk}")
    response = client.get(f"/files/preview/upload/{staged.pk}/?token={token}")
    assert response.status_code == 410 and b"Keep this upload" not in response.content


def test_system_admin_all_offices_perspective_does_not_block_target_uploads(client, users, offices):
    from apps.tracking.services import confirm_receipt, create_draft_record, route_record
    record = create_draft_record(user=users["sup"], subject="All office perspective", instructions="Review carefully")
    route_record(record, [offices["MED"]], user=users["sup"])
    confirm_receipt(record, user=users["med"])
    context = resolve_upload_context(users["admin"], "tracking-route", target_id=record.pk)
    client.force_login(users["admin"])
    response = client.post("/uploads/drafts/?office=all", {"workflow": "tracking-route", "target_id": record.pk,
        "form_nonce": form_nonce_for(users["admin"], context)})
    assert response.status_code == 201
    assert UploadDraft.objects.get().owner_office_id_snapshot == users["admin"].office_id


def test_external_repository_upload_recovers_a_file_without_tracking_side_effects(client, users, offices):
    own = ExternalOffice.objects.create(name="Own external counterpart", managing_office=offices["MED"])
    foreign = ExternalOffice.objects.create(name="Another counterpart", managing_office=offices["SUP"])
    client.force_login(users["med_admin"])
    url = "/documents/external/upload/"
    initial = client.get(url)
    invalid = client.post(url, {**recovery_fields(initial), "file": file(), "office": offices["MED"].pk,
        "source": "UPLOAD", "ocr_language": "auto", "external_offices": [foreign.pk]})
    assert invalid.status_code == 302 and not Document.objects.exists()
    restored = client.get(invalid.url)
    assert restored.context["form"].errors["external_offices"]
    fixed = client.post(invalid.url, {**recovery_fields(restored), "office": offices["MED"].pk,
        "source": "UPLOAD", "ocr_language": "auto", "external_offices": [own.pk]})
    assert fixed.status_code == 302
    document = Document.objects.get()
    assert document.collection == "EXTERNAL" and document.files.count() == 1
    assert not TrackingRecord.objects.exists() and not ExternalTrackingRecord.objects.exists()


def test_forged_recovery_id_or_revoked_account_cannot_expose_saved_bytes(client, users, offices):
    from apps.core.upload_drafts import open_draft, stage_files
    context = resolve_upload_context(users["med"], "tracking-create")
    draft = open_draft(user=users["med"], context=context, form_nonce=form_nonce_for(users["med"], context))
    stage_files(draft.pk, [file()], user=users["med"], expected_revision=0, client_upload_ids=[uuid4()])
    client.force_login(users["med_admin"])
    assert client.get(f"/tracking/new/?upload_draft={draft.pk}").status_code == 403
    client.force_login(users["med"])
    assert client.get(f"/tracking/new/?upload_draft={draft.pk}&upload_draft={uuid4()}").status_code == 400
    type(users["med"]).objects.filter(pk=users["med"].pk).update(office=offices["SUP"])
    assert client.get(f"/tracking/new/?upload_draft={draft.pk}").status_code == 403
    assert draft.files.count() == 1 and not TrackingRecord.objects.exists()

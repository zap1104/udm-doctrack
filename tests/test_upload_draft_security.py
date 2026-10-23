"""Target authority and partial staging failures must not broaden scope."""
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import QueryDict

from apps.core.models import StagedUpload
from apps.core.upload_context import form_nonce_for, resolve_upload_context
from apps.core.upload_drafts import open_draft, read_draft, stage_files
from apps.tracking.models import ExternalOffice
from apps.tracking.services import attach_files, create_draft_record, route_record

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def storage(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def uploaded(payload=b"file contents"):
    return SimpleUploadedFile("proof.txt", payload, "text/plain")


def manifest(user, workflow="tracking-create", **kwargs):
    context = resolve_upload_context(user, workflow, **kwargs)
    return open_draft(user=user, context=context, form_nonce=form_nonce_for(user, context)), context


def test_storage_failure_preserves_saved_neighbor_and_returns_retry_error(users):
    draft, _ = manifest(users["med"])
    storage = StagedUpload._meta.get_field("file").storage
    original, calls = storage.save, []

    def second_fails(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("simulated storage failure")
        return original(*args, **kwargs)

    with patch.object(storage, "save", side_effect=second_fails):
        result = stage_files(draft.pk, [uploaded(), uploaded(b"other contents")], user=users["med"],
            expected_revision=0, client_upload_ids=[uuid4(), uuid4()])
    assert len(result.accepted_ids) == 1 and len(result.errors) == 1
    assert draft.files.filter(state="saved").count() == 1
    with draft.files.get().file.open("rb") as stream:
        assert stream.read() == b"file contents"


@pytest.mark.parametrize("query", ["office=1&office=2", "office=", "office=-1", "office=not-an-office"])
def test_invalid_or_repeated_office_context_is_refused(users, query):
    with pytest.raises((PermissionDenied, ValidationError)):
        resolve_upload_context(users["admin"], "repository-upload", query=QueryDict(query))


def test_staff_cannot_select_another_office_or_inactive_context(users, offices):
    with pytest.raises(PermissionDenied):
        resolve_upload_context(users["med"], "repository-upload", query=QueryDict(f"office={offices['SUP'].pk}"))
    offices["SUP"].is_active = False
    offices["SUP"].save()
    with pytest.raises(PermissionDenied):
        resolve_upload_context(users["admin"], "repository-upload", query=QueryDict(f"office={offices['SUP'].pk}"))


def test_selected_admin_context_does_not_impersonate_internal_custodian(users, offices):
    record = create_draft_record(user=users["med"], subject="Waiting", instructions="Review")
    route_record(record, [offices["SUP"]], user=users["med"])
    with pytest.raises(PermissionDenied):
        resolve_upload_context(users["admin"], "tracking-version", target_id=record.pk, group_id=1,
            query=QueryDict(f"office={offices['SUP'].pk}"))


def test_target_routed_while_upload_form_is_open_loses_edit_permission(users, offices):
    record = create_draft_record(user=users["med"], subject="Changed", instructions="Review")
    draft, context = manifest(users["med"], "tracking-edit", target_id=record.pk)
    route_record(record, [offices["SUP"]], user=users["med"])
    with pytest.raises(PermissionDenied):
        read_draft(draft.pk, user=users["med"], context=context)


def test_foreign_group_and_version_file_limit(users):
    record = create_draft_record(user=users["med"], subject="Version", instructions="Review")
    other = create_draft_record(user=users["med"], subject="Other", instructions="Review")
    attached = attach_files(record, [uploaded()], user=users["med"])[0]
    with pytest.raises(PermissionDenied):
        resolve_upload_context(users["med"], "tracking-version", target_id=other.pk, group_id=attached.file_group_id)
    draft, _ = manifest(users["med"], "tracking-version", target_id=record.pk, group_id=attached.file_group_id)
    result = stage_files(draft.pk, [uploaded(b"revision"), uploaded(b"extra")], user=users["med"],
        expected_revision=0, client_upload_ids=[uuid4(), uuid4()])
    assert len(result.accepted_ids) == len(result.errors) == 1


def test_external_collection_has_its_own_permissions_and_binding(users, offices):
    from apps.tracking.external_services import create_record
    outside = ExternalOffice.objects.create(name="External Office", managing_office=offices["MED"])
    record = create_record(user=users["med_admin"], title="External case", from_external_office=outside)
    with pytest.raises(PermissionDenied):
        manifest(users["med"], "external-create")
    draft, context = manifest(users["med_admin"], "external-attachment", target_id=record.pk)
    assert draft.collection == "EXTERNAL" and draft.external_record_id == record.pk
    assert draft.tracking_record_id is None
    with pytest.raises(PermissionDenied):
        read_draft(draft.pk, user=users["med_admin"], context=resolve_upload_context(users["med_admin"], "tracking-create"))
    assert read_draft(draft.pk, user=users["med_admin"], context=context).pk == draft.pk

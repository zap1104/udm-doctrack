"""External filing follows custody while repository ownership stays fixed."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Office
from apps.core.models import Notification
from apps.documents.external_forms import ExternalUploadForm
from apps.documents.external_services import archive_external_record
from apps.documents.models import Document
from apps.documents.services import ingest_upload, save_document_metadata
from apps.tracking.models import ExternalOffice, ExternalRoutingStep, ExternalTrackingRecord, Status

pytestmark = pytest.mark.django_db


@pytest.fixture
def counterpart(offices):
    return ExternalOffice.objects.create(name="Shared College", code="SC", managing_office=offices["SUP"])


@pytest.fixture
def upload_settings(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_BACKGROUND_TASKS = True


def upload_data(counterpart, **extra):
    return {"source": "UPLOAD", "ocr_language": "auto", "external_offices": [counterpart.pk], **extra}


def test_upload_uses_active_account_office_without_owner_input(users, offices, counterpart):
    form = ExternalUploadForm(upload_data(counterpart, office=offices["SUP"].pk),
        {"file": SimpleUploadedFile("proof.txt", b"Proof")}, user=users["med_admin"])
    assert form.is_valid(), form.errors
    assert "office" not in form.fields
    assert form.cleaned_data["office"] == offices["MED"]


def test_upload_form_allows_shared_directory(users, counterpart):
    form = ExternalUploadForm(user=users["med_admin"])
    assert counterpart in form.fields["external_offices"].queryset


def test_upload_form_accepts_explicit_system_context(users, offices, counterpart):
    form = ExternalUploadForm(upload_data(counterpart), {"file": SimpleUploadedFile("proof.txt", b"Proof")},
        user=users["admin"], recording_office=offices["SUP"])
    assert form.is_valid(), form.errors
    assert form.cleaned_data["office"] == offices["SUP"]


def test_shared_directory_ingest_preserves_own_filing_office(users, offices, counterpart, upload_settings):
    document, _ = ingest_upload(user=users["med_admin"], uploaded_file=SimpleUploadedFile("proof.txt", b"Proof"),
        collection="EXTERNAL", external_offices=[counterpart])
    assert document.office == offices["MED"]
    assert document.external_office_links.get().external_office == counterpart


def test_ingest_rechecks_stale_inactive_office(users, offices, counterpart, upload_settings):
    Office.objects.filter(pk=offices["MED"].pk).update(is_active=False)
    with pytest.raises(ValidationError):
        ingest_upload(user=users["med_admin"], office=offices["MED"], uploaded_file=SimpleUploadedFile("proof.txt", b"Proof"),
            collection="EXTERNAL", external_offices=[counterpart])
    assert not Document.objects.exists()


@pytest.fixture
def completed_case(users, offices, counterpart):
    record = ExternalTrackingRecord.objects.create(tracking_number="EXT-CUSTODY-REPOSITORY-1", title="Received by Supply",
        managing_office=offices["MED"], current_office=offices["SUP"], status=Status.COMPLETED_PENDING_UPLOAD,
        completed_at=timezone.now(), created_by=users["med_admin"], revision=1)
    ExternalRoutingStep.objects.create(record=record, sequence=1, from_external_office=counterpart,
        to_office=offices["SUP"], from_name_snapshot=counterpart.name, to_name_snapshot=offices["SUP"].name,
        sent_at=timezone.now(), received_at=timezone.now(), received_by=users["sup_admin"])
    return record


def test_new_filing_and_notification_follow_responsible_office(completed_case, users, offices):
    document = archive_external_record(completed_case, user=users["sup_admin"], expected_revision=1)
    assert document.office == offices["SUP"]
    assert Notification.objects.filter(document=document, office=offices["SUP"]).exists()
    assert not Notification.objects.filter(document=document, office=offices["MED"]).exists()


def test_original_recording_admin_cannot_file_after_custody_transfer(completed_case, users):
    with pytest.raises(PermissionDenied):
        archive_external_record(completed_case, user=users["med_admin"], expected_revision=1)
    assert not Document.objects.exists()


def test_filed_document_owner_remains_fixed(completed_case, users, offices):
    document = Document.objects.create(title="Already filed", collection="EXTERNAL", office=offices["MED"],
        external_tracking_record=completed_case)
    assert archive_external_record(completed_case, user=users["med_admin"], expected_revision=1).pk == document.pk
    with pytest.raises(ValidationError):
        save_document_metadata(document, user=users["admin"], data={"office": offices["SUP"]},
                               tag_names=[], metadata_values={})
    document.refresh_from_db()
    assert document.office == offices["MED"]


def test_external_review_hides_owner_and_ignores_forged_owner(client, users, offices):
    from apps.documents.forms import DocumentMetadataForm

    document = Document.objects.create(title="External", collection="EXTERNAL", office=offices["MED"])
    client.force_login(users["med_admin"])
    response = client.get(reverse("documents:review", args=[document.pk]))
    assert 'name="office"' not in response.content.decode()
    form = DocumentMetadataForm({"title": "Corrected", "office": offices["SUP"].pk, "year": 2026,
        "access_level": "OFFICE", "ocr_language": "auto"}, instance=document, user=users["admin"])
    assert form.is_valid(), form.errors
    assert form.cleaned_data["office"] == offices["MED"]


def test_external_upload_view_uses_system_query_context(client, users, offices, counterpart, upload_settings):
    client.force_login(users["admin"])
    response = client.post(reverse("documents:external_upload") + f'?office={offices["SUP"].pk}',
        upload_data(counterpart, office=offices["MED"].pk, file=SimpleUploadedFile("proof.txt", b"Proof")))
    assert response.status_code == 302
    assert Document.objects.get().office == offices["SUP"]


@pytest.mark.parametrize("query", ["office=bad", "office=999999", "office=1&office=2"])
def test_invalid_upload_context_fails_before_mutation(client, users, counterpart, upload_settings, query):
    client.force_login(users["admin"])
    response = client.post(reverse("documents:external_upload") + "?" + query,
        upload_data(counterpart, file=SimpleUploadedFile("proof.txt", b"Proof")))
    assert response.status_code == 400
    assert not Document.objects.exists()


def test_office_less_system_upload_explains_required_context(client, users):
    actor = users["admin"]
    actor.office = None
    actor.save(update_fields=["office"])
    client.force_login(actor)
    response = client.get(reverse("documents:external_upload"))
    assert response.status_code == 400
    assert "perspective" in response.content.decode().lower()


def test_upload_carries_context_and_has_no_owner_control(client, users, offices):
    client.force_login(users["admin"])
    response = client.get(reverse("documents:external_upload"), {"office": offices["SUP"].pk})
    html = response.content.decode()
    assert 'name="office"' not in html
    assert f'?office={offices["SUP"].pk}' in html


def test_report_export_describes_filing_office(client, users):
    client.force_login(users["med_admin"])
    response = client.get(reverse("documents:external_reports"), {"export": "csv"})
    assert "Filing office" in response.content.decode().splitlines()[0]

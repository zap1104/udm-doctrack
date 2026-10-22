"""Repository collections must stay separate even when permissions overlap."""

import re
from datetime import timedelta
from html import unescape
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from apps.documents.external_services import archive_external_record
from apps.documents.models import Document, DocumentFile
from apps.documents.services import (
    deletion_review_state,
    ingest_upload,
    review_document_deletion,
    save_document_metadata,
    suggested_tags_for,
)
from apps.search.services import autocomplete_terms, search_documents
from apps.tracking.models import ExternalAttachment, ExternalOffice, ExternalRoutingStep, ExternalTrackingRecord, Status


def test_document_collection_contract():
    assert Document._meta.get_field("collection").default == "OVPA"
    assert hasattr(Document.objects, "in_collection")
    assert hasattr(Document, "tracking_origin")


def test_external_review_initial_does_not_replace_managing_office():
    from apps.documents.views import MetadataReviewView

    document = Document(title="External", office_id=1, collection="EXTERNAL")
    assert MetadataReviewView()._initial(document, {"office_id": 2})["office"] == 1


@pytest.mark.django_db
def test_external_collection_does_not_enter_legacy_repository_or_search(client, users, offices):
    ordinary = Document.objects.create(title="Shared phrase ordinary", office=offices["MED"], year=2026)
    external = Document.objects.create(title="Shared phrase external", office=offices["MED"], year=2026, collection="EXTERNAL")
    client.force_login(users["med_admin"])
    response = client.get(reverse("documents:repository"))
    assert response.status_code == 200
    assert ordinary in response.context["documents"]
    assert external not in response.context["documents"]
    found = search_documents(user=users["med_admin"], query="", log=False)
    assert [result.document.pk for result in found.results] == [ordinary.pk]
    external_found = search_documents(user=users["med_admin"], collection="EXTERNAL", query="", log=False)
    assert [result.document.pk for result in external_found.results] == [external.pk]


@pytest.mark.django_db
def test_external_shared_mutation_requires_admin_and_rechecks_actor(users, offices):
    document = Document.objects.create(title="External", office=offices["MED"], year=2026, collection="EXTERNAL")
    assert document.can_user_view(users["med"])
    assert not document.can_user_edit(users["med"])
    with pytest.raises(PermissionDenied):
        save_document_metadata(document, user=users["med"], data={"title": "Changed"}, tag_names=[], metadata_values={})
    stale_admin = users["med_admin"]
    type(stale_admin).objects.filter(pk=stale_admin.pk).update(role="USER")
    with pytest.raises(PermissionDenied):
        save_document_metadata(document, user=stale_admin, data={"title": "Changed"}, tag_names=[], metadata_values={})
    document.refresh_from_db()
    assert document.title == "External"


@pytest.mark.django_db
def test_external_repository_filter_failure_never_widens(client, users, offices):
    Document.objects.create(title="External", office=offices["MED"], year=2026, collection="EXTERNAL")
    client.force_login(users["med_admin"])
    response = client.get(reverse("documents:external_repository"), {"external_office": "999999"})
    assert response.status_code == 200
    assert response.context["total"] == 0


@pytest.fixture
def external_case(users, offices, memo_type):
    record = ExternalTrackingRecord.objects.create(
        title="External case for filing", description="Paper evidence", original_reference="PAPER-2026-123",
        tracking_number="UDM-EXT-MED-2026-001", managing_office=offices["MED"],
        current_office=offices["MED"], current_office_snapshot=offices["MED"].name,
        document_type=memo_type, created_by=users["med_admin"],
        status=Status.COMPLETED_PENDING_UPLOAD, completed_at=timezone.now(),
    )
    counterpart = ExternalOffice.objects.create(name="Original college name", code="COL", managing_office=offices["MED"])
    ExternalRoutingStep.objects.create(
        record=record, sequence=1, from_external_office=counterpart, to_office=offices["MED"],
        from_name_snapshot=counterpart.name, to_name_snapshot=offices["MED"].name,
        sent_by=users["med_admin"], sent_at=timezone.now() - timedelta(minutes=1), received_at=timezone.now(),
    )
    return record


@pytest.mark.django_db
def test_external_filing_is_atomic_idempotent_and_searchable(external_case, users, offices, settings, tmp_path, django_capture_on_commit_callbacks):
    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_BACKGROUND_TASKS = True
    counterpart = external_case.routing_steps.get().from_external_office
    counterpart.name = "College of Education"
    counterpart.save(update_fields=["name"])
    import hashlib

    evidence_checksum = hashlib.sha256(b"Evidence paper").hexdigest()
    attachment = ExternalAttachment.objects.create(record=external_case, file=ContentFile(b"Evidence paper", name="evidence.txt"), original_name="evidence.txt", size=14, checksum=evidence_checksum, uploaded_by=users["med_admin"])
    with patch("django_q.tasks.async_task") as queue:
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            document = archive_external_record(external_case, user=users["med_admin"], expected_revision=1)
        queue.assert_not_called()
        assert len(callbacks) == 1
        callbacks[0]()
        queue.assert_called_once()
    assert document.collection == "EXTERNAL" and document.source == "DTS"
    assert document.tracking_record_id is None and document.tracking_origin.pk == external_case.pk
    assert document.external_office_links.get().name_snapshot == "Original college name"
    assert document.files.get().file.name != attachment.file.name
    assert document.files.get().file.read() == b"Evidence paper"
    assert document.files.get().checksum == evidence_checksum
    external_case.refresh_from_db()
    assert external_case.status == Status.COMPLETED and external_case.filed_at and external_case.revision == 2
    assert archive_external_record(external_case, user=users["med_admin"], expected_revision=1).pk == document.pk
    assert external_case.activities.filter(event="FILED").count() == 1
    found = search_documents(user=users["med_admin"], query=external_case.tracking_number, collection="EXTERNAL", log=False)
    assert found.results[0].document.pk == document.pk and found.results[0].relevance == 100


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["med", "viewer", "sup_admin"])
def test_external_filing_rejects_non_managing_admin(external_case, users, role):
    with pytest.raises(PermissionDenied):
        archive_external_record(external_case, user=users[role], expected_revision=1)
    assert not Document.objects.filter(external_tracking_record=external_case).exists()


@pytest.mark.django_db
def test_external_filing_rechecks_revision(external_case, users):
    with pytest.raises(ValidationError):
        archive_external_record(external_case, user=users["med_admin"], expected_revision=0)
    assert not Document.objects.filter(external_tracking_record=external_case).exists()


@pytest.mark.django_db
def test_failed_external_filing_cleans_copies_preserves_case(external_case, users, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    ExternalAttachment.objects.create(record=external_case, file=ContentFile(b"First", name="first.txt"), original_name="first.txt")
    ExternalAttachment.objects.create(record=external_case, file="missing-file.txt", original_name="missing.txt")
    before = set(tmp_path.rglob("*.txt"))
    with pytest.raises(ValidationError, match="could not be read"):
        archive_external_record(external_case, user=users["med_admin"], expected_revision=1)
    assert set(tmp_path.rglob("*.txt")) == before
    assert not Document.objects.filter(external_tracking_record=external_case).exists()
    external_case.refresh_from_db()
    assert external_case.status == Status.COMPLETED_PENDING_UPLOAD and external_case.revision == 1
    assert external_case.activities.count() == 0


@pytest.mark.django_db
def test_external_filing_keeps_copies_if_queue_is_temporarily_unavailable(external_case, users, settings, tmp_path, django_capture_on_commit_callbacks):
    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_BACKGROUND_TASKS = True
    ExternalAttachment.objects.create(record=external_case, file=ContentFile(b"Text", name="queue.txt"), original_name="queue.txt")
    with patch("django_q.tasks.async_task", side_effect=OSError("Queue unavailable")):
        with django_capture_on_commit_callbacks(execute=True):
            document = archive_external_record(external_case, user=users["med_admin"], expected_revision=1)
    assert document.files.get().file.storage.exists(document.files.get().file.name)
    external_case.refresh_from_db()
    assert external_case.status == Status.COMPLETED


@pytest.mark.django_db
def test_external_relation_requires_matching_collection(external_case, offices):
    with pytest.raises(IntegrityError), transaction.atomic():
        Document.objects.create(title="Bad collection", office=offices["MED"], year=2026, external_tracking_record=external_case)


@pytest.mark.django_db
def test_external_tags_titles_never_enter_legacy_suggestions(users, offices):
    from apps.core.models import Tag

    external = Document.objects.create(title="Secretcounterpart external", office=offices["MED"], collection="EXTERNAL")
    tag = Tag.objects.create(name="secretcounterpart")
    external.tags.add(tag)
    for role in ("admin", "med", "med_admin"):
        assert not suggested_tags_for(users[role]).filter(pk=tag.pk).exists()
        assert not autocomplete_terms(users[role], "secretcounterpart")


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["med", "viewer"])
def test_external_upload_service_rejects_non_admin_before_extraction(users, role):
    with patch("apps.documents.services.extract_document_text") as extract:
        with pytest.raises(PermissionDenied):
            ingest_upload(user=users[role], uploaded_file=SimpleUploadedFile("file.txt", b"Text"), collection="EXTERNAL")
        extract.assert_not_called()


@pytest.mark.django_db
def test_external_upload_requires_a_counterpart_from_its_managing_office(users, offices):
    wrong = ExternalOffice.objects.create(name="Other office counterpart", managing_office=offices["SUP"])
    for selected in ([], [wrong]):
        with patch("apps.documents.services.extract_document_text") as extract:
            with pytest.raises(ValidationError, match="at least one active external office"):
                ingest_upload(user=users["med_admin"], uploaded_file=SimpleUploadedFile("proof.txt", b"Proof"), collection="EXTERNAL", external_offices=selected)
            extract.assert_not_called()
    assert not Document.objects.in_collection("EXTERNAL").exists()


@pytest.mark.django_db
def test_external_upload_keeps_source_and_counterpart_snapshot(users, offices, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_BACKGROUND_TASKS = True
    counterpart = ExternalOffice.objects.create(name="College of Business", code="CBA", managing_office=offices["MED"])
    document, _suggestion = ingest_upload(user=users["med_admin"], uploaded_file=SimpleUploadedFile("proof.txt", b"Proof"), collection="EXTERNAL", external_offices=[counterpart], source="SCAN")
    counterpart.name = "Renamed counterpart"
    counterpart.save(update_fields=["name"])
    assert document.collection == "EXTERNAL" and document.source == "SCAN"
    assert document.external_office_links.get().name_snapshot == "College of Business"
    assert "College of Business" in document.index_meta


@pytest.mark.django_db
def test_external_metadata_review_keeps_managing_office_when_ocr_suggests_another(client, users, offices):
    from apps.documents.models import MetadataSuggestion

    document = Document.objects.create(title="External document", office=offices["MED"], collection="EXTERNAL")
    MetadataSuggestion.objects.create(document=document, suggested={"office_id": offices["SUP"].pk})
    client.force_login(users["med_admin"])
    response = client.get(reverse("documents:review", args=[document.pk]))
    assert response.status_code == 200
    assert response.context["form"].fields["office"].disabled
    assert response.context["form"]["office"].value() == document.office_id


@pytest.mark.django_db
def test_pure_internal_case_cannot_be_filed_as_external(external_case, users):
    external_case.routing_steps.all().delete()
    with pytest.raises(ValidationError, match="at least one external office"):
        archive_external_record(external_case, user=users["med_admin"], expected_revision=1)
    assert not Document.objects.filter(external_tracking_record=external_case).exists()
    external_case.refresh_from_db()
    assert external_case.status == Status.COMPLETED_PENDING_UPLOAD


@pytest.mark.django_db
def test_external_reports_export_and_related_scope(client, users, offices):
    ordinary = Document.objects.create(title="Ordinary", office=offices["MED"], year=2026)
    external = Document.objects.create(title="External", office=offices["MED"], year=2026, collection="EXTERNAL")
    client.force_login(users["med_admin"])
    report = client.get(reverse("documents:external_reports"))
    assert report.status_code == 200 and report.context["total"] == 1
    exported = client.get(reverse("documents:external_reports"), {"export": "csv"})
    assert "External" in exported.content.decode() and "Ordinary" not in exported.content.decode()
    assert external not in client.get(ordinary.get_absolute_url()).context["related"]
    assert ordinary not in client.get(external.get_absolute_url()).context["related"]
    assert reverse("documents:external_repository") in client.get(external.get_absolute_url()).content.decode()


@pytest.mark.django_db
def test_external_deletion_has_same_recovery_period_and_own_queue(client, users, offices):
    document = Document.objects.create(title="External for removal", office=offices["MED"], collection="EXTERNAL", retention_until=timezone.localdate())
    file = DocumentFile.objects.create(document=document, file="external-proof.txt", original_name="proof.txt")
    review_document_deletion(document, user=users["med_admin"], decision="DELETE", reason="Approved disposal", confirmed=True, expected_state=deletion_review_state(document))
    assert document.deletion_recovery_open
    assert document.deletion_purge_at == document.deleted_at + timedelta(days=30)
    assert DocumentFile.objects.filter(pk=file.pk).exists()
    client.force_login(users["med_admin"])
    assert client.get(reverse("documents:deletion_queue"), {"status": "deleted"}).context["total"] == 0
    assert client.get(reverse("documents:external_deletion_queue"), {"status": "deleted"}).context["total"] == 1
    review_document_deletion(document, user=users["med_admin"], decision="RESTORE", reason="Still needed", next_review_date=timezone.localdate() + timedelta(days=60), expected_state=deletion_review_state(document))
    assert document.is_active and document.deleted_at is None


@pytest.mark.django_db
def test_expired_external_document_purge_preserves_external_tracking_attachment(external_case, users, offices, settings, tmp_path):
    from apps.documents.tasks import purge_deleted_documents

    settings.MEDIA_ROOT = tmp_path
    attachment = ExternalAttachment.objects.create(record=external_case, file=ContentFile(b"Retain tracking history", name="retained.txt"), original_name="retained.txt")
    document = Document.objects.create(title="Expired external repository copy", office=offices["MED"], collection="EXTERNAL", is_active=False, deleted_at=timezone.now() - timedelta(days=31))
    DocumentFile.objects.create(document=document, file=attachment.file.name, original_name="retained.txt")
    result = purge_deleted_documents()
    assert result == {"purged": 1, "failed": 0}
    assert not Document.objects.filter(pk=document.pk).exists()
    assert attachment.file.storage.exists(attachment.file.name)


@pytest.mark.django_db
def test_legacy_dashboard_report_and_facet_population_parity(client, users, offices, memo_type):
    from apps.core.models import Tag
    from apps.core.views import DashboardMemoMixin

    ordinary = Document.objects.create(title="Ordinary population", office=offices["MED"], year=2026)
    client.force_login(users["med_admin"])
    before = client.get(reverse("core:repository_reports")).context["total_documents"]
    external = Document.objects.create(title="External population", office=offices["MED"], year=2099, collection="EXTERNAL", document_type=memo_type)
    external.tags.add(Tag.objects.create(name="external-only-facet"))
    assert client.get(reverse("core:repository_reports")).context["total_documents"] == before == 1
    assert list(DashboardMemoMixin()._scoped(users["med_admin"], offices["MED"])[1]) == [ordinary]
    repository = client.get(reverse("documents:repository")).context
    assert repository["all_count"] == 1 and 2099 not in repository["years"]
    assert not repository["form"].fields["tag"].queryset.filter(name="external-only-facet").exists()
    assert not repository["type_folders"]


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["med", "viewer", "med_admin", "admin"])
def test_external_shared_detail_download_permissions(client, users, offices, settings, tmp_path, role):
    settings.MEDIA_ROOT = tmp_path
    document = Document.objects.create(title="External readable document", office=offices["MED"], collection="EXTERNAL")
    file = DocumentFile.objects.create(document=document, file=ContentFile(b"External evidence", name="read.txt"), original_name="read.txt")
    client.force_login(users[role])
    detail = client.get(document.get_absolute_url())
    assert detail.status_code == 200
    assert detail.context["can_edit"] == (role in {"med_admin", "admin"})
    endpoint = reverse("documents:file_download", args=[file.pk])
    assert client.get(endpoint).status_code == 403
    match = re.search(r'href="(' + re.escape(endpoint) + r'\?[^"]+)"', detail.content.decode())
    assert match is not None
    response = client.get(unescape(match.group(1)))
    assert response.status_code == 200 and b"".join(response.streaming_content) == b"External evidence"


@pytest.mark.django_db(transaction=True)
def test_concurrent_external_filing_creates_one_document(external_case, users):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import close_old_connections

    start = Barrier(2)
    record_id, user_id = external_case.pk, users["med_admin"].pk
    user_model = type(users["med_admin"])

    def file_once():
        close_old_connections()
        try:
            record = ExternalTrackingRecord.objects.get(pk=record_id)
            actor = user_model.objects.get(pk=user_id)
            start.wait(timeout=10)
            return archive_external_record(record, user=actor, expected_revision=1).pk
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(file_once) for _ in range(2)]
        document_ids = [future.result(timeout=20) for future in futures]
    assert document_ids[0] == document_ids[1]
    assert Document.objects.filter(external_tracking_record_id=record_id).count() == 1
    assert external_case.activities.filter(event="FILED").count() == 1

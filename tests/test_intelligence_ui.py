import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.core.models import Tag
from apps.documents.models import Document, DocumentFile
from apps.documents.processing import process_run, request_processing
from apps.documents.tagging import input_revision, vocabulary_digest

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def readable_source(users, settings, tmp_path):
    settings.ENABLE_SOURCE_UNIT_EXTRACTION = True
    settings.ENABLE_BACKGROUND_TASKS = False
    settings.MEDIA_ROOT = tmp_path
    doc = Document.objects.create(
        title="Readable source", year=2026, access_level="OFFICE", office=users["med"].office, uploaded_by=users["med"]
    )
    DocumentFile.objects.create(
        document=doc,
        file=SimpleUploadedFile(
            "source.txt", b"A complete readable document. <script>alert(1)</script> source evidence."
        ),
        original_name="source.txt",
    )
    run = request_processing(doc, user=users["med"])[0]
    process_run(run.pk, expected_generation=run.generation)
    return doc, run.units.get()


def test_source_ui_is_safe_scoped_and_current(client, users, readable_source):
    doc, unit = readable_source
    listing = reverse("documents:source_units", args=[doc.pk])
    detail = reverse("documents:source_unit", args=[doc.pk, unit.pk])
    client.force_login(users["med"])
    page = client.get(listing)
    assert page.status_code == 200
    assert "Source coverage" in page.content.decode()
    assert "1 readable of 1 expected" in page.content.decode()
    preview = client.get(detail)
    assert preview.status_code == 200
    assert "&lt;script&gt;" in preview.content.decode()
    assert "<script>alert(1)</script>" not in preview.content.decode()
    assert "no-store" in preview.headers["Cache-Control"]
    client.force_login(users["hr"])
    denied = client.get(detail)
    assert denied.status_code in {403, 404}
    assert "source evidence" not in denied.content.decode()
    client.force_login(users["med"])
    unit.run.is_current = False
    unit.run.save(update_fields=["is_current"])
    assert client.get(detail).status_code == 404


def test_existing_document_predictions_require_explicit_authorized_post(client, users, readable_source, settings):
    from apps.documents.ml.lifecycle import activate_model
    from tests.ml_fixtures import make_candidate

    settings.ENABLE_TAG_ML = True
    model = make_candidate(users)
    activate_model(model.pk, user=users["admin"], mode="suggestion")
    doc, _ = readable_source
    endpoint = reverse("documents:tag_predict", args=[doc.pk])
    data = {"input_revision": input_revision(doc), "vocabulary": vocabulary_digest()}
    client.force_login(users["med"])
    assert client.get(endpoint).status_code == 405
    assert not doc.tag_predictions.exists()
    assert client.post(endpoint, {**data, "input_revision": "stale"}).status_code == 409
    assert not doc.tag_predictions.exists()
    assert client.post(endpoint, data).status_code == 302
    assert doc.tag_predictions.exists()
    assert not doc.tags.exists()
    doc.refresh_from_db()
    assert doc.extraction_generation == 0
    client.force_login(users["viewer"])
    assert client.post(endpoint, data).status_code == 403
    client.force_login(users["hr"])
    assert client.post(endpoint, data).status_code in {403, 404}


def test_tag_review_post_is_explicit_and_stale_revision_conflicts(client, users, readable_source):
    doc, _ = readable_source
    tag = Tag.objects.create(name="approved topic", globally_approved=True)
    endpoint = reverse("documents:tag_review", args=[doc.pk])
    data = {
        "tag": tag.pk,
        "decision": "confirm",
        "input_revision": input_revision(doc),
        "vocabulary": vocabulary_digest(),
    }
    client.force_login(users["viewer"])
    assert client.post(endpoint, data).status_code == 403
    client.force_login(users["med"])
    stale = {**data, "input_revision": "stale"}
    assert client.post(endpoint, stale).status_code == 409
    assert not doc.tags.exists()
    assert client.post(endpoint, data).status_code == 302
    assert set(doc.tags.values_list("pk", flat=True)) == {tag.pk}


def test_training_eligibility_keeps_existing_office_authority(client, users, readable_source):
    doc, _ = readable_source
    endpoint = reverse("documents:training_eligibility", args=[doc.pk])
    client.force_login(users["med"])
    assert client.post(endpoint, {"eligible": "on"}).status_code == 403
    client.force_login(users["med_admin"])
    assert (
        client.post(
            endpoint, {"eligible": "on", "family": "office-fixture", "input_revision": input_revision(doc)}
        ).status_code
        == 302
    )
    doc.refresh_from_db()
    assert doc.training_eligible


def test_legacy_done_status_does_not_claim_verified_coverage(client, users):
    doc = Document.objects.create(
        title="Legacy text",
        year=2026,
        office=users["med"].office,
        uploaded_by=users["med"],
        ocr_status="DONE",
        ocr_text="Previously extracted text",
    )
    client.force_login(users["med"])
    response = client.get(reverse("documents:extraction_status", args=[doc.pk]))
    assert response.status_code == 200
    assert "Coverage has not been verified" in response.content.decode()
    assert "alert-success" not in response.content.decode()


def test_review_forms_have_distinct_ids_and_real_csrf_protection(users, readable_source, settings):
    from django.test import Client

    doc, _ = readable_source
    tag = Tag.objects.create(name="csrf topic", globally_approved=True)
    client = Client(enforce_csrf_checks=True)
    client.force_login(users["med"])
    page = client.get(reverse("documents:source_units", args=[doc.pk]), secure=True)
    assert page.status_code == 200
    html = page.content.decode()
    assert 'id="review_input_revision"' in html
    assert 'id="complete_input_revision"' in html
    endpoint = reverse("documents:tag_review", args=[doc.pk])
    data = {
        "tag": tag.pk,
        "decision": "confirm",
        "input_revision": input_revision(doc),
        "vocabulary": vocabulary_digest(),
    }
    assert client.post(endpoint, data, secure=True).status_code == 403
    token = client.cookies[settings.CSRF_COOKIE_NAME].value
    accepted = client.post(endpoint, data, secure=True, HTTP_X_CSRFTOKEN=token, HTTP_REFERER="https://testserver/")
    assert accepted.status_code == 302
    assert doc.tags.filter(pk=tag.pk).exists()


def test_forged_unit_parent_fails_closed(client, users, readable_source):
    doc, unit = readable_source
    other = Document.objects.create(
        title="Other office", year=2026, access_level="OFFICE", office=users["hr"].office, uploaded_by=users["hr"]
    )
    client.force_login(users["hr"])
    response = client.get(reverse("documents:source_unit", args=[other.pk, unit.pk]))
    assert response.status_code == 404
    assert "source evidence" not in response.content.decode()


def test_only_system_administrator_can_approve_aliases(client, users):
    alias_url = reverse("documents:tag_vocabulary")
    tag = Tag.objects.create(name="purchase request", globally_approved=True)
    client.force_login(users["med_admin"])
    assert client.get(alias_url).status_code == 403
    assert client.post(alias_url, {"action": "alias", "tag": tag.pk, "phrase": "pr"}).status_code == 403
    client.force_login(users["admin"])
    assert client.post(alias_url, {"action": "alias", "tag": tag.pk, "phrase": "pr"}).status_code == 302
    assert tag.approved_aliases.filter(phrase="pr", approved_by=users["admin"], is_active=True).exists()

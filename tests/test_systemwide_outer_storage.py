"""Request-level rollback and feedback across grouped file mutations."""

from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.core.models import UploadDraft
from apps.documents.models import Document, DocumentFile, OcrStatus
from apps.tracking.models import Attachment
from apps.tracking.services import confirm_receipt, create_draft_record, route_record

pytestmark = pytest.mark.django_db


def upload():
    return SimpleUploadedFile("proof.pdf", b"%PDF-1.4\nAudit attachment evidence\n%%EOF", "application/pdf")


def files_under(path):
    return {item.relative_to(path) for item in path.rglob("*") if item.is_file()}


def test_rejected_route_rolls_back_new_attachment_bytes(client, users, offices, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    record = create_draft_record(user=users["med"], subject="Route rollback", instructions="Review")
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    client.force_login(users["sup"])
    with patch("apps.tracking.views.services.route_record", side_effect=ValidationError("Cannot route now")) as attempt:
        response = client.post(reverse("tracking:route", args=[record.pk]), {
            "offices": [offices["HR"].pk], "action": "FORWARD", "instructions": "Next office",
            "deadline_choice": "none", "attachments": upload(),
        })
    assert response.status_code == 302
    attempt.assert_called_once()
    assert not Attachment.objects.filter(record=record).exists()
    saved = UploadDraft.objects.get(owner=users["sup"])
    assert saved.state == "open" and saved.files.get().state == "saved"
    assert files_under(tmp_path) == {Path(saved.files.get().file.name)}
    assert "Cannot route now" in client.get(response.url).content.decode()
    record.refresh_from_db()
    assert record.current_office_id == offices["SUP"].pk
    assert record.current_batch == 1


def test_draft_edit_later_failure_rolls_back_bytes_and_metadata(client, users, offices, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    record = create_draft_record(user=users["med"], subject="Original draft", instructions="Review")
    client.force_login(users["med"])
    with patch("apps.tracking.upload_drafts.log_action", side_effect=RuntimeError("later database failure")) as failure:
        with pytest.raises(RuntimeError, match="later database failure"):
            client.post(reverse("tracking:create") + f"?draft={record.pk}", {
                "subject": "Changed draft", "instructions": "Review safely", "priority": "NORMAL",
                "classification": "INTERNAL", "receiving_offices": [offices["SUP"].pk],
                "deadline_choice": "none", "attachments": upload(),
            })
    failure.assert_called_once()
    record.refresh_from_db()
    assert record.subject == "Original draft"
    assert not record.attachments.exists()
    saved = UploadDraft.objects.get(owner=users["med"])
    assert saved.state == "open" and saved.files.get().state == "saved"
    assert files_under(tmp_path) == {Path(saved.files.get().file.name)}


def test_frozen_repository_rejects_added_file_without_success_feedback(client, users, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_BACKGROUND_TASKS = False
    document = Document.objects.create(
        title="Partial extraction", year=2026, office=users["med"].office,
        uploaded_by=users["med"], ocr_text="Original usable text", ocr_status=OcrStatus.DONE,
    )
    client.force_login(users["med"])
    with patch("apps.documents.services.extract_document_text") as extractor:
        response = client.post(reverse("documents:add_files", args=[document.pk]), {"files": upload()}, follow=True)
    assert response.status_code == 403
    extractor.assert_not_called()
    assert not DocumentFile.objects.filter(document=document).exists()
    assert files_under(tmp_path) == set()
    document.refresh_from_db()
    assert document.ocr_text == "Original usable text"
    assert document.ocr_status == OcrStatus.DONE
    messages = [str(message) for message in response.wsgi_request._messages]
    assert not any("refreshed the search index" in message for message in messages)
    assert not any("saved" in message.lower() or "uploaded" in message.lower() for message in messages)


@pytest.mark.django_db(transaction=True)
def test_retry_queue_failure_is_not_reported_as_running(client, users, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_BACKGROUND_TASKS = True
    document = Document.objects.create(
        title="Retry feedback", year=2026, office=users["med"].office,
        uploaded_by=users["med"], ocr_text="Original usable text", ocr_status=OcrStatus.DONE,
    )
    DocumentFile.objects.create(document=document, file=upload(), original_name="proof.pdf")
    client.force_login(users["med"])
    with patch("django_q.tasks.async_task", side_effect=ConnectionError("Simulated queue outage")):
        response = client.post(reverse("documents:re_extract", args=[document.pk]), follow=True)
    assert response.status_code == 200
    document.refresh_from_db()
    assert document.ocr_status == OcrStatus.FAILED
    messages = [str(message) for message in response.context["messages"]]
    assert not any("Reading the document in the background" in message for message in messages)
    assert any("extraction" in message.lower() and "retry" in message.lower() for message in messages)


def test_smoke_checks_exercise_signed_downloads_for_seeded_files(users, settings, tmp_path, monkeypatch):
    from scripts import smoke_pages

    settings.MEDIA_ROOT = tmp_path
    record = create_draft_record(user=users["med"], subject="Smoke file", instructions="Review")
    route_record(record, [users["sup"].office], user=users["med"])
    attachment = Attachment.objects.create(record=record, file=upload(), original_name="proof.pdf", uploaded_by=users["med"])
    document = Document.objects.create(title="Smoke repository", year=2026, office=users["med"].office, uploaded_by=users["med"])
    DocumentFile.objects.create(document=document, file=upload(), original_name="proof.pdf")
    # The smoke command samples one user per role. Keep its staff sample in MED,
    # where both seeded files are visible, instead of relying on user ordering.
    type(users["med"]).objects.filter(role="USER").exclude(pk=users["med"].pk).update(is_active=False)
    for name in ("STATIC_PAGES", "QUERY_PAGES", "WRITE_PAGES", "ADMIN_PAGES", "OFFICE_ADMIN_PAGES", "MASTER_DATA_SLUGS", "SYSTEM_ADMIN_SLUGS"):
        monkeypatch.setattr(smoke_pages, name, [])
    requests = []
    original = smoke_pages.Smoke.get

    def observe(self, client, url, who, expected=(200, 302)):
        requests.append(url)
        if "/attachment/" in url or "/documents/file/" in url:
            expected = (200,)  # These seeded files exist; a 404 is a regression.
        return original(self, client, url, who, expected)

    monkeypatch.setattr(smoke_pages.Smoke, "get", observe)
    assert smoke_pages._run() == 0
    downloads = [url for url in requests if "/attachment/" in url or "/documents/file/" in url]
    assert len(downloads) == 8  # Both file kinds are visible to all four fixture roles.

    from django.utils import timezone

    from apps.core.file_lifecycle_models import lifecycle_write
    with lifecycle_write():
        Attachment.objects.filter(pk=attachment.pk).update(retired_at=timezone.now(), retirement_reason="Smoke fixture")
    requests.clear()
    def observe_retired(self, client, url, who, expected=(200, 302)):
        if "/attachment/" in url:
            assert expected == (410,), "Smoke must distinguish deliberate retirement from a broken retained download"
        return original(self, client, url, who, expected=expected)
    monkeypatch.setattr(smoke_pages.Smoke, "get", observe_retired)
    assert smoke_pages._run() == 0
    assert all("token=" in url for url in downloads)

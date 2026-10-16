"""Completed background suggestions must reach the original upload session."""

from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.documents.models import Document, OcrStatus
from apps.documents.tasks import extract_document_task


@pytest.mark.django_db
def test_upload_session_review_loads_completed_background_suggestions(
    client, users, memo_type, settings, tmp_path, django_capture_on_commit_callbacks,
):
    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_BACKGROUND_TASKS = True
    settings.SUGGESTION_ENGINE = "rules"
    client.force_login(users["med"])
    uploaded = SimpleUploadedFile(
        "scan.txt",
        b"Subject: Completed worker memorandum\n"
        b"From: Records officer\n"
        b"Reference: MED-MEMO-2026\n"
        b"This memorandum requires review by the office.\n",
        content_type="text/plain",
    )
    with patch("django_q.tasks.async_task"), django_capture_on_commit_callbacks(execute=True):
        response = client.post(reverse("documents:upload"), {
            "file": uploaded,
            "office": users["med"].office_id,
            "source": "UPLOAD",
            "ocr_language": "auto",
        })
    assert response.status_code == 302
    document = Document.objects.get(uploaded_by=users["med"])
    review_url = reverse("documents:review", args=[document.pk])
    pending = client.get(review_url)
    assert pending.status_code == 200
    assert pending.context["is_extraction_pending"] is True

    result = extract_document_task(document.pk, user_id=users["med"].pk)
    assert result["status"] == OcrStatus.DONE

    refreshed = client.get(review_url)
    assert refreshed.status_code == 200
    assert refreshed.context["is_extraction_pending"] is False
    assert refreshed.context["form"].initial["title"] == "Completed worker memorandum"
    assert refreshed.context["form"].initial["author_name"] == "Records officer"
    assert refreshed.context["form"].initial["reference_number"] == "MED-MEMO-2026"
    assert refreshed.context["form"].initial["document_type"] == memo_type.pk
    assert refreshed.context["suggestion"]["title"] == "Completed worker memorandum"

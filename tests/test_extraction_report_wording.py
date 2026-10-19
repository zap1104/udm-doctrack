import pytest

from apps.documents.models import Document, OcrStatus


@pytest.mark.django_db
@pytest.mark.parametrize("existing_text", ["", "Previously extracted searchable text"])
def test_skipped_report_does_not_claim_existing_text_is_unsearchable(client, users, existing_text):
    Document.objects.create(
        title="Skipped or cancelled extraction", office=users["med"].office,
        uploaded_by=users["med"], year=2026, ocr_status=OcrStatus.SKIPPED,
        ocr_text=existing_text,
    )
    client.force_login(users["admin"])
    response = client.get("/documents/reports/")
    assert response.status_code == 200
    assert response.context["extraction"]["skipped"] == 1
    body = response.content.decode()
    assert "1 skipped or cancelled" in body
    assert "skipped, searchable by metadata only" not in body

from __future__ import annotations

import csv
import io
from datetime import date

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from apps.accounts.models import User
from apps.core.models import Notification
from apps.core.notifications import mark_read, notify_office, unread_count
from apps.core.utils import validate_upload
from apps.documents import services as document_services
from apps.tracking import services as tracking_services


@pytest.mark.django_db
def test_html_disguised_as_pdf_is_rejected():
    uploaded = SimpleUploadedFile("memo.pdf", b"<html><script>alert(1)</script></html>", content_type="application/pdf")
    with pytest.raises(Exception, match="do not match"):
        validate_upload(uploaded)


@pytest.mark.django_db(transaction=True)
def test_background_upload_enqueues_after_commit(users, monkeypatch):
    uploaded = SimpleUploadedFile("memo.pdf", b"%PDF-1.7\n", content_type="application/pdf")
    called = []
    monkeypatch.setattr("django_q.tasks.async_task", lambda *args, **kwargs: called.append((args, kwargs)))
    with override_settings(ENABLE_BACKGROUND_TASKS=True):
        document, suggestion = document_services.ingest_upload(
            user=users["med"], uploaded_file=uploaded, office=users["med"].office
        )
    assert document.ocr_status == "PENDING"
    assert suggestion.title == ""
    assert called and called[0][0][0] == "apps.documents.tasks.extract_document_task"
    assert called[0][0][1] == document.pk


@pytest.mark.django_db
def test_notifications_are_read_per_user_not_per_office(offices):
    first = User.objects.create_user(username="first", password="TestPass123!", office=offices["MED"])
    second = User.objects.create_user(username="second", password="TestPass123!", office=offices["MED"])
    notification = notify_office(offices["MED"], kind="ROUTED", title="Incoming", message="A record is waiting")
    assert unread_count(first) == 1
    assert unread_count(second) == 1
    assert mark_read(notification, first)
    assert unread_count(first) == 0
    assert unread_count(second) == 1
    assert Notification.objects.filter(office=offices["MED"]).count() == 1


@pytest.mark.django_db
def test_healthz_reports_database_and_migrations(client):
    response = client.get("/healthz/")
    assert response.status_code == 200
    assert response.json()["checks"]["database"] is True
    assert response.json()["checks"]["migrations"] is True


@pytest.mark.django_db
def test_report_export_applies_office_filter(client, users, offices):
    med_record = tracking_services.create_draft_record(
        user=users["admin"], originating_office=offices["MED"], subject="MED only", instructions="work"
    )
    tracking_services.create_draft_record(
        user=users["admin"], originating_office=offices["SUP"], subject="SUP only", instructions="work"
    )
    client.force_login(users["admin"])
    response = client.get(f"/reports/export/?office={offices['MED'].pk}")
    rows = list(csv.reader(io.StringIO(response.content.decode())))
    body = "\n".join(",".join(row) for row in rows)
    assert med_record.tracking_number in body
    assert "SUP only" not in body
    assert f"{offices['MED'].code}" in response["Content-Disposition"]


@pytest.mark.django_db
@override_settings(EMAIL_CONFIGURED=False)
def test_password_reset_is_honest_when_email_is_not_configured(client):
    response = client.get("/accounts/password-reset/")
    assert response.status_code == 302
    assert response["Location"].endswith("/accounts/login/")


@pytest.mark.django_db
def test_different_users_behind_one_ip_do_not_lock_each_other_out(client, users):
    ip = "198.51.100.77"
    for _ in range(3):
        client.post("/accounts/login/", {"username": "med", "password": "wrong"}, REMOTE_ADDR=ip)
    response = client.post(
        "/accounts/login/", {"username": "sup", "password": "TestPass123!"}, REMOTE_ADDR=ip
    )
    assert response.status_code in {200, 302}
    assert client.session.get("_auth_user_id") == str(users["sup"].pk)


def test_sensitive_image_never_calls_an_external_ocr_provider(monkeypatch):
    from apps.documents import extraction

    monkeypatch.setattr(
        extraction,
        "_ocr_space",
        lambda *args, **kwargs: pytest.fail("external OCR must not be called for an opted-out document"),
    )
    with override_settings(OCR_BACKEND="ocrspace", OCR_SPACE_API_KEY="test-key"):
        result = extraction.extract_document_text(
            io.BytesIO(b"\x89PNG\r\n\x1a\n"),
            "sensitive.png",
            allow_external_ocr=False,
            language_hint="fil",
        )
    assert result.status == "SKIPPED"
    assert "no file content was sent" in " ".join(result.notes)


def test_ocrspace_retries_one_transient_failure(monkeypatch):
    import json
    import urllib.error

    from apps.documents import extraction

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(
                {
                    "ParsedResults": [
                        {
                            "ParsedText": "Recovered text",
                            "TextOverlay": {"Lines": [{"Words": [{"Confidence": 88}]}]},
                        }
                    ]
                }
            ).encode()

    calls = []

    def fake_urlopen(request, timeout):
        calls.append(timeout)
        if len(calls) == 1:
            raise urllib.error.URLError("temporary")
        return Response()

    monkeypatch.setattr(extraction.urllib.request, "urlopen", fake_urlopen)
    with override_settings(
        OCR_SPACE_API_KEY="test-key",
        OCR_PROVIDER_RETRIES=1,
        OCR_RETRY_BASE_SECONDS=0,
        OCR_PROVIDER_TIMEOUT_SECONDS=7,
    ):
        result = extraction._ocr_space(io.BytesIO(b"%PDF-1.7"), "scan.pdf", language_hint="fil")
    assert result.status == "DONE"
    assert result.confidence == 0.88
    assert result.engine == "ocr.space:eng"
    assert any("Filipino/Taglish" in note for note in result.notes)
    assert calls == [7, 7]
    assert any("retry" in note.lower() for note in result.notes)


@pytest.mark.django_db
def test_retention_date_is_computed_and_due_state_never_deletes(users, memo_type):
    from apps.documents.models import Document

    document = Document.objects.create(
        title="Historic leap-day memorandum",
        office=users["med"].office,
        document_type=memo_type,
        document_date=date(2020, 2, 29),
        year=2020,
        uploaded_by=users["med"],
    )
    assert document.retention_until == date(2025, 2, 28)
    assert document.retention_is_due is True
    assert Document.objects.due_for_retention_review(date(2026, 1, 1)).filter(pk=document.pk).exists()
    document.refresh_from_db()
    assert document.is_active is True


@pytest.mark.django_db
def test_bulk_receipt_records_one_history_event_per_selected_record(client, users, offices, memo_type):
    records = []
    for subject in ("First incoming memo", "Second incoming memo"):
        record = tracking_services.create_draft_record(
            user=users["med"], subject=subject, instructions="For action", document_type=memo_type
        )
        tracking_services.route_record(
            record, [offices["SUP"]], user=users["med"], instructions="For action"
        )
        records.append(record)

    client.force_login(users["sup"])
    response = client.post(
        "/tracking/bulk-receipt/",
        {
            "record_ids": [str(record.pk) for record in records],
            "note": "Received together at the records desk.",
            "confirm_custody": "on",
        },
    )
    assert response.status_code == 302
    for record in records:
        record.refresh_from_db()
        assert record.status == "RECEIVED"
        assert record.activities.filter(event="RECEIVED").count() == 1
        step = record.routing_steps.get(to_office=offices["SUP"])
        assert step.receipt_note == "Received together at the records desk."


@pytest.mark.django_db
def test_search_click_is_attributed_to_the_originating_user_and_query(client, second_client, users, memo_type):
    from apps.documents.models import Document, SearchQueryLog, SearchResultClick

    document = Document.objects.create(
        title="Searchable memorandum",
        office=users["med"].office,
        document_type=memo_type,
        year=2026,
        uploaded_by=users["med"],
    )
    query_log = SearchQueryLog.objects.create(
        user=users["med"], query="searchable", result_count=1, duration_ms=4
    )
    client.force_login(users["med"])
    response = client.get(f"/search/click/{query_log.pk}/{document.pk}/1/")
    assert response.status_code == 302
    assert response["Location"] == document.get_absolute_url()
    click = SearchResultClick.objects.get(query_log=query_log)
    assert click.user == users["med"]
    assert click.document == document
    assert click.rank == 1
    query_log.refresh_from_db()
    assert query_log.clicked_document == document

    second_client.force_login(users["sup"])
    denied = second_client.get(f"/search/click/{query_log.pk}/{document.pk}/1/")
    assert denied.status_code == 404
    assert SearchResultClick.objects.filter(query_log=query_log).count() == 1


@pytest.mark.django_db
def test_tracking_archive_defaults_to_external_ocr_disabled(users, offices, memo_type):
    record = tracking_services.create_draft_record(
        user=users["med"], subject="Sensitive completed record", instructions="For internal action", document_type=memo_type
    )
    tracking_services.route_record(record, [offices["SUP"]], user=users["med"], instructions="For action")
    tracking_services.confirm_receipt(record, user=users["sup"])
    tracking_services.complete_record(record, user=users["sup"], note="Completed")
    document = document_services.archive_tracking_record(record, user=users["sup"])
    assert document.allow_external_ocr is False
    assert "disabled by default" in document.ocr_notes

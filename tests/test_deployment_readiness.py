from __future__ import annotations

import csv
import io
from datetime import date

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from apps.accounts.models import User
from apps.core.models import Notification
from apps.core.notifications import mark_read, notify_office, unread_count, unread_for
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
    notification = notify_office(
        offices["MED"], kind=Notification.Kind.ROUTED, title="Incoming", message="A record is waiting"
    )
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


@pytest.mark.django_db
def test_notification_count_returns_swappable_badge_and_respects_read_state(client, users, offices):
    notification = notify_office(
        offices["MED"],
        kind=Notification.Kind.ROUTED,
        title="Incoming",
        message="A record is waiting",
        url="/tracking/1/",
    )
    client.force_login(users["med"])
    response = client.get("/notifications/count/")
    body = response.content.decode()
    assert response.status_code == 200
    assert "every 60s [document.visibilityState=='visible']" in body
    assert "hx-swap=\"outerHTML\"" in body
    assert "notification-count" in body
    assert response["Cache-Control"] == "no-store"

    client.post(f"/notifications/{notification.pk}/read/")
    response = client.get("/notifications/count/")
    assert "notification-count d-none" in response.content.decode()


@pytest.mark.django_db
def test_notification_read_is_scoped_to_the_user_office(client, users, offices):
    notification = notify_office(
        offices["MED"], kind=Notification.Kind.ROUTED, title="Private office update", message="Not for SUP"
    )
    client.force_login(users["sup"])
    response = client.post(f"/notifications/{notification.pk}/read/")
    assert response.status_code == 404
    assert Notification.objects.get(pk=notification.pk).reads.count() == 0


@pytest.mark.django_db
def test_notification_urls_are_relative_only(offices):
    external = notify_office(
        offices["MED"], kind=Notification.Kind.SHARED, title="External", message="No redirect", url="https://evil.example/"
    )
    relative = notify_office(
        offices["MED"], kind=Notification.Kind.SHARED, title="Internal", message="Safe redirect", url="/tracking/1/"
    )
    assert external.url == ""
    assert relative.url == "/tracking/1/"


@pytest.mark.django_db
def test_unread_excludes_resolved_and_already_read(users, offices):
    from django.utils import timezone

    resolved = notify_office(offices["MED"], kind=Notification.Kind.RECEIVED, title="Resolved", message="Done")
    resolved.resolved_at = timezone.now()
    resolved.save(update_fields=["resolved_at"])
    read = notify_office(offices["MED"], kind=Notification.Kind.ROUTED, title="Read", message="Seen")
    mark_read(read, users["med"])
    unread = notify_office(offices["MED"], kind=Notification.Kind.ROUTED, title="Unread", message="Open")
    assert list(unread_for(users["med"])) == [unread]


@pytest.mark.django_db
def test_mark_all_read_is_idempotent(client, users, offices):
    from apps.core.models import NotificationRead
    from apps.core.notifications import mark_all_read

    notifications = [
        notify_office(offices["MED"], kind=Notification.Kind.ROUTED, title=f"N{idx}", message="Open")
        for idx in range(3)
    ]
    assert mark_all_read(users["med"], Notification.objects.filter(pk__in=[n.pk for n in notifications])) == 3
    assert mark_all_read(users["med"], Notification.objects.filter(pk__in=[n.pk for n in notifications])) == 3
    assert NotificationRead.objects.filter(user=users["med"], notification__in=notifications).count() == 3

    client.force_login(users["med"])
    assert client.post("/notifications/read-all/").status_code == 302


@pytest.mark.django_db
def test_non_template_response_does_not_eagerly_count_notifications(client, users):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    client.force_login(users["med"])
    with CaptureQueriesContext(connection) as captured:
        response = client.get("/healthz/")
    assert response.status_code == 200
    assert not any("notification" in query["sql"].lower() for query in captured.captured_queries)


@pytest.mark.django_db
def test_notification_maintenance_resolves_stale_info_and_prunes_only_old_resolved(users, offices, settings):
    from datetime import timedelta

    from django.utils import timezone

    from apps.core.tasks import prune_notifications

    settings.NOTIFICATION_INFO_RESOLVE_DAYS = 30
    settings.NOTIFICATION_RETENTION_DAYS = 90
    stale_info = notify_office(
        offices["MED"], kind=Notification.Kind.COMPLETED, title="Old completion", message="Informational"
    )
    stale_info.created_at = timezone.now() - timedelta(days=31)
    stale_info.save(update_fields=["created_at"])
    old_resolved = notify_office(
        offices["MED"], kind=Notification.Kind.SHARED, title="Old resolved", message="Prunable"
    )
    old_resolved.resolved_at = timezone.now() - timedelta(days=91)
    old_resolved.save(update_fields=["resolved_at"])
    active = notify_office(offices["MED"], kind=Notification.Kind.ROUTED, title="Active", message="Keep")

    result = prune_notifications()

    stale_info.refresh_from_db()
    assert stale_info.resolved_at is not None
    assert not Notification.objects.filter(pk=old_resolved.pk).exists()
    assert Notification.objects.filter(pk=active.pk).exists()
    assert result["resolved"] >= 1
    assert result["deleted"] >= 1

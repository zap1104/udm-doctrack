from __future__ import annotations

import csv
import io

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

"""Regression tests for actual authorization, recovery, and upload threats."""

import io
import logging
import time
import zipfile
from unittest.mock import patch

import pytest
from django.contrib import admin
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.accounts.email_verification import email_verification_token
from apps.accounts.forms import VerifiedEmailPasswordResetForm
from apps.core.security import RedactingFormatter, allow_sensitive_request
from apps.core.utils import checksum_of, validate_upload
from apps.documents import services
from apps.documents.forms import DocumentMetadataForm
from apps.documents.models import AccessLevel, Document, DocumentAccessGrant, DocumentFile


@pytest.fixture
def document(users, offices):
    return Document.objects.create(title="Private Supply record", office=offices["SUP"], uploaded_by=users["sup"], year=2026)


@pytest.mark.django_db
def test_staff_flag_does_not_bypass_office_scope(users):
    request = RequestFactory().get("/django-admin/")
    users["med_admin"].is_staff = True
    request.user = users["med_admin"]
    assert not admin.site.has_permission(request)
    request.user = users["admin"]
    assert admin.site.has_permission(request)


@pytest.mark.django_db
def test_office_admin_cannot_reset_global_admin_in_same_office(client, users):
    users["admin"].office = users["med_admin"].office
    users["admin"].save()
    client.force_login(users["med_admin"])
    assert client.get(reverse("accounts:user_edit", args=[users["admin"].pk])).status_code == 404


@pytest.mark.django_db
def test_duplicate_warning_cannot_reveal_another_offices_title(document, users):
    uploaded = SimpleUploadedFile("private.txt", b"private university correspondence")
    DocumentFile.objects.create(document=document, file=uploaded, original_name=uploaded.name, checksum=checksum_of(uploaded))
    assert services.duplicate_of(uploaded, user=users["med"]) is None
    assert services.duplicate_of(uploaded, user=users["sup"]) == document


@pytest.mark.django_db
def test_metadata_ownership_is_scoped_in_form_and_service(document, users, offices):
    form = DocumentMetadataForm(instance=document, user=users["sup"])
    assert list(form.fields["office"].queryset) == [offices["SUP"]]
    with pytest.raises(PermissionDenied):
        services.save_document_metadata(document, user=users["sup"], data={"office": offices["MED"]}, tag_names=[], metadata_values={})
    with pytest.raises(PermissionDenied):
        services.add_file_to_document(document, SimpleUploadedFile("note.txt", b"memo"), user=users["med"])


@pytest.mark.django_db
def test_viewer_cannot_edit_even_if_they_were_once_uploader(document, users):
    document.uploaded_by = users["viewer"]
    assert not document.can_user_edit(users["viewer"])
    assert not document.can_user_edit(AnonymousUser())


@pytest.mark.django_db
def test_restricted_document_requires_explicit_grant_even_in_owning_office(document, users):
    document.office = users["med"].office
    document.access_level = AccessLevel.RESTRICTED
    document.save()
    assert document.can_user_view(users["sup"])
    assert document.can_user_view(users["admin"])
    assert not document.can_user_view(users["med"])
    DocumentAccessGrant.objects.create(document=document, user=users["med"], granted_by=users["admin"])
    assert document.can_user_view(users["med"])


@pytest.mark.django_db
def test_a_granted_reader_cannot_reshare_another_offices_record(users, offices):
    from apps.tracking.services import create_draft_record, grant_access, route_record

    record = create_draft_record(user=users["med"], subject="Restricted routing", instructions="Review")
    route_record(record, [offices["SUP"]], user=users["med"])
    grant_access(record, user=users["med"], target_user=users["hr"])
    assert record.can_user_view(users["hr"])
    with pytest.raises(PermissionDenied):
        grant_access(record, user=users["hr"], office=offices["REC"])


@pytest.mark.django_db
def test_ingest_service_refuses_other_office(users, offices):
    with pytest.raises(PermissionDenied):
        services.ingest_upload(user=users["med"], office=offices["SUP"], uploaded_file=SimpleUploadedFile("note.txt", b"memo"))


def test_office_archive_expansion_limit(settings):
    settings.MAX_UPLOAD_UNCOMPRESSED_MB = 1
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "types")
        archive.writestr("word/document.xml", b"a" * (2 * 1024 * 1024))
    uploaded = SimpleUploadedFile("bomb.docx", buffer.getvalue())
    with pytest.raises(ValidationError, match="expands"):
        validate_upload(uploaded)
    assert uploaded.tell() == 0


def test_multiple_file_limit_and_unknown_content_type(settings):
    from apps.core.forms import MultipleFileField

    settings.MAX_UPLOAD_FILES = 1
    with pytest.raises(ValidationError, match="at most"):
        MultipleFileField().clean([SimpleUploadedFile("one.txt", b"one"), SimpleUploadedFile("two.txt", b"two")])
    settings.ALLOWED_UPLOAD_EXTENSIONS = ["svg"]
    with pytest.raises(ValidationError, match="no supported"):
        validate_upload(SimpleUploadedFile("active.svg", b'<svg onload="alert(1)"/>'))


@pytest.mark.django_db
def test_direct_media_url_does_not_serve_private_file(client, settings, tmp_path):
    settings.DEBUG = True
    settings.MEDIA_ROOT = tmp_path
    (tmp_path / "secret.txt").write_text("private record")
    assert client.get("/media/secret.txt").status_code == 404


@pytest.mark.django_db
def test_verified_email_required_for_recovery(users):
    user = users["med"]
    user.email = "owner@example.edu"
    user.save()
    form = VerifiedEmailPasswordResetForm()
    assert list(form.get_users(user.email)) == []
    user.verified_email = user.email
    user.save()
    assert list(form.get_users(user.email)) == [user]
    user.email = "changed@example.edu"
    user.save()
    assert list(form.get_users(user.email)) == []


@pytest.mark.django_db
def test_email_verification_is_post_only_account_bound_and_single_use(client, users):
    user = users["med"]
    user.email = "owner@example.edu"
    user.save()
    token = email_verification_token.make_token(user)
    url = reverse("accounts:verify_email_confirm", args=[user.pk, token])
    client.force_login(users["sup"])
    assert client.post(url).status_code == 400
    client.force_login(user)
    assert client.get(url).status_code == 200
    user.refresh_from_db()
    assert not user.email_is_verified
    assert client.post(url).status_code == 302
    user.refresh_from_db()
    assert user.email_is_verified
    assert client.post(url).status_code == 400


@pytest.mark.django_db
def test_email_token_expires_and_cannot_verify_changed_address(users, settings):
    from datetime import timedelta

    user = users["med"]
    user.email = "owner@example.edu"
    token = email_verification_token.make_token(user)
    now = email_verification_token._now()
    with patch.object(email_verification_token, "_now", return_value=now + timedelta(seconds=settings.PASSWORD_RESET_TIMEOUT + 1)):
        assert not email_verification_token.check_token(user, token)
    user.email = "attacker@example.edu"
    assert not email_verification_token.check_token(user, token)


@pytest.mark.django_db
def test_verification_delivery_and_throttle(client, users, settings, mailoutbox):
    settings.EMAIL_CONFIGURED = True
    settings.SITE_BASE_URL = "https://doctrack.example.edu"
    user = users["med"]
    user.email = "owner@example.edu"
    user.save()
    client.force_login(user)
    for _ in range(4):
        assert client.post(reverse("accounts:verify_email")).status_code == 302
    assert len(mailoutbox) == 3
    assert "https://doctrack.example.edu/accounts/email/verify/" in mailoutbox[0].body


@pytest.mark.django_db
def test_sensitive_throttle_is_shared_between_clients_and_expires():
    assert allow_sensitive_request("test", "same-user", limit=2, seconds=60)
    assert allow_sensitive_request("test", "same-user", limit=2, seconds=60)
    assert not allow_sensitive_request("test", "same-user", limit=2, seconds=60)
    assert allow_sensitive_request("test", "other-user", limit=2, seconds=60)
    from datetime import timedelta

    from django.utils import timezone

    from apps.core.models import SecurityThrottle

    SecurityThrottle.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert allow_sensitive_request("test", "same-user", limit=2, seconds=60)


@pytest.mark.django_db(transaction=True)
def test_security_throttle_cannot_be_bypassed_by_parallel_requests():
    from concurrent.futures import ThreadPoolExecutor

    from django.db import connection

    def attempt(_):
        try:
            return allow_sensitive_request("parallel", "shared", limit=2, seconds=60)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=6) as pool:
        assert sum(pool.map(attempt, range(6))) == 2


@pytest.mark.django_db
def test_background_polling_does_not_refresh_idle_activity(client, users, settings):
    client.force_login(users["med"])
    client.get("/")
    session = client.session
    last = time.time() - settings.SESSION_COOKIE_AGE + 10
    session["_last_user_activity"] = last
    session.save()
    assert client.get("/notifications/count/").status_code == 200
    assert client.session["_last_user_activity"] == last
    session = client.session
    session["_last_user_activity"] = time.time() - settings.SESSION_COOKIE_AGE - 1
    session.save()
    assert client.get("/tracking/").status_code == 302
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_private_pages_and_download_errors_are_not_cached(client, users):
    client.force_login(users["med"])
    assert "no-store" in client.get("/tracking/")["Cache-Control"]
    assert "no-store" in client.get("/documents/file/999999/")["Cache-Control"]


@pytest.mark.django_db
def test_public_health_does_not_expose_component_diagnostics(client, users):
    assert client.get("/healthz/?deep=1").json() == {"status": "ok"}
    client.force_login(users["med"])
    assert client.get("/healthz/?deep=1").json() == {"status": "ok"}
    client.force_login(users["admin"])
    assert "checks" in client.get("/healthz/").json()


@pytest.mark.django_db
def test_csrf_is_enforced_for_profile_changes(users):
    browser = Client(enforce_csrf_checks=True)
    browser.force_login(users["med"])
    assert browser.post("/accounts/profile/", {"first_name": "Injected"}, secure=True).status_code == 403


def test_log_formatter_redacts_secrets_and_exception_text(settings):
    settings.EMAIL_HOST_PASSWORD = "smtp-secret-value"
    formatter = RedactingFormatter("%(message)s")
    record = logging.LogRecord("doctrack", logging.WARNING, "", 1, "provider password=%s token=abc123", ("smtp-secret-value",), None)
    line = formatter.format(record)
    assert "smtp-secret-value" not in line and "abc123" not in line


def test_provider_notes_do_not_expose_configured_api_secrets(settings):
    from apps.documents.extraction import ExtractionResult, _clip

    settings.OCR_SPACE_API_KEY = "private-api-key-value"
    result = _clip(ExtractionResult(notes=["Invalid key: private-api-key-value"]))
    assert "private-api-key-value" not in result.notes[0]


def test_administrator_regex_cannot_stall_document_processing():
    from types import SimpleNamespace

    from apps.documents.suggestions import _rule_matches

    rule = SimpleNamespace(pattern="(a+)+$", match_type="REGEX")
    assert not _rule_matches(rule, "a" * 20000 + "!")
    rule.pattern = "purchase.*request"
    assert _rule_matches(rule, "purchase urgent request")


@pytest.mark.django_db
def test_demo_seed_is_refused_when_disabled(settings):
    settings.ALLOW_DEMO_SEED = False
    with pytest.raises(CommandError, match="disabled"):
        call_command("seed_demo", records=0)

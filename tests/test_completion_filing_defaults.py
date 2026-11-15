"""Completion commits before optional approval; omission always means pending filing."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connections
from django.urls import reverse
from django.utils import timezone

from apps.documents import services as repository
from apps.documents.models import Document
from apps.tracking import external_services, services
from apps.tracking.external_forms import ExternalCompleteForm
from apps.tracking.forms import CompleteForm
from apps.tracking.models import Status

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def storage(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def active_case(users, offices, external):
    actor = users["med_admin"]
    file = SimpleUploadedFile("signed.txt", b"Final signed file", "text/plain")
    if external:
        source = external_services.create_external_office(user=actor, name="External Registrar")
        record = external_services.create_record(user=actor, title="Completion choice", from_external_office=source)
        external_services.add_attachment(record, user=actor, uploaded_file=file, expected_revision=record.revision)
        external_services.route_record(record, user=actor, expected_revision=record.revision, to_office=offices["MED"], sent_at=timezone.now() - timedelta(days=1))
        external_services.confirm_receipt(record, user=actor, expected_revision=record.revision)
    else:
        record = services.create_draft_record(user=users["sup"], subject="Completion choice", instructions="Review")
        services.attach_files(record, [file], user=users["sup"])
        services.route_record(record, [offices["MED"]], user=users["sup"])
        services.confirm_receipt(record, user=actor)
    return record, actor


def completion_url(record, external):
    return reverse("tracking:external_action", args=[record.pk, "complete"]) if external else reverse("tracking:complete", args=[record.pk])


@pytest.mark.parametrize("external", [False, True])
def test_approval_option_is_available_to_authorized_actor_and_unchecked(users, offices, external):
    record, actor = active_case(users, offices, external)
    form = ExternalCompleteForm(user=actor, record=record) if external else CompleteForm(user=actor, record=record)
    assert "archive_now" in form.fields and form.fields["archive_now"].initial is False
    assert form.fields["archive_now"].label == "Approve into Repository now"


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("choice", [None, "", "on"])
def test_completion_option_controls_filing_and_replays_never_duplicate(client, users, offices, external, choice):
    record, actor = active_case(users, offices, external)
    client.force_verified_login(actor)
    data = {"note": "Complete", "revision": getattr(record, "revision", "")}
    if choice is not None:
        data["archive_now"] = choice
    response = client.post(completion_url(record, external), data)
    assert response.status_code == 302
    record.refresh_from_db()
    assert record.status == (Status.COMPLETED if choice == "on" else Status.COMPLETED_PENDING_UPLOAD)
    assert Document.objects.count() == (1 if choice == "on" else 0)
    client.post(completion_url(record, external), data)
    assert Document.objects.count() == (1 if choice == "on" else 0)
    assert all(item.file.storage.exists(item.file.name) for item in record.attachments.all())


@pytest.mark.parametrize("external", [False, True])
def test_forged_staff_checkbox_cannot_approve_repository(client, users, offices, external):
    record, _ = active_case(users, offices, external)
    actor = users["med"]
    client.force_verified_login(actor)
    response = client.post(completion_url(record, external), {"note": "Done", "archive_now": "on", "revision": getattr(record, "revision", "")})
    assert response.status_code == 302
    record.refresh_from_db()
    assert record.status == Status.COMPLETED_PENDING_UPLOAD and not Document.objects.exists()


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("failure", [OSError, RuntimeError])
def test_filing_failure_preserves_completed_work_for_retry(client, users, offices, external, failure):
    record, actor = active_case(users, offices, external)
    client.force_verified_login(actor)
    target = "apps.documents.external_services.archive_external_record" if external else "apps.tracking.views.services.approve_upload"
    with patch(target, side_effect=failure("Simulated storage/queue outage")) as attempted:
        response = client.post(completion_url(record, external), {"note": "Done", "archive_now": "on", "revision": getattr(record, "revision", "")})
    assert attempted.call_count == 1
    assert response.status_code == 302
    record.refresh_from_db()
    assert record.status == Status.COMPLETED_PENDING_UPLOAD and not Document.objects.exists()
    assert all(item.file.storage.exists(item.file.name) for item in record.attachments.all())


@pytest.mark.parametrize("external", [False, True])
def test_approval_is_rechecked_after_completion_if_role_changes(client, users, offices, external):
    record, actor = active_case(users, offices, external)
    client.force_verified_login(actor)
    service = external_services if external else services
    original = service.complete_record
    def complete_then_revoke(*args, **kwargs):
        result = original(*args, **kwargs)
        type(actor).objects.filter(pk=actor.pk).update(role=users["med"].role)
        return result
    with patch.object(service, "complete_record", complete_then_revoke):
        response = client.post(completion_url(record, external), {"note": "Done", "archive_now": "on", "revision": getattr(record, "revision", "")})
    assert response.status_code == 302
    record.refresh_from_db()
    assert record.status == Status.COMPLETED_PENDING_UPLOAD and not Document.objects.exists()


@pytest.mark.parametrize("external", [False, True])
def test_viewer_cannot_complete_or_approve_via_checkbox(client, users, offices, external):
    record, _ = active_case(users, offices, external)
    client.force_verified_login(users["viewer"])
    assert client.post(completion_url(record, external), {"archive_now": "on", "revision": getattr(record, "revision", "")}).status_code == 403
    record.refresh_from_db()
    assert record.status == Status.RECEIVED and not Document.objects.exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("file_now", [False, True], ids=["approve", "file-now"])
def test_internal_approval_does_not_file_a_replacement_completion(client, users, offices, monkeypatch, file_now):
    record, approver = active_case(users, offices, external=False)
    if not file_now:
        services.complete_record(record, user=approver, note="First completion")
    client.force_verified_login(approver)
    approval_url = reverse("tracking:archive", args=[record.pk])
    approval_waiting = Event()
    completion_replaced = Event()
    archive = repository.archive_tracking_record

    def pause_approval(observed, **kwargs):
        assert observed.completion_note == "First completion"
        approval_waiting.set()
        assert completion_replaced.wait(10), "The replacement completion never committed."
        return archive(observed, **kwargs)

    def submit_pending_approval():
        close_old_connections()
        try:
            if file_now:
                return client.post(completion_url(record, False), {"note": "First completion", "archive_now": "on"})
            return client.post(approval_url)
        finally:
            connections.close_all()

    with monkeypatch.context() as changes:
        changes.setattr(repository, "archive_tracking_record", pause_approval)
        with ThreadPoolExecutor(max_workers=1) as workers:
            pending = workers.submit(submit_pending_approval)
            try:
                assert approval_waiting.wait(10), "The first request never reached filing."
                services.reopen_record(record, user=users["med"], reason="More work required")
                services.complete_record(record, user=users["med"], note="Replacement completion")
            finally:
                completion_replaced.set()
            assert pending.result(timeout=20).status_code == 302

    record.refresh_from_db()
    assert record.status == Status.COMPLETED_PENDING_UPLOAD
    assert not Document.objects.filter(tracking_record=record).exists()
    assert client.post(approval_url).status_code == 302
    assert Document.objects.get(tracking_record=record).description == "Replacement completion"

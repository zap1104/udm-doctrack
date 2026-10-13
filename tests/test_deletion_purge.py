"""Thirty-day recovery boundaries and real repository-file cleanup."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AuditLog, Notification, Tag
from apps.core.notifications import unread_count
from apps.core.views import audit_entries_for
from apps.documents import services, tasks
from apps.documents.models import Document, DocumentFile
from apps.tracking.models import Attachment, Status, TrackingRecord

pytestmark = pytest.mark.django_db


@pytest.fixture
def removed_document(users, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    document = Document.objects.create(
        title="Document approved for timed deletion", office=users["med"].office,
        uploaded_by=users["med"], retention_until=timezone.localdate(),
    )
    DocumentFile.objects.create(document=document, original_name="purge.txt",
                                file=SimpleUploadedFile("purge.txt", b"Repository content"))
    services.review_document_deletion(
        document, user=users["med_admin"], decision="DELETE", reason="Approved removal",
        confirmed=True, expected_state=services.deletion_review_state(document),
    )
    return document


def age(document, now, *, days=30):
    Document.objects.filter(pk=document.pk).update(deleted_at=now - timedelta(days=days))
    document.refresh_from_db()


def restore(document, user):
    return services.review_document_deletion(
        document, user=user, decision="RESTORE", reason="Required again",
        next_review_date=timezone.localdate() + timedelta(days=365),
        expected_state=services.deletion_review_state(document),
    )


def test_removal_starts_thirty_day_recovery_window(removed_document):
    assert removed_document.deleted_at is not None
    assert removed_document.deletion_purge_at == removed_document.deleted_at + timedelta(days=30)
    assert removed_document.deletion_recovery_open


def test_restore_cancels_countdown(removed_document, users):
    restore(removed_document, users["med_admin"])
    assert removed_document.is_active and removed_document.deleted_at is None
    assert tasks.purge_deleted_documents(now=timezone.now() + timedelta(days=60))["purged"] == 0


def test_restoration_closes_at_exact_deadline(removed_document, users):
    now = timezone.now()
    age(removed_document, now)
    with patch("apps.documents.models.timezone.now", return_value=now):
        assert not removed_document.deletion_recovery_open
        with pytest.raises(ValidationError, match="30-day"):
            restore(removed_document, users["med_admin"])
    assert not Document.objects.get(pk=removed_document.pk).is_active


@pytest.mark.parametrize("seconds,purged", [(-1, 0), (0, 1), (1, 1)])
def test_purge_uses_thirty_full_days(removed_document, seconds, purged):
    deadline = removed_document.deleted_at + timedelta(days=30)
    result = tasks.purge_deleted_documents(now=deadline + timedelta(seconds=seconds))
    assert result["purged"] == purged
    assert Document.objects.filter(pk=removed_document.pk).exists() == (not purged)


def test_purge_removes_repository_bytes_related_rows_and_notifications(removed_document, users):
    now = timezone.now()
    age(removed_document, now)
    stored = removed_document.files.get().file
    tag = Tag.objects.create(name="purge-tag", usage_count=1)
    removed_document.tags.add(tag)
    notice = Notification.objects.create(office=users["med"].office, document=removed_document,
                                         kind=Notification.Kind.SHARED, title="Private deleted title")
    assert unread_count(users["med"]) == 0
    assert tasks.purge_deleted_documents(now=now) == {"purged": 1, "failed": 0}
    assert not stored.storage.exists(stored.name)
    assert not DocumentFile.objects.filter(document_id=removed_document.pk).exists()
    assert not Notification.objects.filter(pk=notice.pk).exists()
    assert unread_count(users["med"]) == 0
    tag.refresh_from_db()
    assert tag.usage_count == 0
    receipt = AuditLog.objects.get(extra__permanent_repository_deletion=True)
    assert receipt.target_id == str(removed_document.pk)
    assert removed_document.title not in receipt.summary
    assert audit_entries_for(users["med_admin"]).filter(pk=receipt.pk).exists()
    assert not audit_entries_for(users["sup_admin"]).filter(pk=receipt.pk).exists()


def test_active_and_unstamped_inactive_documents_are_never_purged(removed_document):
    now = timezone.now()
    Document.objects.filter(pk=removed_document.pk).update(is_active=True, deleted_at=now - timedelta(days=100))
    assert tasks.purge_deleted_documents(now=now)["purged"] == 0
    Document.objects.filter(pk=removed_document.pk).update(is_active=False, deleted_at=None)
    assert tasks.purge_deleted_documents(now=now)["purged"] == 0


@pytest.mark.parametrize("failure", ["exception", "silent"])
def test_failed_storage_cleanup_retries_without_allowing_restore(removed_document, users, failure):
    now = timezone.now()
    age(removed_document, now)
    stored = removed_document.files.get().file
    with patch.object(stored.storage, "delete", side_effect=OSError("Storage unavailable") if failure == "exception" else None):
        assert tasks.purge_deleted_documents(now=now) == {"purged": 0, "failed": 1}
    assert Document.objects.filter(pk=removed_document.pk).exists()
    assert stored.storage.exists(stored.name)
    with pytest.raises(ValidationError, match="30-day"):
        restore(removed_document, users["med_admin"])
    assert tasks.purge_deleted_documents(now=now) == {"purged": 1, "failed": 0}
    assert tasks.purge_deleted_documents(now=now) == {"purged": 0, "failed": 0}


def test_shared_storage_key_is_not_erased(removed_document, users):
    now = timezone.now()
    age(removed_document, now)
    stored = removed_document.files.get().file
    other = Document.objects.create(title="Unrelated document", office=users["sup"].office)
    DocumentFile.objects.create(document=other, file=stored.name, original_name="shared.txt")
    assert tasks.purge_deleted_documents(now=now)["purged"] == 1
    assert stored.storage.exists(stored.name)
    assert Document.objects.filter(pk=other.pk).exists()


def test_tracking_original_and_completed_history_survive_purge(removed_document, users):
    now = timezone.now()
    age(removed_document, now)
    record = TrackingRecord.objects.create(
        subject="Completed Tracking history", originating_office=users["med"].office,
        current_office=users["med"].office, created_by=users["med"],
        status=Status.COMPLETED, is_archived=True,
    )
    original = Attachment.objects.create(record=record, file=SimpleUploadedFile("original.txt", b"Tracking original"),
                                          original_name="original.txt", uploaded_by=users["med"])
    Document.objects.filter(pk=removed_document.pk).update(tracking_record=record)
    assert tasks.purge_deleted_documents(now=now)["purged"] == 1
    record.refresh_from_db()
    assert record.is_archived and record.status == Status.COMPLETED
    assert original.file.storage.exists(original.file.name)
    assert not TrackingRecord.objects.pending_filing().filter(pk=record.pk).exists()


def test_purged_document_extraction_exits_safely(removed_document):
    now = timezone.now()
    age(removed_document, now)
    tasks.purge_deleted_documents(now=now)
    result = tasks.extract_document_task(removed_document.pk)
    assert result["status"] == "SKIPPED" and result["characters"] == 0


def test_expired_screen_has_no_restore_form_or_available_choice(removed_document, users, client):
    age(removed_document, timezone.now())
    client.force_login(users["med_admin"])
    response = client.get(reverse("documents:deletion_review", args=[removed_document.pk]))
    assert response.status_code == 200
    html = response.content.decode()
    assert "Awaiting permanent deletion" in html
    assert "data-deletion-review-form" not in html and 'value="RESTORE"' not in html
    assert response.context["document"].deletion_purge_at is not None


def test_disappearing_record_during_review_post_returns_clear_not_found(removed_document, users, client):
    client.force_login(users["med_admin"])
    url = reverse("documents:deletion_review", args=[removed_document.pk])
    token = client.get(url).context["form"].initial["state_token"]
    with patch("apps.documents.views.services.review_document_deletion", side_effect=Document.DoesNotExist):
        response = client.post(url, {"decision": "RESTORE", "state_token": token, "reason": "Required again",
                                     "next_review_date": (timezone.localdate() + timedelta(days=1)).isoformat()})
    assert response.status_code == 404


def test_mandatory_purge_audit_failure_preserves_files_and_row(removed_document):
    now = timezone.now()
    age(removed_document, now)
    stored = removed_document.files.get().file
    with patch.object(AuditLog.objects, "create", side_effect=RuntimeError("Audit unavailable")):
        assert tasks.purge_deleted_documents(now=now) == {"purged": 0, "failed": 1}
    assert stored.storage.exists(stored.name)
    assert Document.objects.filter(pk=removed_document.pk).exists()


def test_shared_tracking_storage_key_is_preserved(removed_document, users):
    now = timezone.now()
    age(removed_document, now)
    stored = removed_document.files.get().file
    record = TrackingRecord.objects.create(subject="Independent original", originating_office=users["med"].office,
                                            created_by=users["med"])
    Attachment.objects.create(record=record, file=stored.name, original_name="original.txt", uploaded_by=users["med"])
    assert tasks.purge_deleted_documents(now=now)["purged"] == 1
    assert stored.storage.exists(stored.name)


def test_background_schedule_is_registered_once(settings):
    from django.conf import settings as configured

    if "django_q" not in configured.INSTALLED_APPS:
        pytest.skip("Requires the background-enabled regression run.")
    from django_q.models import Schedule

    settings.ENABLE_BACKGROUND_TASKS = True
    call_command("ensure_schedules", verbosity=0)
    call_command("ensure_schedules", verbosity=0)
    assert Schedule.objects.filter(name="deleted-document-purging", func="apps.documents.tasks.purge_deleted_documents").count() == 1


def test_failed_oldest_cleanup_does_not_starve_newer_documents(removed_document, users):
    now = timezone.now()
    age(removed_document, now, days=31)
    other = Document.objects.create(title="Newer expired document", office=users["med"].office,
                                    is_active=False, deleted_at=now - timedelta(days=30))
    stored = removed_document.files.get().file
    with patch.object(stored.storage, "delete", side_effect=OSError("One damaged object")):
        assert tasks.purge_deleted_documents(now=now, limit=1) == {"purged": 0, "failed": 1}
        assert tasks.purge_deleted_documents(now=now, limit=1) == {"purged": 1, "failed": 0}
    assert not Document.objects.filter(pk=other.pk).exists()
    removed_document.refresh_from_db()
    assert removed_document.purge_attempted_at == now


@pytest.mark.django_db(transaction=True)
def test_two_cleanup_workers_only_purge_once(users, settings, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import close_old_connections, connections

    settings.MEDIA_ROOT = tmp_path
    now = timezone.now()
    document = Document.objects.create(title="Concurrent permanent cleanup", office=users["med"].office,
                                        is_active=False, deleted_at=now - timedelta(days=30))
    DocumentFile.objects.create(document=document, file=SimpleUploadedFile("concurrent.txt", b"Content"),
                                original_name="concurrent.txt")
    ready = Barrier(2)

    def purge():
        close_old_connections()
        try:
            ready.wait(timeout=10)
            return tasks.purge_deleted_documents(now=now)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = [future.result(timeout=20) for future in [workers.submit(purge), workers.submit(purge)]]
    assert sum(item["purged"] for item in results) == 1
    assert sum(item["failed"] for item in results) == 0
    assert AuditLog.objects.filter(target_id=str(document.pk), extra__permanent_repository_deletion=True).count() == 1

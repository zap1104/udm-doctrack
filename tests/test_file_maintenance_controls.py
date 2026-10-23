"""Operator commands, recoverable Repository content and audit administration."""
from datetime import timedelta
from io import StringIO

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.core.file_lifecycle import retire_excess_versions
from apps.core.file_maintenance import reconcile_file_retention
from apps.core.models import StorageCleanupIntent
from apps.documents.models import Document, DocumentFile, DocumentFileVersion

pytestmark = pytest.mark.django_db


def test_commands_default_to_bounded_inert_dry_run():
    for command in ["maintain_file_uploads", "reconcile_file_retention"]:
        output = StringIO()
        with CaptureQueriesContext(connection) as queries:
            call_command(command, stdout=output)
        assert "Dry run:" in output.getvalue()
        assert not any(row["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for row in queries)


@pytest.mark.parametrize("limit", [0, -1, 1001, True])
def test_reconciliation_rejects_unbounded_batch(limit):
    with pytest.raises(ValidationError):
        reconcile_file_retention(limit=limit)


@pytest.mark.parametrize("days", [None, 29])
def test_soft_deleted_repository_history_is_not_retired_during_recovery(users, days):
    document = Document.objects.create(title="Recoverable history", office=users["med"].office,
        year=2026, is_active=False, deleted_at=timezone.now() - timedelta(days=days) if days else None)
    root = DocumentFile.objects.create(document=document, file="private/legacy.txt", original_name="legacy.txt")
    for number in range(2, 8):
        DocumentFileVersion.objects.create(document_file=root, version_number=number,
            file=root.file.name, original_name=root.original_name)
    assert reconcile_file_retention(dry_run=False)["retired"] == 0
    assert retire_excess_versions(root) == []
    assert not root.versions.filter(retired_at__isnull=False).exists()


def test_cleanup_admin_is_read_only_and_office_scoped(client, users):
    own = StorageCleanupIntent.objects.create(storage_identity="", storage_key="private/own", reason="Blocked", state="blocked",
        office_id_snapshot=users["med_admin"].office_id)
    other = StorageCleanupIntent.objects.create(storage_identity="", storage_key="private/other", reason="Other", state="blocked",
        office_id_snapshot=users["sup_admin"].office_id)
    client.force_login(users["admin"])
    assert client.get(reverse("admin:core_storagecleanupintent_change", args=[own.pk])).status_code == 200
    assert client.post(reverse("admin:core_storagecleanupintent_change", args=[own.pk]), {"state": "complete"}).status_code == 403
    client.force_login(users["med_admin"])
    # Some deployments intentionally reserve Django administration for system
    # administrators. In either case, direct cross-office rows remain refused.
    assert client.get(reverse("admin:core_storagecleanupintent_change", args=[other.pk])).status_code in {302, 403, 404}


def test_background_maintenance_schedule_is_registered_once(settings):
    if "django_q" not in settings.INSTALLED_APPS:
        pytest.skip("Requires the background-enabled gate")
    from django_q.models import Schedule
    settings.ENABLE_BACKGROUND_TASKS = True
    call_command("ensure_schedules", verbosity=0)
    call_command("ensure_schedules", verbosity=0)
    assert Schedule.objects.filter(name="private-file-maintenance", func="apps.core.file_maintenance.cleanup_file_uploads").count() == 1

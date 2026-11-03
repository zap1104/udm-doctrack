"""Demo creation uses real metadata authorization and reports incomplete seeds."""

from io import StringIO

import pytest
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.accounts.models import User
from apps.core.management.commands import seed_demo
from apps.documents.models import Document
from apps.tracking.models import TrackingRecord

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("phase", ["archive", "bulk_archive"])
def test_demo_archive_tagging_uses_authorized_administrator(users, offices, memo_type, monkeypatch, phase):
    """A Records staff uploader cannot edit another office's repository tags."""
    monkeypatch.setattr(seed_demo, "ARCHIVE_DOCUMENTS", [
        ("SUP", "MEMO", "Seed authorization sample", 2026, "Sample description", ["seed sample"]),
    ])
    monkeypatch.setattr(seed_demo, "BULK_ARCHIVE_TITLES", [
        ("SUP", "MEMO", "Seed authorization sample", ["seed sample"]),
    ])
    command = seed_demo.Command(stdout=StringIO())
    seed_users = {"admin": users["admin"], "records": users["med"]}
    assert users["med"].office_id != offices["SUP"].pk

    if phase == "archive":
        command._archive(offices, {"MEMO": memo_type}, seed_users)
    else:
        command._bulk_archive(offices, {"MEMO": memo_type}, seed_users, months=12)

    documents = list(Document.objects.all())
    assert documents
    for document in documents:
        assert document.office_id == offices["SUP"].pk
        assert document.uploaded_by_id == users["med"].pk  # Preserve original uploader provenance.
        assert list(document.tags.values_list("name", flat=True)) == ["seed sample"]
        assert set(document.tag_reviews.values_list("actor_id", flat=True)) == {users["admin"].pk}


def test_seed_failure_preserves_accounts_and_returns_command_error(settings, monkeypatch):
    settings.ALLOW_DEMO_SEED = True
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    monkeypatch.setattr(seed_demo.Command, "_records", lambda *args: None)

    def fail_archive(*args):
        raise PermissionDenied("Injected metadata permission failure")

    monkeypatch.setattr(seed_demo.Command, "_archive", fail_archive)
    stdout, stderr = StringIO(), StringIO()

    with pytest.raises(CommandError, match="Sample records could not be created"):
        call_command("seed_demo", records=0, stdout=stdout, stderr=stderr)

    assert User.objects.filter(username="admin", is_active=True, role=User.Role.SYSTEM_ADMIN).exists()
    assert "Demo data ready" not in stdout.getvalue()
    assert "Your accounts and master data are fine" in stderr.getvalue()


def test_seed_completes_narrative_and_generated_samples(settings):
    settings.ALLOW_DEMO_SEED = True
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    stdout, stderr = StringIO(), StringIO()

    call_command("seed_demo", records=12, months=2, stdout=stdout, stderr=stderr)

    assert stderr.getvalue() == ""
    assert TrackingRecord.objects.count() == 12
    assert set(TrackingRecord.objects.values_list("subject", flat=True)) >= {row[3] for row in seed_demo.SAMPLE_RECORDS}
    assert set(Document.objects.values_list("title", flat=True)) >= {row[2] for row in seed_demo.ARCHIVE_DOCUMENTS}
    assert "Generated archive:" in stdout.getvalue()
    assert "Demo data ready" in stdout.getvalue()

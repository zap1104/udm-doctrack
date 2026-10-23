"""Private expiry/discard never consumes or publicly exposes unfinished data."""
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.core.models import UploadDraft
from tests.test_file_retention_cleanup import maintenance
from tests.test_upload_draft_races import parallel, stage

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def storage(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def test_expiry_closes_manifest_and_releases_bytes_only_after_cleanup(users):
    worker = maintenance()
    draft, _ = stage(users["med"])
    file = draft.files.get()
    UploadDraft.objects.filter(pk=draft.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
    assert worker.maintain_file_uploads()["expired"] == 0
    result = worker.maintain_file_uploads(dry_run=False)
    draft.refresh_from_db()
    file.refresh_from_db()
    assert result["expired"] == 1 and draft.state == "expired" and not file.file.name


def test_recovery_list_is_owner_only_and_full_discard_is_confirmed(client, users):
    maintenance()
    draft, _ = stage(users["med"])
    client.force_login(users["med"])
    url = reverse("core:upload_drafts")
    assert str(draft.pk) in client.get(url).content.decode()
    discard = reverse("core:upload_draft_discard", args=[draft.pk])
    assert client.post(discard, {"revision": draft.revision}).status_code == 400
    assert client.post(discard, {"revision": draft.revision, "confirm": "on"}).status_code == 302
    draft.refresh_from_db()
    assert draft.state == "discarded"
    client.force_login(users["admin"])
    assert str(draft.pk) not in client.get(url).content.decode()
    assert client.post(discard, {"revision": draft.revision, "confirm": "on"}).status_code in {403, 404}


@pytest.mark.django_db(transaction=True)
def test_expiry_racing_consumption_cannot_delete_a_committed_permanent_copy(users):
    from apps.core.upload_drafts import finalize_upload
    from apps.tracking.models import TrackingRecord
    worker = maintenance()
    draft, context = stage(users["med"])
    future = timezone.now() + timedelta(days=2)
    results = parallel(lambda: finalize_upload(draft.pk, user=users["med"], context=context,
        cleaned_data={"subject": "Expiry race", "instructions": "Review"}, expected_revision=draft.revision),
        lambda: worker.maintain_file_uploads(now=future, dry_run=False))
    assert len(results) == 2
    draft.refresh_from_db()
    assert draft.state in {"consumed", "expired"}
    if TrackingRecord.objects.exists():
        file = TrackingRecord.objects.get().attachments.get()
        with file.file.open("rb") as stream:
            assert stream.read() == b"Saved file"
        assert draft.state == "consumed"

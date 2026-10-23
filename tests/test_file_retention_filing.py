"""Filing carries audit metadata without reopening retired source binaries."""
import hashlib
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.core.file_lifecycle import discard_draft_group
from apps.documents.file_versions import copy_tracking_history
from apps.documents.models import Document
from tests.test_file_retention_services import history

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def storage(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.mark.parametrize("external", [False, True])
def test_copy_only_available_versions_and_keep_complete_audit_metadata(users, external):
    record, actor, versions = history(users, external, 7)
    for source in versions[:2]:
        source.file.storage.delete(source.file.name)
    document = Document.objects.create(title="Filing retained versions", year=2026, office=actor.office,
        collection="EXTERNAL" if external else "OVPA")
    copied = []
    roots = copy_tracking_history(record, document, copied)
    assert len(roots) == document.files.count() == 1 and len(copied) == 5
    snapshots = list(roots[0].versions.all())
    assert [row.version_number for row in snapshots] == list(range(1, 8))
    assert [row.version_number for row in snapshots if row.available_content] == [3, 4, 5, 6, 7]
    assert not snapshots[0].file.name and snapshots[0].retired_at
    for source, snapshot in zip(versions, snapshots, strict=True):
        assert (source.checksum, source.created_at, source.uploader_office_snapshot) == (snapshot.checksum, snapshot.uploaded_at, snapshot.uploader_office_snapshot)
        if snapshot.available_content:
            with snapshot.file.open("rb") as stream:
                assert hashlib.sha256(stream.read()).hexdigest() == source.checksum
    assert roots[0].file.name == snapshots[-1].file.name


def test_discarded_groups_are_not_filed(users):
    record, actor, versions = history(users, False, 1)
    discard_draft_group(record, versions[0].file_group_id, user=actor, reason="Wrong file", expected_version=1)
    document = Document.objects.create(title="No discarded file", year=2026, office=actor.office)
    assert copy_tracking_history(record, document, []) == []


@pytest.mark.parametrize("external", [False, True])
def test_filing_rejects_bytes_from_a_different_recorded_backend(users, external):
    from apps.core.file_lifecycle_models import lifecycle_write
    record, actor, versions = history(users, external, 1)
    source = versions[0]
    with lifecycle_write():
        type(source).objects.filter(pk=source.pk).update(storage_identity="different-backend")
    document = Document.objects.create(title="Do not guess backend", year=2026, office=actor.office,
        collection="EXTERNAL" if external else "OVPA")
    with patch.object(source.file.storage, "open", wraps=source.file.storage.open) as opening:
        with pytest.raises(ValidationError, match="storage|backend"), transaction.atomic():
            copy_tracking_history(record, document, [])
        opening.assert_not_called()
    assert not document.files.exists()


def test_corrupt_retained_file_and_missing_metadata_still_refuse_filing(users):
    record, actor, versions = history(users, False, 7)
    document = Document.objects.create(title="Corrupt source", year=2026, office=actor.office)
    storage = versions[2].file.storage
    original = storage.open
    def missing(name, *args, **kwargs):
        if name == versions[2].file.name:
            raise OSError("Retained file missing")
        return original(name, *args, **kwargs)
    with patch.object(storage, "open", missing), pytest.raises(ValidationError), transaction.atomic():
        copy_tracking_history(record, document, [])
    versions[0].delete()
    with pytest.raises(ValidationError, match="incomplete"), transaction.atomic():
        copy_tracking_history(record, document, [])

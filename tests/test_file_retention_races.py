"""Actual PostgreSQL serialization for discard, versions and dispatch."""
import pytest
from django.core.exceptions import ValidationError

from apps.core.file_lifecycle import discard_draft_group
from apps.tracking.file_versions import upload_attachment_version
from apps.tracking.services import route_record
from tests.test_file_discard import draft_file
from tests.test_file_retention_services import upload
from tests.test_upload_draft_races import parallel

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def storage(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.mark.parametrize("action", ["version", "dispatch"])
def test_discard_serializes_against_upload_and_first_dispatch(users, offices, action):
    actor, record, attached = draft_file(users)
    def change():
        if action == "version":
            return upload_attachment_version(record, attached.file_group_id, upload(2), user=actor,
                expected_version=1, change_summary="Signed")
        return route_record(record, [offices["SUP"]], user=actor)
    results = parallel(lambda: discard_draft_group(record, attached.file_group_id, user=actor,
        reason="Wrong file", expected_version=1), change)
    attached.file_group.refresh_from_db()
    record.refresh_from_db()
    if action == "version":
        assert sum(isinstance(row, ValidationError) for row in results) == 1
        assert record.attachments.count() == (1 if attached.file_group.discarded_at else 2)
    elif attached.file_group.discarded_at:
        # Dispatch after a completed discard may proceed with the remaining
        # draft metadata; it cannot revive the discarded content.
        assert not record.attachments.filter(retired_at__isnull=True).exists()
    else:
        assert sum(isinstance(row, ValidationError) for row in results) == 1
        assert record.routing_steps.exists()

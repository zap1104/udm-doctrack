"""Normal lists are bounded independently of unavailable audit depth."""
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.core.file_lifecycle import discard_draft_group
from apps.tracking.version_views import tracking_file_rows
from tests.test_file_retention_services import history

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def storage(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def test_normal_list_excludes_discarded_and_audit_only_versions_without_reading_bytes(users):
    record, actor, versions = history(users, False, 7)
    with patch.object(versions[0].file.storage, "open", side_effect=AssertionError("No body reads on list")):
        rows = tracking_file_rows(record, actor)
    assert len(rows) == 1
    assert [row["item"].version_number for row in rows[0]["history"]] == [6, 5, 4, 3]
    assert rows[0]["audit_count"] == 2
    discard_draft_group(record, versions[0].file_group_id, user=actor, reason="Accident", expected_version=7)
    assert tracking_file_rows(record, actor) == []


def test_audit_history_is_parent_authorized_and_paginated(client, users):
    from django.urls import reverse

    from apps.core.models import FileLifecycleEvent
    record, actor, versions = history(users, False, 7)
    group = versions[0].file_group
    FileLifecycleEvent.objects.bulk_create([FileLifecycleEvent(resource_kind="tracking", parent_id=record.pk,
        group_id=group.pk, action="access", reason="Audit fixture") for _ in range(1000)])
    url = reverse("core:file_history", args=["tracking", group.pk])
    client.force_login(actor)
    with CaptureQueriesContext(connection) as queries:
        response = client.get(url)
    assert response.status_code == 200
    assert len(response.context["history"]["entries"]) <= 25
    assert response.context["history"]["next_cursor"]
    assert len(queries) < 30
    client.force_login(users["sup"])
    assert client.get(url).status_code in {403, 404}

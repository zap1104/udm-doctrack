"""File meaning is visible without exposing retired bytes or filed mutations."""
import pytest
from django.template.loader import render_to_string

from apps.tracking.version_views import tracking_file_rows
from tests.test_file_retention_services import history

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def storage(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.mark.parametrize("external", [False, True])
def test_compact_current_file_card_has_four_older_versions_and_separate_audit(users, external):
    record, actor, versions = history(users, external, 7)
    rows = tracking_file_rows(record, actor, external=external)
    html = render_to_string("partials/_file_versions.html", {"file_rows": rows})
    assert "V7 · Current" in html
    assert "Older versions (4)" in html and "File audit history" in html
    assert '>TXT</span>' in html
    assert "Discard" in html and rows[0]["discard_url"] in html
    assert f'file-version-{"external" if external else "tracking"}-{versions[-1].pk}' in html
    assert "V1 ·" not in html and "V2 ·" not in html


def test_discard_is_not_offered_after_any_dispatch_even_with_incorrect_draft_status(users, offices):
    from apps.tracking.models import Status
    from apps.tracking.services import route_record
    record, actor, _ = history(users, False, 1)
    route_record(record, [offices["SUP"]], user=actor)
    type(record).objects.filter(pk=record.pk).update(status=Status.DRAFT)
    record.refresh_from_db()
    assert not tracking_file_rows(record, users["admin"])[0]["can_discard"]


def test_saved_upload_preview_is_labeled_as_saved_upload_not_version_one(client, users):
    from apps.core.file_access import file_url
    from tests.test_upload_draft_races import stage
    draft, _ = stage(users["med"])
    client.force_login(users["med"])
    response = client.get(file_url("upload", draft.files.get().pk, users["med"]))
    assert response.status_code == 200 and "Saved upload" in response.content.decode()
    assert "Viewing Version 1" not in response.content.decode()


def test_creation_office_is_clear_and_fixed_after_files_are_bound(client, users):
    from tests.test_upload_draft_races import stage
    draft, _ = stage(users["admin"], workflow="repository-upload")
    client.force_login(users["admin"])
    response = client.get("/documents/upload/", {"upload_draft": draft.pk})
    assert response.status_code == 200
    assert response.context["form"].fields["office"].disabled
    assert "saved form" in response.context["form"].fields["office"].help_text

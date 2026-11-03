"""External repository history uses the external permission area for links."""

import pytest

from apps.accounts.permission_models import CapabilityRestriction, UserPermissionPolicy
from apps.core.file_history import history_url
from apps.documents.models import Document, DocumentFile

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "area,action,want_preview,want_download",
    [
        ("repository", "view_content", True, True),
        ("repository", "download", True, True),
        ("external_repository", "view_content", False, False),
        ("external_repository", "download", True, False),
    ],
)
def test_external_history_links_use_external_repository_capabilities(
    client, users, area, action, want_preview, want_download,
):
    actor = users["admin"]
    document = Document.objects.create(
        title="External retained file", collection="EXTERNAL", office=users["med"].office,
        uploaded_by=actor, year=2026,
    )
    group = DocumentFile.objects.create(
        document=document, file="documents/external-history.txt", original_name="external-history.txt",
        uploaded_by=actor,
    )
    policy = UserPermissionPolicy.objects.create(user=actor)
    CapabilityRestriction.objects.create(policy=policy, area=area, action=action)
    client.force_verified_login(actor)

    response = client.get(history_url(group))

    assert response.status_code == 200
    entries = response.context["history"]["entries"]
    assert len(entries) == 1
    assert bool(entries[0]["preview_url"]) is want_preview
    assert bool(entries[0]["download_url"]) is want_download

"""Repository cards respect file-content restrictions after filtering/refetch."""

import pytest
from django.urls import reverse

from apps.accounts.permission_models import CapabilityRestriction, UserPermissionPolicy
from apps.documents.models import Document

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("params", [{}, {"q": "Visible external record"}])
@pytest.mark.parametrize("blocked_area,want_body", [("external_repository", False), ("repository", True)])
def test_external_repository_card_uses_its_own_content_policy(client, users, params, blocked_area, want_body):
    actor = users["med"]
    Document.objects.create(
        title="Visible external record", collection="EXTERNAL", office=actor.office,
        uploaded_by=actor, year=2026, description="", ocr_text="secret-external-ocr-evidence",
    )
    Document.objects.create(
        title="Inaccessible external record", collection="EXTERNAL", office=users["sup"].office,
        uploaded_by=users["sup"], year=2026, ocr_text="unshared-external-body",
    )
    Document.objects.create(
        title="Separate OVPA record", collection="OVPA", office=actor.office,
        uploaded_by=actor, year=2026, ocr_text="separate-ovpa-body",
    )
    policy = UserPermissionPolicy.objects.create(user=actor)
    CapabilityRestriction.objects.create(policy=policy, area=blocked_area, action="view_content")
    client.force_verified_login(actor)

    response = client.get(reverse("documents:external_repository"), params)

    assert response.status_code == 200
    assert b"Visible external record" in response.content
    assert (b"secret-external-ocr-evidence" in response.content) is want_body
    assert b"Inaccessible external record" not in response.content
    assert b"unshared-external-body" not in response.content
    assert b"Separate OVPA record" not in response.content
    assert b"separate-ovpa-body" not in response.content


@pytest.mark.parametrize("params", [{}, {"q": "Visible metadata record"}])
def test_external_repository_preserves_metadata_description_when_content_is_blocked(client, users, params):
    actor = users["med"]
    Document.objects.create(
        title="Visible metadata record", collection="EXTERNAL", office=actor.office,
        uploaded_by=actor, year=2026, description="Allowed metadata description",
        ocr_text="secret-external-ocr-evidence",
    )
    policy = UserPermissionPolicy.objects.create(user=actor)
    CapabilityRestriction.objects.create(policy=policy, area="external_repository", action="view_content")
    client.force_verified_login(actor)

    response = client.get(reverse("documents:external_repository"), params)

    assert response.status_code == 200
    assert b"Allowed metadata description" in response.content
    assert b"secret-external-ocr-evidence" not in response.content

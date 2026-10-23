"""Retired optional screens must not leave links or reachable write actions."""

import pytest
from django.urls import reverse

from apps.documents.models import SavedDocumentView

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("role", ["admin", "med_admin", "med", "viewer"])
def test_repository_navigation_has_no_retired_sections(client, users, role):
    client.force_login(users[role])
    response = client.get(reverse("documents:repository"))
    assert response.status_code == 200
    html = response.content.decode()
    for retired in (
        "My repository views",
        "Local topic models",
        "Save these filters",
        "/documents/views/",
        "/documents/intelligence/models/",
    ):
        assert retired not in html
    assert (reverse("documents:upload") in html) == users[role].can_start_work
    assert reverse("search:index") in html


@pytest.mark.parametrize("role", ["admin", "med_admin"])
def test_external_repository_has_no_saved_filter_control(client, users, role):
    client.force_login(users[role])
    response = client.get(reverse("documents:external_repository"))
    assert response.status_code == 200
    assert "Save these filters" not in response.content.decode()


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("page", ["list", "open", "delete", "models"])
def test_retired_page_urls_refuse_reads_and_writes_without_deleting_history(client, users, method, page):
    saved = SavedDocumentView.objects.create(
        owner=users["admin"], name="Existing private history", collection="OVPA", filters={}
    )
    paths = {
        "list": "/documents/views/",
        "open": f"/documents/views/{saved.pk}/",
        "delete": f"/documents/views/{saved.pk}/delete/",
        "models": "/documents/intelligence/models/",
    }
    client.force_login(users["admin"])
    response = getattr(client, method)(paths[page])
    assert response.status_code == 404
    assert SavedDocumentView.objects.filter(pk=saved.pk).exists()


def test_vocabulary_remains_available_without_a_removed_model_page_link(client, users):
    client.force_login(users["admin"])
    response = client.get(reverse("documents:tag_vocabulary"))
    assert response.status_code == 200
    assert "Local topic models" not in response.content.decode()
    assert "/documents/intelligence/models/" not in response.content.decode()

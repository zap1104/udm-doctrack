from importlib.util import find_spec

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import QueryDict

from apps.accounts.models import User
from apps.documents.models import Document

pytestmark = pytest.mark.django_db


def api():
    assert find_spec("apps.documents.saved_views"), "Private saved repository filters are missing"
    from apps.documents import saved_views

    return saved_views


def test_saved_views_validate_collection_filters_and_owner(users):
    service = api()
    doc = Document.objects.create(
        title="Alpha", year=2026, document_date="2026-10-01", office=users["med"].office, uploaded_by=users["med"]
    )
    view = service.create_saved_view(
        user=users["med"],
        name="<b>Personal view</b>",
        collection="OVPA",
        params=QueryDict("q=Alpha&date_from=2026-01-01"),
    )
    url = service.open_saved_view(view.pk, user=users["med"])
    assert url.startswith("/documents/?") and "q=Alpha" in url and "date_from=2026-01-01" in url
    with pytest.raises(PermissionDenied):
        service.open_saved_view(view.pk, user=users["hr"])
    for query in ("office=999999", "q=one&q=two", "owner=1", "date_from=2026-10-02&date_to=2026-10-01"):
        with pytest.raises(ValidationError):
            service.create_saved_view(user=users["med"], name="Invalid", collection="OVPA", params=QueryDict(query))
    assert Document.objects.count() == 1
    assert not doc.files.exists()


def test_saved_office_filter_revalidates_after_transfer(users):
    service = api()
    doc = Document.objects.create(
        title="Office file", year=2026, office=users["med"].office, uploaded_by=users["med_admin"]
    )
    view = service.create_saved_view(
        user=users["med_admin"], name="Own office", collection="OVPA", params=QueryDict(f"office={doc.office_id}")
    )
    User.objects.filter(pk=users["med_admin"].pk).update(office=users["hr"].office)
    with pytest.raises(ValidationError):
        service.open_saved_view(view.pk, user=users["med_admin"])


def test_external_saved_view_and_rendered_labels_stay_private(client, users):
    from django.urls import reverse

    from apps.documents.models import SavedDocumentView

    service = api()
    Document.objects.create(
        title="External alpha",
        year=2026,
        collection="EXTERNAL",
        office=users["med"].office,
        uploaded_by=users["med_admin"],
    )
    view = service.create_saved_view(
        user=users["med_admin"],
        name="<script>private-label</script>",
        collection="EXTERNAL",
        params=QueryDict("q=alpha"),
    )
    assert service.open_saved_view(view.pk, user=users["med_admin"]).startswith("/documents/external/?q=alpha")
    client.force_login(users["med_admin"])
    page = client.get(reverse("documents:saved_views"))
    assert page.status_code == 200
    assert "&lt;script&gt;private-label&lt;/script&gt;" in page.content.decode()
    assert "<script>private-label</script>" not in page.content.decode()
    client.force_login(users["hr"])
    assert client.get(reverse("documents:open_saved_view", args=[view.pk])).status_code == 404
    assert client.post(reverse("documents:delete_saved_view", args=[view.pk])).status_code == 404
    assert SavedDocumentView.objects.filter(pk=view.pk).exists()

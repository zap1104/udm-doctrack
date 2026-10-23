"""External creation uses server-validated office context, never a third office input."""

from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import pytest
from django.http import QueryDict
from django.urls import reverse

from apps.tracking.external_models import ExternalOffice, ExternalTrackingRecord

pytestmark = pytest.mark.django_db


class Links(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.hrefs = []
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            values = dict(attrs)
            if values.get("href"):
                self.hrefs.append(values["href"])


@pytest.fixture
def counterpart(offices):
    return ExternalOffice.objects.create(name="Shared external college", code="COL", managing_office=offices["SUP"])


@pytest.mark.parametrize("route", ["tracking:external_create", "tracking:external_office_create"])
def test_external_creation_has_no_managing_office_input(client, users, route):
    client.force_login(users["med_admin"])
    response = client.get(reverse(route))
    assert response.status_code == 200
    assert "managing_office" not in response.context["form"].fields
    assert b"managing office" not in response.content.lower()


def test_first_page_can_select_another_offices_external_entry(client, users, offices, counterpart):
    client.force_login(users["med_admin"])
    response = client.post(reverse("tracking:external_create"), {
        "title": "Shared counterpart", "from_endpoint": f"internal:{offices['MED'].pk}",
        "to_endpoint": f"external:{counterpart.pk}", "managing_office": offices["SUP"].pk,
    })
    assert response.status_code == 302
    record = ExternalTrackingRecord.objects.get(title="Shared counterpart")
    assert record.managing_office_id == offices["MED"].pk
    assert record.status == "DRAFT" and not record.routing_steps.exists()
    review = client.get(response.url)
    assert review.context["form"]["to_endpoint"].value() == f"external:{counterpart.pk}"


def test_system_admin_without_office_uses_selected_context(client, users, offices, counterpart):
    admin = users["admin"]
    admin.office = None
    admin.save(update_fields=["office"])
    client.force_login(admin)
    url = reverse("tracking:external_create") + f"?office={offices['HR'].pk}"
    response = client.post(url, {
        "title": "Explicit per-request context", "from_endpoint": f"external:{counterpart.pk}",
        "to_endpoint": f"internal:{offices['MED'].pk}", "managing_office": offices["SUP"].pk,
    })
    assert response.status_code == 302
    record = ExternalTrackingRecord.objects.get(title="Explicit per-request context")
    assert record.managing_office_id == offices["HR"].pk
    assert parse_qs(urlsplit(response.url).query)["office"] == [str(offices["HR"].pk)]
    assert not record.routing_steps.exists()


@pytest.mark.parametrize("route", ["tracking:external_create", "tracking:external_office_create"])
def test_unassigned_system_admin_needs_office_context(client, users, route):
    admin = users["admin"]
    admin.office = None
    admin.save(update_fields=["office"])
    client.force_login(admin)
    response = client.get(reverse(route))
    assert response.status_code == 400
    assert b"select an internal office" in response.content.lower()
    assert not ExternalTrackingRecord.objects.exists()


@pytest.mark.parametrize("bad", ["unknown", "999999", "repeated", "foreign", "inactive"])
def test_invalid_creation_context_never_falls_back(client, users, offices, counterpart, bad):
    client.force_login(users["med_admin"])
    own = offices["MED"].pk
    value = {"unknown": "broken", "999999": "999999", "foreign": str(offices["SUP"].pk)}.get(bad)
    if bad == "inactive":
        offices["MED"].is_active = False
        offices["MED"].save(update_fields=["is_active"])
        value = str(own)
    suffix = f"office={own}&office={own}" if bad == "repeated" else f"office={value}"
    response = client.post(reverse("tracking:external_create") + "?" + suffix, {
        "title": "Invalid context must not save", "from_endpoint": f"external:{counterpart.pk}",
        "to_endpoint": f"internal:{offices['HR'].pk}",
    })
    assert response.status_code == 400
    assert not ExternalTrackingRecord.objects.exists()


def test_selected_office_is_carried_through_external_navigation(client, users, offices):
    client.force_login(users["admin"])
    selected = str(offices["MED"].pk)
    response = client.get(reverse("tracking:external_list"), {"office": selected})
    assert response.status_code == 200
    links = Links(response.content.decode()).hrefs
    for route in ["tracking:external_create", "tracking:external_offices", "documents:external_repository"]:
        candidates = [href for href in links if urlsplit(href).path == reverse(route)]
        assert any(parse_qs(urlsplit(href).query).get("office") == [selected] for href in candidates), route
    directory = client.get(reverse("tracking:external_offices"), {"office": selected})
    links = Links(directory.content.decode()).hrefs
    assert any(urlsplit(href).path == reverse("tracking:external_office_create") and
               parse_qs(urlsplit(href).query).get("office") == [selected] for href in links)


def test_shared_directory_does_not_grant_edit_controls(client, users, offices, counterpart):
    own = ExternalOffice.objects.create(name="Own entry", managing_office=offices["MED"])
    client.force_login(users["med_admin"])
    response = client.get(reverse("tracking:external_offices"))
    assert response.status_code == 200
    assert counterpart.name.encode() in response.content
    links = Links(response.content.decode()).hrefs
    assert reverse("tracking:external_office_edit", args=[own.pk]) in links
    assert reverse("tracking:external_office_edit", args=[counterpart.pk]) not in links
    denied = client.post(reverse("tracking:external_office_edit", args=[counterpart.pk]), {
        "name": "Forged directory edit", "revision": counterpart.revision, "is_active": "on",
    })
    assert denied.status_code in {403, 404}
    counterpart.refresh_from_db()
    assert counterpart.name == "Shared external college"


def test_duplicate_external_choices_have_distinct_labels(client, users, offices):
    first = ExternalOffice.objects.create(name="Same college", managing_office=offices["MED"])
    second = ExternalOffice.objects.create(name="Same college", managing_office=offices["SUP"])
    client.force_login(users["med_admin"])
    response = client.get(reverse("tracking:external_create"))
    external_group = dict(response.context["form"].fields["to_endpoint"].choices)["External UDM offices"]
    labels = dict(external_group)
    assert labels[f"external:{first.pk}"] != labels[f"external:{second.pk}"]


def test_repeated_legacy_owner_cannot_override_context(client, users, offices, counterpart):
    client.force_login(users["med_admin"])
    payload = QueryDict("", mutable=True)
    payload.update(title="Legacy owner cannot transfer", from_endpoint=f"external:{counterpart.pk}",
                   to_endpoint=f"internal:{offices['HR'].pk}")
    payload.setlist("managing_office", [str(offices["SUP"].pk), str(offices["HR"].pk)])
    response = client.post(reverse("tracking:external_create"), payload)
    assert response.status_code == 302
    assert ExternalTrackingRecord.objects.get(title="Legacy owner cannot transfer").managing_office_id == offices["MED"].pk


@pytest.mark.parametrize("assigned", [True, False])
def test_repository_upload_link_preserves_selected_filing_context(client, users, offices, assigned, counterpart, settings, tmp_path):
    from django.core.files.uploadedfile import SimpleUploadedFile

    settings.MEDIA_ROOT = tmp_path
    admin = users["admin"]
    admin.office = offices["MED"] if assigned else None
    admin.save(update_fields=["office"])
    client.force_login(admin)
    selected = str(offices["SUP"].pk)
    response = client.get(reverse("documents:external_repository"), {"office": selected})
    links = Links(response.content.decode()).hrefs
    href = next(link for link in links if urlsplit(link).path == reverse("documents:external_upload"))
    assert parse_qs(urlsplit(href).query).get("office") == [selected]
    upload = client.get(href)
    assert upload.status_code == 200
    assert upload.context["form"].recording_office.pk == offices["SUP"].pk
    saved = client.post(href, {"source": "UPLOAD", "ocr_language": "auto", "external_offices": [counterpart.pk],
        "file": SimpleUploadedFile("context.txt", b"Context preserved")})
    assert saved.status_code == 302
    assert parse_qs(urlsplit(saved.url).query).get("office") == [selected]
    review = client.get(saved.url)
    assert review.status_code == 200
    document = review.context["document"]
    assert document.office_id == offices["SUP"].pk
    reviewed = client.post(saved.url, {"title": "Reviewed context", "year": 2026,
        "access_level": "OFFICE", "ocr_language": "auto"})
    assert reviewed.status_code == 302
    assert parse_qs(urlsplit(reviewed.url).query).get("office") == [selected]
    detail = client.get(reviewed.url)
    back = next(link for link in Links(detail.content.decode()).hrefs
                if urlsplit(link).path == reverse("documents:external_repository"))
    assert parse_qs(urlsplit(back).query).get("office") == [selected]
    repository = client.get(back)
    upload_again = next(link for link in Links(repository.content.decode()).hrefs
                        if urlsplit(link).path == reverse("documents:external_upload"))
    assert client.get(upload_again).context["form"].recording_office.pk == offices["SUP"].pk


def test_directory_navigation_and_save_preserve_selected_office(client, users, offices):
    client.force_login(users["admin"])
    selected = str(offices["SUP"].pk)
    url = reverse("tracking:external_office_create") + "?office=" + selected
    response = client.get(url)
    links = Links(response.content.decode()).hrefs
    report_link = next(link for link in links if urlsplit(link).path == reverse("tracking:external_reports"))
    assert parse_qs(urlsplit(report_link).query).get("office") == [selected]
    saved = client.post(url, {"name": "Office context survives saving", "code": "CTX"})
    assert saved.status_code == 302
    assert parse_qs(urlsplit(saved.url).query).get("office") == [selected]

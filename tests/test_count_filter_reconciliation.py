"""Filter transitions must preserve the same records and office perspective."""

from datetime import timedelta
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin

import pytest
from django.utils import timezone

from apps.documents.models import AccessLevel, Document, Source
from tests.test_filter_agreement import traffic as traffic

pytestmark = pytest.mark.django_db


class Controls(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.links, self.forms = [], []
        self.form = self.link = self.select = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self.link = [attrs.get("href", ""), ""]
        elif tag == "form":
            self.form = (attrs, [])
            self.forms.append(self.form)
        elif self.form and tag == "input" and attrs.get("name"):
            self.form[1].append((attrs["name"], attrs.get("value", "")))
        elif self.form and tag == "select":
            self.select = attrs.get("name")
        elif self.form and self.select and tag == "option" and "selected" in attrs:
            self.form[1].append((self.select, attrs.get("value", "")))

    def handle_data(self, data):
        if self.link is not None:
            self.link[1] += data

    def handle_endtag(self, tag):
        if tag == "a" and self.link is not None:
            self.links.append((self.link[0], " ".join(self.link[1].split())))
            self.link = None
        elif tag == "form":
            self.form = None
        elif tag == "select":
            self.select = None


def controls(response):
    assert response.status_code == 200
    return Controls(response.content.decode())


def ids(response):
    assert response.status_code == 200
    return {row.pk for row in response.context["page_obj"].object_list}


@pytest.mark.parametrize("path, key", [("/", "scope"), ("/tracking/reports/", "scope_office"),
                                       ("/tracking/", "resolved"), ("/search/?mode=tracking", "resolved")])
def test_office_codes_have_same_perspective_as_ids(client, users, offices, traffic, path, key):
    client.force_login(users["admin"])
    response = client.get(path + ("&" if "?" in path else "?") + "office=sUp")
    assert response.status_code == 200
    picked = response.context[key]
    picked = picked["office"] if key == "scope" else picked.as_office if key == "resolved" else picked
    assert picked == offices["SUP"]


@pytest.mark.parametrize("path", ["/tracking/?", "/search/?mode=tracking&"])
@pytest.mark.parametrize("mixed", [False, True])
def test_originating_offices_use_valid_resolved_values(client, users, offices, traffic, path, mixed):
    client.force_login(users["admin"])
    query = f"offices={offices['MED'].pk}&offices=bogus" if mixed else "offices=med"
    response = client.get(path + query)
    expected = {traffic[key].pk for key in ("arriving", "received", "elsewhere")}
    assert ids(response) == expected
    assert response.context["selected_office_ids"] == {str(offices["MED"].pk)}
    if not mixed:
        assert not response.context["form"].errors
    else:
        assert "offices" in response.context["resolved"].invalid


@pytest.mark.parametrize("path", ["/tracking/?", "/search/?mode=tracking&"])
def test_invalid_admin_office_does_not_enable_own_office_direction(client, users, traffic, path):
    client.force_login(users["admin"])
    response = client.get(path + "office=missing&scope=incoming")
    resolved = response.context["resolved"]
    assert resolved.all_offices and resolved.direction_dropped
    assert "office" in resolved.invalid
    assert ids(response) == {record.pk for record in traffic.values()}


@pytest.mark.parametrize("path", ["/tracking/?", "/search/?mode=tracking&"])
@pytest.mark.parametrize("legacy", ["status=OVERDUE", "scope=overdue"])
@pytest.mark.parametrize("label, expected", [("All deadlines", ""), ("On time", "no")])
def test_legacy_overdue_can_be_cleared_or_changed(client, users, traffic, path, legacy, label, expected):
    client.force_login(users["admin"])
    response = client.get(path + legacy + "&status=RECEIVED&status=IN_PROCESS")
    link = next(url for url, text in controls(response).links if text == label)
    changed = client.get(path.split("?")[0] + link)
    assert changed.context["resolved"].overdue == expected
    assert set(changed.context["resolved"].statuses) == {"RECEIVED", "IN_PROCESS"}


def test_search_submit_preserves_every_selected_tracking_filter(client, users, offices, traffic):
    users["admin"].office = offices["SUP"]
    users["admin"].save(update_fields=["office"])
    client.force_login(users["admin"])
    pairs = [("mode", "tracking"), ("office", str(offices["SUP"].pk)), ("scope", "incoming"),
             ("status", "RECEIVED"), ("status", "IN_PROCESS"), ("offices", str(offices["MED"].pk)),
             ("offices", str(offices["HR"].pk)), ("owner", "custody"), ("overdue", "no")]
    response = client.get("/search/?" + urlencode(pairs))
    form = next(fields for attrs, fields in controls(response).forms if "workspace-search-form" in attrs.get("class", ""))
    for pair in pairs:
        assert pair in form
    submitted = [(name, value) for name, value in form if name != "q"] + [("q", "Received")]
    result = client.get("/search/?" + urlencode(submitted))
    assert ids(result) == {traffic["received"].pk}
    assert result.context["resolved"].as_office == offices["SUP"]


def test_office_picker_preserves_translated_overdue(client, users, traffic):
    client.force_login(users["admin"])
    response = client.get("/tracking/?status=OVERDUE")
    form = next(fields for attrs, fields in controls(response).forms if attrs.get("class") == "dashboard-scope")
    assert ("overdue", "yes") in form


@pytest.mark.parametrize("role", ["admin", "med_admin", "med", "viewer"])
def test_search_perspective_picker_only_for_system_admin(client, users, role):
    client.force_login(users[role])
    body = client.get("/search/?mode=tracking").content.decode()
    assert ('id="search-as-office"' in body) == (role == "admin")


def filed(users, offices, code, **kwargs):
    return Document.objects.create(title=f"Filed in {code}", office=offices[code], year=2026,
                                   source=Source.UPLOAD, uploaded_by=users["admin"],
                                   ocr_status="SKIPPED", access_level=AccessLevel.OVPA, **kwargs)


def test_review_count_link_keeps_due_filter(client, users, offices):
    due = filed(users, offices, "MED", retention_until=timezone.localdate())
    filed(users, offices, "HR", retention_until=timezone.localdate() + timedelta(days=120))
    client.force_login(users["admin"])
    response = client.get("/documents/?retention=due")
    link = next(url for url, text in controls(response).links if text == "Review documents")
    assert ids(client.get(urljoin("/documents/", link))) == {due.pk}


def test_tag_count_link_keeps_selected_tag(client, users, offices, tag_urgent):
    tagged = filed(users, offices, "MED")
    tagged.tags.add(tag_urgent)
    filed(users, offices, "HR")
    client.force_login(users["admin"])
    response = client.get(f"/documents/?tag={tag_urgent.pk}")
    link = next(url for url, text in controls(response).links if text == "urgent 1")
    assert ids(client.get(urljoin("/documents/", link))) == {tagged.pk}


@pytest.mark.parametrize("role", ["admin", "med", "viewer"])
def test_retired_office_folder_and_search_match_visible_documents(client, users, offices, role):
    retired = filed(users, offices, "HR")
    filed(users, offices, "MED")
    offices["HR"].is_active = False
    offices["HR"].save(update_fields=["is_active"])
    client.force_login(users[role])
    response = client.get(f"/documents/?office={offices['HR'].pk}")
    assert ids(response) == {retired.pk}
    search = client.get(f"/search/?office={offices['HR'].pk}")
    assert not search.context["form"].errors
    assert search.context["response"].total_matches == 1


@pytest.mark.parametrize("role", ["med_admin", "med", "viewer"])
@pytest.mark.parametrize("path", ["/tracking/", "/search/?mode=tracking"])
def test_foreign_perspective_cannot_expand_non_admin_tracking(client, users, offices, traffic, role, path):
    client.force_login(users[role])
    base = path + ("&" if "?" in path else "?") + "scope=incoming"
    expected = ids(client.get(base))
    response = client.get(base + f"&office={offices['HR'].pk}")
    assert ids(response) == expected
    assert "office" in response.context["resolved"].invalid


@pytest.mark.parametrize("office_value", ["hr", "HR", "9223372036854775808", "9" * 64])
def test_repository_search_office_values_cannot_expand_office_admin(client, users, offices, office_value):
    filed(users, offices, "HR")
    filed(users, offices, "MED")
    client.force_login(users["med_admin"])
    response = client.get("/search/", {"office": office_value})
    assert response.status_code == 200
    assert response.context["form"].errors
    assert not response.context["results"]


def test_repository_search_understands_office_code(client, users, offices):
    target = filed(users, offices, "HR")
    filed(users, offices, "MED")
    client.force_login(users["admin"])
    response = client.get("/search/?office=hr")
    assert not response.context["form"].errors
    assert response.context["response"].total_matches == 1
    assert response.context["results"][0].document.pk == target.pk

"""File navigation retains External perspective without granting file access."""
from urllib.parse import parse_qs, urlsplit

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.core.file_access import file_url
from apps.core.file_history import history_url
from apps.documents.services import ingest_upload
from apps.tracking import external_services
from apps.tracking.file_versions import upload_external_attachment_version

pytestmark = pytest.mark.django_db


@pytest.fixture
def files(users, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_BACKGROUND_TASKS = False
    actor = users["med_admin"]
    entry = external_services.create_external_office(user=actor, name="File context college")
    case = external_services.create_record(user=actor, title="External file custody", from_external_office=entry)
    first = external_services.add_attachment(case, user=actor, expected_revision=case.revision,
        uploaded_file=SimpleUploadedFile("original.txt", b"First version"))
    upload_external_attachment_version(case, first.file_group_id, SimpleUploadedFile("signed.txt", b"Signed copy"),
        user=actor, expected_revision=case.revision, expected_version=1, change_summary="Signed outside DocTrack")
    document, _ = ingest_upload(user=actor, collection="EXTERNAL", external_offices=[entry],
        uploaded_file=SimpleUploadedFile("filed.txt", b"Filed original"))
    return case, first.file_group, document


def scope(url, selected):
    assert parse_qs(urlsplit(url).query).get("office") == [str(selected)], urlsplit(url).path


@pytest.mark.parametrize("kind", ["external", "repository"])
def test_external_file_preview_and_history_return_to_selected_office(client, users, files, kind):
    case, group, document = files
    parent = case if kind == "external" else document
    selected = users["med_admin"].office_id
    client.force_login(users["admin"])
    detail = client.get(parent.get_absolute_url(), {"office": selected})
    row = detail.context["file_rows"][0]
    scope(row["current"]["preview_url"], selected)
    for previous in row["history"]:
        scope(previous["preview_url"], selected)
    if kind == "external":
        scope(row["upload_url"], selected)
        scope(row["discard_url"], selected)
    scope(row["audit_url"], selected)
    preview = client.get(row["current"]["preview_url"])
    assert preview.status_code == 200
    scope(preview.context["back_url"], selected)
    history = client.get(row["audit_url"])
    assert history.status_code == 200
    scope(history.context["history"]["back_url"], selected)
    for entry in history.context["history"]["entries"]:
        if entry.get("preview_url"):
            scope(entry["preview_url"], selected)
    case.refresh_from_db()
    assert case.status == "DRAFT"


def test_external_version_upload_keeps_selected_scope_and_file_identity(client, users, files):
    case, group, _ = files
    selected = users["med_admin"].office_id
    client.force_login(users["admin"])
    url = reverse("tracking:external_file_version", args=[case.pk, group.pk]) + f"?office={selected}"
    form = client.get(url)
    assert form.status_code == 200
    scope(form.context["back_url"], selected)
    assert case.title.encode() in form.content
    response = client.post(url, {f"version-{group.pk}-file": SimpleUploadedFile("latest.txt", b"Latest signed copy"),
        f"version-{group.pk}-expected_version": 2, f"version-{group.pk}-expected_revision": case.revision,
        f"version-{group.pk}-change_summary": "Additional signature"})
    assert response.status_code == 302
    scope(response.url, selected)
    assert group.versions.count() == 3 and case.file_groups.count() == 1


def test_external_file_discard_preserves_scope_without_removing_history(client, users, files):
    case, group, _ = files
    selected = users["med_admin"].office_id
    client.force_login(users["admin"])
    url = reverse("tracking:external_file_discard", args=[case.pk, group.pk]) + f"?office={selected}"
    form = client.get(url)
    scope(form.context["back_url"], selected)
    response = client.post(url, {"reason": "Remove accidental draft file", "confirm": "on",
        "expected_version": 2, "expected_revision": case.revision})
    assert response.status_code == 302
    scope(response.url, selected)
    assert group.versions.count() == 2


def test_forged_file_perspective_does_not_grant_history_or_preview(client, users, files):
    case, group, _ = files
    client.force_login(users["sup_admin"])
    selected = users["med_admin"].office_id
    url = file_url("external", group.latest_version.pk, users["sup_admin"])
    assert client.get(url + f"&office={selected}").status_code in {403, 404}
    assert client.get(history_url(group), {"office": selected}).status_code in {403, 404}


def test_worksheet_form_keeps_selected_office_when_submitted(client, users, files):
    import io
    from html.parser import HTMLParser

    from django.http import QueryDict
    from openpyxl import Workbook

    class Forms(HTMLParser):
        def __init__(self, html):
            super().__init__()
            self.forms, self.current = [], None
            self.feed(html)

        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if tag == "form" and values.get("method", "").lower() == "get":
                self.current = QueryDict(mutable=True)
            if tag == "input" and self.current is not None and values.get("name"):
                self.current.appendlist(values["name"], values.get("value", ""))

        def handle_endtag(self, tag):
            if tag == "form" and self.current is not None:
                self.forms.append(self.current)
                self.current = None

    case, _, _ = files
    workbook = Workbook()
    workbook.active.append(["First worksheet"])
    workbook.create_sheet("Second").append(["Second worksheet"])
    content = io.BytesIO()
    workbook.save(content)
    item = external_services.add_attachment(case, user=users["med_admin"], expected_revision=case.revision,
        uploaded_file=SimpleUploadedFile("sheets.xlsx", content.getvalue()))
    selected = users["med_admin"].office_id
    client.force_login(users["admin"])
    url = file_url("external", item.pk, users["admin"]) + f"&office={selected}"
    response = client.get(url)
    assert response.status_code == 200
    form = next(form for form in Forms(response.content.decode()).forms if "token" in form)
    assert form.getlist("office") == [str(selected)]
    form["worksheet"] = "Second"
    second = client.get(urlsplit(url).path, form)
    assert second.status_code == 200 and second.context["worksheet"] == "Second"
    scope(second.context["back_url"], selected)


def test_file_history_pagination_keeps_scope_and_all_audit_rows(client, users, files):
    from apps.core.models import FileLifecycleEvent

    case, group, _ = files
    for _ in range(26):
        FileLifecycleEvent.objects.create(resource_kind="external", collection="EXTERNAL", parent_id=case.pk,
            group_id=group.pk, office_id_snapshot=users["med_admin"].office_id, action="retired", reason="Audit pagination fixture")
    client.force_login(users["admin"])
    selected = users["med_admin"].office_id
    first = client.get(history_url(group), {"office": selected})
    next_url = first.context["history"]["next_url"]
    scope(next_url, selected)
    second = client.get(next_url)
    scope(second.context["history"]["back_url"], selected)
    rows = [*first.context["history"]["entries"], *second.context["history"]["entries"]]
    assert len(rows) == len({(row["type"], row["item"].pk) for row in rows}) == 28


def test_invalid_external_version_preview_returns_to_scoped_saved_form(client, users, files):
    case, group, _ = files
    selected = users["med_admin"].office_id
    client.force_login(users["admin"])
    url = reverse("tracking:external_file_version", args=[case.pk, group.pk]) + f"?office={selected}"
    initial = client.get(url)
    saved = initial.context["form"].recovery
    response = client.post(url, {
        "upload_form_nonce": saved["nonce"], "upload_batch_id": saved["batch_id"],
        "upload_draft": str(saved["draft_id"] or ""), "upload_revision": saved["revision"],
        f"version-{group.pk}-file": SimpleUploadedFile("unfinished.txt", b"Keep this staged version"),
        f"version-{group.pk}-expected_version": 2, f"version-{group.pk}-expected_revision": case.revision,
        f"version-{group.pk}-change_summary": "",
    })
    assert response.status_code == 302
    restored = client.get(response.url)
    preview = client.get(restored.context["form"].recovery["files"][0]["preview_url"])
    assert preview.status_code == 200 and b"Keep this staged version" in preview.content
    scope(preview.context["back_url"], selected)
    returned = client.get(preview.context["back_url"])
    assert returned.status_code == 200
    assert len(returned.context["file_rows"][0]["form"].recovery["files"]) == 1
    assert group.versions.count() == 2

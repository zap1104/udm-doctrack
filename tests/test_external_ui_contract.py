"""External forms must retain shared accessibility and workflow controls."""

from collections import Counter
from html.parser import HTMLParser

import pytest
from django.urls import reverse


class Controls(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    @property
    def names(self):
        return Counter(
            attrs["name"]
            for tag, attrs in self.elements
            if tag in {"input", "select", "textarea"} and "name" in attrs
        )


pytestmark = pytest.mark.django_db


def test_external_create_connects_the_shared_document_type_picker(client, users, memo_type):
    client.force_login(users["med_admin"])
    response = client.get(reverse("tracking:external_create"))
    assert response.status_code == 200
    controls = Controls(response.content.decode())
    # The real document-types.js enhancer requires these containers to bind
    # selection changes and to hide the main type for single-type packets.
    assert any("data-document-type-picker" in attrs for _, attrs in controls.elements)
    assert any("data-main-type-field" in attrs for _, attrs in controls.elements)
    for name in response.context["form"].fields:
        assert controls.names[name] == 1, name


def test_external_invalid_creation_has_linkable_field_errors(client, users):
    client.force_login(users["med_admin"])
    response = client.post(reverse("tracking:external_create"), {})
    assert response.status_code == 400
    controls = Controls(response.content.decode())
    assert any(attrs.get("id") == "id_title_error" and attrs.get("role") == "alert"
               for _, attrs in controls.elements)
    assert any(tag == "a" and attrs.get("href") == "#id_title" for tag, attrs in controls.elements)
    for name in response.context["form"].fields:
        assert controls.names[name] == 1, name


def test_external_review_keeps_one_revision_and_one_recipient_selector(client, users):
    from apps.tracking.external_services import create_external_office, create_record

    admin = users["med_admin"]
    counterpart = create_external_office(user=admin, name="College paper office", code="COL")
    record = create_record(user=admin, title="Paper review", from_external_office=counterpart)
    client.force_login(admin)
    response = client.get(reverse("tracking:external_review", args=[record.pk]))
    assert response.status_code == 200
    controls = Controls(response.content.decode())
    for name in response.context["form"].fields:
        assert controls.names[name] == 1, name
    assert controls.names["revision"] == 1
    assert controls.names["to_endpoint"] == 1
    assert controls.names["to_office"] == controls.names["to_external_office"] == 0


def test_external_repository_file_totals_are_available_without_row_queries(client, users, offices, django_assert_num_queries):
    from apps.documents.models import Document, DocumentFile

    document = Document.objects.create(title="External paper files", office=offices["MED"], collection="EXTERNAL")
    for name in ("cover.pdf", "evidence.pdf"):
        DocumentFile.objects.create(document=document, file="test-only/" + name, original_name=name)
    client.force_login(users["med_admin"])
    response = client.get(reverse("documents:external_repository"))
    assert response.status_code == 200
    displayed = list(response.context["documents"])
    assert [row.pk for row in displayed] == [document.pk]
    with django_assert_num_queries(0):
        assert displayed[0].file_count == 2

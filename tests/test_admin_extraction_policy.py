"""Raw administrator forms must honor extraction and content restrictions."""

import pytest
from django.urls import reverse

from apps.accounts.permission_models import CapabilityRestriction, UserPermissionPolicy
from apps.documents.models import Document

pytestmark = pytest.mark.django_db


def restrict(user, area, action):
    policy, _ = UserPermissionPolicy.objects.get_or_create(user=user)
    CapabilityRestriction.objects.create(policy=policy, area=area, action=action)


def admin_payload(response):
    """Submit the real form, including the inline management forms."""
    form = response.context["adminform"].form
    data = {
        name: form[name].value()
        for name in form.fields
        if form[name].value() is not None and form[name].value() is not False
    }
    data["_save"] = "Save"
    for inline in response.context["inline_admin_formsets"]:
        formset = inline.formset
        for name in formset.management_form.fields:
            data[f"{formset.prefix}-{name}"] = formset.management_form[name].value()
        for inline_form in formset.forms:
            for name in inline_form.fields:
                value = inline_form[name].value()
                if value is not None:
                    data[f"{inline_form.prefix}-{name}"] = value
    return data


@pytest.mark.parametrize("collection,area", [("OVPA", "repository"), ("EXTERNAL", "external_repository")])
def test_raw_admin_cannot_replace_extraction_when_extract_is_blocked(client, users, collection, area):
    actor = users["admin"]
    document = Document.objects.create(
        title="Original metadata", office=users["med"].office, uploaded_by=actor,
        collection=collection, year=2026, ocr_text="Original extracted evidence",
        ocr_status="FAILED", ocr_engine="original-engine", ocr_confidence=0.25,
        ocr_notes="Original failure details", page_count=2,
        allow_external_ocr=True, ocr_language="auto",
    )
    document.rebuild_index()
    restrict(actor, area, "extract")
    client.force_verified_login(actor)
    url = reverse("admin:documents_document_change", args=[document.pk])
    response = client.get(url)
    assert response.status_code == 200
    data = admin_payload(response)
    data.update(
        title="Corrected metadata", ocr_text="forgeduniquetext", ocr_status="DONE",
        ocr_engine="forged-engine", ocr_confidence="0.99", ocr_notes="Forged success",
        page_count="99", extraction_generation="99", extraction_failed_generation="99",
        index_title="Forged index", search_index_revision="forged-revision",
        ocr_language="eng",
    )
    # Consent opt-out is a metadata edit, even while extraction is blocked.
    data.pop("allow_external_ocr", None)
    assert client.post(url, data).status_code == 302
    document.refresh_from_db()
    assert document.title == "Corrected metadata"
    assert document.ocr_language == "eng"
    assert document.allow_external_ocr is False
    assert document.ocr_text == "Original extracted evidence"
    assert document.ocr_status == "FAILED"
    assert document.ocr_engine == "original-engine"
    assert document.ocr_confidence == 0.25
    assert document.ocr_notes == "Original failure details"
    assert document.page_count == 2
    assert document.extraction_generation == 0
    assert document.extraction_failed_generation is None
    assert document.search_index_revision == ""
    assert "forgeduniquetext" not in str(document.search_vector)


@pytest.mark.parametrize("collection,area", [("OVPA", "repository"), ("EXTERNAL", "external_repository")])
def test_raw_admin_add_cannot_supply_derived_extraction_for_blocked_collection(client, users, collection, area):
    actor = users["admin"]
    restrict(actor, area, "extract")
    client.force_verified_login(actor)
    url = reverse("admin:documents_document_add")
    response = client.get(url)
    assert response.status_code == 200
    data = admin_payload(response)
    data.update(
        title="New metadata", office=users["med"].office_id, collection=collection,
        uploaded_by=actor.pk, year="2026", ocr_text="forgeduniquetext", ocr_status="DONE",
        ocr_engine="forged-engine", ocr_confidence="0.99", ocr_notes="Forged success",
        page_count="99", ocr_language="eng",
    )
    assert client.post(url, data).status_code == 302
    document = Document.objects.get(title="New metadata")
    assert document.ocr_text == ""
    assert document.ocr_status == "PENDING"
    assert document.ocr_engine == ""
    assert document.ocr_confidence is None
    assert document.ocr_notes == ""
    assert document.page_count == 0
    assert document.ocr_language == "eng"


@pytest.mark.parametrize("collection,area", [("OVPA", "repository"), ("EXTERNAL", "external_repository")])
def test_content_restriction_does_not_reintroduce_ocr_as_readonly_admin_fields(client, users, collection, area):
    actor = users["admin"]
    document = Document.objects.create(
        title="Visible metadata", office=users["med"].office, collection=collection, year=2026,
        ocr_text="secret-extracted-body", ocr_notes="secret-extraction-notes",
    )
    restrict(actor, area, "view_content")
    client.force_verified_login(actor)
    response = client.get(reverse("admin:documents_document_change", args=[document.pk]))
    assert response.status_code == 200
    assert b"secret-extracted-body" not in response.content
    assert b"secret-extraction-notes" not in response.content
    assert "ocr_text" not in response.context["adminform"].form.fields
    assert "ocr_notes" not in response.context["adminform"].form.fields


def test_unrestricted_system_admin_retains_existing_ocr_controls(client, users):
    document = Document.objects.create(
        title="Allowed manual review", office=users["med"].office, uploaded_by=users["admin"], year=2026,
    )
    client.force_verified_login(users["admin"])
    url = reverse("admin:documents_document_change", args=[document.pk])
    response = client.get(url)
    data = admin_payload(response)
    data.update(ocr_text="Authorized corrected evidence", ocr_status="DONE", page_count="3")
    assert client.post(url, data).status_code == 302
    document.refresh_from_db()
    assert document.ocr_text == "Authorized corrected evidence"
    assert document.ocr_status == "DONE"
    assert document.page_count == 3

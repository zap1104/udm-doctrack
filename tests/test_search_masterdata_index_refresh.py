"""Administration edits keep repository search aligned with current metadata."""

import pytest

from apps.accounts.forms import OfficeForm
from apps.accounts.models import Office
from apps.core.models import DocumentType, MetadataFieldDefinition, Tag
from apps.documents.models import DocumentMetadata
from apps.search.services import search_documents
from tests.test_search import make_document


def matching_ids(user, query):
    response = search_documents(user=user, query=query, min_relevance=0, log=False)
    return {result.document.pk for result in response.results}


@pytest.mark.django_db
def test_tag_rename_refreshes_existing_document_search(client, users, offices, settings):
    settings.SEARCH_ENABLE_TRIGRAM = False
    tag = Tag.objects.create(name="oldfilingneedle")
    document = make_document(offices["MED"], "Ordinary filed request")
    document.tags.add(tag)
    document.rebuild_index()
    assert document.pk in matching_ids(users["admin"], tag.name)
    client.force_login(users["admin"])

    response = client.post(f"/administration/tags/{tag.pk}/", {
        "name": "quartzfilingneedle", "category": tag.category,
        "description": tag.description, "is_active": "on",
    })

    assert response.status_code == 302
    tag.refresh_from_db()
    assert tag.name == "quartzfilingneedle"
    assert document.pk in matching_ids(users["admin"], tag.name)
    assert document.pk not in matching_ids(users["admin"], "oldfilingneedle")


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/accounts/offices/", "/administration/offices/"])
def test_office_rename_refreshes_existing_document_search(client, users, settings, path):
    settings.SEARCH_ENABLE_TRIGRAM = False
    office = Office.objects.create(code="IDXOFF", name="oldofficeneedle", cluster="OVPA")
    document = make_document(office, "Ordinary filed request")
    assert document.pk in matching_ids(users["admin"], office.name)
    client.force_login(users["admin"])

    response = client.post(f"{path}{office.pk}/", {
        "code": office.code, "name": "quartzofficeneedle",
        "short_name": office.short_name, "cluster": office.cluster,
        "parent": "", "head_name": office.head_name, "email": office.email,
        "location": office.location, "colour": office.colour,
        "sort_order": office.sort_order, "is_active": "on",
    })

    assert response.status_code == 302
    office.refresh_from_db()
    assert office.name == "quartzofficeneedle"
    assert document.pk in matching_ids(users["admin"], office.name)
    assert document.pk not in matching_ids(users["admin"], "oldofficeneedle")


@pytest.mark.django_db
@pytest.mark.parametrize("secondary", [False, True], ids=["primary", "secondary"])
def test_type_rename_refreshes_primary_and_secondary_document_search(
    client, users, offices, memo_type, settings, secondary,
):
    settings.SEARCH_ENABLE_TRIGRAM = False
    kind = DocumentType.objects.create(code="IDXTYPE", name="oldtypeneedle")
    document = make_document(
        offices["MED"], "Ordinary filed request",
        document_type=memo_type if secondary else kind,
    )
    if secondary:
        document.document_types.add(memo_type, kind)
        document.rebuild_index()
    assert document.pk in matching_ids(users["admin"], kind.name)
    client.force_login(users["admin"])

    response = client.post(f"/administration/document-types/{kind.pk}/", {
        "code": kind.code, "name": "quartztypeneedle", "description": kind.description,
        "retention_years": kind.retention_years, "sort_order": kind.sort_order,
        "is_active": "on",
    })

    assert response.status_code == 302
    kind.refresh_from_db()
    assert kind.name == "quartztypeneedle"
    assert document.pk in matching_ids(users["admin"], kind.name)
    assert document.pk not in matching_ids(users["admin"], "oldtypeneedle")


@pytest.mark.django_db
@pytest.mark.parametrize("searchable", [False, True], ids=["disable", "enable"])
def test_metadata_searchable_toggle_refreshes_existing_values(
    client, users, offices, settings, searchable,
):
    settings.SEARCH_ENABLE_TRIGRAM = False
    field = MetadataFieldDefinition.objects.create(
        key="index_review_note", label="Review note", is_searchable=not searchable,
    )
    document = make_document(offices["MED"], "Ordinary filed request")
    value = DocumentMetadata.objects.create(
        document=document, field=field, value="quartzmetadataneedle",
    )
    document.rebuild_index()
    assert (document.pk in matching_ids(users["admin"], value.value)) is not searchable
    client.force_login(users["admin"])
    payload = {
        "key": field.key, "label": field.label, "field_type": field.field_type,
        "choices_csv": "", "help_text": "", "sort_order": field.sort_order,
        "is_active": "on",
    }
    if searchable:
        payload["is_searchable"] = "on"

    response = client.post(f"/administration/metadata-fields/{field.pk}/", payload)

    assert response.status_code == 302
    field.refresh_from_db()
    assert field.is_searchable is searchable
    assert (document.pk in matching_ids(users["admin"], value.value)) is searchable
    value.refresh_from_db()
    assert value.value == "quartzmetadataneedle"


@pytest.mark.django_db
def test_office_rename_refreshes_a_document_filed_during_the_save(
    client, users, settings, monkeypatch, django_capture_on_commit_callbacks,
):
    settings.SEARCH_ENABLE_TRIGRAM = False
    office = Office.objects.create(code="IDXGAP", name="oldconcurrentofficeneedle", cluster="OVPA")
    original_document = make_document(office, "Previously filed request")
    filed_during_save = []
    original_save = OfficeForm.save

    def file_before_office_save(form, *args, **kwargs):
        # The refresh helper has already found and locked the original document.
        # A filing that starts against the still-current office label is absent
        # from those original IDs, yet must not retain that old label forever.
        previous_office = Office.objects.get(pk=form.instance.pk)
        filed_during_save.append(make_document(previous_office, "Newly filed request"))
        return original_save(form, *args, **kwargs)

    monkeypatch.setattr(OfficeForm, "save", file_before_office_save)
    client.force_login(users["admin"])
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(f"/accounts/offices/{office.pk}/", {
            "code": office.code, "name": "quartzconcurrentofficeneedle",
            "short_name": office.short_name, "cluster": office.cluster,
            "parent": "", "head_name": office.head_name, "email": office.email,
            "location": office.location, "colour": office.colour,
            "sort_order": office.sort_order, "is_active": "on",
        })

    assert response.status_code == 302
    assert len(filed_during_save) == 1
    expected = {original_document.pk, filed_during_save[0].pk}
    assert matching_ids(users["admin"], "quartzconcurrentofficeneedle") == expected
    assert not matching_ids(users["admin"], "oldconcurrentofficeneedle")

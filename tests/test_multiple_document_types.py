"""A mixed-type record retains its classifications throughout its lifecycle."""

from importlib import import_module
from types import SimpleNamespace

import pytest
from django import forms
from django.apps import apps
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.http import QueryDict
from django.utils import timezone

from apps.core.models import DocumentType
from apps.documents.forms import DocumentMetadataForm
from apps.documents.models import Document
from apps.documents.services import archive_tracking_record, save_document_metadata
from apps.search.services import search_documents
from apps.tracking import services
from apps.tracking.forms import DEADLINE_NONE, CreateRecordForm
from apps.tracking.models import RoutingSLA


@pytest.fixture
def letter_type(db):
    return DocumentType.objects.create(code="LETTER", name="Letter", retention_years=30)


def create_data(offices, memo_type, letter_type):
    data = QueryDict(mutable=True)
    data.update({"subject": "Mixed document packet", "instructions": "Please review this packet.",
                 "classification": "INTERNAL", "priority": "NORMAL", "deadline_choice": DEADLINE_NONE,
                 "document_type": str(memo_type.pk), "document_types_submitted": "1"})
    data.setlist("receiving_offices", [str(offices["SUP"].pk)])
    data.setlist("document_types", [str(memo_type.pk), str(letter_type.pk)])
    return data


@pytest.mark.django_db
def test_tracking_form_accepts_multiple_types_and_requires_selected_main(users, offices, memo_type, letter_type):
    data = create_data(offices, memo_type, letter_type)
    form = CreateRecordForm(data, user=users["med"])
    assert isinstance(form.fields["document_types"], forms.ModelMultipleChoiceField)
    assert form.is_valid(), form.errors
    rendered = str(form["document_types"])
    assert f'value="{memo_type.pk}"' in rendered and f'value="{letter_type.pk}"' in rendered
    assert set(form.cleaned_data["document_types"].values_list("pk", flat=True)) == {memo_type.pk, letter_type.pk}
    data["document_type"] = ""
    form = CreateRecordForm(data, user=users["med"])
    assert not form.is_valid() and "document_type" in form.errors


@pytest.mark.django_db
def test_create_review_edit_keeps_all_types(client, users, offices, memo_type, letter_type):
    client.force_login(users["med"])
    response = client.post("/tracking/new/", dict(create_data(offices, memo_type, letter_type).lists()))
    assert response.status_code == 302
    record = services.active_for(users["med"]).get(subject="Mixed document packet")
    assert record.document_type_id == memo_type.pk
    assert set(record.document_types.values_list("pk", flat=True)) == {memo_type.pk, letter_type.pk}
    review = client.get(response.url)
    assert "Memorandum" in review.content.decode() and "Letter" in review.content.decode()
    assert "Main type: Memorandum" in review.content.decode()
    edit = client.get(f"/tracking/new/?draft={record.pk}")
    assert set(map(int, edit.context["form"]["document_types"].value())) == {memo_type.pk, letter_type.pk}


@pytest.mark.django_db
def test_draft_edit_replaces_selections_and_main_without_changing_identity(client, users, offices, memo_type, letter_type):
    record = services.create_draft_record(user=users["med"], subject="Draft packet", instructions="For action",
                                         document_type=memo_type)
    client.force_login(users["med"])
    data = create_data(offices, memo_type, letter_type)
    data["document_type"] = str(letter_type.pk)
    response = client.post(f"/tracking/new/?draft={record.pk}", dict(data.lists()))
    assert response.status_code == 302
    record.refresh_from_db()
    assert record.document_type_id == letter_type.pk
    assert set(record.document_types.values_list("pk", flat=True)) == {memo_type.pk, letter_type.pk}
    data.setlist("document_types", [str(memo_type.pk)])
    response = client.post(f"/tracking/new/?draft={record.pk}", dict(data.lists()))
    assert response.status_code == 302
    record.refresh_from_db()
    assert record.document_type_id == memo_type.pk
    assert set(record.document_types.values_list("pk", flat=True)) == {memo_type.pk}
    assert services.active_for(users["med"]).count() == 1


@pytest.mark.django_db
def test_secondary_type_does_not_change_main_deadline_or_explicit_no_deadline(users, offices, memo_type, letter_type):
    RoutingSLA.objects.create(office=offices["SUP"], document_type=memo_type, due_days=7)
    RoutingSLA.objects.create(office=offices["SUP"], document_type=letter_type, due_days=1)
    for explicit_none in [False, True]:
        record = services.create_draft_record(user=users["med"], subject="Mixed SLA packet", instructions="For action",
                                             document_type=memo_type, document_types=[memo_type, letter_type])
        services.route_record(record, [offices["SUP"]], user=users["med"], **({"due_at": None} if explicit_none else {}))
        record.refresh_from_db()
        if explicit_none:
            assert record.due_at is None
        else:
            assert round((record.due_at - timezone.now()).total_seconds() / 86400) == 7


@pytest.mark.django_db
def test_filing_carries_all_types_and_preserves_main_retention(users, offices, memo_type, letter_type):
    record = services.create_draft_record(user=users["med"], subject="Two document types", instructions="For action",
                                         document_type=memo_type, document_types=[memo_type, letter_type])
    services.route_record(record, [offices["SUP"]], user=users["med"])
    services.confirm_receipt(record, user=users["sup"])
    services.complete_record(record, user=users["sup"])
    document = archive_tracking_record(record, user=users["admin"])
    assert document.document_type_id == memo_type.pk
    assert set(document.document_types.values_list("pk", flat=True)) == {memo_type.pk, letter_type.pk}
    assert document.retention_until == document.document_date.replace(year=document.document_date.year + 5)
    assert "Memorandum" in document.index_meta and "Letter" in document.index_meta


@pytest.mark.django_db
def test_secondary_type_filter_does_not_duplicate_or_leak_documents(client, users, offices, memo_type, letter_type):
    visible = Document.objects.create(title="Mixed visible packet", office=offices["MED"],
                                      document_type=memo_type, uploaded_by=users["med"])
    visible.document_types.set([memo_type, letter_type])
    hidden = Document.objects.create(title="Other office private packet", office=offices["HR"],
                                     document_type=letter_type, access_level="RESTRICTED", uploaded_by=users["hr"])
    hidden.document_types.set([letter_type])
    client.force_login(users["med"])
    response = client.get("/documents/", {"document_type": letter_type.pk})
    assert response.context["total"] == 1
    assert {doc.pk for doc in response.context["page_obj"]} == {visible.pk}
    assert "Other office private packet" not in response.content.decode()
    assert response.context["form"].fields["document_type"].queryset.filter(pk=letter_type.pk).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("selection", ["empty", "one", "duplicate"])
def test_native_form_automatically_handles_empty_and_single_selections(users, offices, memo_type, letter_type, selection):
    record = services.create_draft_record(user=users["med"], subject="Existing draft", instructions="For action",
                                         document_type=memo_type)
    data = create_data(offices, memo_type, letter_type)
    data.setlist("document_types", [] if selection == "empty" else [str(letter_type.pk)] * (2 if selection == "duplicate" else 1))
    form = CreateRecordForm(data, instance=record, user=users["med"])
    assert form.is_valid(), form.errors
    form.save()
    record.refresh_from_db()
    assert record.document_type_id == (None if selection == "empty" else letter_type.pk)
    assert set(record.document_types.values_list("pk", flat=True)) == (set() if selection == "empty" else {letter_type.pk})


@pytest.mark.django_db
@pytest.mark.parametrize("invalid", ["unknown", "inactive"])
def test_invalid_extra_type_is_rejected_without_changing_draft(client, users, offices, memo_type, letter_type, invalid):
    record = services.create_draft_record(user=users["med"], subject="Protected draft", instructions="For action",
                                         document_type=memo_type)
    if invalid == "inactive":
        letter_type.is_active = False
        letter_type.save(update_fields=["is_active"])
    data = create_data(offices, memo_type, letter_type)
    data.setlist("document_types", [str(memo_type.pk), str(letter_type.pk if invalid == "inactive" else 999999999)])
    client.force_login(users["med"])
    response = client.post(f"/tracking/new/?draft={record.pk}", dict(data.lists()))
    assert response.status_code == 200 and "document_types" in response.context["form"].errors
    record.refresh_from_db()
    assert record.subject == "Protected draft"
    assert set(record.document_types.values_list("pk", flat=True)) == {memo_type.pk}


@pytest.mark.django_db
def test_retired_existing_selections_survive_edit(users, offices, memo_type, letter_type):
    record = services.create_draft_record(user=users["med"], subject="Older mixed packet", instructions="For action",
                                         document_type=memo_type, document_types=[memo_type, letter_type])
    letter_type.is_active = False
    letter_type.save(update_fields=["is_active"])
    form = CreateRecordForm(create_data(offices, memo_type, letter_type), instance=record, user=users["med"])
    assert form.is_valid(), form.errors
    form.save()
    assert set(record.document_types.values_list("pk", flat=True)) == {memo_type.pk, letter_type.pk}


@pytest.mark.django_db
def test_main_report_counts_match_destinations_for_mixed_packets(client, users, offices, memo_type, letter_type):
    mixed = Document.objects.create(title="Mixed memorandum", office=offices["MED"], document_type=memo_type, uploaded_by=users["med"])
    mixed.document_types.set([memo_type, letter_type])
    single = Document.objects.create(title="Plain letter", office=offices["MED"], document_type=letter_type, uploaded_by=users["med"])
    client.force_login(users["admin"])
    context = client.get("/documents/reports/", {"office": offices["MED"].pk}).context
    rows = {row["document_type_id"]: row for row in context["document_types"]}
    assert sum(row["total"] for row in rows.values()) == context["total_documents"] == 2
    for kind in [memo_type, letter_type]:
        assert rows[kind.pk]["total"] == client.get(rows[kind.pk]["workspace_url"]).context["total"] == 1
    default = client.get("/documents/", {"document_type": letter_type.pk}).context
    assert default["total"] == 2
    assert {doc.pk for doc in default["page_obj"]} == {mixed.pk, single.pk}
    assert next(folder for folder in default["type_folders"] if folder["document_type_id"] == letter_type.pk)["total"] == 2


@pytest.mark.django_db
def test_metadata_edits_and_search_keep_secondary_types(users, offices, memo_type, letter_type):
    document = Document.objects.create(title="Maintenance packet", office=offices["MED"], document_type=memo_type, uploaded_by=users["med"])
    document.document_types.set([memo_type, letter_type])
    data = {"title": document.title, "office": offices["MED"].pk, "document_type": memo_type.pk,
            "document_types": [memo_type.pk, letter_type.pk], "document_types_submitted": "1",
            "year": document.year, "access_level": document.access_level, "ocr_language": document.ocr_language,
            "retention_until": document.retention_until}
    form = DocumentMetadataForm(data, instance=document, user=users["med"])
    assert form.is_valid(), form.errors
    saved = save_document_metadata(document, user=users["med"], data=form.cleaned_data, tag_names=[], metadata_values={})
    assert set(saved.document_types.values_list("pk", flat=True)) == {memo_type.pk, letter_type.pk}
    results = search_documents(user=users["med"], query="", document_type=letter_type, log=False)
    assert {result.document.pk for result in results.results} == {document.pk}
    assert "Letter" in saved.index_meta


@pytest.mark.django_db
def test_legacy_metadata_submission_keeps_extras_and_explicit_clear_removes_types(users, offices, memo_type, letter_type):
    document = Document.objects.create(title="Older mixed packet", office=offices["MED"], document_type=memo_type,
                                       uploaded_by=users["med"])
    document.document_types.set([memo_type, letter_type])
    save_document_metadata(document, user=users["med"], data={"document_type": memo_type}, tag_names=[], metadata_values={})
    assert set(document.document_types.values_list("pk", flat=True)) == {memo_type.pk, letter_type.pk}
    save_document_metadata(document, user=users["med"], data={"document_type": None, "document_types": []},
                           tag_names=[], metadata_values={})
    document.refresh_from_db()
    assert document.document_type_id is None and not document.document_types.exists()
    assert "Memorandum" not in document.index_meta and "Letter" not in document.index_meta


@pytest.mark.django_db
def test_service_rejects_invalid_main_and_other_office_edit_atomically(users, offices, memo_type, letter_type):
    document = Document.objects.create(title="Do not replace", office=offices["MED"], document_type=memo_type, uploaded_by=users["med"])
    document.document_types.set([memo_type])
    data = {"title": "Changed", "document_type": memo_type, "document_types": [letter_type]}
    with pytest.raises(ValidationError):
        save_document_metadata(document, user=users["med"], data=data, tag_names=[], metadata_values={})
    with pytest.raises(PermissionDenied):
        save_document_metadata(document, user=users["hr"], data=data, tag_names=[], metadata_values={})
    document.refresh_from_db()
    assert document.title == "Do not replace" and document.document_type_id == memo_type.pk
    assert set(document.document_types.values_list("pk", flat=True)) == {memo_type.pk}


@pytest.mark.django_db
@pytest.mark.parametrize("model_name", ["tracking", "documents"])
def test_type_backfills_preserve_legacy_single_and_unspecified_records(users, offices, memo_type, model_name):
    if model_name == "tracking":
        legacy = services.create_draft_record(user=users["med"], subject="Legacy draft", instructions="For action", document_type=memo_type)
        unspecified = services.create_draft_record(user=users["med"], subject="Unspecified draft", instructions="For action")
        module = "apps.tracking.migrations.0010_trackingrecord_document_types"
    else:
        legacy = Document.objects.create(title="Legacy filing", office=offices["MED"], document_type=memo_type, uploaded_by=users["med"])
        unspecified = Document.objects.create(title="Unspecified filing", office=offices["MED"], uploaded_by=users["med"])
        module = "apps.documents.migrations.0007_document_document_types"
    legacy.document_types.clear()
    before = legacy.__class__.objects.filter(pk=legacy.pk).values().get()
    migration = import_module(module)
    for _ in range(2):
        migration.backfill_types(apps, SimpleNamespace(connection=connection))
        assert set(legacy.document_types.values_list("pk", flat=True)) == {memo_type.pk}
        assert not unspecified.document_types.exists()
        assert legacy.__class__.objects.filter(pk=legacy.pk).values().get() == before

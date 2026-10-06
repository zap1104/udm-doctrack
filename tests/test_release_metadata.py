"""Retiring classifications must not invalidate existing repository records."""
import pytest

from apps.core.models import DocumentType
from apps.documents.forms import DocumentMetadataForm
from apps.documents.models import Document
from apps.tracking import services
from apps.tracking.forms import CreateRecordForm


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["admin", "med_admin"])
def test_metadata_edit_preserves_retired_existing_type(users, offices, memo_type, role):
    document = Document.objects.create(title="Old inspection", office=offices["MED"],
                                       document_type=memo_type, uploaded_by=users["admin"])
    memo_type.is_active = False
    memo_type.save(update_fields=["is_active"])
    other = DocumentType.objects.create(code="RETIRED", name="Other retired type", is_active=False)
    form = DocumentMetadataForm({
        "title": "Updated inspection", "office": offices["MED"].pk,
        "document_type": memo_type.pk, "access_level": document.access_level,
        "ocr_language": document.ocr_language,
        "year": document.year,
    }, instance=document, user=users[role])
    assert form.is_valid(), form.errors
    saved = form.save()
    assert saved.title == "Updated inspection" and saved.document_type_id == memo_type.pk
    assert not form.fields["document_type"].queryset.filter(pk=other.pk).exists()


@pytest.mark.django_db
def test_new_metadata_cannot_select_retired_type(users, memo_type):
    memo_type.is_active = False
    memo_type.save(update_fields=["is_active"])
    form = DocumentMetadataForm(user=users["admin"])
    assert not form.fields["document_type"].queryset.filter(pk=memo_type.pk).exists()


@pytest.mark.django_db
def test_metadata_edit_preserves_retired_existing_office_without_widening_scope(users, offices):
    document = Document.objects.create(title="Old office record", office=offices["MED"], uploaded_by=users["admin"])
    offices["MED"].is_active = False
    offices["MED"].save(update_fields=["is_active"])
    offices["SUP"].is_active = False
    offices["SUP"].save(update_fields=["is_active"])
    for role in ["admin", "med_admin"]:
        form = DocumentMetadataForm({"title": "Kept classification", "office": offices["MED"].pk,
                                     "access_level": document.access_level, "ocr_language": document.ocr_language,
                                     "year": document.year},
                                    instance=document, user=users[role])
        assert form.is_valid(), form.errors
        assert form.cleaned_data["office"] == offices["MED"]
        assert not form.fields["office"].queryset.filter(pk=offices["SUP"].pk).exists()


@pytest.mark.django_db
def test_draft_edit_preserves_retired_existing_type(users, offices, memo_type):
    record = services.create_draft_record(
        user=users["med"], subject="Historical classified draft", instructions="Please review this draft.",
        document_type=memo_type,
    )
    memo_type.is_active = False
    memo_type.save(update_fields=["is_active"])
    other = DocumentType.objects.create(code="OTHER", name="Other retired classification", is_active=False)
    form = CreateRecordForm({
        "subject": "Updated classified draft", "instructions": "Please review this draft.",
        "document_type": memo_type.pk, "receiving_offices": [offices["SUP"].pk], "deadline_choice": "none",
        "classification": record.classification, "priority": record.priority,
    }, instance=record, user=users["med"])
    assert form.is_valid(), form.errors
    assert form.save().document_type_id == memo_type.pk
    assert not form.fields["document_type"].queryset.filter(pk=other.pk).exists()
    assert not CreateRecordForm(user=users["med"]).fields["document_type"].queryset.filter(pk=memo_type.pk).exists()

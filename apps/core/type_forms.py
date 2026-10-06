"""Shared validation for record and repository type selections."""

from django import forms
from django.db.models import Q

from .models import DocumentType


class DocumentTypeSelectionMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        selected = self.instance.selected_document_types
        choices = DocumentType.objects.filter(Q(is_active=True) | Q(pk__in=[kind.pk for kind in selected]))
        multiple = self.fields["document_types"]
        multiple.label = "Document types"
        multiple.help_text = "Select every type included in this record. Leave empty if not specified."
        multiple.widget = forms.SelectMultiple(attrs={
            "size": 5, "class": "form-select js-searchable", "data-item-noun": "document type",
            "data-placeholder": "Find a document type…", "data-document-type-selection": "",
        })
        multiple.queryset = choices
        main = self.fields["document_type"]
        main.queryset = choices
        main.label = "Main document type"
        main.empty_label = "Choose the main type"
        main.help_text = "Required for multiple types. This type controls deadlines and retention."
        main.widget.attrs["data-main-document-type"] = ""
        self.initial["document_types"] = [kind.pk for kind in selected]
        suggested = self.initial.get("document_type")
        if suggested and len(selected) <= 1:
            self.initial["document_types"] = [suggested]
        if suggested and suggested not in self.initial["document_types"]:
            self.initial["document_types"].append(suggested)

    def clean(self):
        cleaned = super().clean()
        if "document_types" not in cleaned or "document_type" not in cleaned:
            return cleaned
        main = cleaned.get("document_type")
        selected = list(cleaned["document_types"])
        # Old callers submit only document_type. The marker distinguishes that
        # from a new form deliberately clearing its multiple selection.
        if "document_types" not in self.data and not self.data.get("document_types_submitted"):
            selected = [main] if main else []
            if self.instance.pk and main and main.pk == self.instance.document_type_id:
                selected = self.instance.selected_document_types
        ids = {kind.pk for kind in selected}
        if len(selected) <= 1:
            cleaned["document_type"] = selected[0] if selected else None
        elif main and main.pk not in ids:
            self.add_error("document_type", "Choose a main type from the selected document types.")
        elif len(selected) > 1 and not main:
            self.add_error("document_type", "Choose a main document type for deadlines and retention.")
        cleaned["document_types"] = self.fields["document_types"].queryset.filter(pk__in=ids)
        return cleaned


class DocumentTypeAdminForm(DocumentTypeSelectionMixin, forms.ModelForm):
    document_types_submitted = forms.CharField(required=False, initial="1", widget=forms.HiddenInput)

from __future__ import annotations

from django import forms
from django.utils import timezone

from apps.accounts.models import Office
from apps.core.forms import BootstrapFormMixin, DateInput, MultipleFileField
from apps.core.models import DocumentType, MetadataFieldDefinition, Tag

from .models import AccessLevel, Document, Source


class UploadForm(BootstrapFormMixin, forms.Form):
    """Step 1 of Upload / Scan: pick the file, everything else is proposed after."""

    file = forms.FileField(
        label="Document file",
        help_text="PDF, Word, Excel, image or text. Scanned pages go through OCR when it is configured.",
    )
    office = forms.ModelChoiceField(
        queryset=Office.active.all(), label="Owning office", help_text="Which office the record belongs to."
    )
    source = forms.ChoiceField(
        choices=[(Source.UPLOAD, "Uploaded file"), (Source.SCAN, "Scanned document")],
        initial=Source.UPLOAD,
        label="How did this arrive?",
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None and user.office_id and not user.is_system_admin:
            self.fields["office"].queryset = Office.active.filter(pk=user.office_id)
            self.fields["office"].initial = user.office_id
        elif user is not None and user.office_id:
            self.fields["office"].initial = user.office_id


class TagsField(forms.CharField):
    """Comma-separated tags in, clean list out."""

    def to_python(self, value):
        raw = super().to_python(value) or ""
        seen: list[str] = []
        for chunk in raw.replace(";", ",").split(","):
            name = " ".join(chunk.split()).lower()[:64]
            if name and name not in seen:
                seen.append(name)
        return seen


class DocumentMetadataForm(BootstrapFormMixin, forms.ModelForm):
    """Review screen. Suggested values arrive as `initial`; the user owns the result."""

    tags = TagsField(
        required=False,
        label="Tags",
        help_text="Separate tags with commas. Suggested tags are already filled in — remove what does not fit.",
        widget=forms.TextInput(attrs={"placeholder": "preventive maintenance, schedule, med", "list": "tag-options"}),
    )

    class Meta:
        model = Document
        fields = (
            "title", "description", "office", "document_type", "document_date", "year",
            "reference_number", "author_name", "recipient_name", "signatory",
            "access_level", "retention_until",
        )
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3, "placeholder": "One or two sentences about this document"}),
            "document_date": DateInput(),
            "retention_until": DateInput(),
            "title": forms.TextInput(attrs={"placeholder": "Document title"}),
        }
        labels = {
            "author_name": "From / author",
            "recipient_name": "To / recipient",
            "reference_number": "Tracking or control number",
            "access_level": "Who can open this",
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        self.metadata_definitions = list(MetadataFieldDefinition.active.all())
        super().__init__(*args, **kwargs)
        self.fields["office"].queryset = Office.active.all()
        self.fields["document_type"].queryset = DocumentType.active.all()
        self.fields["document_type"].empty_label = "Not specified"
        self.fields["year"].widget.attrs.update({"min": 1950, "max": timezone.localdate().year + 1})
        self.fields["access_level"].choices = AccessLevel.choices

        for definition in self.metadata_definitions:
            self.fields[f"meta_{definition.key}"] = self._build_metadata_field(definition)

    def _build_metadata_field(self, definition: MetadataFieldDefinition):
        common = {
            "label": definition.label,
            "required": definition.is_required,
            "help_text": definition.help_text,
        }
        if definition.field_type == MetadataFieldDefinition.FieldType.LONG_TEXT:
            return forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), **common)
        if definition.field_type == MetadataFieldDefinition.FieldType.NUMBER:
            return forms.CharField(widget=forms.NumberInput(), **common)
        if definition.field_type == MetadataFieldDefinition.FieldType.DATE:
            return forms.CharField(widget=DateInput(), **common)
        if definition.field_type == MetadataFieldDefinition.FieldType.BOOLEAN:
            return forms.ChoiceField(choices=[("", "—"), ("Yes", "Yes"), ("No", "No")], **common)
        if definition.field_type == MetadataFieldDefinition.FieldType.CHOICE:
            choices = [("", "—")] + [(option, option) for option in definition.choices]
            return forms.ChoiceField(choices=choices, **common)
        return forms.CharField(max_length=500, **common)

    def clean_year(self):
        year = self.cleaned_data["year"]
        current = timezone.localdate().year
        if year and (year < 1950 or year > current + 1):
            raise forms.ValidationError(f"Enter a year between 1950 and {current + 1}.")
        return year

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("year") and cleaned.get("document_date"):
            cleaned["year"] = cleaned["document_date"].year
        return cleaned

    @property
    def metadata_fields(self):
        for definition in self.metadata_definitions:
            yield definition, self[f"meta_{definition.key}"]

    def metadata_cleaned(self) -> dict[str, str]:
        return {
            definition.key: (self.cleaned_data.get(f"meta_{definition.key}") or "").strip()
            for definition in self.metadata_definitions
        }


class AddFilesForm(BootstrapFormMixin, forms.Form):
    files = MultipleFileField(label="Add more files")


class RepositoryFilterForm(BootstrapFormMixin, forms.Form):
    q = forms.CharField(
        required=False, label="", widget=forms.TextInput(attrs={"placeholder": "Search metadata, tags, office or text…"})
    )
    year = forms.ChoiceField(required=False, label="", choices=[])
    document_type = forms.ModelChoiceField(
        required=False, label="", queryset=DocumentType.active.all(), empty_label="All types"
    )
    tag = forms.ModelChoiceField(required=False, label="", queryset=Tag.active.all(), empty_label="All tags")

    def __init__(self, *args, years=None, **kwargs):
        super().__init__(*args, **kwargs)
        year_choices = [("", "All years")] + [(str(year), str(year)) for year in (years or [])]
        self.fields["year"].choices = year_choices

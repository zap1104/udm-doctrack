from __future__ import annotations

from datetime import date, timedelta

from django import forms
from django.core import signing
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import Office
from apps.core.forms import BootstrapFormMixin, DateInput, MultipleFileField
from apps.core.models import DocumentType, MetadataFieldDefinition, Tag
from apps.core.type_forms import DocumentTypeSelectionMixin
from apps.search.filter_fields import RepositorySearchControls

from .models import COMPLETED_SOURCE, HISTORICAL_FILTER, AccessLevel, DeletionReview, Document, OcrLanguage, Source


class UploadForm(BootstrapFormMixin, forms.Form):
    """Step 1 of Upload / Scan: pick the file, everything else is proposed after."""

    file = MultipleFileField(
        label="Document files",
        help_text="Select all supporting files now. Contents are locked after this upload; metadata can still be corrected. Original formats are preserved.",
    )
    office = forms.ModelChoiceField(
        queryset=Office.active.all(), label="Owning office", help_text="Which office the record belongs to."
    )
    source = forms.ChoiceField(
        choices=[(Source.UPLOAD, "Uploaded file"), (Source.SCAN, "Scanned document")],
        initial=Source.UPLOAD,
        label="How did this arrive?",
    )
    ocr_language = forms.ChoiceField(
        choices=OcrLanguage.choices,
        initial=OcrLanguage.AUTO,
        label="Text language",
        help_text="A hint for scanned pages. Digital text is read locally regardless of this choice.",
    )
    allow_external_ocr = forms.BooleanField(
        required=False,
        initial=False,
        label="Allow external OCR for scanned pages",
        help_text=(
            "Clear this for sensitive records. The file stays stored, and local text layers are still read, "
            "but images are not sent to OCR.space or Azure."
        ),
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


class DocumentMetadataForm(DocumentTypeSelectionMixin, BootstrapFormMixin, forms.ModelForm):
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
            "title", "description", "office", "document_type", "document_types", "document_date", "year",
            "reference_number", "author_name", "recipient_name", "signatory",
            "access_level", "retention_until", "ocr_language", "allow_external_ocr",
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
            "ocr_language": "Text language",
            "allow_external_ocr": "Allow external OCR for scanned pages",
            "retention_until": "Deletion review date",
        }
        help_texts = {"retention_until": "A review date, not an automatic deletion date. Clear it to leave the review unscheduled."}

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        self.metadata_definitions = list(MetadataFieldDefinition.active.all())
        super().__init__(*args, **kwargs)
        offices = Office.objects.filter(Q(is_active=True) | Q(pk=self.instance.office_id if self.instance.pk else None))
        self.fields["office"].queryset = offices
        if user is not None and not user.is_system_admin:
            self.fields["office"].queryset = offices.filter(pk=self.instance.office_id or user.office_id)
        self.fields["year"].widget.attrs.update({"min": 1950, "max": timezone.localdate().year + 1})
        self.fields["access_level"].choices = AccessLevel.choices
        if self.instance.is_external:
            self.fields["office"].disabled = True
        if not self.instance.document_type_id:
            self.fields["retention_until"].help_text = (
                "Leave blank to use the main document type's review period when first classified. "
                "Documents are never deleted automatically."
            )

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
        kind = cleaned.get("document_type")
        base_year = cleaned["document_date"].year if cleaned.get("document_date") else cleaned.get("year")
        if (not self.instance.document_type_id and kind and kind.retention_years and base_year
                and not cleaned.get("retention_until") and base_year + kind.retention_years > date.max.year):
            self.add_error("retention_until", "The type's review period exceeds supported dates. Enter a deletion review date or correct the document date.")
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


#: Month names in calendar order. Written out rather than generated so they do
#: not follow the server's locale away from the rest of the UI, and kept in this
#: order so the dropdown reads January-to-December however the database
#: happened to return the months that exist.
MONTH_NAMES = [
    ("1", "January"), ("2", "February"), ("3", "March"), ("4", "April"),
    ("5", "May"), ("6", "June"), ("7", "July"), ("8", "August"),
    ("9", "September"), ("10", "October"), ("11", "November"), ("12", "December"),
]


class RepositoryFilterForm(BootstrapFormMixin, RepositorySearchControls):
    """Filters for the repository list.

    Two rules, both learned the hard way on the tracking page:

    1. **Every control is a real field.** `month` and `source` used to be
       hand-written `<select>` elements the form did not declare and the view
       never read. They submitted happily and changed nothing, so picking
       "Archived" returned the same list — a filter that lies is worse than no
       filter, because the reader believes the answer.
    2. **Only offer what can actually return something.** A dropdown listing
       every tag in the system, most of them on no document the reader can
       see, is a menu of dead ends: each pick answers "no results" for a
       filter that was never going to match. The options are built from the
       documents actually visible to this user, so anything on the list finds
       at least one record.

    Tags are ordered by how much they are used rather than alphabetically —
    with a shared vocabulary the useful ones are the common ones, and A-to-Z
    buries them.
    """

    # Existing repository searches returned every plain match. A relevance
    # cutoff is an explicit choice here; Search keeps its configured default.
    minimum_default = 0

    q = forms.CharField(
        required=False, label="", widget=forms.TextInput(attrs={"placeholder": "Search title, reference, metadata or document text…", "autocomplete": "off"})
    )
    office = forms.ModelChoiceField(required=False, label="Office", queryset=Office.objects.none(), empty_label="All available offices")
    document_type = forms.ModelChoiceField(
        required=False, label="", queryset=DocumentType.active.none(), empty_label="All types"
    )
    type_scope = forms.ChoiceField(required=False, choices=[("", "Any selected type"), ("main", "Main type")], widget=forms.HiddenInput)
    tag = forms.ModelChoiceField(required=False, label="", queryset=Tag.active.none(), empty_label="All tags")
    # Named for what the repository actually stores. The old control offered
    # "Completed / Archived / Historical upload", two of which described the
    # same rows and none of which was a field on the model.
    source = forms.ChoiceField(required=False, label="", choices=[])
    year = forms.ChoiceField(required=False, label="", choices=[])
    month = forms.ChoiceField(required=False, label="", choices=[])
    retention = forms.ChoiceField(
        required=False,
        label="",
        choices=[
            ("", "Any deletion review status"),
            ("due", "Deletion review due"),
            ("soon", "Due within 90 days"),
            ("unscheduled", "No deletion review date"),
        ],
    )

    #: Declaration order is also the order the row is read in: what you are
    #: looking for, then what kind of thing it is, then when it is from.
    FIELD_ORDER = ("q", "office", "document_type", "tag", "source", "year", "month", "date_from", "date_to", "retention", "min_relevance", "show_all")

    def __init__(self, *args, years=None, months=None, document_types=None, tags=None,
                 sources=None, offices=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.order_fields(self.FIELD_ORDER)

        self.fields["year"].choices = [("", "All years")] + [
            (str(year), str(year)) for year in (years or [])
        ]
        # Chronological, not by however the database returned them, and only
        # the months that have something in them.
        present = set(months or [])
        self.fields["month"].choices = [("", "All months")] + [
            (value, label) for value, label in MONTH_NAMES if int(value) in present
        ]
        present_sources = set(sources or [])
        # An origin asked for by a link is always accepted, even with nothing
        # in it. Offered only when present, a zero count's link failed the
        # choice check, the filter was dropped, and the page listed every
        # document under a segment reading 0.
        asked = self.data.get("source") if self.is_bound else None
        if asked in Source.values:
            present_sources.add(asked)
        self.fields["source"].choices = [("", "Any origin")] + (
            # Offered when there is anything historical to show, whichever
            # non-tracking origins it came from, or when a link asks for it.
            [(HISTORICAL_FILTER, "Historical (not from tracking)")]
            if present_sources - {COMPLETED_SOURCE} or asked == HISTORICAL_FILTER else []
        ) + [
            (value, label) for value, label in Source.choices if value in present_sources
        ]
        if document_types is not None:
            self.fields["document_type"].queryset = document_types
        if tags is not None:
            self.fields["tag"].queryset = tags
        if offices is not None:
            self.fields["office"].queryset = offices

        for name in ("year", "month", "document_type", "tag", "source", "retention"):
            self.fields[name].widget.attrs.setdefault(
                "aria-label", self.fields[name].widget.attrs.get("aria-label")
                or f"Filter by {name.replace('_', ' ')}"
            )
        self.fields["retention"].widget.attrs["aria-label"] = "Filter by deletion review status"


class DeletionReviewForm(BootstrapFormMixin, forms.Form):
    decision = forms.ChoiceField(label="Review decision", choices=[])
    next_review_date = forms.DateField(required=False, label="Next deletion review date", widget=DateInput(),
                                       help_text="Required when keeping or restoring a document. Choose a date after today; leave blank for removal.")
    reason = forms.CharField(label="Reason / approval reference", max_length=2000,
                             widget=forms.Textarea(attrs={"rows": 4}),
                             help_text="Explain the decision. For removal, include the applicable approval or authority reference.")
    confirmed = forms.BooleanField(required=False,
                                    label="I confirm this document is approved for removal from the repository.")
    state_token = forms.CharField(label="Review session", widget=forms.HiddenInput)
    SALT = "doctrack.document.deletion-review"

    def __init__(self, *args, document, **kwargs):
        from .services import deletion_review_state

        self.document = document
        super().__init__(*args, **kwargs)
        choices = [("", "Choose a decision")]
        if document.is_active:
            choices.append(("KEEP", "Keep document and set next review date"))
            if document.retention_is_due:
                choices.append(("DELETE", "Remove from repository"))
        elif document.deletion_recovery_open:
            choices.append(("RESTORE", "Restore to repository"))
        self.fields["decision"].choices = choices
        self.fields["next_review_date"].widget.attrs["min"] = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.initial["state_token"] = signing.dumps(
            {"pk": document.pk, "state": deletion_review_state(document)}, salt=self.SALT,
        )

    def clean_state_token(self):
        try:
            payload = signing.loads(self.cleaned_data["state_token"], salt=self.SALT, max_age=7200)
            if payload["pk"] != self.document.pk:
                raise signing.BadSignature()
            return payload["state"]
        except (signing.BadSignature, KeyError, TypeError):
            raise forms.ValidationError("This review expired or is invalid. Reload the page and try again.") from None

    def clean(self):
        data = super().clean()
        decision, next_date = data.get("decision"), data.get("next_review_date")
        if decision == DeletionReview.Decision.DELETE:
            if not data.get("confirmed"):
                self.add_error("confirmed", "Confirm approval before removing this document.")
            if next_date:
                self.add_error("next_review_date", "Leave this blank when removing a document.")
        elif decision in {DeletionReview.Decision.KEEP, DeletionReview.Decision.RESTORE}:
            if not next_date or next_date <= timezone.localdate():
                self.add_error("next_review_date", "Choose a next review date after today.")
        return data


class DeletionQueueForm(BootstrapFormMixin, forms.Form):
    q = forms.CharField(required=False, label="Title or reference", max_length=255)
    office = forms.ModelChoiceField(required=False, label="Owning office", queryset=Office.objects.none(),
                                    empty_label="All available offices")
    status = forms.ChoiceField(label="Review status", initial="due", choices=[
        ("due", "Deletion review due"), ("soon", "Due within 90 days"),
        ("unscheduled", "No review date"), ("deleted", "Deleted documents"),
    ])

    def __init__(self, *args, offices, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["office"].queryset = offices

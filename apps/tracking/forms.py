from __future__ import annotations

from django import forms

from apps.accounts.models import Office, User
from apps.core.forms import BootstrapFormMixin, MultipleFileField
from apps.core.models import DocumentType

from .models import RoutingStep, Status, TrackingRecord

DUE_CHOICES = [
    ("1", "1 day"),
    ("3", "3 days"),
    ("5", "5 days"),
    ("7", "1 week"),
    ("0", "No deadline"),
]


class CreateRecordForm(BootstrapFormMixin, forms.ModelForm):
    """Step 1 of Create New DTS. The originating office is never chosen by hand."""

    receiving_offices = forms.ModelMultipleChoiceField(
        queryset=Office.active.all(),
        label="Receiving office(s)",
        help_text="The first office you pick takes custody; the others receive a copy for action.",
        widget=forms.SelectMultiple(
            attrs={"size": 8, "class": "form-select js-searchable", "data-placeholder": "Type to filter offices…"}
        ),
    )
    due_days = forms.ChoiceField(
        choices=DUE_CHOICES, initial="3", label="Action deadline", required=False
    )
    attachments = MultipleFileField(
        required=False,
        label="Attachments",
        help_text="PDF, DOCX, XLSX, JPG or PNG. Up to 25 MB per file.",
    )

    class Meta:
        model = TrackingRecord
        fields = ("subject", "document_type", "classification", "priority", "instructions", "remarks")
        widgets = {
            "subject": forms.TextInput(attrs={"placeholder": "Enter document subject", "autofocus": True}),
            "instructions": forms.Textarea(
                attrs={"rows": 3, "placeholder": "For appropriate action and endorsement"}
            ),
            "remarks": forms.Textarea(attrs={"rows": 2, "placeholder": "Add context or handling instructions"}),
        }
        labels = {"instructions": "Instructions / action required"}

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["document_type"].queryset = DocumentType.active.all()
        self.fields["document_type"].empty_label = "Not specified"
        if user is not None and user.office_id:
            self.fields["receiving_offices"].queryset = Office.active.exclude(pk=user.office_id)

    def clean_subject(self):
        subject = " ".join(self.cleaned_data["subject"].split())
        if len(subject) < 5:
            raise forms.ValidationError("Write a subject of at least 5 characters so the record is findable.")
        return subject


class RouteForm(BootstrapFormMixin, forms.Form):
    """Forward or return a document that this office currently holds."""

    action = forms.ChoiceField(
        choices=[
            (RoutingStep.Action.FORWARD, "Forward to another office"),
            (RoutingStep.Action.RETURN, "Return to the previous office"),
        ],
        initial=RoutingStep.Action.FORWARD,
        widget=forms.RadioSelect,
    )
    offices = forms.ModelMultipleChoiceField(
        queryset=Office.active.all(),
        label="Send to",
        widget=forms.SelectMultiple(
            attrs={"size": 6, "class": "form-select js-searchable", "data-placeholder": "Type to filter offices…"}
        ),
    )
    instructions = forms.CharField(
        label="Instructions for the next office",
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "For appropriate action"}),
        required=False,
    )
    due_days = forms.ChoiceField(choices=DUE_CHOICES, initial="3", required=False, label="Action deadline")
    remark = forms.CharField(
        label="Remark for the timeline",
        widget=forms.Textarea(attrs={"rows": 2}),
        required=False,
    )
    attachments = MultipleFileField(required=False, label="Attach a response or revision")

    def __init__(self, *args, record=None, user=None, **kwargs):
        self.record = record
        self.user = user
        super().__init__(*args, **kwargs)
        if user is not None and user.office_id:
            self.fields["offices"].queryset = Office.active.exclude(pk=user.office_id)

    def clean_offices(self):
        offices = self.cleaned_data["offices"]
        if not offices:
            raise forms.ValidationError("Choose at least one office.")
        return offices


class ConfirmReceiptForm(BootstrapFormMixin, forms.Form):
    note = forms.CharField(
        label="Receipt note (optional)",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Add a note for the sender"}),
    )


class RemarkForm(BootstrapFormMixin, forms.Form):
    remark = forms.CharField(
        label="Remark",
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "What action did your office take?"}),
    )
    attachments = MultipleFileField(required=False, label="Attach files")


class CompleteForm(BootstrapFormMixin, forms.Form):
    note = forms.CharField(
        label="Completion note",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "How was the document resolved?"}),
    )
    archive_now = forms.BooleanField(
        required=False,
        initial=True,
        label="Move the record to Document Management right away",
    )


class GrantAccessForm(BootstrapFormMixin, forms.Form):
    office = forms.ModelChoiceField(queryset=Office.active.all(), required=False, label="Give access to office")
    user = forms.ModelChoiceField(queryset=User.objects.filter(is_active=True), required=False, label="or to user")
    reason = forms.CharField(max_length=255, required=False, label="Reason")

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("office") and not cleaned.get("user"):
            raise forms.ValidationError("Choose an office or a user.")
        return cleaned


class TrackingFilterForm(BootstrapFormMixin, forms.Form):
    q = forms.CharField(
        required=False,
        label="",
        widget=forms.TextInput(attrs={"placeholder": "Search tracking no., subject or office…"}),
    )
    status = forms.ChoiceField(
        required=False,
        label="",
        choices=[("", "All statuses"), ("OVERDUE", "Overdue")] + list(Status.choices),
    )
    office = forms.ModelChoiceField(
        required=False, label="", queryset=Office.active.all(), empty_label="All offices"
    )
    scope = forms.ChoiceField(
        required=False,
        label="",
        choices=[
            ("", "All I can see"),
            ("inbox", "Waiting for my receipt"),
            ("custody", "In my office"),
            ("sent", "Sent by my office"),
            ("mine", "Created by me"),
        ],
    )

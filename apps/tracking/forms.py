from __future__ import annotations

from datetime import datetime, time, timedelta

from django import forms
from django.core.validators import MaxLengthValidator
from django.utils import timezone

from apps.accounts.models import Office, User
from apps.core.forms import BootstrapFormMixin, DateInput, MultipleFileField
from apps.core.models import DocumentType

from .models import (
    ACTIVE_STATUSES,
    MAX_INSTRUCTIONS_CHARS,
    MAX_NOTE_CHARS,
    MAX_REMARK_CHARS,
    RoutingStep,
    Status,
    TrackingRecord,
)

#: How far ahead a deadline may be set. A date beyond this is almost always a
#: typo in the year ("2027" for "2026"), and a deadline nobody will ever chase
#: is worse than no deadline at all.
MAX_DEADLINE_DAYS = 365

DEADLINE_NONE = "none"
DEADLINE_DATE = "date"
DEADLINE_CHOICES = [
    (DEADLINE_NONE, "No deadline — the receiving office acts at its own pace"),
    (DEADLINE_DATE, "Set a deadline date"),
]


def cap_textarea(field, limit: int) -> None:
    """Put a character ceiling on a field Django built from a model TextField.

    A TextField has no max_length, so the form field arrives with no validator
    and no maxlength attribute. Assigning `.max_length` on its own changes
    neither: CharField adds the validator in its constructor and the attribute
    in widget_attrs(), both long finished by the time a ModelForm hands the
    field over. All three have to be set together.
    """
    field.max_length = limit
    field.validators.append(MaxLengthValidator(limit))
    field.widget.attrs["maxlength"] = str(limit)


def deadline_choice_field():
    """Fresh 'is there a deadline?' field. A factory, not a shared instance —
    two forms binding the same Field object would share its widget attrs."""
    return forms.ChoiceField(
        choices=DEADLINE_CHOICES,
        initial=DEADLINE_NONE,
        label="Is there a deadline?",
        widget=forms.RadioSelect(attrs={"data-deadline-choice": "true"}),
    )


def due_date_field():
    """Fresh calendar field for the deadline date.

    `min` is stamped on the widget so the native picker greys out past dates;
    the same rule is enforced again in `clean()` because a browser control is
    a convenience, never a validation boundary.
    """
    today = timezone.localdate()
    return forms.DateField(
        required=False,
        label="Deadline date",
        widget=DateInput(
            attrs={
                "min": today.isoformat(),
                "max": (today + timedelta(days=MAX_DEADLINE_DAYS)).isoformat(),
                "data-deadline-date": "true",
            }
        ),
        help_text="The receiving office is expected to act on or before this date.",
    )


class DeadlineMixin:
    """Shared validation for the deadline pair used on both create steps.

    Kept as a plain mixin (not a Form) so it can sit in front of a ModelForm
    and a Form alike; each form declares the two fields from the factories
    above and inherits the rules from here.
    """

    def clean(self):
        cleaned = super().clean()
        choice = cleaned.get("deadline_choice")
        due_date = cleaned.get("due_date")

        if choice != DEADLINE_DATE:
            # "No deadline" wins over a date left behind by an earlier answer,
            # otherwise switching the radio back to "No" would silently keep it.
            cleaned["due_date"] = None
            return cleaned

        if not due_date:
            self.add_error("due_date", "Choose the deadline date, or select “No deadline”.")
            return cleaned

        today = timezone.localdate()
        if due_date < today:
            self.add_error("due_date", "The deadline cannot be in the past.")
        elif due_date > today + timedelta(days=MAX_DEADLINE_DAYS):
            self.add_error(
                "due_date",
                f"Choose a date within the next {MAX_DEADLINE_DAYS} days. "
                "Check the year if you meant a date sooner than that.",
            )
        return cleaned

    def deadline_datetime(self):
        """The chosen deadline as an aware datetime, or None.

        The date is taken as the *end* of that day: a deadline of "today" means
        close of business, not the instant the form happened to be submitted.
        """
        if not getattr(self, "cleaned_data", None):
            return None
        if self.cleaned_data.get("deadline_choice") != DEADLINE_DATE:
            return None
        due_date = self.cleaned_data.get("due_date")
        if not due_date:
            return None
        return timezone.make_aware(
            datetime.combine(due_date, time(23, 59, 59)), timezone.get_current_timezone()
        )


class CreateRecordForm(DeadlineMixin, BootstrapFormMixin, forms.ModelForm):
    """Step 1 of the tracking slip. The originating office is never chosen by hand."""

    receiving_offices = forms.ModelMultipleChoiceField(
        queryset=Office.active.all(),
        label="Receiving office(s)",
        help_text="Every office you select receives the document. Custody passes to the office that confirms receipt.",
        widget=forms.SelectMultiple(
            attrs={"size": 8, "class": "form-select js-searchable", "data-placeholder": "Type to filter offices…"}
        ),
    )
    deadline_choice = deadline_choice_field()
    due_date = due_date_field()
    attachments = MultipleFileField(
        required=False,
        label="Attachments",
        help_text="PDF, DOCX, XLSX, JPG or PNG. Up to 25 MB per file.",
    )

    class Meta:
        model = TrackingRecord
        fields = (
            "subject", "document_type", "classification", "priority",
            "requested_action", "instructions",
        )
        widgets = {
            "subject": forms.TextInput(attrs={"placeholder": "Enter document subject", "autofocus": True}),
            "instructions": forms.Textarea(
                attrs={
                    "rows": 8,
                    "placeholder": "State what the receiving office must do, and any remarks that go with it.\n"
                                   "Example: For appropriate action and endorsement to the Dean's office.",
                }
            ),
        }
        labels = {"instructions": "Instructions / Remarks"}
        help_texts = {
            "instructions": "Required. This is what the receiving office reads first, and it is printed "
                            "on the routing slip.",
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["document_type"].queryset = DocumentType.active.all()
        self.fields["document_type"].empty_label = "Not specified"
        self.fields["instructions"].required = True
        # route_record() copies this onto every RoutingStep, where it is cut to
        # MAX_INSTRUCTIONS_CHARS. Capping the box at the same number stops the
        # record and its own routing slip quietly disagreeing about what the
        # instructions were.
        cap_textarea(self.fields["instructions"], MAX_INSTRUCTIONS_CHARS)
        self.fields["requested_action"].widget.choices = [
            ("", "Select the requested action")
        ] + list(TrackingRecord.RequestedAction.choices)
        if user is not None and user.office_id:
            self.fields["receiving_offices"].queryset = Office.active.exclude(pk=user.office_id)

    def clean_subject(self):
        subject = " ".join(self.cleaned_data["subject"].split())
        if len(subject) < 5:
            raise forms.ValidationError("Write a subject of at least 5 characters so the record is findable.")
        return subject

    def clean_instructions(self):
        instructions = self.cleaned_data["instructions"].strip()
        if len(instructions) < 5:
            raise forms.ValidationError(
                "Write the instructions or remarks for the receiving office — at least a short sentence."
            )
        return instructions


class ReviewRouteForm(DeadlineMixin, BootstrapFormMixin, forms.Form):
    """Step 2 of the tracking slip.

    The receiving offices were chosen on step 1 and remembered in the session,
    but a session can be lost (browser closed, cookies cleared, a draft opened
    days later). Without this form the draft becomes impossible to route, so
    step 2 always shows a real, editable field pre-filled with the earlier
    choice rather than trusting the session blindly.
    """

    receiving_offices = forms.ModelMultipleChoiceField(
        queryset=Office.active.all(),
        label="Receiving office(s)",
        help_text="Every office listed receives the document. Custody passes to the office that confirms receipt.",
        widget=forms.SelectMultiple(
            attrs={"size": 8, "class": "form-select js-searchable", "data-placeholder": "Type to filter offices…"}
        ),
    )
    deadline_choice = deadline_choice_field()
    due_date = due_date_field()

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        if user is not None and user.office_id:
            self.fields["receiving_offices"].queryset = Office.active.exclude(pk=user.office_id)

    def clean_receiving_offices(self):
        offices = self.cleaned_data["receiving_offices"]
        if not offices:
            raise forms.ValidationError("Choose at least one office to send this to.")
        return offices


class RouteForm(DeadlineMixin, BootstrapFormMixin, forms.Form):
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
    # One notes field, matching the create form. There used to be a second
    # "Remark for the timeline" box that JavaScript kept as a mirror copy of
    # this one, so whatever was typed here appeared twice on screen and was
    # stored twice. route_record() already falls back to the instructions when
    # no separate remark is given.
    instructions = forms.CharField(
        label="Instructions / Remarks",
        max_length=MAX_INSTRUCTIONS_CHARS,
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "For appropriate action"}),
        required=False,
    )
    deadline_choice = deadline_choice_field()
    due_date = due_date_field()
    attachments = MultipleFileField(required=False, label="Attach a response or revision")

    def __init__(self, *args, record=None, user=None, **kwargs):
        self.record = record
        self.user = user
        super().__init__(*args, **kwargs)
        # Hide both the office acting and the office that currently holds the
        # document. For ordinary staff those are the same office, but a system
        # administrator can act for any office (`can_act_for_office`), and
        # excluding only their own office left the current holder selectable —
        # sending it a document it already has, which it would then be asked
        # to receive again.
        blocked = {
            office_id
            for office_id in (
                getattr(user, "office_id", None),
                getattr(record, "current_office_id", None),
            )
            if office_id
        }
        if blocked:
            self.fields["offices"].queryset = Office.active.exclude(pk__in=blocked)

    def clean_offices(self):
        offices = self.cleaned_data["offices"]
        if not offices:
            raise forms.ValidationError("Choose at least one office.")
        return offices


class ConfirmReceiptForm(BootstrapFormMixin, forms.Form):
    note = forms.CharField(
        label="Receipt note (optional)",
        required=False,
        max_length=MAX_NOTE_CHARS,
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Add a note for the sender"}),
    )


class RemarkForm(BootstrapFormMixin, forms.Form):
    # A remark goes into the timeline, which is read as a running account of
    # what each office did. Without a ceiling it accepted a pasted document,
    # and the page it lands on has to render every remark ever added.
    remark = forms.CharField(
        label="Remark",
        max_length=MAX_REMARK_CHARS,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "What action did your office take?"}),
    )
    attachments = MultipleFileField(required=False, label="Attach files")


class CompleteForm(BootstrapFormMixin, forms.Form):
    note = forms.CharField(
        label="Completion note",
        required=False,
        max_length=MAX_NOTE_CHARS,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "How was the document resolved?"}),
    )
    archive_now = forms.BooleanField(
        required=False,
        initial=True,
        label="Move the record to Document Management right away",
    )


class ReopenForm(BootstrapFormMixin, forms.Form):
    """Why a completed record is going back into tracking.

    The reason is required. Undoing a completion is the one action here that
    contradicts something the history already states, so the record has to
    carry an explanation of why beside it.
    """

    reason = forms.CharField(
        label="Why is this going back?",
        max_length=MAX_NOTE_CHARS,
        widget=forms.Textarea(
            attrs={"rows": 2, "placeholder": "Completed too early — the endorsement is still missing"}
        ),
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
        # Only the statuses this page can actually show. The list used to be
        # every status there is, so "Completed" sat in the dropdown of a page
        # that shows active records only — picking it always returned nothing,
        # which reads as "there are none" rather than "they are not kept here".
        # Derived from ACTIVE_STATUSES so the two cannot drift apart again.
        choices=[("", "All statuses"), ("OVERDUE", "Overdue")]
        + [(value, label) for value, label in Status.choices if value in ACTIVE_STATUSES],
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
            ("awaiting", "Awaiting anyone's receipt"),
            ("custody", "In my office"),
            ("sent", "Sent by my office"),
            ("mine", "Created by me"),
        ],
    )

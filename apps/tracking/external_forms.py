"""Scoped forms for External tracking. The services remain the write boundary."""

from django import forms
from django.db.models import Q

from apps.accounts.models import Office
from apps.core.document_types import resolve_document_types
from apps.core.forms import BootstrapFormMixin
from apps.core.models import DocumentType

from .external_models import ExternalOffice
from .models import MAX_INSTRUCTIONS_CHARS, MAX_NOTE_CHARS, MAX_REMARK_CHARS


def actual_time(label, *, required=False):
    return forms.DateTimeField(
        required=required,
        label=label,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "step": "1"}, format="%Y-%m-%dT%H:%M:%S"),
        help_text=(
            "Use a known actual paper time. If the time is unknown, keep notes or evidence instead of inventing one."
            if required
            else "Optional known paper time. Leave unknown times blank and explain them in a note."
        ),
    )


class RevisionForm(BootstrapFormMixin, forms.Form):
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)


class ExternalOfficeForm(BootstrapFormMixin, forms.ModelForm):
    revision = forms.IntegerField(min_value=1, required=False, widget=forms.HiddenInput)

    class Meta:
        model = ExternalOffice
        fields = ["managing_office", "name", "code", "contact_person", "email", "phone", "notes", "is_active"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["managing_office"].queryset = Office.objects.filter(is_active=True)
        if not user.is_system_admin:
            self.fields["managing_office"].queryset = Office.objects.filter(pk=user.office_id)
            self.fields["managing_office"].initial = user.office_id
            self.fields["managing_office"].disabled = True
        if self.instance.pk:
            self.fields["revision"].required = True
            self.initial["revision"] = self.instance.revision
            self.fields["managing_office"].disabled = True
        else:
            self.initial["is_active"] = True


class EndpointMixin:
    def scope_endpoints(self, *, user, record=None):
        external = ExternalOffice.objects.filter(is_active=True)
        external = external.filter(managing_office=record.managing_office) if record else external.visible_to(user)
        for name in ("from_external_office", "to_external_office"):
            if name in self.fields:
                self.fields[name].queryset = external
        for name in ("from_office", "to_office"):
            if name in self.fields:
                self.fields[name].queryset = Office.objects.filter(is_active=True)
        if record is None and not user.is_system_admin and "from_office" in self.fields:
            self.fields["from_office"].queryset = Office.objects.filter(pk=user.office_id, is_active=True)

    def require_endpoint(self, data, prefix):
        if bool(data.get(f"{prefix}_office")) == bool(data.get(f"{prefix}_external_office")):
            raise forms.ValidationError("Choose exactly one internal office or external office.")


class ExternalCreateForm(EndpointMixin, BootstrapFormMixin, forms.Form):
    managing_office = forms.ModelChoiceField(queryset=Office.objects.none())
    title = forms.CharField(max_length=255)
    description = forms.CharField(required=False, max_length=5000, widget=forms.Textarea(attrs={"rows": 3}))
    original_reference = forms.CharField(required=False, max_length=200, label="Original paper reference")
    document_types = forms.ModelMultipleChoiceField(
        queryset=DocumentType.objects.filter(is_active=True),
        required=False,
        label="Document types",
        widget=forms.SelectMultiple(attrs={"size": 5, "data-document-type-selection": ""}),
        help_text="Select every type included in this paper packet.",
    )
    document_type = forms.ModelChoiceField(
        queryset=DocumentType.objects.filter(is_active=True),
        required=False,
        label="Main document type",
        widget=forms.Select(attrs={"data-main-document-type": ""}),
        help_text="Choose a main type for packets with several types. It controls deadlines and deletion review.",
    )
    from_office = forms.ModelChoiceField(queryset=Office.objects.none(), required=False, label="Source internal office")
    from_external_office = forms.ModelChoiceField(
        queryset=ExternalOffice.objects.none(), required=False, label="Source external UDM office"
    )
    actual_at = actual_time("Known source time")

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.scope_endpoints(user=user)
        self.fields["managing_office"].queryset = Office.objects.filter(is_active=True)
        if not user.is_system_admin:
            self.fields["managing_office"].queryset = Office.objects.filter(pk=user.office_id)
            self.fields["managing_office"].initial = user.office_id
            self.fields["managing_office"].disabled = True

    def clean(self):
        data = super().clean()
        self.require_endpoint(data, "from")
        external = data.get("from_external_office")
        managing = data.get("managing_office")
        if external and managing and external.managing_office_id != managing.pk:
            self.add_error("from_external_office", "Choose an entry belonging to the managing office.")
        if "document_types" in data and "document_type" in data:
            selected = data["document_types"]
            if "document_types" not in self.data and data["document_type"]:
                selected = [data["document_type"]]
            try:
                data["document_types"], data["document_type"] = resolve_document_types(selected, data["document_type"])
            except forms.ValidationError as exc:
                self.add_error("document_type", exc)
        return data


class ExternalRouteForm(EndpointMixin, RevisionForm):
    to_office = forms.ModelChoiceField(
        queryset=Office.objects.none(), required=False, label="Recipient internal office"
    )
    to_external_office = forms.ModelChoiceField(
        queryset=ExternalOffice.objects.none(), required=False, label="Recipient external UDM office"
    )
    action = forms.ChoiceField(choices=[("FORWARD", "Forward / endorse"), ("RETURN", "Return")])
    instructions = forms.CharField(
        required=False, max_length=MAX_INSTRUCTIONS_CHARS, widget=forms.Textarea(attrs={"rows": 3})
    )
    sent_at = actual_time("Actual dispatch time")
    due_at = forms.DateTimeField(
        required=False, label="Deadline", widget=forms.DateTimeInput(attrs={"type": "datetime-local"})
    )

    def __init__(self, *args, user, record, **kwargs):
        super().__init__(*args, **kwargs)
        self.scope_endpoints(user=user, record=record)
        self.initial["revision"] = record.revision
        if record.status == "DRAFT":
            self.fields["action"].choices = [("SEND", "Send initial route")]
            self.initial["action"] = "SEND"
        if not record.current_external_office_id:
            self.fields.pop("sent_at")
        else:
            self.fields["sent_at"].required = True

    def clean(self):
        data = super().clean()
        self.require_endpoint(data, "to")
        return data


class ExternalNoteForm(RevisionForm):
    note = forms.CharField(required=False, max_length=MAX_NOTE_CHARS, widget=forms.Textarea(attrs={"rows": 3}))


class ExternalReceiptForm(ExternalNoteForm):
    received_at = actual_time("Actual external receipt time", required=True)


class ExternalMessageForm(RevisionForm):
    message = forms.CharField(max_length=MAX_REMARK_CHARS, widget=forms.Textarea(attrs={"rows": 3}))


class ExternalWorkForm(ExternalMessageForm):
    actual_at = actual_time("Actual external work time", required=True)


class ExternalCompleteForm(ExternalNoteForm):
    completed_at = actual_time("Actual external completion time", required=True)

    def __init__(self, *args, record, **kwargs):
        super().__init__(*args, **kwargs)
        if not record.current_external_office_id:
            self.fields.pop("completed_at")


class ExternalAttachmentForm(RevisionForm):
    uploaded_file = forms.FileField(label="Supporting attachment")
    description = forms.CharField(max_length=255, required=False, widget=forms.Textarea(attrs={"rows": 2}))


class ExternalCorrectionForm(RevisionForm):
    reason = forms.CharField(max_length=MAX_NOTE_CHARS, widget=forms.Textarea(attrs={"rows": 3}))
    sent_at = actual_time("Corrected external dispatch time")
    received_at = actual_time("Corrected external receipt time")
    instructions = forms.CharField(
        required=False, max_length=MAX_INSTRUCTIONS_CHARS, widget=forms.Textarea(attrs={"rows": 3})
    )
    receipt_note = forms.CharField(required=False, max_length=MAX_NOTE_CHARS, widget=forms.Textarea(attrs={"rows": 3}))
    from_external_office = forms.ModelChoiceField(
        queryset=ExternalOffice.objects.none(), label="Corrected external sender"
    )
    to_external_office = forms.ModelChoiceField(
        queryset=ExternalOffice.objects.none(), label="Corrected external recipient"
    )

    def __init__(self, *args, step, **kwargs):
        super().__init__(*args, **kwargs)
        if not step.from_external_office_id:
            self.fields.pop("sent_at")
            self.fields.pop("instructions")
        if not step.to_external_office_id:
            self.fields.pop("received_at")
            self.fields.pop("receipt_note")
        latest_unconsumed = (
            not step.received_at and not step.record.routing_steps.filter(sequence__gt=step.sequence).exists()
        )
        for name in ("from_external_office", "to_external_office"):
            selected_id = getattr(step, name + "_id")
            if not latest_unconsumed or not selected_id:
                self.fields.pop(name)
            else:
                self.fields[name].queryset = ExternalOffice.objects.filter(
                    managing_office=step.record.managing_office
                ).filter(Q(is_active=True) | Q(pk=selected_id))
                self.initial[name] = selected_id

    def clean(self):
        cleaned = super().clean()
        for name in ("sent_at", "received_at"):
            original = self.initial.get(name)
            if name not in self.fields or name not in cleaned or original is None:
                continue
            field = self.fields[name]
            displayed = field.widget.format_value(field.prepare_value(original))
            if self.data.get(self.add_prefix(name)) == displayed:
                # Browser controls display whole seconds. An unchanged input is
                # evidence that only another fact was edited, not permission to
                # truncate the original paper timestamp's microseconds.
                cleaned[name] = original
        return cleaned

"""Form building blocks shared by every app."""

from __future__ import annotations

from django import forms

from .models import OfficeSchedule

TEXT_INPUTS = (
    forms.TextInput,
    forms.EmailInput,
    forms.NumberInput,
    forms.URLInput,
    forms.PasswordInput,
    forms.DateInput,
    forms.DateTimeInput,
    forms.TimeInput,
    forms.Textarea,
)


class BootstrapFormMixin:
    """Adds Bootstrap classes without repeating widget attrs in every form."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            existing = widget.attrs.get("class", "")
            # CheckboxSelectMultiple subclasses SelectMultiple and RadioSelect
            # subclasses Select, so both would fall into the branch below and be
            # stamped `form-select` — dropdown styling on a list of checkboxes.
            # They are tested first because they are the more specific types;
            # this is a latent bug in its own right, not something this form
            # introduced, and it would catch any future multi-checkbox or radio
            # field the same way.
            if isinstance(widget, forms.CheckboxSelectMultiple | forms.RadioSelect):
                widget.attrs["class"] = f"{existing} form-check-input".strip()
            elif isinstance(widget, forms.CheckboxInput):
                widget.attrs["class"] = f"{existing} form-check-input".strip()
            elif isinstance(widget, forms.Select | forms.SelectMultiple):
                widget.attrs["class"] = f"{existing} form-select".strip()
            elif isinstance(widget, TEXT_INPUTS):
                widget.attrs["class"] = f"{existing} form-control".strip()
            if field.required:
                widget.attrs.setdefault("aria-required", "true")


class OfficeScheduleForm(BootstrapFormMixin, forms.ModelForm):
    working_days = forms.TypedChoiceField(coerce=int, label="Working days", choices=[
        (1, "Monday only"), (2, "Monday to Tuesday"), (3, "Monday to Wednesday"),
        (4, "Monday to Thursday"), (5, "Monday to Friday"), (6, "Monday to Saturday"), (7, "Every day"),
    ])
    break_enabled = forms.BooleanField(required=False, label="Exclude a daily break from office time")
    break_start = forms.TimeField(required=False, label="Break starts", input_formats=["%H:%M"],
                                 widget=forms.TimeInput(format="%H:%M", attrs={"type": "time", "step": "60"}))
    break_end = forms.TimeField(required=False, label="Break ends", input_formats=["%H:%M"],
                               widget=forms.TimeInput(format="%H:%M", attrs={"type": "time", "step": "60"}))

    class Meta:
        model = OfficeSchedule
        fields = ["working_days", "opens_at", "closes_at", "break_enabled", "break_start", "break_end"]
        labels = {"opens_at": "Office opens", "closes_at": "Office closes"}
        widgets = {name: forms.TimeInput(format="%H:%M", attrs={"type": "time", "step": "60"})
                   for name in ("opens_at", "closes_at")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial.setdefault("break_enabled", self.instance.break_end > self.instance.break_start)
        for name in ("opens_at", "closes_at"):
            self.fields[name].input_formats = ["%H:%M"]

    def clean(self):
        data = super().clean()
        opens, closes = data.get("opens_at"), data.get("closes_at")
        if opens and closes and closes <= opens:
            self.add_error("closes_at", "Closing time must be after opening time on the same day.")
        if not data.get("break_enabled"):
            if opens:
                data["break_start"] = data["break_end"] = opens
            return data
        start, end = data.get("break_start"), data.get("break_end")
        if start is None:
            self.add_error("break_start", "Enter when the break starts.")
        if end is None:
            self.add_error("break_end", "Enter when the break ends.")
        if start and end and end <= start:
            self.add_error("break_end", "The break must end after it starts.")
        if opens and start and start < opens:
            self.add_error("break_start", "The break must be inside office hours.")
        if closes and end and end > closes:
            self.add_error("break_end", "The break must be inside office hours.")
        if opens and closes and start == opens and end == closes:
            self.add_error("break_end", "Leave some office time outside the break.")
        return data


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """Django's documented recipe for accepting several files in one field."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(
            attrs={"multiple": True}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_clean = super().clean
        if isinstance(data, list | tuple):
            from django.conf import settings

            if len(data) > settings.MAX_UPLOAD_FILES:
                raise forms.ValidationError(f"Upload at most {settings.MAX_UPLOAD_FILES} files at a time.")
            return [single_clean(item, initial) for item in data]
        if data in self.empty_values and initial:
            return initial
        if data in self.empty_values:
            if self.required:
                raise forms.ValidationError(
                    self.error_messages["required"], code="required")
            return []
        return [single_clean(data, initial)]


class ColourInput(forms.TextInput):
    """Native swatch picker.

    Deliberately a TextInput subclass rather than a bare input_type override:
    the value has to keep round-tripping as the "#rrggbb" text the model
    validates, and every browser's colour control already speaks exactly that.
    """

    input_type = "color"


class DateInput(forms.DateInput):
    input_type = "date"


class DateTimeInput(forms.DateTimeInput):
    input_type = "datetime-local"

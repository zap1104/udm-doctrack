"""Form building blocks shared by every app."""

from __future__ import annotations

from django import forms

TEXT_INPUTS = (
    forms.TextInput,
    forms.EmailInput,
    forms.NumberInput,
    forms.URLInput,
    forms.PasswordInput,
    forms.DateInput,
    forms.DateTimeInput,
    forms.Textarea,
)


class BootstrapFormMixin:
    """Adds Bootstrap classes without repeating widget attrs in every form."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            existing = widget.attrs.get("class", "")
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs["class"] = f"{existing} form-check-input".strip()
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                widget.attrs["class"] = f"{existing} form-select".strip()
            elif isinstance(widget, TEXT_INPUTS):
                widget.attrs["class"] = f"{existing} form-control".strip()
            if field.required:
                widget.attrs.setdefault("aria-required", "true")


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """Django's documented recipe for accepting several files in one field."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(attrs={"multiple": True}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_clean(item, initial) for item in data]
        if data in self.empty_values and initial:
            return initial
        if data in self.empty_values:
            if self.required:
                raise forms.ValidationError(self.error_messages["required"], code="required")
            return []
        return [single_clean(data, initial)]


class DateInput(forms.DateInput):
    input_type = "date"


class DateTimeInput(forms.DateTimeInput):
    input_type = "datetime-local"

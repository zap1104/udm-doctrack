"""Personal display preferences; no document or account permissions are changed."""
from django import forms
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views import View

from apps.core.mixins import AppLoginRequiredMixin

from .models import ACCESSIBILITY_DEFAULTS, AccessibilityPreference


class PreferenceSwitchInput(forms.CheckboxInput):
    """A native switch that keeps the existing stored on/off choice values."""

    def __init__(self, on_value, off_value):
        self.on_value = on_value
        self.off_value = off_value
        super().__init__(attrs={"data-preference-on": on_value, "data-preference-off": off_value},
                         check_test=self.is_checked)

    def is_checked(self, value):
        return value is True or value == self.on_value

    def format_value(self, value):
        return self.on_value

    def value_from_datadict(self, data, files, name):
        value = data.get(name, self.off_value)
        if value is True or value in ("on", "true", "True", self.on_value):
            return self.on_value
        if value is False or value in (None, "", "false", "False", self.off_value):
            return self.off_value
        return value  # ChoiceField rejects unrecognized values.


class AccessibilityForm(forms.Form):
    text_size = forms.ChoiceField(label="Text size", choices=AccessibilityPreference.TextSize.choices)
    high_contrast = forms.BooleanField(label="Higher contrast", required=False)
    motion = forms.ChoiceField(label="Reduce motion", choices=AccessibilityPreference.Motion.choices,
                               widget=PreferenceSwitchInput("reduce", "system"))
    underline_links = forms.BooleanField(label="Underline links", required=False)
    spacing = forms.ChoiceField(label="Comfortable spacing", choices=AccessibilityPreference.Spacing.choices,
                                widget=PreferenceSwitchInput("roomy", "standard"))
    larger_targets = forms.BooleanField(label="Larger click targets", required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.widget.attrs["id"] = f"accessibility-{name}"
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.update({"class": "form-check-input", "role": "switch"})
            else:
                field.widget.attrs["class"] = "form-select"


def accessibility_state(request):
    """Read at most once per request; opening a page never creates a row."""
    if not hasattr(request, "_accessibility_state"):
        values = dict(ACCESSIBILITY_DEFAULTS)
        if request.user.is_authenticated:
            preference = AccessibilityPreference.objects.filter(user_id=request.user.pk).first()
            if preference:
                values = preference.as_dict()
        request._accessibility_state = {"values": values, "form": AccessibilityForm(initial=values)}
    return request._accessibility_state


class AccessibilitySettingsView(AppLoginRequiredMixin, View):
    template_name = "accounts/accessibility.html"

    def get(self, request):
        return render(request, self.template_name, {"accessibility_page": True})

    def post(self, request):
        wants_json = "application/json" in request.headers.get("Accept", "")
        action = request.POST.get("action", "save")
        data = ACCESSIBILITY_DEFAULTS if action == "reset" else request.POST
        form = AccessibilityForm(data)
        if action not in {"save", "reset"}:
            form.add_error(None, "Choose Save settings or Reset to defaults.")
        if not form.is_valid():
            if wants_json:
                return JsonResponse({"errors": form.errors.get_json_data()}, status=400)
            state = dict(accessibility_state(request), form=form)
            return render(request, self.template_name,
                          {"accessibility_page": True, "accessibility": state}, status=400)
        preference, _ = AccessibilityPreference.objects.update_or_create(
            user_id=request.user.pk, defaults=form.cleaned_data,
        )
        if wants_json:
            return JsonResponse({"preferences": preference.as_dict()})
        messages.success(request, "Accessibility settings reset." if action == "reset" else "Accessibility settings saved.")
        return redirect("accounts:accessibility")

"""Date and relevance controls shared by Repository and Search."""

from django import forms
from django.conf import settings

from apps.core.forms import DateInput


class RepositorySearchControls(forms.Form):
    minimum_default = None
    date_from = forms.DateField(required=False, label="From", widget=DateInput())
    date_to = forms.DateField(required=False, label="To", widget=DateInput())
    min_relevance = forms.IntegerField(
        required=False, label="Minimum relevance", min_value=0, max_value=100,
        widget=forms.NumberInput(attrs={"type": "range", "min": 0, "max": 100, "step": 5, "class": "form-range", "aria-describedby": "relevance-filter-help"}),
    )
    show_all = forms.BooleanField(required=False, label="Include results below the threshold")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        default = settings.SEARCH_MIN_RELEVANCE_DEFAULT if self.minimum_default is None else self.minimum_default
        self.fields["min_relevance"].initial = default
        if self.is_bound and not self.data.get("min_relevance"):
            # Render the value that cleaning will apply. A native range with
            # no value otherwise shows its browser midpoint instead.
            self.data = self.data.copy()
            self.data["min_relevance"] = str(default)

    def clean_min_relevance(self):
        value = self.cleaned_data.get("min_relevance")
        return self.fields["min_relevance"].initial if value is None else value

    def clean(self):
        cleaned = super().clean()
        date_from, date_to = cleaned.get("date_from"), cleaned.get("date_to")
        if date_from and date_to and date_from > date_to:
            # Workspace views keep valid filters after a stale/invalid input.
            # An invalid interval must not remain as two contradictory bounds.
            cleaned.pop("date_from", None)
            cleaned.pop("date_to", None)
            raise forms.ValidationError("The “From” date is after the “To” date.")
        return cleaned

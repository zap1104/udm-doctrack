from __future__ import annotations

from django import forms
from django.conf import settings

from apps.accounts.models import Office
from apps.core.forms import BootstrapFormMixin, DateInput
from apps.core.models import DocumentType, Tag
from apps.documents.models import Source


class SearchForm(BootstrapFormMixin, forms.Form):
    q = forms.CharField(
        required=False,
        label="",
        widget=forms.TextInput(
            attrs={
                "placeholder": "Search by subject, tag, office, tracking number or words inside the file…",
                "autofocus": True,
                "autocomplete": "off",
                "list": "search-suggestions",
            }
        ),
    )
    year = forms.ChoiceField(required=False, label="Year", choices=[])
    office = forms.ModelChoiceField(
        required=False, label="Office", queryset=Office.active.all(), empty_label="All offices"
    )
    document_type = forms.ModelChoiceField(
        required=False, label="Type", queryset=DocumentType.active.all(), empty_label="All types"
    )
    tag = forms.ModelChoiceField(required=False, label="Tag", queryset=Tag.active.all(), empty_label="Any tag")
    source = forms.ChoiceField(
        required=False, label="Origin", choices=[("", "Any origin")] + list(Source.choices)
    )
    date_from = forms.DateField(required=False, label="From", widget=DateInput())
    date_to = forms.DateField(required=False, label="To", widget=DateInput())
    min_relevance = forms.IntegerField(
        required=False,
        label="Minimum relevance",
        min_value=0,
        max_value=100,
        widget=forms.NumberInput(attrs={"type": "range", "min": 0, "max": 100, "step": 5, "class": "form-range"}),
    )
    show_all = forms.BooleanField(required=False, label="Include results below the threshold")

    def __init__(self, *args, years=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["year"].choices = [("", "All years")] + [(str(year), str(year)) for year in (years or [])]
        self.fields["min_relevance"].initial = settings.SEARCH_MIN_RELEVANCE_DEFAULT

    def clean_min_relevance(self):
        value = self.cleaned_data.get("min_relevance")
        return settings.SEARCH_MIN_RELEVANCE_DEFAULT if value is None else value

    def clean(self):
        cleaned = super().clean()
        date_from, date_to = cleaned.get("date_from"), cleaned.get("date_to")
        if date_from and date_to and date_from > date_to:
            raise forms.ValidationError("The “From” date is after the “To” date.")
        return cleaned

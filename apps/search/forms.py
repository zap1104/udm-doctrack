from __future__ import annotations

from django import forms
from django.conf import settings

from apps.accounts.models import Office
from apps.core.forms import BootstrapFormMixin, DateInput
from apps.core.models import DocumentType, Tag
from apps.documents.models import Source

#: The two search modes, as a value the page can carry in its own URL.
#:
#: Which mode you are in used to be implicit in which page you happened to open:
#: the topbar box and the repository filters and this page were three different
#: searches with three different capabilities, and nothing on screen said so. A
#: reader who wanted to narrow by office had no way of knowing whether the box
#: in front of them could.
QUICK = "quick"
ADVANCED = "advanced"
MODE_CHOICES = [
    (QUICK, "Quick search"),
    (ADVANCED, "Advanced search"),
]

#: Fields that only exist in advanced mode. Named once so the form, the view and
#: the template cannot disagree about what "advanced" contains.
ADVANCED_FIELDS = (
    "year", "office", "document_type", "tag", "source",
    "date_from", "date_to", "min_relevance", "show_all",
)


class SearchForm(BootstrapFormMixin, forms.Form):
    mode = forms.ChoiceField(required=False, choices=MODE_CHOICES, widget=forms.HiddenInput())
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

    @property
    def mode_in_use(self) -> str:
        """Quick unless the request asked for advanced or carries an advanced filter.

        Inferring it from the filters as well as from the parameter matters for
        the links that already exist: a saved URL with `?office=3` predates the
        mode and would otherwise open in quick mode with its own filter applied
        and invisible — the reader would see a narrowed result set and no
        indication why.
        """
        data = self.data if self.is_bound else {}
        if data.get("mode") == ADVANCED:
            return ADVANCED
        if data.get("mode") == QUICK:
            return QUICK
        return ADVANCED if any(data.get(name) for name in ADVANCED_FIELDS) else QUICK

    def clean_min_relevance(self):
        value = self.cleaned_data.get("min_relevance")
        return settings.SEARCH_MIN_RELEVANCE_DEFAULT if value is None else value

    def clean(self):
        cleaned = super().clean()
        # Quick search is the query and nothing else. Without this an advanced
        # filter left in the URL would still narrow a quick search invisibly,
        # which is the confusion the two modes exist to remove.
        if self.mode_in_use == QUICK:
            for name in ADVANCED_FIELDS:
                cleaned[name] = None
            cleaned["show_all"] = False
        return self._validate_dates(cleaned)

    def _validate_dates(self, cleaned):
        date_from, date_to = cleaned.get("date_from"), cleaned.get("date_to")
        if date_from and date_to and date_from > date_to:
            raise forms.ValidationError("The “From” date is after the “To” date.")
        return cleaned


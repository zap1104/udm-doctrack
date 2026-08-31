from __future__ import annotations

from django import forms
from django.conf import settings

from apps.accounts.models import Office
from apps.core.forms import BootstrapFormMixin, DateInput
from apps.core.models import DocumentType, Tag
from apps.documents.models import Source
from apps.tracking.forms import TrackingFilterForm

#: What this page is searching. Two different corpora with two different
#: shapes: the repository holds filed documents with extracted text and a
#: relevance score, tracking holds live records with a status and a custody
#: chain. Nothing scores a tracking record, and nothing gives a document a
#: queue, so the filters and the result rows differ per mode rather than being
#: forced into one shared shape that fits neither.
#:
#: This replaces an earlier Quick/Advanced toggle on the same parameter. That
#: split was about how many filters to show; this one is about what is being
#: searched, which is the distinction the adviser's note was really drawing when
#: it said "repository search is the detailed one; tracking search stays
#: minimal". Repository mode is the detailed search, tracking mode the minimal
#: one, so nothing is lost by folding the two toggles into this.
REPOSITORY = "repository"
TRACKING = "tracking"
MODE_CHOICES = [
    (REPOSITORY, "Repository"),
    (TRACKING, "Tracking"),
]


def mode_from_request(params) -> str:
    """Repository unless tracking is asked for by name.

    Defaulting rather than validating: `/search/` with no mode, and every
    bookmark and link that predates this toggle, must behave exactly as it did
    before, and an unrecognised value is a stale link rather than a reason to
    show an error.
    """
    return TRACKING if params.get("mode") == TRACKING else REPOSITORY


class TrackingSearchForm(TrackingFilterForm):
    """The Tracking page's filters, plus the free-text box search needs.

    A subclass rather than a second form: status, scope, offices and owner keep
    exactly one definition, so the two pages' choice lists cannot drift apart —
    which is the thing that would actually go wrong here.

    `q` lives on this side because the Tracking workspace deliberately has no
    search box; it traded one for the queue pills. This page is the search
    surface, so the box belongs to it.
    """

    q = forms.CharField(
        required=False,
        label="",
        widget=forms.TextInput(
            attrs={
                "placeholder": "Search tracking no., subject or office…",
                "autocomplete": "off",
            }
        ),
    )

    field_order = ("q", "status", "scope", "offices", "owner")


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


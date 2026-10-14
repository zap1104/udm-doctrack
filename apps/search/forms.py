from __future__ import annotations

from django import forms

from apps.accounts.models import Office
from apps.core.document_types import document_type_options
from apps.core.forms import BootstrapFormMixin
from apps.core.models import DocumentType, Tag
from apps.documents.models import Source
from apps.tracking.forms import TrackingFilterForm

from .filter_fields import RepositorySearchControls

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
    """The same query, queue and ordering fields as the Tracking workspace."""

    field_order = ("q", "status", "scope", "offices", "owner", "sort")


class SearchForm(BootstrapFormMixin, RepositorySearchControls):
    field_order = ("q", "year", "office", "document_type", "tag", "source", "date_from", "date_to", "min_relevance", "show_all")
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

    def __init__(self, *args, years=None, visible=None, **kwargs):
        super().__init__(*args, **kwargs)
        if visible is not None:
            self.fields["office"].queryset = Office.objects.filter(documents__in=visible).distinct()
            self.fields["document_type"].queryset = document_type_options(visible)
            self.fields["tag"].queryset = Tag.active.filter(documents__in=visible).distinct()
        self.fields["year"].choices = [("", "All years")] + [(str(year), str(year)) for year in (years or [])]


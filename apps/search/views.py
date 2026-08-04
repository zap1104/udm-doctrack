from __future__ import annotations

from django.http import JsonResponse
from django.shortcuts import render
from django.views.generic import View

from apps.core.mixins import AppLoginRequiredMixin
from apps.documents.models import Document

from .forms import SearchForm
from .services import autocomplete_terms, search_documents


class SearchView(AppLoginRequiredMixin, View):
    """Metadata-first search that still finds records without a tracking number."""

    template_name = "search/search.html"

    def get(self, request):
        visible = Document.objects.visible_to(request.user)
        years = sorted({value for value in visible.values_list("year", flat=True) if value}, reverse=True)
        form = SearchForm(request.GET or None, years=years)

        response = None
        if form.is_valid() and (form.cleaned_data.get("q") or self._has_filters(form)):
            response = search_documents(
                user=request.user,
                query=form.cleaned_data.get("q", ""),
                year=form.cleaned_data.get("year") or None,
                office=form.cleaned_data.get("office"),
                document_type=form.cleaned_data.get("document_type"),
                tag=form.cleaned_data.get("tag"),
                source=form.cleaned_data.get("source") or None,
                date_from=form.cleaned_data.get("date_from"),
                date_to=form.cleaned_data.get("date_to"),
                min_relevance=form.cleaned_data.get("min_relevance"),
                show_below_threshold=form.cleaned_data.get("show_all", False),
            )

        return render(
            request,
            self.template_name,
            {
                "form": form,
                "response": response,
                "results": response.results if response else [],
                "has_searched": response is not None,
            },
        )

    @staticmethod
    def _has_filters(form) -> bool:
        keys = ("year", "office", "document_type", "tag", "source", "date_from", "date_to")
        return any(form.cleaned_data.get(key) for key in keys)


class AutocompleteView(AppLoginRequiredMixin, View):
    def get(self, request):
        terms = autocomplete_terms(request.user, request.GET.get("q", ""))
        return JsonResponse({"results": terms})

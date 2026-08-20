from __future__ import annotations

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import View

from apps.core.mixins import AppLoginRequiredMixin
from apps.documents.models import Document, SearchQueryLog, SearchResultClick

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
                "recent_searches": self._recent_searches(request.user) if response is None else [],
            },
        )

    @staticmethod
    def _recent_searches(user, limit: int = 5):
        """This user's last few distinct searches, for the empty state.

        Only built when there is nothing to show instead — a result page never
        pays for it. Deduplicated in Python rather than with DISTINCT ON so the
        query stays portable; the slice bounds the work whatever the log holds,
        since somebody who searched the same phrase forty times running should
        still get four other suggestions rather than one.
        """
        seen, recent = set(), []
        logs = (
            SearchQueryLog.objects.filter(user=user)
            .exclude(query="")
            .order_by("-created_at")[:40]
        )
        for log in logs:
            key = log.query.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            recent.append(log)
            if len(recent) == limit:
                break
        return recent

    @staticmethod
    def _has_filters(form) -> bool:
        keys = ("year", "office", "document_type", "tag", "source", "date_from", "date_to")
        return any(form.cleaned_data.get(key) for key in keys)


class SearchClickView(AppLoginRequiredMixin, View):
    def get(self, request, log_id, document_id, rank):
        query_log = get_object_or_404(SearchQueryLog, pk=log_id, user=request.user)
        document = get_object_or_404(Document.objects.visible_to(request.user), pk=document_id, is_active=True)
        rank = max(1, min(int(rank), max(query_log.result_count, 1)))
        SearchResultClick.objects.create(
            query_log=query_log,
            user=request.user,
            document=document,
            rank=rank,
        )
        if query_log.clicked_document_id is None:
            SearchQueryLog.objects.filter(pk=query_log.pk, clicked_document__isnull=True).update(
                clicked_document=document
            )
        return redirect(document.get_absolute_url())


class AutocompleteView(AppLoginRequiredMixin, View):
    def get(self, request):
        terms = autocomplete_terms(request.user, request.GET.get("q", ""))
        return JsonResponse({"results": terms})

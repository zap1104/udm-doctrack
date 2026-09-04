from __future__ import annotations

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import View

from apps.accounts.models import Office
from apps.core.mixins import AppLoginRequiredMixin
from apps.documents.models import Document, SearchQueryLog, SearchResultClick
from apps.tracking import services as tracking_services

from .forms import REPOSITORY, TRACKING, SearchForm, TrackingSearchForm, mode_from_request
from .services import autocomplete_terms, search_documents


class SearchView(AppLoginRequiredMixin, View):
    """One search page over two corpora.

    Repository mode searches filed documents and scores them by relevance.
    Tracking mode searches live records and shows them with a status. They are
    branched rather than unioned because the two have nothing in common to sort
    by: a relevance percentage means nothing for a record with no extracted
    text, and a status pill means nothing for a filed document.

    This is a search surface, not a new access path — both branches go through
    the same `visible_to` scoping their own module already applies.
    """

    template_name = "search/search.html"

    def get(self, request):
        mode = mode_from_request(request.GET)
        context = (
            self._tracking(request) if mode == TRACKING else self._repository(request)
        )
        context.update(
            {
                "mode": mode,
                "is_tracking": mode == TRACKING,
                "repository_mode": REPOSITORY,
                "tracking_mode": TRACKING,
            }
        )
        return render(request, self.template_name, context)

    # -- repository ---------------------------------------------------------
    def _repository(self, request) -> dict:
        """Unchanged from before the toggle existed, so `/search/` with no mode
        behaves exactly as every existing bookmark expects."""
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

        return {
            "form": form,
            "response": response,
            "results": response.results if response else [],
            "has_searched": response is not None,
        }

    @staticmethod
    def _has_filters(form) -> bool:
        keys = ("year", "office", "document_type", "tag", "source", "date_from", "date_to")
        return any(form.cleaned_data.get(key) for key in keys)

    # -- tracking -----------------------------------------------------------
    def _tracking(self, request) -> dict:
        """The Tracking workspace's filtering, over the same records, on a page
        that has a search box.

        `active_for` carries the `visible_to` scoping, and the filters run
        through the same `filter_records` / `apply_scope` the workspace uses —
        so a querystring means the same thing on both pages.
        """
        form = TrackingSearchForm(request.GET or None)
        # Every filter that validated, not all-or-nothing: a stale link with one
        # unrecognised value must not quietly return everything while looking
        # like it filtered. Same reasoning as RecordListView.
        form.is_valid()
        data = getattr(form, "cleaned_data", {})

        records = tracking_services.active_for(request.user)
        records = tracking_services.filter_records(
            records,
            query=data.get("q"),
            status=data.get("status"),
            offices=data.get("offices"),
        )
        # The same office resolution the Tracking page uses. Without it this
        # page fell back to the viewer's own office, so the two shared
        # filter_records and apply_scope and still disagreed — 3 records against
        # 0 for the same query string. The drift was never in the queues; it was
        # in who resolved the office.
        as_office = tracking_services.scope_office(request.user, request.GET.get("office"))
        if as_office and data.get("scope") not in tracking_services.OFFICE_SCOPED:
            records = records.filter(
                Q(originating_office=as_office) | Q(current_office=as_office)
            )
        records = tracking_services.apply_scope(
            records, data.get("scope"), request.user, office=as_office
        )
        if data.get("owner"):
            records = tracking_services.apply_scope(records, data["owner"], request.user)

        if form.errors:
            messages.warning(
                request,
                "Ignored a filter that was not recognised: "
                + ", ".join(sorted(form.errors)) + ". Showing the rest.",
            )

        records = records.distinct().order_by("-last_movement_at")
        page = Paginator(records, tracking_services.PAGE_SIZE).get_page(request.GET.get("page"))
        page_records = list(page.object_list)
        # This page listed tracking records without either annotator, so its
        # rows carried no direction and no receiving offices while the Tracking
        # page's did. Same rows, same columns, so the same one query each.
        tracking_services.annotate_direction(page_records, request.user, office=as_office)
        tracking_services.annotate_receiving_offices(page_records)

        # Searched only once something was asked for. An empty box should offer
        # the prompt, not a paginated dump of every active record.
        asked = bool(
            data.get("q") or data.get("status") or data.get("scope")
            or data.get("offices") or data.get("owner")
        )
        return {
            "form": form,
            "response": None,
            "results": page_records,
            "has_searched": asked,
            "page_obj": page,
            "total": page.paginator.count,
            "query": data.get("q") or "",
            "filter_offices": Office.active.all(),
            "selected_office_ids": {str(office.pk) for office in (data.get("offices") or [])},
            "selected_status": data.get("status") or "",
            "selected_scope": data.get("scope") or "",
        }


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

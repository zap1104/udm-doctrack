from __future__ import annotations

from django.contrib import messages
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import View

from apps.accounts.models import Office
from apps.core import filters as core_filters
from apps.core.mixins import AppLoginRequiredMixin
from apps.core.pagination import (
    DEFAULT_PAGE_SIZE,
    page_size_context,
    paginate,
    resolve_page_size,
)
from apps.documents.models import Document, SearchQueryLog, SearchResultClick
from apps.tracking import services as tracking_services
from apps.tracking.forms import queue_pills, status_pills

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

        # Relevance-ranked results are a top-N, not a paged list: page four of
        # a ranking is a worse answer than page one, and the reader who wants
        # more asks for more rather than walking forward through diminishing
        # matches. So the size control moves the cut-off rather than the page —
        # "Show 10" is the ten best matches — using the same parameter and the
        # same words as everywhere else, so there is one control to learn.
        shown, size_token = resolve_page_size(request, DEFAULT_PAGE_SIZE)

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

        # Sliced here rather than passed into `search_documents` as its limit:
        # that limit also sizes the candidate window the ranking is drawn from
        # and feeds `total_matches` and `hidden_count`, so narrowing it to the
        # display size would quietly change the answers as well as the length of
        # the list. SEARCH_RESULT_LIMIT stays the ceiling on what is scored;
        # this is only how much of that scoring is put on the page.
        results = response.results[:shown] if response else []

        return {
            "form": form,
            "response": response,
            "results": results,
            "shown_count": len(results),
            "has_searched": response is not None,
            # No `page_obj`: there are no pages to move between here, so the
            # partial renders the size row and nothing else.
            **page_size_context(size_token),
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
        resolved = core_filters.resolve(request)
        # Said out loud here as well as on the Tracking page. This branch shares
        # every filter with that one and reported none of them, so the same bad
        # office id warned there and passed silently here — the reader believing
        # a narrowed page while looking at all of it.
        if resolved.invalid:
            messages.warning(
                request,
                "Could not apply: " + ", ".join(resolved.invalid)
                + ". The value was not recognised, or your account may not filter by office.",
            )
        records = tracking_services.filter_records(
            records,
            query=data.get("q"),
            # Resolved, not raw: this is what turns `?status=OVERDUE` into the
            # overdue condition, so the two pages answer the legacy spellings
            # the same way.
            status=resolved.statuses,
            offices=data.get("offices"),
            overdue=resolved.overdue,
        )
        # The same office resolution the Tracking page uses. Without it this
        # page fell back to the viewer's own office, so the two shared
        # filter_records and apply_scope and still disagreed — 3 records against
        # 0 for the same query string. The drift was never in the queues; it was
        # in who resolved the office.
        # Same split as RecordListView: only apply_scope understands the
        # ALL_OFFICES sentinel, and it is truthy without being an office.
        narrow_office = resolved.as_office
        queue_office = (
            tracking_services.ALL_OFFICES if resolved.all_offices else narrow_office
        )
        if narrow_office and resolved.scope not in tracking_services.OFFICE_SCOPED:
            records = records.filter(
                Q(originating_office=narrow_office) | Q(current_office=narrow_office)
            )
        records = tracking_services.apply_scope(
            records, resolved.scope, request.user, office=queue_office
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
        # Same page-size control, same parameter, same default as the Tracking
        # workspace — the two pages list the same records and a querystring has
        # to mean the same thing on both.
        page_context = paginate(request, records, tracking_services.PAGE_SIZE)
        page = page_context["page_obj"]
        page_records = list(page.object_list)
        # This page listed tracking records without either annotator, so its
        # rows carried no direction and no receiving offices while the Tracking
        # page's did. Same rows, same columns, so the same one query each.
        tracking_services.annotate_direction(page_records, request.user, office=queue_office)
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
            **page_context,
            "total": page.paginator.count,
            "query": data.get("q") or "",
            "filter_offices": Office.active.all(),
            "selected_office_ids": {str(office.pk) for office in (data.get("offices") or [])},
            "status_choices": status_pills(form),
            "selected_statuses": set(resolved.statuses),
            "selected_scope": data.get("scope") or "",
            # The owner filter was honoured above and had no control on the
            # page, so it could only be reached by hand-editing the URL — and
            # once set there was nothing to say it was on. Same field, same
            # values and same words as the workspace's Show row.
            "selected_owner": data.get("owner") or "",
            # The workspace has always passed this; this page read the raw
            # query string instead, so its Deadline row could not light up for
            # the spellings `resolve` normalises (`?overdue=1`, `true`, `on`)
            # even though they filtered.
            "resolved": resolved,
            # The queues the workspace offers, not all thirteen scope values.
            # See apps.tracking.forms.PILL_SCOPES.
            "queue_choices": queue_pills(form),
            # Which of them the template must draw disabled. Named by the
            # resolver rather than repeated as a literal here, so the pill and
            # the rule that drops the scope cannot disagree.
            "direction_scopes": core_filters.DIRECTION_SCOPES,
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

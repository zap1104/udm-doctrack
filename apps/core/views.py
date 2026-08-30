from __future__ import annotations

import csv
from datetime import datetime, time, timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Avg, Count, DurationField, Exists, F, OuterRef, Q
from django.db.models.functions import TruncMonth
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import TemplateView, View

from apps.accounts.models import Office
from apps.documents.models import Document, SearchQueryLog, SearchResultClick
from apps.tracking import services as tracking_services
from apps.tracking.models import RoutingStep, Status, TrackingRecord

from .colors import STATUS_COLOURS
from .forms import BootstrapFormMixin
from .mixins import AdminRequiredMixin, AppLoginRequiredMixin
from .models import AuditLog, DocumentType, MetadataFieldDefinition, Notification, NotificationRead, Tag, TagRule
from .utils import log_action

NOTIFICATION_KIND_META = {
    Notification.Kind.ROUTED: {"label": "Needs receipt", "icon": "!", "css": "is-actionable"},
    Notification.Kind.RECEIVED: {"label": "Receipt confirmed", "icon": "✓", "css": "is-received"},
    Notification.Kind.COMPLETED: {"label": "Completed", "icon": "✓", "css": "is-completed"},
    Notification.Kind.SHARED: {"label": "Shared with your office", "icon": "↗", "css": "is-shared"},
}


def decorate_notification(notification):
    notification.kind_meta = NOTIFICATION_KIND_META.get(
        notification.kind, {"label": "Update", "icon": "•", "css": ""}
    )
    notification.safe_url = (
        notification.url
        if url_has_allowed_host_and_scheme(notification.url, allowed_hosts=set(), require_https=False)
        else ""
    )
    return notification


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
class DashboardView(AppLoginRequiredMixin, TemplateView):
    template_name = "core/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        inbox = tracking_services.inbox_for(user)
        custody = tracking_services.in_custody_for(user)
        outgoing = tracking_services.outgoing_for(user)
        overdue = tracking_services.overdue_for(user)

        attention = list(inbox[:5])
        if len(attention) < 5:
            attention += [record for record in overdue[: 5 - len(attention)] if record not in attention]
        if len(attention) < 5:
            attention += [record for record in custody[: 5 - len(attention)] if record not in attention]

        today = timezone.localdate()
        office_today = TrackingRecord.objects.none()
        if user.office_id:
            office_today = TrackingRecord.objects.visible_to(user).filter(
                routing_steps__to_office_id=user.office_id, routing_steps__received_at__date=today
            )

        forwarded_today = 0
        if user.office_id:
            forwarded_today = (
                TrackingRecord.objects.visible_to(user)
                .filter(routing_steps__sent_at__date=today, routing_steps__from_office_id=user.office_id)
                .distinct()
                .count()
            )
        completed_today = (
            TrackingRecord.objects.visible_to(user)
            .filter(status=Status.COMPLETED, completed_at__date=today)
            .distinct()
            .count()
        )

        attention = attention[:5]
        tracking_services.annotate_can_confirm(attention, user)

        recent = list(tracking_services.active_for(user)[:8])
        show_office_columns = user.is_records_staff
        if show_office_columns:
            # Both panels in one pass — the helper groups by record, so a
            # second call would only repeat the same query.
            _annotate_destinations(attention + recent)

        context.update(
            {
                "inbox_count": inbox.count(),
                "inbox_new_today": inbox.filter(last_movement_at__date=today).count(),
                "custody_count": custody.count(),
                "outgoing_count": outgoing.count(),
                "overdue_count": overdue.count(),
                "attention_records": attention,
                "recent_records": recent,
                "show_office_columns": show_office_columns,
                "recent_documents": Document.objects.visible_to(user).with_related().order_by("-created_at")[:5],
                "received_today": office_today.distinct().count(),
                "forwarded_today": forwarded_today,
                "completed_today": completed_today,
                "greeting": _greeting(),
            }
        )
        return context


#: Office codes listed in the dashboard's "To" column before it collapses to
#: "+N more". Four fits on one line at the width the column actually gets.
DESTINATIONS_SHOWN = 4


def _unique_offices(offices) -> list:
    """De-duplicated, in the order first seen.

    Offices, not codes: the cells render colour-coded badges now, which need
    the colour and the name as well as the code.
    """
    seen, ordered = set(), []
    for office in offices:
        if office is not None and office.pk not in seen:
            seen.add(office.pk)
            ordered.append(office)
    return ordered


def _annotate_destinations(records) -> None:
    """Attach the office(s) each record's current batch was sent to.

    Sets two lists, because the two dashboard panels ask different questions:

    * `destination_offices` — every office in the current batch. What "where is
      this headed" means on the recent-activity list.
    * `pending_offices_shown` — only the offices that have not confirmed
      receipt yet. What "who are we waiting on" means on the pending-receipt
      panel. Rows that are there for another reason (overdue, or already in
      this office's custody) have nothing outstanding, so they fall back to the
      full list.

    Records staff and administrators watch traffic between offices, not just
    their own queue. Done as one grouped query rather than
    `record.current_offices()` per row, which would be a query per record.
    """
    if not records:
        return
    steps = (
        RoutingStep.objects.filter(record__in=records)
        .select_related("to_office")
        # received_at is read below, and the badge needs the office name and
        # colour. Leaving any of them out here would defer the field and fetch
        # it one row at a time, reintroducing the per-row query this avoids.
        .only(
            "record_id", "batch", "received_at",
            "to_office__code", "to_office__name", "to_office__colour",
        )
    )
    by_record: dict[int, list[RoutingStep]] = {}
    for step in steps:
        by_record.setdefault(step.record_id, []).append(step)

    for record in records:
        current = [
            step for step in by_record.get(record.pk, []) if step.batch == record.current_batch
        ]
        destinations = _unique_offices(step.to_office for step in current)
        awaiting = _unique_offices(
            step.to_office for step in current if step.received_at is None
        )
        # A document sent to every office would make one row several lines
        # tall, so the tail collapses to a count; the record page has them all.
        record.destination_offices = destinations[:DESTINATIONS_SHOWN]
        record.destination_more = max(0, len(destinations) - DESTINATIONS_SHOWN)
        outstanding = awaiting or destinations
        record.pending_offices_shown = outstanding[:DESTINATIONS_SHOWN]
        record.pending_more = max(0, len(outstanding) - DESTINATIONS_SHOWN)


def _greeting() -> str:
    hour = timezone.localtime().hour
    if hour < 12:
        return "Good morning"
    if hour < 18:
        return "Good afternoon"
    return "Good evening"


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
#: Months of history the charts cover. A year is the reporting unit offices
#: actually use, and it keeps every column chart to twelve readable bars.
REPORT_MONTHS = 12

#: Widest year a filter may name. Django's `__year` lookup builds real
#: `datetime` objects for the range bounds, so a year outside what `datetime`
#: can represent raises rather than matching nothing: `?year=10000` raised
#: ValueError and `?year=99999999999999` raised OverflowError, each a 500 on a
#: page any signed-in user can reach by editing the address bar. `isdigit()`
#: alone does not bound anything — it is happy with fourteen digits.
MIN_FILTER_YEAR, MAX_FILTER_YEAR = 1900, 2999


def _filter_year(raw: str) -> int | None:
    """A usable year from the query string, or None if it is not one."""
    raw = (raw or "").strip()
    if not raw.isdigit():
        return None
    value = int(raw)
    return value if MIN_FILTER_YEAR <= value <= MAX_FILTER_YEAR else None


def _percent(part: int, whole: int) -> int:
    return int(round(100 * part / whole)) if whole else 0


def _bar(part: int, whole: int) -> int:
    """Bar width as a percentage. Zero stays zero — a minimum-width stub would
    paint a value that is not there — but a real value never rounds away."""
    if not part or not whole:
        return 0
    return max(1, int(round(100 * part / whole)))


def _humanise_duration(delta) -> str:
    """A timedelta as office language: '2 days 4 hrs', '3 hrs', '18 mins'."""
    if delta is None:
        return "—"
    total = int(delta.total_seconds())
    if total < 60:
        return "under a minute"
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    if days:
        return f"{days} day{'s' if days != 1 else ''} {hours} hr{'s' if hours != 1 else ''}"
    if hours:
        return f"{hours} hr{'s' if hours != 1 else ''} {minutes} min{'s' if minutes != 1 else ''}"
    return f"{minutes} min{'s' if minutes != 1 else ''}"


def _month_window():
    """The last `REPORT_MONTHS` calendar months, plus the datetime they start at.

    Built by walking months rather than subtracting days so February and the
    31-day months land on the right buckets.
    """
    months: list = []
    cursor = timezone.localdate().replace(day=1)
    for _ in range(REPORT_MONTHS):
        months.append(cursor)
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    months.reverse()
    since = timezone.make_aware(
        datetime.combine(months[0], time.min), timezone.get_current_timezone()
    )
    return months, since


def _month_series(queryset, field: str, since):
    """{month: count} for one date field, so two series can share an axis."""
    rows = (
        queryset.filter(**{f"{field}__gte": since})
        .annotate(month=TruncMonth(field))
        .values("month")
        .annotate(total=Count("id", distinct=True))
        .order_by("month")
    )
    return {
        timezone.localtime(row["month"]).date().replace(day=1): row["total"]
        for row in rows
        if row["month"]
    }


def report_filters_from_request(request):
    params = request.GET
    office = Office.objects.filter(pk=params["office"]).first() if params.get("office", "").isdigit() else None
    year = _filter_year(params.get("year", ""))
    status = params.get("status", "")
    if status not in dict(Status.choices) and status != "OVERDUE":
        status = ""
    document_type = (
        DocumentType.objects.filter(pk=params["document_type"]).first()
        if params.get("document_type", "").isdigit() else None
    )
    return {"office": office, "year": year, "status": status, "document_type": document_type}


def apply_report_filters(records, filters):
    office, year = filters["office"], filters["year"]
    if office:
        records = records.filter(Q(originating_office=office) | Q(current_office=office))
    if year:
        records = records.filter(created_at__year=year)
    if filters["status"] == "OVERDUE":
        records = records.filter(due_at__lt=timezone.now()).exclude(status=Status.COMPLETED)
    elif filters["status"]:
        records = records.filter(status=filters["status"])
    if filters["document_type"]:
        records = records.filter(document_type=filters["document_type"])
    return records


class ReportsView(AppLoginRequiredMixin, TemplateView):
    """Records overview. Every number here is computed from the routing steps —
    nothing on this page is a placeholder waiting for a dataset."""

    template_name = "reports/reports.html"

    def _filters(self):
        """Read the filter row, ignoring anything that is not a real choice."""
        return report_filters_from_request(self.request)

    def _apply(self, records, filters):
        return apply_report_filters(records, filters)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        filters = self._filters()

        records = self._apply(TrackingRecord.objects.visible_to(user), filters).distinct()
        documents = Document.objects.visible_to(user)
        if filters["office"]:
            documents = documents.filter(office=filters["office"])
        if filters["year"]:
            documents = documents.filter(created_at__year=filters["year"])
        if filters["document_type"]:
            documents = documents.filter(document_type=filters["document_type"])
        documents = documents.distinct()

        total_records = records.count()
        total_documents = documents.count()
        overdue = records.filter(due_at__lt=timezone.now()).exclude(status=Status.COMPLETED).count()
        awaiting = (
            records.filter(routing_steps__received_at__isnull=True)
            .exclude(status=Status.COMPLETED)
            .distinct()
            .count()
        )
        completed = records.filter(status=Status.COMPLETED).count()

        context.update(
            {
                "filters": filters,
                "report_generated_at": timezone.localtime(),
                "filter_offices": Office.active.all().order_by("name"),
                "filter_types": DocumentType.active.all().order_by("name"),
                "filter_years": self._years(user),
                "has_filters": any(filters.values()),
                "total_records": total_records,
                "total_documents": total_documents,
                "overdue": overdue,
                "awaiting_receipt": awaiting,
                "completed_records": completed,
                "completion_rate": _percent(completed, total_records),
                "by_status": self._by_status(records, total_records),
                "office_flow": self._office_flow(records),
                "monthly": self._monthly(records),
                "turnaround": self._turnaround(records),
                "overdue_offices": self._overdue_offices(records),
                "document_types": self._document_types(documents),
                "document_months": self._document_months(documents),
                "untagged_documents": documents.filter(tags__isnull=True).distinct().count(),
                "documents_without_text": documents.filter(ocr_text="").distinct().count(),
                "top_searches": self._top_searches(),
                "search_analytics": self._search_analytics(),
            }
        )
        return context

    # -- filter options ---------------------------------------------------
    def _years(self, user):
        years = (
            TrackingRecord.objects.visible_to(user)
            .dates("created_at", "year", order="DESC")
        )
        return [value.year for value in years]

    # -- tracking panels ---------------------------------------------------
    def _by_status(self, records, total):
        """One row per status: share of the whole, plus a bar against the
        largest status so the shortest bar is still visible."""
        rows = list(records.values("status").annotate(total=Count("id", distinct=True)).order_by("-total"))
        labels = dict(Status.choices)
        ceiling = max([row["total"] for row in rows], default=0)
        for row in rows:
            row["label"] = labels.get(row["status"], row["status"])
            row["percent"] = _percent(row["total"], total)
            row["bar_percent"] = _bar(row["total"], ceiling)
            row["colour"] = STATUS_COLOURS.get(row["status"], STATUS_COLOURS["DRAFT"])
        return rows

    def _office_flow(self, records):
        """Transferred vs received per office — both series from routing steps.

        Transferred counts steps an office *sent*; received counts steps another
        office confirmed. Counting records by `originating_office` (what this
        panel used to do) misses every forward after the first hop.
        """
        steps = RoutingStep.objects.filter(record__in=records)
        sent = {
            row["from_office__code"]: row["total"]
            for row in steps.exclude(from_office__isnull=True)
            .values("from_office__code")
            .annotate(total=Count("id"))
            if row["from_office__code"]
        }
        received = {
            row["to_office__code"]: row["total"]
            for row in steps.filter(received_at__isnull=False)
            .values("to_office__code")
            .annotate(total=Count("id"))
        }
        names = {
            office.code: office.name
            for office in Office.objects.filter(Q(code__in=sent) | Q(code__in=received))
        }

        rows = []
        for code in sorted(set(sent) | set(received)):
            rows.append(
                {
                    "code": code,
                    "name": names.get(code, code),
                    "sent": sent.get(code, 0),
                    "received": received.get(code, 0),
                }
            )
        rows.sort(key=lambda row: row["sent"] + row["received"], reverse=True)
        rows = rows[:10]
        ceiling = max((max(row["sent"], row["received"]) for row in rows), default=0)
        for row in rows:
            row["sent_percent"] = _bar(row["sent"], ceiling)
            row["received_percent"] = _bar(row["received"], ceiling)
        return rows

    def _monthly(self, records):
        """Created vs completed, month by month, on one shared scale."""
        months, since = _month_window()
        created = _month_series(records, "created_at", since)
        finished = _month_series(records.filter(status=Status.COMPLETED), "completed_at", since)

        ceiling = max(
            [created.get(month, 0) for month in months] + [finished.get(month, 0) for month in months] + [0]
        )
        rows = []
        for month in months:
            opened, closed = created.get(month, 0), finished.get(month, 0)
            rows.append(
                {
                    "month": month,
                    "created": opened,
                    "completed": closed,
                    "created_percent": _bar(opened, ceiling),
                    "completed_percent": _bar(closed, ceiling),
                }
            )
        return {"rows": rows, "ceiling": ceiling, "total": sum(row["created"] for row in rows)}

    def _turnaround(self, records):
        """Real averages from the timestamps the routing steps already carry."""
        steps = RoutingStep.objects.filter(record__in=records, received_at__isnull=False)
        receipt = steps.aggregate(
            value=Avg(F("received_at") - F("sent_at"), output_field=DurationField())
        )["value"]

        done = records.filter(status=Status.COMPLETED, completed_at__isnull=False)
        processing = done.filter(first_received_at__isnull=False).aggregate(
            value=Avg(F("completed_at") - F("first_received_at"), output_field=DurationField())
        )["value"]
        lifetime = done.aggregate(
            value=Avg(F("completed_at") - F("created_at"), output_field=DurationField())
        )["value"]

        with_deadline = done.filter(due_at__isnull=False)
        deadline_total = with_deadline.count()
        on_time = with_deadline.filter(completed_at__lte=F("due_at")).count()

        return {
            "receipt": _humanise_duration(receipt),
            "processing": _humanise_duration(processing),
            "lifetime": _humanise_duration(lifetime),
            "receipt_samples": steps.count(),
            "on_time": on_time,
            "on_time_total": deadline_total,
            "on_time_percent": _percent(on_time, deadline_total),
            "unreceived": RoutingStep.objects.filter(
                record__in=records, received_at__isnull=True
            ).count(),
        }

    def _overdue_offices(self, records):
        """Where overdue documents are sitting — a queue to chase, not a total."""
        rows = list(
            records.filter(due_at__lt=timezone.now())
            .exclude(status=Status.COMPLETED)
            .exclude(current_office__isnull=True)
            .values("current_office__code", "current_office__name")
            .annotate(total=Count("id", distinct=True))
            .order_by("-total")[:8]
        )
        ceiling = max([row["total"] for row in rows], default=0)
        for row in rows:
            row["percent"] = _bar(row["total"], ceiling)
        return rows

    # -- document panels ---------------------------------------------------
    def _document_types(self, documents):
        rows = list(
            documents.values("document_type__name")
            .annotate(total=Count("id", distinct=True))
            .order_by("-total")[:8]
        )
        ceiling = max([row["total"] for row in rows], default=0)
        for row in rows:
            row["label"] = row["document_type__name"] or "Unclassified"
            row["percent"] = _bar(row["total"], ceiling)
        return rows

    def _document_months(self, documents):
        months, since = _month_window()
        series = _month_series(documents, "created_at", since)
        ceiling = max(series.values(), default=0)
        return [
            {
                "month": month,
                "total": series.get(month, 0),
                "percent": _bar(series.get(month, 0), ceiling),
            }
            for month in months
        ]

    def _top_searches(self):
        rows = list(
            SearchQueryLog.objects.values("query")
            .annotate(total=Count("id", distinct=True), clicks=Count("result_clicks"))
            .order_by("-total")[:8]
        )
        ceiling = max([row["total"] for row in rows], default=0)
        for row in rows:
            row["percent"] = _bar(row["total"], ceiling)
        return rows

    def _search_analytics(self):
        total_queries = SearchQueryLog.objects.count()
        clicked_queries = SearchQueryLog.objects.filter(result_clicks__isnull=False).distinct().count()
        clicks = SearchResultClick.objects.count()
        average_rank = SearchResultClick.objects.aggregate(value=Avg("rank"))["value"]
        return {
            "queries": total_queries,
            "clicks": clicks,
            "clicked_query_percent": _percent(clicked_queries, total_queries),
            "average_rank": round(average_rank, 1) if average_rank is not None else None,
        }


class HealthzView(View):
    """Cheap platform probe; ?deep=1 adds a recent django-q success check."""
    def get(self, request):
        from django.core.cache import cache
        from django.core.files.storage import default_storage
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        checks = {}
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            checks["database"] = True
        except Exception:
            checks["database"] = False
        try:
            checks["cache"] = "doctrack_cache" in connection.introspection.table_names()
            cache.get("healthz")
        except Exception:
            checks["cache"] = False
        try:
            executor = MigrationExecutor(connection)
            checks["migrations"] = not executor.migration_plan(executor.loader.graph.leaf_nodes())
        except Exception:
            checks["migrations"] = False
        try:
            if hasattr(default_storage, "location"):
                checks["storage"] = bool(default_storage.location)
            else:
                default_storage.exists(".healthz")
                checks["storage"] = True
        except Exception:
            checks["storage"] = False
        if request.GET.get("deep") == "1":
            try:
                from django_q.models import Success
                checks["worker"] = Success.objects.filter(stopped__gte=timezone.now() - timedelta(minutes=10)).exists()
            except Exception:
                checks["worker"] = False
        healthy = all(checks.values())
        response = JsonResponse({"status": "ok" if healthy else "unhealthy", "checks": checks}, status=200 if healthy else 503)
        response["Cache-Control"] = "no-store"
        return response


class NotificationListView(AppLoginRequiredMixin, View):
    PAGE_SIZE = 25

    def get(self, request):
        user = request.user
        read_subquery = NotificationRead.objects.filter(notification_id=OuterRef("pk"), user=user)
        notification_query = (
            Notification.objects.filter(office_id=user.office_id)
            .select_related("tracking_record", "document")
            .annotate(is_read=Exists(read_subquery))
        )
        active_filter = request.GET.get("filter", "all")
        if active_filter == "unread":
            notification_query = notification_query.filter(resolved_at__isnull=True, is_read=False)
        elif active_filter not in {"all", "unread"}:
            active_filter = "all"

        active_kind = request.GET.get("kind", "")
        valid_kinds = {value for value, _label in Notification.Kind.choices}
        if active_kind not in valid_kinds:
            active_kind = ""
        if active_kind:
            notification_query = notification_query.filter(kind=active_kind)

        notification_query = notification_query.order_by("is_read", "-created_at")
        page_obj = Paginator(notification_query, self.PAGE_SIZE).get_page(request.GET.get("page"))
        today = timezone.localdate()
        yesterday = today - timedelta(days=1)
        previous_day_group = None
        for notification in page_obj.object_list:
            decorate_notification(notification)
            day = timezone.localtime(notification.created_at).date()
            notification.day_group = "Today" if day == today else "Yesterday" if day == yesterday else day.strftime("%d %B %Y")
            notification.show_day_header = notification.day_group != previous_day_group
            previous_day_group = notification.day_group

        return render(
            request,
            "core/notifications.html",
            {
                "notifications": page_obj.object_list,
                "page_obj": page_obj,
                "notification_count": page_obj.paginator.count,
                "active_filter": active_filter,
                "active_kind": active_kind,
                "notification_kinds": Notification.Kind.choices,
            },
        )


class NotificationCountView(AppLoginRequiredMixin, View):
    def get(self, request):
        from .notifications import in_app_enabled, unread_count

        enabled = in_app_enabled(request.user)
        unread = unread_count(request.user) if enabled else 0
        response = render(
            request,
            "partials/_notification_badge.html",
            {"unread_notifications": unread, "notification_in_app_enabled": enabled},
        )
        response["Cache-Control"] = "no-store"
        return response


class NotificationReadView(AppLoginRequiredMixin, View):
    def post(self, request, pk):
        from .notifications import mark_read

        notification = get_object_or_404(Notification, pk=pk, office_id=request.user.office_id)
        if not mark_read(notification, request.user):
            raise Http404
        if request.headers.get("HX-Request") == "true":
            notification.is_read = True
            decorate_notification(notification)
            return render(request, "core/_notification_row.html", {"notification": notification})
        if notification.url and url_has_allowed_host_and_scheme(
            notification.url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return redirect(notification.url)
        return redirect("core:notifications")


class NotificationMarkAllReadView(AppLoginRequiredMixin, View):
    def post(self, request):
        from .notifications import mark_all_read

        mark_all_read(request.user, Notification.objects.filter(office_id=request.user.office_id, resolved_at__isnull=True))
        return redirect("core:notifications")


class PrintLogView(AppLoginRequiredMixin, View):
    """Records that a user actually opened a print dialog.

    Printing happens in the browser, so there is no request to log on its own —
    the page posts here from its `beforeprint` handler. Paper copies of routed
    documents leave the system entirely, which is exactly the movement an audit
    trail exists to capture.
    """

    MAX_LABEL = 120

    def post(self, request):
        label = (request.POST.get("label") or "a page").strip()[: self.MAX_LABEL]
        reference = (request.POST.get("reference") or "").strip()[: self.MAX_LABEL]
        summary = f"Printed {label}" + (f" ({reference})" if reference else "")
        log_action(
            AuditLog.Action.PRINT,
            summary,
            actor=request.user,
            target_type=request.POST.get("target_type", "")[:64],
            target_id=request.POST.get("target_id", "")[:64],
            extra={"label": label, "reference": reference},
            request=request,
        )
        return JsonResponse({"logged": True})


#: Leading characters that make Excel, LibreOffice and Sheets read a cell as a
#: formula rather than text. A subject line is free text typed by staff, so
#: "=cmd|..." or "+HYPERLINK(...)" reaches this export intact and executes when
#: somebody opens the file — the spreadsheet, not the browser, is the sink.
_CSV_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value) -> str:
    """One CSV field, neutered so a spreadsheet cannot execute it.

    Prefixing with an apostrophe is the conventional escape: every major
    spreadsheet strips it on display and treats the rest as literal text.
    """
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_CSV_FORMULA_LEADS) else text


class ReportExportView(AppLoginRequiredMixin, View):
    """CSV of the active tracking queue — useful evidence for the defence."""

    def get(self, request):
        filters = report_filters_from_request(request)
        records = apply_report_filters(TrackingRecord.objects.visible_to(request.user), filters).with_related().distinct().order_by("-created_at")
        total = records.count()
        cap = 5000
        response = HttpResponse(content_type="text/csv")
        stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
        parts = ["doctrack-records"]
        if filters["office"]:
            parts.append(filters["office"].code)
        if filters["year"]:
            parts.append(str(filters["year"]))
        if filters["status"]:
            parts.append(filters["status"].lower())
        response["Content-Disposition"] = f'attachment; filename="{"-".join(parts)}-{stamp}.csv"'
        writer = csv.writer(response)
        writer.writerow(["Active filters", "; ".join(f"{key}={value}" for key, value in filters.items() if value) or "none"])
        writer.writerow(["Exported rows", min(total, cap), "Row cap", cap, "Total matching rows", total])
        writer.writerow(
            ["Tracking number", "Subject", "Type", "Originating office", "Current office",
             "Status", "Created", "Last movement", "Completed"]
        )
        for record in records[:cap]:
            writer.writerow(
                _csv_cell(value)
                for value in (
                    record.tracking_number,
                    record.subject,
                    record.document_type.name if record.document_type_id else "",
                    record.originating_office.code,
                    record.current_office.code if record.current_office_id else "",
                    record.display_status_label,
                    timezone.localtime(record.created_at).strftime("%Y-%m-%d %H:%M"),
                    timezone.localtime(record.last_movement_at).strftime("%Y-%m-%d %H:%M"),
                    timezone.localtime(record.completed_at).strftime("%Y-%m-%d %H:%M") if record.completed_at else "",
                )
            )
        return response


# ---------------------------------------------------------------------------
# Administration — master data
# ---------------------------------------------------------------------------
def _model_form(model_class, field_names):
    from django import forms

    return type(
        f"{model_class.__name__}Form",
        (BootstrapFormMixin, forms.ModelForm),
        {"Meta": type("Meta", (), {"model": model_class, "fields": field_names})},
    )


MASTER_DATA = {
    "offices": {
        "model": Office,
        "label": "Offices",
        "singular": "office",
        "fields": [
            "code", "name", "short_name", "cluster", "parent", "head_name", "email",
            "location", "colour", "sort_order", "is_active",
        ],
        "columns": [("name", "Office"), ("code", "Code"), ("cluster", "Cluster"), ("is_active", "Active")],
        "help": "Every office that can send or receive a document. The code appears inside "
                "every tracking number, so changing one does not rewrite numbers already issued.",
        # Offices are not any one office's to edit, and the system is meant to
        # reach past OVPA later — so new offices must be addable without a code
        # change, but only by somebody whose remit is the whole institution.
        "system_admin_only": True,
    },
    "document-types": {
        "model": DocumentType,
        "label": "Document types",
        "singular": "document type",
        "fields": ["code", "name", "description", "retention_years", "sort_order", "is_active"],
        "columns": [("name", "Name"), ("code", "Code"), ("retention_years", "Retention (years)"), ("is_active", "Active")],
        "help": "Memorandum, letter, work order… Types drive filing, filters and search weighting.",
    },
    "tags": {
        "model": Tag,
        "label": "Tags",
        "singular": "tag",
        "fields": ["name", "category", "description", "is_active"],
        "columns": [("name", "Tag"), ("category", "Category"), ("usage_count", "Used on"), ("is_active", "Active")],
        "help": "Shared vocabulary for filing. Keep tags short and lower-case.",
    },
    "metadata-rules": {
        "model": TagRule,
        "label": "Metadata rules",
        "singular": "metadata rule",
        "fields": [
            "name", "pattern", "match_type", "search_field", "suggest_tag", "suggest_document_type",
            "suggest_office", "suggest_metadata_key", "suggest_metadata_value", "confidence", "priority", "is_active",
        ],
        "columns": [("name", "Rule"), ("match_type", "Match"), ("pattern", "Pattern"), ("priority", "Priority"), ("is_active", "Active")],
        "help": "When extracted text matches a pattern, the system proposes this tag, type or office. "
                "Rules are how the archive gets smarter without any AI training.",
    },
    "metadata-fields": {
        "model": MetadataFieldDefinition,
        "label": "Metadata fields",
        "singular": "metadata field",
        "fields": [
            "key", "label", "field_type", "choices_csv", "help_text", "is_required",
            "is_searchable", "show_in_list", "sort_order", "is_active",
        ],
        "columns": [("label", "Field"), ("key", "Key"), ("field_type", "Type"), ("is_required", "Required"), ("is_searchable", "Searchable")],
        "help": "Add a field here and it appears on every metadata review screen — no code change needed.",
    },
}


def master_data_for(user):
    """The master-data sections this user may open.

    Filtered rather than shown-and-refused, so an office administrator is not
    offered a tile that answers with a permission error.
    """
    return {
        slug: config
        for slug, config in MASTER_DATA.items()
        if not config.get("system_admin_only") or user.is_system_admin
    }


class MasterDataAccessMixin:
    """Refuse a section this user may not open.

    The gate is on the section config, not on the URL, so adding a restricted
    section to MASTER_DATA is enough to restrict it — there is no second list to
    keep in step.
    """

    def config_or_404(self, slug):
        config = MASTER_DATA.get(slug)
        if not config:
            raise Http404("Unknown master data section")
        if config.get("system_admin_only") and not self.request.user.is_system_admin:
            raise PermissionDenied(
                f"Only system administrators can change {config['label'].lower()}."
            )
        return config


class AdministrationHomeView(AdminRequiredMixin, TemplateView):
    template_name = "administration/home.html"

    def get_context_data(self, **kwargs):
        from apps.accounts.models import User

        context = super().get_context_data(**kwargs)
        context.update(
            {
                "user_count": User.objects.filter(is_active=True).count(),
                "office_count": Office.objects.filter(is_active=True).count(),
                "type_count": DocumentType.objects.filter(is_active=True).count(),
                "tag_count": Tag.objects.filter(is_active=True).count(),
                "rule_count": TagRule.objects.filter(is_active=True).count(),
                "field_count": MetadataFieldDefinition.objects.filter(is_active=True).count(),
                "recent_audit": AuditLog.objects.all()[:10],
                "master_data": master_data_for(self.request.user),
            }
        )
        return context


class MasterDataListView(MasterDataAccessMixin, AdminRequiredMixin, View):
    template_name = "administration/masterdata_list.html"

    def get(self, request, slug):
        config = self.config_or_404(slug)
        objects = config["model"].objects.all()
        query = request.GET.get("q", "").strip()
        if query:
            first_field = config["columns"][0][0]
            objects = objects.filter(**{f"{first_field}__icontains": query})
        return render(
            request,
            self.template_name,
            {"slug": slug, "config": config, "objects": objects, "query": query,
             "master_data": master_data_for(request.user)},
        )


class MasterDataEditView(MasterDataAccessMixin, AdminRequiredMixin, View):
    template_name = "administration/masterdata_form.html"

    def dispatch(self, request, *args, **kwargs):
        # Resolved before dispatch so both GET and POST are gated by one check.
        # AdminRequiredMixin runs first (it is later in the MRO), so an
        # unauthenticated request is still redirected rather than 404'd.
        if request.user.is_authenticated:
            self.config = self.config_or_404(kwargs.get("slug"))
        return super().dispatch(request, *args, **kwargs)

    def _instance(self, pk):
        return get_object_or_404(self.config["model"], pk=pk) if pk else None

    def get(self, request, slug, pk=None):
        form_class = _model_form(self.config["model"], self.config["fields"])
        form = form_class(instance=self._instance(pk))
        return render(
            request,
            self.template_name,
            {"slug": slug, "config": self.config, "form": form, "object": self._instance(pk),
             "master_data": master_data_for(request.user)},
        )

    def post(self, request, slug, pk=None):
        instance = self._instance(pk)
        form_class = _model_form(self.config["model"], self.config["fields"])
        form = form_class(request.POST, instance=instance)
        if form.is_valid():
            obj = form.save()
            from .utils import log_action

            log_action(
                AuditLog.Action.UPDATE if pk else AuditLog.Action.CREATE,
                f"{'Updated' if pk else 'Created'} {self.config['singular']} “{obj}”",
                actor=request.user,
                target=obj,
                request=request,
            )
            messages.success(request, f"Saved {self.config['singular']} “{obj}”.")
            return redirect(reverse("core:masterdata_list", args=[slug]))
        messages.error(request, "Check the highlighted fields.")
        return render(
            request,
            self.template_name,
            {"slug": slug, "config": self.config, "form": form, "object": instance,
             "master_data": master_data_for(request.user)},
        )


class AuditLogView(AdminRequiredMixin, TemplateView):
    template_name = "administration/audit_log.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        entries = AuditLog.objects.select_related("actor")
        action = self.request.GET.get("action", "")
        query = self.request.GET.get("q", "").strip()
        if action:
            entries = entries.filter(action=action)
        if query:
            entries = entries.filter(Q(summary__icontains=query) | Q(actor_label__icontains=query))
        context.update(
            {
                "entries": entries[:300],
                "actions": AuditLog.Action.choices,
                "selected_action": action,
                "query": query,
                "master_data": MASTER_DATA,
            }
        )
        return context


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
def error_403(request, exception=None):
    return render(request, "errors/403.html", {"reason": str(exception) if exception else ""}, status=403)


def error_404(request, exception=None):
    return render(request, "errors/404.html", status=404)


def error_500(request):
    return render(request, "errors/500.html", status=500)

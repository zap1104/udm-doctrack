from __future__ import annotations

import csv
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Avg, Count, Exists, OuterRef, Q
from django.db.models.functions import TruncMonth
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import TemplateView, View

from apps.accounts.models import Office
from apps.documents.models import Document, SearchQueryLog, SearchResultClick, Source
from apps.tracking import services as tracking_services
from apps.tracking.models import COMPLETED_STATUSES, RoutingStep, Status, TrackingRecord

from . import analytics
from .analytics import bar as _bar
from .analytics import month_series as _month_series
from .analytics import month_window as _month_window
from .analytics import percent as _percent
from .business_time import average_business_seconds, humanise_business_seconds
from .forms import BootstrapFormMixin
from .mixins import AdminRequiredMixin, AppLoginRequiredMixin
from .models import AuditLog, DocumentType, MetadataFieldDefinition, Notification, NotificationRead, Tag, TagRule
from .utils import log_action

NOTIFICATION_KIND_META = {
    Notification.Kind.ROUTED: {"label": "Needs receipt", "icon": "!", "css": "is-actionable"},
    Notification.Kind.RECEIVED: {"label": "Receipt confirmed", "icon": "✓", "css": "is-received"},
    Notification.Kind.COMPLETED: {"label": "Completed", "icon": "✓", "css": "is-completed"},
    Notification.Kind.SHARED: {"label": "Shared with your office", "icon": "↗", "css": "is-shared"},
    # Both are things somebody has to act on, so they wear the actionable
    # treatment rather than the informational one.
    Notification.Kind.UNRECEIVED: {"label": "Still not received", "icon": "!", "css": "is-actionable"},
    Notification.Kind.OVERDUE: {"label": "Past its deadline", "icon": "!", "css": "is-actionable"},
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
#: Colour per breakdown slice, as the CSS custom properties themselves rather
#: than hex values. The stacked bar already paints these from
#: `.breakdown-seg--<key>` in doctrack.css; the ring and its swatches read the
#: same variables, so a palette change lands in one file and every mark on the
#: page moves together. Hard-coding hex here would be a second palette that
#: silently stops matching the first.
BREAKDOWN_COLOURS = {
    "incoming": "var(--udm-green)",
    "pending_receipt": "var(--udm-gold)",
    "in_process": "var(--udm-teal)",
    "overdue": "var(--udm-red)",
    "pending_upload": "var(--chart-one)",
    "historical": "var(--udm-muted)",
    "completed": "var(--chart-three)",
}


class DashboardView(AppLoginRequiredMixin, TemplateView):
    template_name = "core/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        scope = self._scope()

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
            # Both halves of completion: the work was finished today whether or
            # not an administrator has approved it into the repository yet.
            # Counting only COMPLETED would report zero for an office that
            # finished ten documents this morning and is waiting on approval.
            .filter(status__in=COMPLETED_STATUSES, completed_at__date=today)
            .distinct()
            .count()
        )

        attention = attention[:5]
        tracking_services.annotate_can_confirm(attention, user)
        # Same test the tracking list uses (apps/tracking/views.py): the bulk
        # footer only appears when at least one row on this page could actually
        # be received, so nobody is shown an attestation they cannot satisfy.
        can_bulk_receive = any(record.can_confirm_now for record in attention)

        recent = list(tracking_services.active_for(user)[:8])
        show_office_columns = user.is_records_staff
        if show_office_columns:
            # Both panels in one pass — the helper groups by record, so a
            # second call would only repeat the same query.
            _annotate_destinations(attention + recent)

        breakdown = self._combined_breakdown(user, scope["office"])
        context.update(self._analytics(user, scope, breakdown))
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
                "can_bulk_receive": can_bulk_receive,
                "can_start_work": _can_start_work(user),
                "breakdown": breakdown,
                "breakdown_summary": self._breakdown_summary(breakdown),
                "printed_at": timezone.localtime(),
            }
        )
        return context

    # -- scope ------------------------------------------------------------
    def _scope(self):
        """Which office the analytics panels describe.

        Administrators watch traffic between offices, so they may narrow the
        page to one of them. Everybody else gets their own office and no
        control, exactly as before.

        Gated on `is_office_admin` — the property behind AdminRequiredMixin's
        ("ADMIN", "SYSTEM_ADMIN") — not on `is_records_staff`, which is everyone
        except a viewer and would hand the picker to the ordinary office users
        it is not for.

        The chosen office is applied *on top of* `visible_to(user)`, never
        instead of it, so `?office=` can only ever narrow what somebody is
        already entitled to see. An office administrator, whose rights stop at
        their own office, therefore gains nothing by naming a different one.
        """
        user = self.request.user
        if not user.is_office_admin:
            return {"office": None, "offices": [], "can_pick": False, "label": "Your office"}

        raw = self.request.GET.get("office", "")
        office = Office.objects.filter(pk=raw).first() if raw.isdigit() else None
        return {
            "office": office,
            "offices": Office.active.all().order_by("name"),
            "can_pick": True,
            "label": office.name if office else "All offices",
        }

    def _scoped(self, user, office):
        """The two base querysets the analytics panels and the memo read from."""
        records = TrackingRecord.objects.visible_to(user)
        documents = Document.objects.visible_to(user)
        if office:
            # The same pairing Reports filters on, so "MED" means the same thing
            # on both pages: raised by that office, or sitting there now.
            records = records.filter(Q(originating_office=office) | Q(current_office=office))
            documents = documents.filter(office=office)
        return records.distinct(), documents.distinct()

    # -- analytics panels --------------------------------------------------
    def _analytics(self, user, scope, breakdown):
        """The six chart panels, all from apps/core/analytics.py.

        Every figure comes from the same scoped querysets the tables above use,
        so nothing on the page can disagree with anything else on it.
        """
        records, documents = self._scoped(user, scope["office"])

        overdue_rows = analytics.overdue_offices(records)
        overdue = analytics.overdue_summary(records, overdue_rows, breakdown["total"])
        uploads = analytics.uploads_by_office(documents, records)
        trend = analytics.turnaround_by_month(records)

        return {
            "scope": scope,
            "overdue_offices": overdue_rows,
            "overdue_summary": overdue,
            "tracking_donut": self._domain_donut(breakdown, "tracking"),
            "repository_donut": self._domain_donut(breakdown, "repository"),
            "monthly": analytics.monthly_volume(records),
            "turnaround_trend": trend,
            "turnaround_trend_points": self._trend_points(trend),
            "turnaround_trend_geometry": self._trend_geometry(),
            "turnaround": analytics.turnaround(records),
            "uploads_by_office": uploads,
            "live_by_status": analytics.live_records_by_status(records),
            "memo": self._memo(scope, breakdown, overdue, trend, uploads),
        }

    def _domain_donut(self, breakdown, group):
        """Ring segments for one domain's slice of the combined breakdown.

        One ring per domain rather than a single combined one. The combined
        version could show the split between tracking and the repository but
        not the shape of either: five tracking stages competing with two
        repository ones in a single ring left the tracking slices too thin to
        read, which is the half somebody acts on.

        The ring is drawn with one `conic-gradient`, which needs its stops as
        cumulative percentages — computed here rather than in the template,
        because a running total is arithmetic and templates in this codebase do
        not do arithmetic.

        Percentages are recomputed against this domain's own subtotal. Reusing
        the grand-total percentages already on the slices would leave each ring
        summing to its share of everything rather than to 100%, so a Repository
        ring covering a third of all documents would be drawn as a third of a
        circle with two thirds of it blank.

        Rounding is absorbed by the final segment so the ring always closes:
        independently rounded values leave either a hairline gap or an overlap,
        and a ring with a slit in it reads as a rendering fault.
        """
        domain = [
            row for row in breakdown["slices"]
            if row["group"] == group and row["total"]
        ]
        if not domain:
            return {"stops": "", "slices": [], "total": 0}

        subtotal = sum(row["total"] for row in domain)
        # Copied, not mutated: `_breakdown_summary` and `_memo` read the
        # grand-total percentages off these same dicts, and rewriting them here
        # would silently change what the prose beneath the rings says.
        domain = [{**row, "percent": _percent(row["total"], subtotal)} for row in domain]

        stops, running = [], 0
        for index, row in enumerate(domain):
            share = row["percent"] if index < len(domain) - 1 else 100 - running
            start, running = running, running + share
            stops.append("{} {}% {}%".format(row["colour"], start, running))
        return {"stops": ", ".join(stops), "slices": domain, "total": subtotal}

    #: Plot box for the turnaround trend line, in SVG user units. Fixed, so the
    #: polyline can be built from plain numbers here and scaled by CSS in the
    #: browser rather than recomputed per viewport.
    #:
    #: 640x170. It was briefly 640x240, to compensate for the panel being moved
    #: into a half-width column where the flatter ratio resolved to about 110px
    #: of height — not enough to see one of three series cross another. The panel
    #: spans the page again, so the plot is roughly 630px wide beside its summary
    #: and 170 gives it about 168px of height. The compensation went with the
    #: width that caused it.
    #:
    #: No left gutter, and that part stays: it used to reserve 34 units for
    #: y-axis labels that were never drawn — the scale is stated in words beneath
    #: the chart — so the gutter was dead space at any width.
    TREND_WIDTH, TREND_HEIGHT = 640, 170
    TREND_PAD_LEFT, TREND_PAD_TOP, TREND_PAD_BOTTOM = 0, 12, 24

    #: Horizontal rules behind the plot, as fractions of the ceiling. The last
    #: is the baseline and is drawn heavier.
    TREND_GRID_STEPS = (1.0, 0.75, 0.5, 0.25, 0.0)

    def _trend_geometry(self):
        """The plot box and its grid lines, in the units the viewBox declares.

        Computed here rather than written into the template as literals. They
        were literals, and the plot's own geometry lived in Python: the two
        agreed only because they had been matched by hand, so changing the box
        moved the lines off the data silently.
        """
        plot_h = self.TREND_HEIGHT - self.TREND_PAD_TOP - self.TREND_PAD_BOTTOM
        lines = []
        for index, step in enumerate(self.TREND_GRID_STEPS):
            lines.append(
                {
                    "y": round(self.TREND_PAD_TOP + (1 - step) * plot_h, 1),
                    # The baseline is the axis, not another rule behind the data.
                    "axis": index == len(self.TREND_GRID_STEPS) - 1,
                }
            )
        return {
            "width": self.TREND_WIDTH,
            "height": self.TREND_HEIGHT,
            "view_box": f"0 0 {self.TREND_WIDTH} {self.TREND_HEIGHT}",
            "grid": lines,
        }

    def _trend_points(self, trend):
        """The three turnaround series as SVG polylines.

        Months with no completions are skipped rather than plotted at zero: a
        month in which nothing was finished did not take zero days to finish
        things, and a line dropping to the axis would say exactly that.
        """
        rows = trend["rows"]
        if not rows or not trend["has_data"]:
            return []

        plot_w = self.TREND_WIDTH - self.TREND_PAD_LEFT
        plot_h = self.TREND_HEIGHT - self.TREND_PAD_TOP - self.TREND_PAD_BOTTOM
        step = plot_w / len(rows)
        ceiling = trend["ceiling"] or 1

        def place(index, value):
            x = self.TREND_PAD_LEFT + (index + 0.5) * step
            y = self.TREND_PAD_TOP + (1 - value / ceiling) * plot_h
            return round(x, 1), round(y, 1)

        series = [
            ("receipt", "To mark-as-received", "var(--chart-two)"),
            ("processing", "Processing", "var(--chart-one)"),
            ("lifetime", "Total lifetime", "var(--chart-three)"),
        ]
        built = []
        for key, label, colour in series:
            points = [
                place(index, row[key])
                for index, row in enumerate(rows)
                if row[key] is not None
            ]
            if not points:
                continue
            built.append(
                {
                    "key": key,
                    "label": label,
                    "colour": colour,
                    "polyline": " ".join(f"{x},{y}" for x, y in points),
                    "dots": [{"x": x, "y": y} for x, y in points],
                }
            )
        return built

    def _memo(self, scope, breakdown, overdue, trend, uploads):
        """The dashboard's own numbers, said in sentences.

        Assembled here rather than in the template for the same reason
        `_breakdown_summary` is: a memo is prose about figures, and prose built
        beside the figures cannot drift from them.

        Descriptive, never comparative. It reports what is on the page; it does
        not say whether that is better or worse than last month, because this
        page has no basis for a verdict on an office and a printed memo carrying
        one would outlive the context that produced it.
        """
        total = breakdown["total"]
        # "All offices" takes a plural verb; a named office and "Your office"
        # take a singular one. The generic labels are lowercased mid-sentence —
        # an office's real name keeps its capitals, "All offices" should not.
        several = scope["can_pick"] and not scope["office"]
        holder = scope["label"] if scope["office"] else scope["label"].lower()
        lines = [
            "As at {:%d %B %Y}, {} {} {} document{} across tracking and the "
            "repository: {} still moving ({}%) and {} filed ({}%).".format(
                timezone.localtime(),
                holder,
                "hold" if several else "holds",
                total,
                "" if total == 1 else "s",
                breakdown["tracking_total"],
                breakdown["tracking_percent"],
                breakdown["repository_total"],
                breakdown["repository_percent"],
            )
        ]

        late = overdue["total"]
        if late:
            sentence = (
                "{} document{} {} past the deadline set for {} ({}% of everything "
                "on record)".format(
                    late,
                    "" if late == 1 else "s",
                    "is" if late == 1 else "are",
                    "it" if late == 1 else "them",
                    overdue["percent_of_all"],
                )
            )
            if overdue["oldest_days"]:
                sentence += ", the oldest by {} day{}".format(
                    overdue["oldest_days"], "" if overdue["oldest_days"] == 1 else "s"
                )
            lines.append(sentence + ".")
        else:
            lines.append("Nothing is past its deadline.")

        latest = trend["latest"]
        if latest and latest["lifetime"] is not None:
            lines.append(
                "Documents completed in {:%B} took an average of {} from being raised "
                "to being finished, counted in office hours.".format(
                    latest["month"], latest["lifetime_label"]
                )
            )
        if latest and latest["has_on_time"]:
            lines.append(
                "{} of {} document{} with a deadline {} completed on time ({}%).".format(
                    latest["on_time"],
                    latest["closed"],
                    "" if latest["closed"] == 1 else "s",
                    "was" if latest["closed"] == 1 else "were",
                    latest["on_time_percent"],
                )
            )

        leader = uploads["leader"]
        if leader:
            lines.append(
                "{} added the most to the repository this month ({} of {}, {}%).".format(
                    leader["name"], leader["total"], uploads["total"], leader["percent"]
                )
            )
        return lines

    def _combined_breakdown(self, user, office=None):
        """One percentage across both modules, sliced six ways.

        The dashboard showed raw counts from each module side by side, which
        left the reader to work out what share of everything each pile was —
        and the two piles were never added up, so "how much is there altogether"
        had no answer on the page.

        The combined figure belongs here. The split historical/completed view
        belongs in Reports and is deliberately *not* offered here behind a
        picker: one screen that changes what it means depending on a control was
        explicitly refused, and rightly — a printed copy of it cannot say which
        mode produced it.

        No arrows, no percent-change indicators anywhere. A month-on-month arrow
        on a records backlog reads as a verdict on the office, which is not
        something this page is entitled to hand out.
        """
        records, documents = self._scoped(user, office)
        totals = analytics.combined_totals(records, documents)
        incoming = totals["incoming"]
        pending_receipt = totals["pending_receipt"]
        in_process = totals["in_process"]
        overdue_total = totals["overdue"]
        awaiting_upload = totals["pending_upload"]
        historical = totals["historical"]
        completed = totals["completed"]

        tracking_url = reverse("tracking:list")
        slices = [
            # Every slice links through to the list behind it: a percentage
            # nobody can open is a number the reader has to take on trust.
            {"key": "incoming", "label": "Incoming", "total": incoming,
             "url": f"{tracking_url}?scope=incoming", "group": "tracking"},
            {"key": "pending_receipt", "label": "Pending receipt", "total": pending_receipt,
             "url": f"{tracking_url}?scope=pending-receipt", "group": "tracking"},
            {"key": "in_process", "label": "In process", "total": in_process,
             "url": f"{tracking_url}?status=IN_PROCESS", "group": "tracking"},
            # Overdue goes to Reports, not to the filtered tracking list: the
            # question behind clicking it is "why are these late and whose are
            # they", which is a report, not a list of rows.
            {"key": "overdue", "label": "Overdue", "total": overdue_total,
             "url": f"{reverse('core:reports')}?status=OVERDUE", "group": "tracking"},
            {"key": "pending_upload", "label": "Completed - pending upload",
             "total": awaiting_upload,
             "url": f"{tracking_url}?scope=pending-upload", "group": "tracking"},
            {"key": "historical", "label": "Repository - historical", "total": historical,
             "url": f"{reverse('documents:repository')}?source={Source.UPLOAD}",
             "group": "repository"},
            {"key": "completed", "label": "Repository - completed", "total": completed,
             "url": f"{reverse('documents:repository')}?source={Source.DTS}",
             "group": "repository"},
        ]

        total = sum(row["total"] for row in slices)
        ceiling = max([row["total"] for row in slices], default=0)
        for row in slices:
            row["percent"] = _percent(row["total"], total)
            row["bar_percent"] = _bar(row["total"], ceiling)
            row["colour"] = BREAKDOWN_COLOURS[row["key"]]

        tracking_total = sum(row["total"] for row in slices if row["group"] == "tracking")
        return {
            "slices": slices,
            "total": total,
            "tracking_total": tracking_total,
            "repository_total": total - tracking_total,
            "tracking_percent": _percent(tracking_total, total),
            "repository_percent": _percent(total - tracking_total, total),
        }

    def _breakdown_summary(self, breakdown) -> list[str]:
        """The numbers, said in sentences.

        Written out because the dashboard is printed and handed to people who
        were not the ones filtering it — a printed ring of coloured segments
        with no words is a picture of a number, not a finding. Assembled from
        the same figures the panel renders, so the prose cannot drift from the
        chart above it.

        Deliberately descriptive and never comparative: it says what is there,
        not whether that is better or worse than last month. This page has no
        basis for a verdict on an office and should not imply one.
        """
        total = breakdown["total"]
        if not total:
            return ["There are no documents in tracking or in the repository yet."]

        by_key = {row["key"]: row for row in breakdown["slices"]}
        sentences = [
            f"There are {total} document{'s' if total != 1 else ''} altogether: "
            f"{breakdown['tracking_total']} still moving through tracking "
            f"({breakdown['tracking_percent']}%) and {breakdown['repository_total']} "
            f"filed in the repository ({breakdown['repository_percent']}%)."
        ]

        awaiting = by_key["pending_receipt"]["total"]
        if awaiting:
            sentences.append(
                f"{awaiting} document{'s are' if awaiting != 1 else ' is'} waiting for a "
                f"receiving office to confirm receipt "
                f"({by_key['pending_receipt']['percent']}% of everything)."
            )

        overdue_total = by_key["overdue"]["total"]
        if overdue_total:
            sentences.append(
                f"{overdue_total} {'are' if overdue_total != 1 else 'is'} past the deadline set "
                f"for {'them' if overdue_total != 1 else 'it'}. Reports breaks these down by "
                f"office."
            )
        else:
            sentences.append("Nothing is past its deadline.")

        pending_upload = by_key["pending_upload"]["total"]
        if pending_upload:
            sentences.append(
                f"{pending_upload} {'have' if pending_upload != 1 else 'has'} been completed and "
                f"{'are' if pending_upload != 1 else 'is'} waiting for an administrator to "
                f"approve {'them' if pending_upload != 1 else 'it'} into the repository."
            )
        return sentences


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


def _can_start_work(user) -> bool:
    """Whether to offer the New Tracking Slip / Upload buttons.

    Mirrors the two gates the target views apply — `WriteAccessRequiredMixin`
    turns a viewer away, and `OfficeAssignedMixin` turns away an account with no
    office — so the dashboard does not offer a button that answers with a
    redirect and a warning.

    This hides a control; it does not grant one. Both views still refuse the
    request on their own, which is the check that matters: a hidden button is
    not a permission, and the endpoints stay reachable to anyone who knows the
    URL. The tracking list and the repository show these buttons to everyone and
    let the redirect explain, which is defensible but reads as a dead end on the
    one page a viewer is most likely to start from.
    """
    if user.is_viewer:
        return False
    return user.office_id is not None or user.is_superuser


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


def _office_from_name(raw: str):
    """Resolve an office typed by name or code.

    Offered alongside the dropdown because a dropdown of every office in the
    university is a scrolling exercise once this reaches past OVPA, and because
    somebody who knows the office already knows its name.

    Exact code first, then exact name, then a unique prefix. A prefix that
    matches two offices resolves to neither: quietly picking the first would
    hand somebody another office's report while showing them the name they
    typed. Generating a report notifies nobody by design — this is an
    anti-tampering control, an office must not learn it is being reviewed — so
    an ambiguous match is a mistake nobody else is in a position to catch.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    exact = Office.objects.filter(Q(code__iexact=raw) | Q(name__iexact=raw)).first()
    if exact:
        return exact
    matches = list(Office.objects.filter(name__istartswith=raw)[:2])
    return matches[0] if len(matches) == 1 else None


def report_filters_from_request(request):
    params = request.GET
    office = Office.objects.filter(pk=params["office"]).first() if params.get("office", "").isdigit() else None
    # The dropdown wins when both are set, so a stale name in the box cannot
    # silently override the office somebody just picked from the list.
    office_name = params.get("office_name", "").strip()
    if office is None and office_name:
        office = _office_from_name(office_name)
    year = _filter_year(params.get("year", ""))
    status = params.get("status", "")
    if status not in dict(Status.choices) and status != "OVERDUE":
        status = ""
    document_type = (
        DocumentType.objects.filter(pk=params["document_type"]).first()
        if params.get("document_type", "").isdigit() else None
    )
    return {
        "office": office,
        "office_name": office_name,
        "office_name_unmatched": bool(office_name) and office is None,
        "year": year,
        "status": status,
        "document_type": document_type,
    }


def apply_report_filters(records, filters):
    office, year = filters["office"], filters["year"]
    if office:
        records = records.filter(Q(originating_office=office) | Q(current_office=office))
    if year:
        records = records.filter(created_at__year=year)
    if filters["status"] == "OVERDUE":
        records = records.filter(due_at__lt=timezone.now()).exclude(status__in=COMPLETED_STATUSES)
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
        overdue = records.filter(due_at__lt=timezone.now()).exclude(status__in=COMPLETED_STATUSES).count()
        awaiting = (
            records.filter(routing_steps__received_at__isnull=True)
            .exclude(status__in=COMPLETED_STATUSES)
            .distinct()
            .count()
        )
        # Work finished, approved or not — approval can lag by weeks and a
        # report that waits on it understates the office every time.
        completed = records.filter(status__in=COMPLETED_STATUSES).count()

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
                "office_volume": self._office_volume(records),
                "turnaround": self._turnaround(records),
                "turnaround_by_office": self._turnaround_by_office(records),
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
    # The aggregations below live in apps/core/analytics.py because the
    # dashboard shows the same figures. Kept as thin methods rather than
    # inlined at the call site so a subclass can still override one panel.
    def _by_status(self, records, total):
        return analytics.by_status(records, total)

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
        return analytics.monthly_volume(records)

    def _office_volume(self, records):
        """Which office handled the most documents, per month and cumulatively.

        "Handled" means received: an office that confirms receipt has taken the
        document on, which is the work being acknowledged here. Counting what an
        office *sent* would credit a busy pass-through desk over the office that
        actually did something with it.

        Both series are shown together because they answer different questions —
        this month's volume says who is under load now, the running total says
        who has carried the year — and management asked for this to acknowledge
        offices, which is a question about the year.
        """
        months, since = _month_window()
        steps = RoutingStep.objects.filter(record__in=records, received_at__isnull=False)

        rows = (
            steps.filter(received_at__gte=since)
            .annotate(month=TruncMonth("received_at"))
            .values("month", "to_office__code", "to_office__name")
            .annotate(total=Count("id"))
        )
        per_office: dict[str, dict] = {}
        for row in rows:
            if not row["month"] or not row["to_office__code"]:
                continue
            month = timezone.localtime(row["month"]).date().replace(day=1)
            entry = per_office.setdefault(
                row["to_office__code"],
                {
                    "code": row["to_office__code"],
                    "name": row["to_office__name"] or row["to_office__code"],
                    "by_month": {},
                },
            )
            entry["by_month"][month] = entry["by_month"].get(month, 0) + row["total"]

        # Everything before the window, so "cumulative" means since records began.
        opening = {
            row["to_office__code"]: row["total"]
            for row in steps.filter(received_at__lt=since)
            .values("to_office__code")
            .annotate(total=Count("id"))
            if row["to_office__code"]
        }

        current_month = months[-1] if months else None
        leaderboard = []
        for code, entry in per_office.items():
            cumulative = opening.get(code, 0) + sum(entry["by_month"].values())
            leaderboard.append(
                {
                    "code": code,
                    "name": entry["name"],
                    "this_month": entry["by_month"].get(current_month, 0),
                    "cumulative": cumulative,
                    "series": [entry["by_month"].get(month, 0) for month in months],
                }
            )
        # Offices that handled nothing in the window but carry history still
        # belong on a cumulative leaderboard.
        for code, total in opening.items():
            if code not in per_office:
                leaderboard.append(
                    {"code": code, "name": code, "this_month": 0,
                     "cumulative": total, "series": [0] * len(months)}
                )

        leaderboard.sort(key=lambda row: (row["cumulative"], row["this_month"]), reverse=True)
        leaderboard = leaderboard[:10]

        cumulative_ceiling = max([row["cumulative"] for row in leaderboard], default=0)
        month_ceiling = max([row["this_month"] for row in leaderboard], default=0)
        for row in leaderboard:
            row["cumulative_percent"] = _bar(row["cumulative"], cumulative_ceiling)
            row["this_month_percent"] = _bar(row["this_month"], month_ceiling)
        return {
            "rows": leaderboard,
            "months": months,
            "current_month": current_month,
        }

    def _turnaround(self, records):
        return analytics.turnaround(records)

    def _turnaround_by_office(self, records):
        """The same four metrics, per office.

        An overall average hides the thing management actually wants to know:
        which office is slow. One office taking a fortnight is invisible inside
        a mean that eleven prompt offices also contribute to.

        Attributed by the office that *received* the step or holds the record,
        because that is who had the document and could act on it — attributing
        by originating office would charge the raiser for everybody else's wait.
        """
        steps = list(
            RoutingStep.objects.filter(record__in=records, received_at__isnull=False)
            .select_related("to_office")
            .values_list("to_office__code", "to_office__name", "sent_at", "received_at")
        )
        done = list(
            records.filter(status__in=COMPLETED_STATUSES, completed_at__isnull=False)
            .select_related("current_office")
            .values_list(
                "current_office__code", "current_office__name",
                "created_at", "first_received_at", "completed_at", "due_at",
            )
        )

        offices: dict[str, dict] = {}

        def bucket(code, name):
            if not code:
                return None
            return offices.setdefault(
                code,
                {
                    "code": code, "name": name or code,
                    "receipt_pairs": [], "processing_pairs": [], "lifetime_pairs": [],
                    "on_time": 0, "on_time_total": 0, "records": 0,
                },
            )

        for code, name, sent_at, received_at in steps:
            row = bucket(code, name)
            if row is not None:
                row["receipt_pairs"].append((sent_at, received_at))

        for code, name, created_at, first_received_at, completed_at, due_at in done:
            row = bucket(code, name)
            if row is None:
                continue
            row["records"] += 1
            row["lifetime_pairs"].append((created_at, completed_at))
            if first_received_at:
                row["processing_pairs"].append((first_received_at, completed_at))
            if due_at:
                row["on_time_total"] += 1
                if completed_at <= due_at:
                    row["on_time"] += 1

        rows = []
        for row in offices.values():
            rows.append(
                {
                    "code": row["code"],
                    "name": row["name"],
                    "records": row["records"],
                    "receipt": humanise_business_seconds(
                        average_business_seconds(row["receipt_pairs"])
                    ),
                    "processing": humanise_business_seconds(
                        average_business_seconds(row["processing_pairs"])
                    ),
                    "lifetime": humanise_business_seconds(
                        average_business_seconds(row["lifetime_pairs"])
                    ),
                    "on_time": row["on_time"],
                    "on_time_total": row["on_time_total"],
                    "on_time_percent": _percent(row["on_time"], row["on_time_total"]),
                    # Sorted on, not shown: the office with the longest wait to
                    # be received is the one worth putting at the top.
                    "_sort": average_business_seconds(row["receipt_pairs"]) or 0,
                }
            )
        rows.sort(key=lambda row: row["_sort"], reverse=True)
        return rows

    def _overdue_offices(self, records):
        return analytics.overdue_offices(records)

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


#: Rows shown per panel on the audit screen.
AUDIT_ROWS = 300


class AuditLogView(AdminRequiredMixin, TemplateView):
    """The append-only trails, side by side.

    Two of them, because they answer different questions and live in different
    tables. `AuditLog` records what was done to the system; the record-access
    panel reads the VIEWED and PRINTED entries on `RecordActivity`, which record
    who *looked at* and who *printed* a document.

    The second panel is the point of logging reads at all. A view-only account
    leaves no other trace, so without somewhere to inspect these rows the
    requirement they were built for is not met — a log nobody can read is a log
    that does not exist.
    """

    template_name = "administration/audit_log.html"

    def record_access_entries(self):
        """VIEWED and PRINTED, narrowed to what this administrator may see.

        Office-scoped for the same reason the account screens are: an office
        administrator inspecting who read which document must not thereby be
        handed the reading history of every other office's documents. The scope
        follows the *record*, via the same visibility rule the rest of the
        system uses, rather than the actor — otherwise an office would lose
        sight of an outsider who read one of its own documents, which is the
        case the trail matters most for.
        """
        from apps.tracking.models import QUIET_EVENTS, RecordActivity, TrackingRecord

        access_events = set(QUIET_EVENTS) | {RecordActivity.Event.PRINTED}
        entries = (
            RecordActivity.objects.filter(event__in=access_events)
            .select_related("actor", "actor_office", "record")
            .order_by("-created_at", "-id")
        )
        if not self.request.user.is_system_admin:
            visible = TrackingRecord.objects.visible_to(self.request.user)
            entries = entries.filter(record__in=visible)

        record_query = self.request.GET.get("record", "").strip()
        who_query = self.request.GET.get("who", "").strip()
        if record_query:
            entries = entries.filter(
                Q(record__tracking_number__icontains=record_query)
                | Q(record__subject__icontains=record_query)
            )
        if who_query:
            entries = entries.filter(
                Q(actor__username__icontains=who_query)
                | Q(actor__first_name__icontains=who_query)
                | Q(actor__last_name__icontains=who_query)
            )
        return entries, record_query, who_query

    def system_log_entries(self):
        """`AuditLog`, narrowed to what this administrator may see.

        Scoped by the actor's office for anyone but a system administrator.
        This screen used to be reachable only by the global ADMIN role, so the
        unscoped queryset was correct; opening it to office administrators is
        what makes it a leak, and it is the same leak the account screens had —
        a role that gained a boundary, behind a queryset that never had one.

        Rows with no actor are system actions belonging to no office, so they
        stay with the system administrators.
        """
        entries = AuditLog.objects.select_related("actor")
        if not self.request.user.is_system_admin:
            if not self.request.user.office_id:
                return entries.none()
            entries = entries.filter(actor__office_id=self.request.user.office_id)
        return entries

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        entries = self.system_log_entries()
        action = self.request.GET.get("action", "")
        query = self.request.GET.get("q", "").strip()
        if action:
            entries = entries.filter(action=action)
        if query:
            entries = entries.filter(Q(summary__icontains=query) | Q(actor_label__icontains=query))

        access_entries, record_query, who_query = self.record_access_entries()
        context.update(
            {
                "entries": entries[:AUDIT_ROWS],
                "actions": AuditLog.Action.choices,
                "selected_action": action,
                "query": query,
                "access_entries": access_entries[:AUDIT_ROWS],
                "record_query": record_query,
                "who_query": who_query,
                "view_dedup_minutes": settings.VIEW_LOG_DEDUP_MINUTES,
                # Only the panel the administrator asked about, so a search for
                # one record does not leave the other panel looking unfiltered.
                "access_is_filtered": bool(record_query or who_query),
                "master_data": master_data_for(self.request.user),
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

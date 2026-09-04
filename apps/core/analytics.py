"""Aggregations shared by the Reports page and the Dashboard.

Everything here was originally a private method on `ReportsView`. The dashboard
now shows the same figures — overdue by office, the monthly volume series, the
turnaround averages, the status mix — and a second copy growing inside
`DashboardView` would drift from the first the moment either page was touched.
So they live here as plain functions over querysets, and both views call them.

Two rules hold throughout:

* **Nothing writes.** Every function takes a queryset somebody else has already
  scoped with `visible_to(user)` and returns dictionaries. Scoping is the
  caller's job precisely so it cannot be forgotten here.
* **Durations are office hours first.** `average_business_seconds` is the
  headline everywhere, with calendar time beside it, because that is the
  distinction Reports already draws — a dashboard that measured the same thing
  on a different basis would read as a contradiction, not a second view.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from django.db.models import Avg, Count, DurationField, F, Q
from django.db.models.functions import TruncMonth
from django.utils import timezone

from apps.accounts.models import Office
from apps.documents.models import Source
from apps.tracking.models import ACTIVE_STATUSES, COMPLETED_STATUSES, RoutingStep, Status

from .business_time import (
    OFFICE_HOURS_CAVEAT,
    average_business_seconds,
    business_seconds_between,
    humanise_business_seconds,
)
from .colors import STATUS_COLOURS

#: Months of history the charts cover. A year is the reporting unit offices
#: actually use, and it keeps every column chart to twelve readable bars.
REPORT_MONTHS = 12


# ---------------------------------------------------------------------------
# Small shared arithmetic
# ---------------------------------------------------------------------------
def percent(part: int, whole: int) -> int:
    return int(round(100 * part / whole)) if whole else 0


def bar(part: int, whole: int) -> int:
    """Bar width as a percentage. Zero stays zero — a minimum-width stub would
    paint a value that is not there — but a real value never rounds away."""
    if not part or not whole:
        return 0
    return max(1, int(round(100 * part / whole)))


def humanise_duration(delta) -> str:
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


def month_window(months_back: int = REPORT_MONTHS):
    """The last `months_back` calendar months, plus the datetime they start at.

    Built by walking months rather than subtracting days so February and the
    31-day months land on the right buckets.
    """
    months: list = []
    cursor = timezone.localdate().replace(day=1)
    for _ in range(months_back):
        months.append(cursor)
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    months.reverse()
    since = timezone.make_aware(
        datetime.combine(months[0], time.min), timezone.get_current_timezone()
    )
    return months, since


def month_series(queryset, field: str, since):
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


def _month_of(value):
    """The calendar month a stored timestamp belongs to, in local time."""
    return timezone.localtime(value).date().replace(day=1)


# ---------------------------------------------------------------------------
# Tracking panels
# ---------------------------------------------------------------------------
def by_status(records, total: int) -> list[dict]:
    """One row per status: share of the whole, plus a bar against the largest
    status so the shortest bar is still visible."""
    rows = list(
        records.values("status").annotate(total=Count("id", distinct=True)).order_by("-total")
    )
    labels = dict(Status.choices)
    ceiling = max([row["total"] for row in rows], default=0)
    for row in rows:
        row["label"] = labels.get(row["status"], row["status"])
        row["percent"] = percent(row["total"], total)
        row["bar_percent"] = bar(row["total"], ceiling)
        row["colour"] = STATUS_COLOURS.get(row["status"], STATUS_COLOURS["DRAFT"])
    return rows


def overdue_offices(records, limit: int = 8) -> list[dict]:
    """Where overdue documents are sitting — a queue to chase, not a total.

    Each row carries how long the *oldest* item in that pile has been late, not
    just how many there are. A count alone cannot tell an office holding twelve
    documents one day late apart from an office holding three that have been
    late for a month, and the second is the one somebody has to go and see.
    """
    now = timezone.now()
    late = (
        records.filter(due_at__lt=now)
        .exclude(status__in=COMPLETED_STATUSES)
        .exclude(current_office__isnull=True)
    )
    rows = list(
        late.values("current_office__code", "current_office__name")
        .annotate(total=Count("id", distinct=True))
        .order_by("-total")[:limit]
    )

    # The earliest deadline per office, in one pass over the same queryset,
    # rather than a query per row.
    earliest: dict[str, object] = {}
    for code, due_at in late.values_list("current_office__code", "due_at"):
        if due_at is None:
            continue
        if code not in earliest or due_at < earliest[code]:
            earliest[code] = due_at

    ceiling = max([row["total"] for row in rows], default=0)
    for row in rows:
        code = row["current_office__code"]
        row["code"] = code
        row["name"] = row["current_office__name"] or code
        row["percent"] = bar(row["total"], ceiling)
        # The reports template reads `percent`; the dashboard reads
        # `bar_percent` like every other bar on that page. Same number.
        row["bar_percent"] = row["percent"]
        due_at = earliest.get(code)
        # Whole days late, floored: "3 days" must mean the deadline is three
        # full days behind, never "some part of a third day".
        row["oldest_days"] = max(0, (now - due_at).days) if due_at else 0
    return rows


def overdue_summary(records, rows: list[dict], total_documents: int) -> dict:
    """The headline figures above the per-office list.

    `total` is counted over the whole queryset rather than summed from `rows`,
    which are capped at the longest few queues — summing a truncated list would
    quietly under-report the thing the banner exists to state.
    """
    total = (
        records.filter(due_at__lt=timezone.now())
        .exclude(status__in=COMPLETED_STATUSES)
        .distinct()
        .count()
    )
    return {
        "total": total,
        "oldest_days": max([row["oldest_days"] for row in rows], default=0),
        "percent_of_all": percent(total, total_documents),
        "office_count": len([row for row in rows if row["total"]]),
    }


def monthly_volume(records) -> dict:
    """Created, transferred-or-endorsed and completed — cumulative.

    Three series, and each one runs as a running total from the start of records
    rather than resetting every month. The monthly-reset version answered "how
    busy was March", which is a question about staffing; the cumulative version
    answers "is the backlog growing", which is the question the pairing exists
    for — Created is the tracking side, Completed is the repository side, and
    the gap between the two curves is the work still in the building. On a
    monthly reset that gap is invisible.

    Transferred-or-endorsed counts routing steps rather than records, since one
    document endorsed onward four times is four transfers of work.

    The running totals start from *all* history, not from the window, so the
    first bar is the true position in that month and not a fresh zero.
    """
    months, since = month_window()
    completed_records = records.filter(status__in=COMPLETED_STATUSES)
    steps = RoutingStep.objects.filter(record__in=records)

    created = month_series(records, "created_at", since)
    finished = month_series(completed_records, "completed_at", since)
    transferred = month_series(steps, "sent_at", since)

    # Everything before the window, so the curves begin where they really are.
    opening = {
        "created": records.filter(created_at__lt=since).count(),
        "transferred": steps.filter(sent_at__lt=since).count(),
        "completed": completed_records.filter(completed_at__lt=since).count(),
    }

    rows = []
    running = dict(opening)
    for month in months:
        running["created"] += created.get(month, 0)
        running["transferred"] += transferred.get(month, 0)
        running["completed"] += finished.get(month, 0)
        rows.append(
            {
                "month": month,
                "created": running["created"],
                "transferred": running["transferred"],
                "completed": running["completed"],
                # This month's own additions, kept for the tooltip: a cumulative
                # curve alone cannot say what changed in March.
                "created_delta": created.get(month, 0),
                "transferred_delta": transferred.get(month, 0),
                "completed_delta": finished.get(month, 0),
            }
        )

    ceiling = max([row["transferred"] for row in rows] + [row["created"] for row in rows] + [0])
    for row in rows:
        row["created_percent"] = bar(row["created"], ceiling)
        row["transferred_percent"] = bar(row["transferred"], ceiling)
        row["completed_percent"] = bar(row["completed"], ceiling)

    return {
        "rows": rows,
        "ceiling": ceiling,
        "total": rows[-1]["created"] if rows else 0,
        "outstanding": (rows[-1]["created"] - rows[-1]["completed"]) if rows else 0,
    }


def turnaround(records) -> dict:
    """Real averages from the timestamps the routing steps already carry.

    Each duration is reported twice: in office hours, and on the calendar.
    Neither replaces the other. Office hours answer "how much working time did
    the office have to act", which is the fair way to judge an office; calendar
    time is what the requester actually waited, which is the fair way to answer
    them. Showing only the first would flatter every office that let something
    sit over a weekend; showing only the second charges them for the weekend.
    """
    steps = RoutingStep.objects.filter(record__in=records, received_at__isnull=False)
    receipt = steps.aggregate(
        value=Avg(F("received_at") - F("sent_at"), output_field=DurationField())
    )["value"]
    receipt_office = average_business_seconds(steps.values_list("sent_at", "received_at"))

    # Turnaround measures how long the *work* took, so it ends at completion
    # rather than at approval — the wait for an administrator is somebody else's
    # queue and would otherwise be charged to the office that finished on time.
    done = records.filter(status__in=COMPLETED_STATUSES, completed_at__isnull=False)
    processing_set = done.filter(first_received_at__isnull=False)
    processing = processing_set.aggregate(
        value=Avg(F("completed_at") - F("first_received_at"), output_field=DurationField())
    )["value"]
    processing_office = average_business_seconds(
        processing_set.values_list("first_received_at", "completed_at")
    )
    lifetime = done.aggregate(
        value=Avg(F("completed_at") - F("created_at"), output_field=DurationField())
    )["value"]
    lifetime_office = average_business_seconds(done.values_list("created_at", "completed_at"))

    with_deadline = done.filter(due_at__isnull=False)
    deadline_total = with_deadline.count()
    on_time = with_deadline.filter(completed_at__lte=F("due_at")).count()

    return {
        # Office hours: the headline figures.
        "receipt": humanise_business_seconds(receipt_office),
        "processing": humanise_business_seconds(processing_office),
        "lifetime": humanise_business_seconds(lifetime_office),
        # Calendar: kept beside them, never instead of them.
        "receipt_calendar": humanise_duration(receipt),
        "processing_calendar": humanise_duration(processing),
        "lifetime_calendar": humanise_duration(lifetime),
        "office_hours_caveat": OFFICE_HOURS_CAVEAT,
        "receipt_samples": steps.count(),
        "on_time": on_time,
        "on_time_total": deadline_total,
        "on_time_percent": percent(on_time, deadline_total),
        "unreceived": RoutingStep.objects.filter(
            record__in=records, received_at__isnull=True
        ).count(),
    }


#: A working day, for turning the office-hours averages into a number that fits
#: on a chart axis. Reports says "4 hrs 20 mins"; a twelve-month trend line
#: cannot, so it plots working days and labels the axis in days.
WORKING_HOURS_PER_DAY = 8
WORKING_SECONDS_PER_DAY = WORKING_HOURS_PER_DAY * 3600


def turnaround_by_month(records, months_back: int = REPORT_MONTHS) -> dict:
    """The three turnaround averages, one point per month.

    `turnaround()` returns a single average over the whole period, which cannot
    show whether an office is getting faster or slower — and that is the entire
    question a trend line exists to answer.

    Bucketed by when the *work finished*, not when it started, so a month's
    figure covers documents actually closed in it. Office hours, the same basis
    Reports uses, so the two pages cannot disagree about the same metric.
    """
    months, since = month_window(months_back)
    step_rows = RoutingStep.objects.filter(
        record__in=records, received_at__isnull=False, received_at__gte=since
    ).values_list("sent_at", "received_at")
    done_rows = records.filter(
        status__in=COMPLETED_STATUSES,
        completed_at__isnull=False,
        completed_at__gte=since,
    ).values_list("created_at", "first_received_at", "completed_at", "due_at")

    buckets = {
        month: {"receipt": [], "processing": [], "lifetime": [], "on_time": 0, "closed": 0}
        for month in months
    }

    for sent_at, received_at in step_rows:
        bucket = buckets.get(_month_of(received_at))
        if bucket is not None:
            bucket["receipt"].append(business_seconds_between(sent_at, received_at))

    for created_at, first_received_at, completed_at, due_at in done_rows:
        bucket = buckets.get(_month_of(completed_at))
        if bucket is None:
            continue
        bucket["lifetime"].append(business_seconds_between(created_at, completed_at))
        if first_received_at:
            bucket["processing"].append(
                business_seconds_between(first_received_at, completed_at)
            )
        if due_at:
            bucket["closed"] += 1
            if completed_at <= due_at:
                bucket["on_time"] += 1

    def average_seconds(samples):
        return sum(samples) / len(samples) if samples else None

    def average_days(samples):
        seconds = average_seconds(samples)
        return None if seconds is None else round(seconds / WORKING_SECONDS_PER_DAY, 1)

    rows = []
    for month in months:
        bucket = buckets[month]
        rows.append(
            {
                "month": month,
                "receipt": average_days(bucket["receipt"]),
                "processing": average_days(bucket["processing"]),
                "lifetime": average_days(bucket["lifetime"]),
                # Days for the axis, office language for the prose beside it.
                # A sentence reading "an average of 0.0 working days" is not
                # something anybody would write; "under a minute" is.
                "lifetime_label": humanise_business_seconds(
                    average_seconds(bucket["lifetime"])
                ),
                "on_time": bucket["on_time"],
                "closed": bucket["closed"],
                "on_time_percent": percent(bucket["on_time"], bucket["closed"]),
                # A month with nothing due has no rate. 0% would read as
                # "everything was late" when the truth is "nothing was owed".
                "has_on_time": bool(bucket["closed"]),
            }
        )

    measured = [
        value
        for row in rows
        for value in (row["receipt"], row["processing"], row["lifetime"])
        if value is not None
    ]
    # Rounded up to a whole day so axis labels are whole days, and never zero —
    # a zero-height axis has nothing to plot against.
    ceiling = max(1, int(max(measured, default=0)) + 1) if measured else 1

    return {
        "rows": rows,
        "ceiling": ceiling,
        "has_data": bool(measured),
        "office_hours_caveat": OFFICE_HOURS_CAVEAT,
        "latest": rows[-1] if rows else None,
    }


def uploads_by_office(documents, records, limit: int = 8) -> dict:
    """What each office put into the repository this month.

    One combined figure per office, because from the repository's side there is
    no difference worth splitting: a document uploaded directly and a tracked
    record completed and filed are both an office adding to the record. Two
    separate rankings would make an office that does one of each look half as
    productive as one that does two of the same.

    This month only. A cumulative version would rank offices by how long they
    have existed, which Reports' office-volume panel already covers and which
    is not a thing anybody can act on today.
    """
    months, _ = month_window()
    current_month = months[-1] if months else timezone.localdate().replace(day=1)
    since = timezone.make_aware(
        datetime.combine(current_month, time.min), timezone.get_current_timezone()
    )

    uploaded = {
        row["office__code"]: row["total"]
        for row in documents.filter(created_at__gte=since)
        .values("office__code")
        .annotate(total=Count("id", distinct=True))
        if row["office__code"]
    }
    filed = {
        row["current_office__code"]: row["total"]
        for row in records.filter(status__in=COMPLETED_STATUSES, completed_at__gte=since)
        .values("current_office__code")
        .annotate(total=Count("id", distinct=True))
        if row["current_office__code"]
    }

    names = {
        office.code: office.name
        for office in Office.objects.filter(Q(code__in=uploaded) | Q(code__in=filed))
    }

    rows = []
    for code in set(uploaded) | set(filed):
        total = uploaded.get(code, 0) + filed.get(code, 0)
        if not total:
            continue
        rows.append(
            {
                "code": code,
                "name": names.get(code, code),
                "uploaded": uploaded.get(code, 0),
                "filed": filed.get(code, 0),
                "total": total,
            }
        )
    # Name as the tiebreak so a redeploy cannot reorder equal rows; ascending
    # by name within a descending sort, hence the two-pass ordering.
    rows.sort(key=lambda row: row["name"])
    rows.sort(key=lambda row: row["total"], reverse=True)
    rows = rows[:limit]

    grand_total = sum(row["total"] for row in rows)
    ceiling = max([row["total"] for row in rows], default=0)
    for row in rows:
        row["bar_percent"] = bar(row["total"], ceiling)
        row["percent"] = percent(row["total"], grand_total)

    # Named only when one office is genuinely ahead. Calling a tie "the top
    # office" hands out a distinction the numbers did not award.
    leader = None
    if rows and (len(rows) == 1 or rows[0]["total"] > rows[1]["total"]):
        leader = rows[0]

    return {
        "rows": rows,
        "month": current_month,
        "total": grand_total,
        "leader": leader,
    }


def combined_totals(records, documents) -> dict:
    """One whole, split across tracking and the repository.

    The tracking slices are statuses, and statuses are mutually exclusive by
    construction, so the ring is a partition without anything having to exclude
    anything else. That was not true while Overdue sat among them: it is a
    deadline condition lying across all three live stages — on the demo data 10
    pending-receipt and 15 in-process records were also overdue — so the ring
    reported 109 slice entries over 44 records while still drawing a closed
    circle, because the percentages are normalised to their own sum. Overdue is
    a stat card now, which is where a cross-cutting condition belongs.

    Counted over ACTIVE_STATUSES, the set the Tracking page itself lists, so
    each slice equals the rows behind `?status=`. Drafts are excluded: a draft
    has not been sent, so it is not yet moving between offices, and it is
    visible only to the office writing it.

    Not office-scoped, and deliberately so — the slices link to `?status=`,
    which is not office-scoped either. The per-office queues are the stat cards,
    which count through `apply_scope` and link to `?scope=`. Making the ring
    office-scoped as well looked right until an administrator opened it: the
    queues resolve against the viewer's own office, so a system administrator
    saw their own office's three records where the page showed the university's.
    """
    live = records.filter(status__in=ACTIVE_STATUSES)

    def by_status(status):
        return live.filter(status=status).distinct().count()

    repository_total = documents.distinct().count()
    historical = documents.filter(source=Source.UPLOAD).distinct().count()

    return {
        "pending_receipt": by_status(Status.PENDING_RECEIPT),
        "received": by_status(Status.RECEIVED),
        "in_process": by_status(Status.IN_PROCESS),
        "pending_upload": by_status(Status.COMPLETED_PENDING_UPLOAD),
        # Not a slice; the stat card and the memo both read it from here.
        "overdue": (
            records.filter(due_at__lt=timezone.now())
            .exclude(status__in=COMPLETED_STATUSES)
            .distinct()
            .count()
        ),
        "historical": historical,
        "completed": repository_total - historical,
    }


def live_records_by_status(records) -> list[dict]:
    """Records still in play, by status.

    Overdue is deliberately absent: it is a deadline condition sitting on top of
    these statuses, not a status of its own, and it already has the whole banner
    at the top of the dashboard. Counting it here as well would move records out
    of the status they are actually in and understate the live queue.
    """
    live = records.exclude(status=Status.COMPLETED)
    total = live.distinct().count()
    return by_status(live, total)

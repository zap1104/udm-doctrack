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
from math import cos, pi, sin

from django.db.models import Avg, Count, DurationField, F, Q
from django.db.models.functions import TruncMonth
from django.utils import timezone

from apps.accounts.models import Office
from apps.documents.models import COMPLETED_SOURCE
from apps.tracking.models import ACTIVE_STATUSES, COMPLETED_STATUSES, RoutingStep, Status, overdue_q

from .business_time import (
    _two_units,
    average_business_seconds,
    business_seconds_between,
    humanise_business_seconds,
    load_holidays,
    office_hours_caveat,
    working_day_hours,
    working_day_seconds,
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
    paint a value that is not there — but a real value never rounds away.

    `whole` is the total the panel states, never the largest row. A bar track
    is 100% of something, and measured against the busiest row the leader
    always filled it: 3 documents of 15 drew a full bar beside the words "20%".
    """
    if not part or not whole:
        return 0
    return max(1, int(round(100 * part / whole)))


def share_text(part: int, whole: int) -> str:
    """A share as printed beside its bar.

    `percent` rounds, so one document in three hundred printed "0%" beside a
    bar `bar` had floored at 1% so that it shows: the label and the mark
    disagreeing about whether anything is there. "<1%" says both. ">99%" is
    the same guard at the other end, because 999 of 1,000 is not all of them.
    """
    if not part or not whole:
        return "0%"
    value = percent(part, whole)
    if value == 0:
        return "<1%"
    if value == 100 and part < whole:
        return ">99%"
    return f"{value}%"


def axis_ticks(ceiling: int, steps: int = 4) -> list[dict]:
    """Labelled y-axis ticks for a column chart scaled to `ceiling`.

    Round numbers — 0, 50, 100, 150 — then the ceiling itself on top. Round
    steps rather than exact quarters of the ceiling: quarters of 180 are 45, 90
    and 135, which a reader has to work at, and quarters of 5 round to 0, 1, 3,
    4, 5 — gridlines at uneven spacing with a value missing. A round step keeps
    the lines evenly spaced and the labels easy to read against.

    The step is the smallest of 1, 2, 2.5 and 5 times a power of ten that
    divides the scale into at most `steps` intervals, and never less than 1: a
    count has no half-documents. The ceiling is always the top tick, so the
    axis and "Tallest column = N" never disagree, and a round tick within 10% of
    it is dropped rather than printed on top of it.

    Each tick carries the height of the value it prints, so a label never sits
    at a height that is not its value. A zero ceiling is an empty chart: one tick
    at zero, and no division by it.
    """
    if ceiling <= 0:
        return [{"value": 0, "offset_percent": 0.0}]

    magnitude = 1
    while True:
        # 2.5 times a power of ten is only a whole number from 10 upward.
        multiples = (1, 2, 2.5, 5) if magnitude >= 10 else (1, 2, 5)
        fitting = [int(m * magnitude) for m in multiples if ceiling // int(m * magnitude) <= steps]
        if fitting:
            step = fitting[0]
            break
        magnitude *= 10

    values = list(range(0, ceiling, step))
    if values and values[-1] and (ceiling - values[-1]) * 10 < ceiling:
        values.pop()
    values.append(ceiling)
    return [
        {"value": value, "offset_percent": round(100 * value / ceiling, 2)}
        for value in values
    ]


#: The ring, in SVG user units. The same geometry the conic-gradient ring had in
#: pixels: a 190 box, and a hole inset 46 from its edge. The SVG scales with the
#: box, so the narrower rings CSS draws below 992px keep these proportions.
RING_SIZE = 190
RING_OUTER = 95
RING_INNER = 49


def _ring_point(percent: float, radius: float) -> str:
    """A point on a circle about the ring's centre, `percent` of the way round
    clockwise from twelve o'clock: where a conic-gradient starts, and the way
    it runs."""
    angle = 2 * pi * percent / 100
    centre = RING_SIZE / 2
    x = centre + radius * sin(angle)
    y = centre - radius * cos(angle)
    return f"{x:.3f} {y:.3f}"


def ring_arc(start: float, end: float) -> str:
    """SVG path `d` for the ring segment from `start`% to `end`%.

    An annular sector: along the outer circle, in, and back along the inner
    one. Empty for a segment with no width, which would be a zero-area path
    nobody can see or point at.

    A segment covering the whole ring is two half-circles on each radius. An
    SVG arc whose start and end points coincide draws nothing at all, so a ring
    with a single slice (an office whose every document is pending receipt)
    would otherwise render as an empty box. Painted with `fill-rule: evenodd`
    so the inner circle cuts the hole.
    """
    span = end - start
    if span <= 0:
        return ""
    outer, inner = RING_OUTER, RING_INNER
    if span >= 100:
        top_o, bottom_o = _ring_point(0, outer), _ring_point(50, outer)
        top_i, bottom_i = _ring_point(0, inner), _ring_point(50, inner)
        return (
            f"M {top_o} A {outer} {outer} 0 1 1 {bottom_o} A {outer} {outer} 0 1 1 {top_o} Z "
            f"M {top_i} A {inner} {inner} 0 1 0 {bottom_i} A {inner} {inner} 0 1 0 {top_i} Z"
        )
    large = 1 if span > 50 else 0
    return (
        f"M {_ring_point(start, outer)} "
        f"A {outer} {outer} 0 {large} 1 {_ring_point(end, outer)} "
        f"L {_ring_point(end, inner)} "
        f"A {inner} {inner} 0 {large} 0 {_ring_point(start, inner)} Z"
    )


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
    return _two_units(days, "day", hours, "hr", minutes, "min")


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
    """One row per status, its bar the share of every document it states.

    Every document is in exactly one stage, so the bars add up to one full
    track across the rows. `bar_percent` is `percent` with the 1% floor that
    keeps a single document visible."""
    rows = list(
        records.values("status").annotate(total=Count("id", distinct=True)).order_by("-total")
    )
    labels = dict(Status.choices)
    for row in rows:
        row["label"] = labels.get(row["status"], row["status"])
        row["percent"] = percent(row["total"], total)
        row["bar_percent"] = bar(row["total"], total)
        row["colour"] = STATUS_COLOURS.get(row["status"], STATUS_COLOURS["DRAFT"])
    return rows


#: Rows a ranked panel shows before the rest collapse into one "Other" line.
#:
#: Twelve, not eight. Eight truncated a nine-office university by exactly one,
#: which is the worst size a cap can be: the reader loses a single row and the
#: remainder line reads "Other (1 office)", which looks like a bug. Twelve shows
#: every office today and still keeps the panel bounded if the university grows.
TOP_N = 12


def cap_with_remainder(rows, limit, noun):
    """Split a ranked list into the rows shown and a label for the rest.

    Returns `(kept, cut, label)`, where `label` is None when nothing was cut.

    A capped list with no tail cannot add up: the bars a reader sums are a
    subset of the headline they sit under, and nothing on screen says so. Every
    ranked panel here had that shape, and `overdue_summary["total"]` is counted
    over the whole queryset — correctly — which is exactly what made the gap
    visible to anyone who added the rows.

    The caller builds the remainder row itself, because each panel carries
    different fields and a row missing the ones its template reads is a worse
    bug than the one this fixes. What lives here is the part that must not
    differ: where the cut falls and what the leftover is called.
    """
    if len(rows) <= limit:
        return rows, [], None
    kept, cut = rows[:limit], rows[limit:]
    return kept, cut, f"Other ({len(cut)} {noun}{'' if len(cut) == 1 else 's'})"


def overdue_offices(records, limit: int = TOP_N) -> list[dict]:
    """Where overdue documents are sitting — a queue to chase, not a total.

    Each row carries how long the *oldest* item in that pile has been late, not
    just how many there are. A count alone cannot tell an office holding twelve
    documents one day late apart from an office holding three that have been
    late for a month, and the second is the one somebody has to go and see.

    `share` is that office's portion of every overdue document, and it is taken
    over the *whole* grouping before the `limit` slice. Summing the returned
    rows instead would divide by a truncated total and report shares adding up
    to 100% across the few offices shown — the same trap `overdue_summary`
    documents for its own headline figure.
    """
    now = timezone.now()
    late = records.filter(overdue_q()).exclude(current_office__isnull=True)
    grouped = list(
        late.values("current_office__code", "current_office__name")
        .annotate(total=Count("id", distinct=True))
        .order_by("-total")
    )
    everywhere = sum(row["total"] for row in grouped)
    # Plain slicing, so `limit=0` still returns nothing — that is what the
    # dashboard's summary test leans on to prove the total survives the cap.
    rows = grouped[:limit]
    cut = grouped[limit:]

    # The earliest deadline per office, in one pass over the same queryset,
    # rather than a query per row.
    earliest: dict[str, object] = {}
    for code, due_at in late.values_list("current_office__code", "due_at"):
        if due_at is None:
            continue
        if code not in earliest or due_at < earliest[code]:
            earliest[code] = due_at

    for row in rows:
        code = row["current_office__code"]
        row["code"] = code
        row["name"] = row["current_office__name"] or code
        # `share` is the reportable figure — this office's portion of every
        # overdue document there is. `percent` and `bar_percent` are the bar
        # drawn at that share, floored at 1% so a single document shows.
        row["share"] = percent(row["total"], everywhere)
        row["percent"] = row["bar_percent"] = bar(row["total"], everywhere)
        due_at = earliest.get(code)
        # Whole days late, floored: "3 days" must mean the deadline is three
        # full days behind, never "some part of a third day".
        row["oldest_days"] = max(0, (now - due_at).days) if due_at else 0

    # The tail, as a row. Without it the office lines never sum to the headline
    # they sit under, and `overdue_summary["total"]` is counted over the whole
    # queryset, so the gap is visible to anybody who adds them up. Its bar is
    # drawn like the others: its share of the whole is as real as theirs.
    if cut:
        # Read from `earliest`, because the loop above enriches only the kept
        # rows — a cut row has no `oldest_days` of its own to take a max over.
        cut_dues = [
            earliest[row["current_office__code"]]
            for row in cut
            if row["current_office__code"] in earliest
        ]
        cut_total = sum(row["total"] for row in cut)
        rows.append(
            {
                "code": "",
                "name": f"Other ({len(cut)} office{'' if len(cut) == 1 else 's'})",
                "total": cut_total,
                "percent": bar(cut_total, everywhere),
                "bar_percent": bar(cut_total, everywhere),
                "share": percent(cut_total, everywhere),
                "oldest_days": max(
                    (max(0, (now - due).days) for due in cut_dues), default=0
                ),
                "is_remainder": True,
            }
        )
    return rows


def overdue_summary(records, rows: list[dict], total_documents: int) -> dict:
    """The headline figures above the per-office list.

    `total` is counted over the whole queryset rather than summed from `rows`,
    which are capped at the longest few queues — summing a truncated list would
    quietly under-report the thing the banner exists to state.
    """
    total = (
        records.filter(overdue_q())
        .distinct()
        .count()
    )
    return {
        "total": total,
        "oldest_days": max([row["oldest_days"] for row in rows], default=0),
        "percent_of_all": percent(total, total_documents),
        "office_count": len([row for row in rows if row["total"]]),
    }


#: The series the chart draws, in order, with the colour class each is painted
#: in. Handovers count routing steps where the other two count documents — one
#: document endorsed four times is four handovers — so that column runs above
#: the others and usually sets the scale. Every column carries its own number,
#: and the legend and the note say which unit each counts, because a reader who
#: can see all three needs to know that one of them is not documents.
VOLUME_SERIES = (
    # The colour class suffix, named for the meaning: Completed is the
    # Completed-status green everywhere, handovers the Pending receipt amber of
    # a document between offices. Completed was gold on this chart and
    # handovers green, the reverse of every other page.
    ("Created", "created", "created"),
    ("Handovers", "transferred", "handover"),
    ("Completed", "completed", "completed"),
)


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

    # Over every plotted series, so no column can be drawn above the plot.
    ceiling = max(
        [max(row[key] for _, key, _ in VOLUME_SERIES) for row in rows] + [0]
    )
    for row in rows:
        # Every column carries its own value, above its own bar. One number per
        # month said nothing about the two columns it did not sit on, and which
        # column it sat on changed with the data.
        row["columns"] = [
            {
                "label": label,
                "series": colour,
                "value": row[key],
                "percent": bar(row[key], ceiling),
            }
            for label, key, colour in VOLUME_SERIES
        ]
        # A month with nothing in any series draws nothing and says nothing: the
        # axis already reads 0. A month with something in it names every series,
        # a zero included, so a series is never missing without explanation.
        row["has_values"] = any(column["value"] for column in row["columns"])

    return {
        "rows": rows,
        "ceiling": ceiling,
        "ticks": axis_ticks(ceiling),
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
    holidays = load_holidays()
    steps = RoutingStep.objects.filter(record__in=records, received_at__isnull=False)
    receipt_row = steps.aggregate(
        value=Avg(F("received_at") - F("sent_at"), output_field=DurationField()),
        samples=Count("id"),
    )
    receipt = receipt_row["value"]
    receipt_office = average_business_seconds(
        steps.values_list("sent_at", "received_at"), holidays
    )

    # Turnaround measures how long the *work* took, so it ends at completion
    # rather than at approval — the wait for an administrator is somebody else's
    # queue and would otherwise be charged to the office that finished on time.
    done = records.filter(status__in=COMPLETED_STATUSES, completed_at__isnull=False)
    processing_set = done.filter(first_received_at__isnull=False)
    # Each average and how many it is taken over, from one query.
    processing_row = processing_set.aggregate(
        value=Avg(F("completed_at") - F("first_received_at"), output_field=DurationField()),
        samples=Count("id"),
    )
    processing = processing_row["value"]
    processing_office = average_business_seconds(
        processing_set.values_list("first_received_at", "completed_at"), holidays
    )
    lifetime_row = done.aggregate(
        value=Avg(F("completed_at") - F("created_at"), output_field=DurationField()),
        samples=Count("id"),
    )
    lifetime = lifetime_row["value"]
    lifetime_office = average_business_seconds(
        done.values_list("created_at", "completed_at"), holidays
    )

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
        "office_hours_caveat": office_hours_caveat(),
        # How many each average is taken over, so "4 hrs" from three documents
        # is not read with the weight of "4 hrs" from three hundred.
        "receipt_samples": receipt_row["samples"],
        "processing_samples": processing_row["samples"],
        "lifetime_samples": lifetime_row["samples"],
        "on_time": on_time,
        "on_time_total": deadline_total,
        "on_time_percent": percent(on_time, deadline_total),
        # Handovers the receipt average cannot include yet: sent in the batch
        # the document is on now, not yet confirmed, on a document still in
        # circulation. It counted every unconfirmed step ever written, which
        # included siblings on finished documents and hops the document had
        # already moved past — 83 on the seeded data, beside a Pending receipt
        # card reading 20. None of those will ever be confirmed.
        "awaiting_confirmation": RoutingStep.objects.filter(
            record__in=records,
            received_at__isnull=True,
            batch=F("record__current_batch"),
        ).exclude(record__status__in=COMPLETED_STATUSES).count(),
    }


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

    # Office seconds for the chart and the headline, calendar seconds beside
    # them — the same pair Reports shows for the whole period.
    buckets = {
        month: {
            "receipt": [], "processing": [], "lifetime": [],
            "receipt_calendar": [], "processing_calendar": [], "lifetime_calendar": [],
            "on_time": 0, "closed": 0,
        }
        for month in months
    }

    def calendar_seconds(start, end):
        return max(0.0, (end - start).total_seconds())

    holidays = load_holidays()

    for sent_at, received_at in step_rows:
        bucket = buckets.get(_month_of(received_at))
        if bucket is not None:
            bucket["receipt"].append(business_seconds_between(sent_at, received_at, holidays))
            bucket["receipt_calendar"].append(calendar_seconds(sent_at, received_at))

    for created_at, first_received_at, completed_at, due_at in done_rows:
        bucket = buckets.get(_month_of(completed_at))
        if bucket is None:
            continue
        bucket["lifetime"].append(business_seconds_between(created_at, completed_at, holidays))
        bucket["lifetime_calendar"].append(calendar_seconds(created_at, completed_at))
        if first_received_at:
            bucket["processing"].append(
                business_seconds_between(first_received_at, completed_at, holidays)
            )
            bucket["processing_calendar"].append(
                calendar_seconds(first_received_at, completed_at)
            )
        if due_at:
            bucket["closed"] += 1
            if completed_at <= due_at:
                bucket["on_time"] += 1

    def average_seconds(samples):
        return sum(samples) / len(samples) if samples else None

    day = working_day_seconds()

    def average_days(samples):
        seconds = average_seconds(samples)
        return None if seconds is None else round(seconds / day, 1)

    def calendar_label(samples):
        seconds = average_seconds(samples)
        return humanise_duration(None if seconds is None else timedelta(seconds=seconds))

    rows = []
    for month in months:
        bucket = buckets[month]
        row = {
            "month": month,
            "receipt": average_days(bucket["receipt"]),
            "processing": average_days(bucket["processing"]),
            "lifetime": average_days(bucket["lifetime"]),
        }
        # Days for the axis, office language for the prose beside it. A
        # sentence reading "an average of 0.0 working days" is not something
        # anybody would write; "under a minute" is. The month's own figures,
        # so the summary beside the chart can say what its last point says.
        for key in ("receipt", "processing", "lifetime"):
            row[f"{key}_label"] = humanise_business_seconds(average_seconds(bucket[key]))
            row[f"{key}_calendar"] = calendar_label(bucket[f"{key}_calendar"])
            row[f"{key}_samples"] = len(bucket[key])
        rows.append(
            {
                **row,
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
        "ticks": axis_ticks(ceiling),
        "has_data": bool(measured),
        "office_hours_caveat": office_hours_caveat(),
        "latest": rows[-1] if rows else None,
        "working_day_hours": working_day_hours(),
    }


def uploads_by_office(documents, records, limit: int = TOP_N) -> dict:
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

    # `.order_by()` before each grouping: both querysets arrive `.distinct()`
    # from the dashboard, and a distinct queryset puts its Meta.ordering columns
    # in the GROUP BY — one row per document, each counting 1, and the dict kept
    # the last. Every office read 1.
    uploaded = {
        row["office__code"]: row["total"]
        for row in documents.filter(created_at__gte=since)
        .order_by()
        .values("office__code")
        .annotate(total=Count("id", distinct=True))
        if row["office__code"]
    }
    filed = {
        row["current_office__code"]: row["total"]
        for row in records.filter(status__in=COMPLETED_STATUSES, completed_at__gte=since)
        .order_by()
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

    # Before the slice, like `overdue_offices` forty lines up, and for the
    # reason its docstring gives: summing the rows that survive a top-N divides
    # by a truncated total, so the offices shown add to 100% and the ones cut
    # off have vanished from the denominator. Here it was worse than a wrong
    # percentage — `grand_total` is returned as `total`, so the panel's own
    # headline was truncated too and contradicted `total_documents`.
    grand_total = sum(row["total"] for row in rows)
    rows, cut, remainder_label = cap_with_remainder(rows, limit, "office")

    # Each bar is the share of `grand_total` printed beside it. Scaled to the
    # busiest office instead, the leader always drew a full track, so an office
    # with 3 of 15 filled the card next to the words "20%".
    for row in rows:
        row["bar_percent"] = bar(row["total"], grand_total)
        row["percent"] = percent(row["total"], grand_total)

    # The tail, so the rows add up to `total` rather than to a subset of it,
    # with its bar drawn at its share like every other row.
    if cut:
        cut_total = sum(row["total"] for row in cut)
        rows.append(
            {
                "code": "",
                "name": remainder_label,
                "uploaded": sum(row["uploaded"] for row in cut),
                "filed": sum(row["filed"] for row in cut),
                "total": cut_total,
                "bar_percent": bar(cut_total, grand_total),
                "percent": percent(cut_total, grand_total),
                "is_remainder": True,
            }
        )

    # Named only when one office is genuinely ahead. Calling a tie "the top
    # office" hands out a distinction the numbers did not award.
    #
    # Over the real offices only. "Other" is several offices added together, so
    # comparing first place against it would decide the leadership on how many
    # offices fell outside the cap, and with a cap of zero it would hand the
    # distinction to the remainder row itself.
    ranked = [row for row in rows if not row.get("is_remainder")]
    leader = None
    if ranked and (len(ranked) == 1 or ranked[0]["total"] > ranked[1]["total"]):
        leader = ranked[0]

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

    Counted over ACTIVE_STATUSES minus drafts, so each slice equals the rows
    behind `?status=`. The docstring claimed drafts were excluded while the
    filter it named included them, so the ring ran one short of the page it
    describes without anything saying why.

    Excluded rather than given a slice of their own, because a draft is visible
    only to its author: a Draft slice would make the ring mean something
    different for every viewer of the same data. The ring is therefore
    documents *in circulation*, which is what the caption now says.

    Not office-scoped, and deliberately so — the slices link to `?status=`,
    which is not office-scoped either. The per-office queues are the stat cards,
    which count through `apply_scope` and link to `?scope=`. Making the ring
    office-scoped as well looked right until an administrator opened it: the
    queues resolve against the viewer's own office, so a system administrator
    saw their own office's three records where the page showed the university's.
    """
    live = records.filter(status__in=ACTIVE_STATUSES).exclude(status=Status.DRAFT)

    def by_status(status):
        return live.filter(status=status).distinct().count()

    repository_total = documents.distinct().count()
    # Everything that did not come out of tracking. It tested `source == UPLOAD`,
    # which left a scanned document counted Completed here while the repository
    # tile beside it read Historical — see documents.models.COMPLETED_SOURCE.
    historical = documents.exclude(source=COMPLETED_SOURCE).distinct().count()

    return {
        "pending_receipt": by_status(Status.PENDING_RECEIPT),
        "received": by_status(Status.RECEIVED),
        "in_process": by_status(Status.IN_PROCESS),
        "pending_upload": by_status(Status.COMPLETED_PENDING_UPLOAD),
        # Not a slice; the stat card and the memo both read it from here.
        "overdue": records.filter(overdue_q()).distinct().count(),
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

"""Timing for external slips, separate from the legacy OVPA populations."""

from django.utils import timezone

from apps.core.business_time import business_seconds_between, load_holidays, with_office_schedule

from .models import Status


def stage_for(record, office=None):
    """The current custodian's stage; office direction is an independent axis."""
    return record.status


@with_office_schedule
def timing_for_record(record, *, office=None, now=None):
    now = now or timezone.now()
    steps = list(record.routing_steps.order_by("sequence"))
    activities = list(record.activities.filter(actual_at__isnull=False).order_by("actual_at", "pk"))
    end = record.completed_at or now
    office_id = getattr(office, "pk", office)
    totals = {
        "internal_seconds": None,
        "external_seconds": None,
        "receipt_wait_seconds": None,
        "overall_seconds": None,
        "started_at": None,
        "ended_at": record.completed_at,
    }
    # Original SENT/RECEIVED activity timestamps are immutable audit evidence,
    # and must not override the corrected movement dates on routing steps.
    known = (
        [step.sent_at for step in steps]
        + [step.received_at for step in steps if step.received_at]
        + [row.actual_at for row in activities if row.event not in {"SENT", "RECEIVED"}]
    )
    if not known:
        return totals
    earliest = min(known)
    totals["started_at"] = earliest
    holidays = load_holidays()
    totals["overall_seconds"] = business_seconds_between(earliest, max(end, earliest), holidays)

    # Completed work awaiting filing is outside custody work, even after reopening.
    pauses = []
    pause_start = None
    for row in activities:
        if row.event == "COMPLETED":
            pause_start = row.actual_at
        elif row.event == "REOPENED" and pause_start:
            pauses.append((pause_start, row.actual_at))
            pause_start = None
    if pause_start:
        pauses.append((pause_start, end))

    def add(name, start, finish, *, pause_work=True):
        finish = min(finish, end)
        if start > finish:
            return
        seconds = business_seconds_between(start, finish, holidays)
        if pause_work:
            for left, right in pauses:
                seconds -= business_seconds_between(max(start, left), min(finish, right), holidays)
        totals[name] = (totals[name] or 0) + max(seconds, 0)

    # A known paper source is evidence of external custody before the first dispatch.
    origin = next((row for row in activities if row.event == "CREATED"), None)
    if origin and (steps[0].from_external_office_id if steps else record.current_external_office_id):
        add("external_seconds", origin.actual_at, steps[0].sent_at if steps else end)
    for index, step in enumerate(steps):
        received = step.received_at
        add("receipt_wait_seconds", step.sent_at, received or end, pause_work=False)
        if received is None or received > end:
            continue
        custody_end = steps[index + 1].sent_at if index + 1 < len(steps) else end
        if step.to_office_id and (not office_id or step.to_office_id == office_id):
            add("internal_seconds", received, custody_end)
        elif step.to_external_office_id:
            add("external_seconds", received, custody_end)
    return totals


def summarize(records, *, office=None, now=None):
    # Count cases once even when callers filter across several historical endpoints.
    unique = {record.pk: record for record in records}
    stages = [
        {"status": value, "label": label, "count": sum(stage_for(row, office) == value for row in unique.values())}
        for value, label in Status.choices
    ]
    counts = {item["status"]: item["count"] for item in stages}
    return {
        "total": len(unique),
        "draft": counts[Status.DRAFT],
        "pending_filing": counts[Status.COMPLETED_PENDING_UPLOAD],
        "completed": counts[Status.COMPLETED],
        "working": sum(counts[stage] for stage in [Status.PENDING_RECEIPT, Status.RECEIVED, Status.IN_PROCESS]),
        "stages": stages,
    }

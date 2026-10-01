"""Verify turnaround arithmetic with known intervals, leaving no records behind.

Run from the repository root using the project's Python environment. Temporary
records, confirmations and their timestamps are inside a rolled-back transaction.
No login, notification, upload or external service is used.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, time
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.db import transaction  # noqa: E402
from django.test import override_settings  # noqa: E402
from django.utils import timezone  # noqa: E402

from apps.accounts.models import Office, User  # noqa: E402
from apps.core import analytics  # noqa: E402
from apps.core.business_time import NO_HOLIDAYS, humanise_business_seconds  # noqa: E402
from apps.tracking.models import RoutingStep, Status, TrackingRecord  # noqa: E402


def at(day, hour, minute=0):
    return timezone.make_aware(datetime(2026, 9, day, hour, minute))


class Audit:
    def __init__(self):
        self.actor = User.objects.filter(is_active=True, office__isnull=False).first()
        if self.actor is None:
            raise RuntimeError("An existing active office account is required.")
        self.office = Office.objects.exclude(pk=self.actor.office_id).first() or self.actor.office
        self.prefix = "TURN-CHECK-" + uuid4().hex[:16]
        self.ids = []
        self.checked = 0
        self.failures = []
        self.month = date(2026, 9, 1)
        self.now = at(30, 17)

    def equal(self, label, actual, expected):
        self.checked += 1
        if actual != expected:
            self.failures.append({"check": label, "actual": actual, "expected": expected})

    def record(self, created, received, completed, *, due=None, status=Status.COMPLETED):
        record = TrackingRecord.objects.create(
            tracking_number=f"{self.prefix}-{len(self.ids)}", subject="Temporary turnaround verification",
            instructions="Verification only", originating_office=self.actor.office,
            current_office=self.office, created_by=self.actor, status=status,
            first_received_at=received, completed_at=completed, due_at=due, current_batch=1,
        )
        TrackingRecord.objects.filter(pk=record.pk).update(created_at=created)
        record.refresh_from_db()
        self.ids.append(record.pk)
        return record

    def step(self, record, sent, received, *, batch=1):
        return RoutingStep.objects.create(
            record=record, sequence=record.routing_steps.count() + 1, batch=batch,
            from_office=self.actor.office, to_office=self.office, sent_by=self.actor,
            sent_at=sent, received_at=received,
        )

    def panel(self, *records):
        queryset = TrackingRecord.objects.filter(pk__in=[record.pk for record in records])
        result = analytics.turnaround(queryset, self.month, now=self.now)
        return queryset, {stage["key"]: stage for stage in result["stages"]}, result

    def run(self):
        self.equal("zero working time is explicit", humanise_business_seconds(0), "0 mins")
        self.equal("missing working time is distinct", humanise_business_seconds(None), "—")
        self.equal("positive seconds are not zero", humanise_business_seconds(0.5), "under a minute")
        self.equal("zero elapsed time is explicit", analytics.humanise_duration(at(14, 8) - at(14, 8)), "0 mins")

        record = self.record(at(14, 8), at(14, 8, 15), at(14, 10), due=at(14, 10))
        self.step(record, at(14, 8), at(14, 8, 15))
        self.step(record, at(14, 9), at(14, 9, 5))
        records, stages, result = self.panel(record)
        receipt = stages["receipt"]
        self.equal("two handovers", receipt["samples"], 2)
        self.equal("one unique document", receipt["documents"], 1)
        self.equal("handover mean in seconds", receipt["average_seconds"], 600)
        self.equal("short mean label", receipt["average_label"], "10 mins")
        self.equal("processing starts at first receipt", stages["processing"]["average_seconds"], 6300)
        self.equal("lifetime starts at creation", stages["lifetime"]["average_seconds"], 7200)
        self.equal("deadline equality is on time", result["on_time"], 1)
        self.equal("only deadline records form denominator", result["on_time_total"], 1)
        daily = analytics.turnaround_by_day(records, self.month, now=self.now)
        monthly = analytics.turnaround_by_month(records, year=2026, now=self.now)
        day = next(row for row in daily["rows"] if row["month"] == date(2026, 9, 14))
        month = monthly["rows"][8]
        for name, row in (("daily", day), ("monthly", month)):
            self.equal(name + " short wait precision", row["receipt"], 600 / 28800)
            self.equal(name + " handovers", row["receipt_samples"], 2)
            self.equal(name + " unique documents", row["receipt_documents"], 1)
        self.equal("export sums handovers per document", analytics.record_durations(record, record.routing_steps.all(), now=self.now)["receipt"], 1200)

        instant = self.record(at(14, 8), at(14, 8), at(14, 8))
        self.step(instant, at(14, 8), at(14, 8))
        weekend = self.record(at(12, 9), at(12, 10), at(12, 10))
        self.step(weekend, at(12, 9), at(12, 10))
        _, stages, _ = self.panel(instant, weekend)
        self.equal("zero intervals count", stages["receipt"]["samples"], 2)
        self.equal("zero intervals mean", stages["receipt"]["average_seconds"], 0)
        self.equal("zero observations", stages["receipt"]["zero_working_time"], 2)
        self.equal("instant confirmation count", stages["receipt"]["instantaneous"], 1)
        self.equal("actual outside-hours interval count", stages["receipt"]["outside_office_hours"], 1)
        self.equal("zero is not fastest", stages["receipt"]["fastest"], None)

        weighted = []
        for day_number, hours in ((14, 1), (15, 3), (15, 3), (15, 3)):
            item = self.record(at(day_number, 8), at(day_number, 8), at(day_number, 8 + hours))
            weighted.append(item)
        _, stages, _ = self.panel(*weighted)
        self.equal("monthly mean weights individual documents", stages["lifetime"]["average_seconds"], 9000)
        self.equal("weighted mean denominator", stages["lifetime"]["samples"], 4)

        cross_month = self.record(timezone.make_aware(datetime(2026, 8, 31, 16)), at(1, 9), at(1, 10))
        self.step(cross_month, timezone.make_aware(datetime(2026, 8, 31, 16)), at(1, 9))
        _, stages, _ = self.panel(cross_month)
        self.equal("month assigned by confirmation", stages["receipt"]["samples"], 1)
        self.equal("cross-month working hours", stages["receipt"]["average_seconds"], 7200)

        pending = self.record(at(14, 8), None, None, status=Status.PENDING_RECEIPT)
        self.step(pending, at(14, 9), None)
        self.step(pending, at(14, 9), None)
        self.step(pending, at(14, 9), None, batch=0)
        _, stages, result = self.panel(pending, record)
        self.equal("waiting handovers count current batch only", result["awaiting_confirmation"], 2)
        self.equal("waiting unique documents", result["awaiting_confirmation_documents"], 1)
        self.equal("waiting stage documents", result["awaiting_confirmation_pending_documents"], 1)
        self.equal("waiting other-stage documents", result["awaiting_confirmation_other_documents"], 0)
        self.equal("waiting is not a zero receipt sample", stages["receipt"]["samples"], 2)

        filed_later = self.record(at(14, 8), at(14, 9), at(14, 10), status=Status.COMPLETED_PENDING_UPLOAD)
        _, stages, _ = self.panel(filed_later)
        self.equal("completed awaiting upload counts as completed work", stages["lifetime"]["samples"], 1)

        future = self.record(at(14, 8), at(30, 18), at(30, 19))
        self.step(future, at(14, 9), at(30, 18))
        records, stages, _ = self.panel(future)
        self.equal("future receipt excluded", stages["receipt"]["samples"], 0)
        self.equal("future completion excluded", stages["lifetime"]["samples"], 0)
        self.equal("future events absent from daily graph", analytics.turnaround_by_day(records, self.month, now=self.now)["has_data"], False)
        self.equal("future events absent from annual graph", analytics.turnaround_by_month(records, year=2026, now=self.now)["has_data"], False)

        invalid = self.record(at(14, 10), at(14, 9), at(14, 8))
        self.step(invalid, at(14, 10), at(14, 9))
        _, stages, _ = self.panel(invalid)
        self.equal("reversed receipt excluded", stages["receipt"]["excluded"], 1)
        self.equal("reversed processing excluded", stages["processing"]["excluded"], 1)
        self.equal("reversed lifetime excluded", stages["lifetime"]["excluded"], 1)
        self.equal("invalid export durations are absent", analytics.record_durations(invalid, invalid.routing_steps.all(), now=self.now), {"receipt": None, "processing": None, "lifetime": None})


def main():
    with override_settings(OFFICE_DAY_START=time(8), OFFICE_DAY_END=time(17),
                           OFFICE_LUNCH_START=time(12), OFFICE_LUNCH_END=time(13)), \
            patch("apps.core.analytics.load_holidays", return_value=NO_HOLIDAYS):
        audit = Audit()
        with transaction.atomic():
            try:
                audit.run()
            finally:
                transaction.set_rollback(True)
    audit.equal("temporary records rolled back", TrackingRecord.objects.filter(tracking_number__startswith=audit.prefix).count(), 0)
    result = {"checks": audit.checked, "failures": audit.failures, "persistent_changes": False}
    print(json.dumps(result, indent=2, default=str))
    return bool(audit.failures)


if __name__ == "__main__":
    raise SystemExit(main())

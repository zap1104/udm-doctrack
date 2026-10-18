"""Known calendar intervals, independent of the day the audit happens to run."""

from datetime import datetime, time

import pytest
from django.utils import timezone

from apps.core.analytics import record_durations
from apps.core.models import Holiday, OfficeSchedule
from apps.tracking import external_services
from apps.tracking.external_analytics import timing_for_record
from apps.tracking.models import Status, TrackingRecord

pytestmark = pytest.mark.django_db


def at(day, hour, minute=0, second=0):
    return timezone.make_aware(datetime(2026, 10, day, hour, minute, second))


@pytest.fixture(autouse=True)
def known_schedule():
    OfficeSchedule.objects.update_or_create(
        pk=1,
        defaults={
            "opens_at": time(8), "closes_at": time(17),
            "break_start": time(12), "break_end": time(13), "working_days": 5,
        },
    )


INTERVALS = [
    pytest.param(at(17, 9), at(18, 17), 0, id="weekend-only"),
    pytest.param(at(16, 16), at(19, 9), 7200, id="friday-to-monday"),
    pytest.param(at(13, 12), at(13, 13), 0, id="lunch-only"),
    pytest.param(at(13, 7, 59, 59), at(13, 8, 0, 1), 1, id="opening-second"),
    pytest.param(at(13, 16, 59, 59), at(13, 17, 0, 1), 1, id="closing-second"),
]


@pytest.mark.parametrize("start,end,expected", INTERVALS)
def test_internal_processing_counts_only_known_working_seconds(users, offices, start, end, expected):
    record = TrackingRecord.objects.create(
        tracking_number="CALENDAR-BOUNDARY", subject="Known processing interval",
        instructions="Review", originating_office=offices["MED"], created_by=users["med"],
        status=Status.COMPLETED, first_received_at=start, completed_at=end,
    )
    TrackingRecord.objects.filter(pk=record.pk).update(created_at=start)
    record.refresh_from_db()
    values = record_durations(record, [], now=end)
    assert values["processing"] == expected
    assert values["lifetime"] == expected
    assert values["receipt"] is None


@pytest.mark.parametrize("start,end,expected", INTERVALS)
@pytest.mark.parametrize("custodian", ["internal", "external"])
def test_external_workflow_keeps_zero_measured_custody_distinct_from_unknown(
    users, offices, monkeypatch, start, end, expected, custodian,
):
    monkeypatch.setattr(timezone, "now", lambda: start)
    source = external_services.create_external_office(user=users["med_admin"], name="Calendar source")
    record = external_services.create_record(
        user=users["med_admin"], title="Known custody interval", from_external_office=source,
    )
    if custodian == "internal":
        external_services.route_record(
            record, user=users["med_admin"], expected_revision=1, to_office=offices["MED"],
        )
        external_services.confirm_receipt(record, user=users["med"], expected_revision=2)
    else:
        target = external_services.create_external_office(user=users["med_admin"], name="Calendar recipient")
        external_services.route_record(
            record, user=users["med_admin"], expected_revision=1, to_external_office=target,
        )
        external_services.record_external_receipt(record, user=users["med_admin"], expected_revision=2)
    totals = timing_for_record(record, office=offices["MED"], now=end)
    measured = f"{custodian}_seconds"
    unmeasured = "external_seconds" if custodian == "internal" else "internal_seconds"
    assert totals[measured] == expected
    assert totals[unmeasured] is None
    assert totals["receipt_wait_seconds"] == 0
    assert totals["overall_seconds"] == expected


def test_entered_holiday_excludes_internal_processing_and_external_receipt_wait(users, offices, monkeypatch):
    start, end = at(13, 8), at(13, 17)
    Holiday.objects.create(name="Audit closure", date=start.date())
    record = TrackingRecord.objects.create(
        tracking_number="HOLIDAY-BOUNDARY", subject="Holiday processing",
        instructions="Review", originating_office=offices["MED"], created_by=users["med"],
        status=Status.COMPLETED, first_received_at=start, completed_at=end,
    )
    TrackingRecord.objects.filter(pk=record.pk).update(created_at=start)
    record.refresh_from_db()
    assert record_durations(record, [], now=end)["processing"] == 0
    monkeypatch.setattr(timezone, "now", lambda: start)
    source = external_services.create_external_office(user=users["med_admin"], name="Holiday sender")
    external = external_services.create_record(
        user=users["med_admin"], title="Holiday wait", from_external_office=source,
    )
    external_services.route_record(
        external, user=users["med_admin"], expected_revision=1, to_office=offices["MED"],
    )
    totals = timing_for_record(external, now=end)
    assert totals["receipt_wait_seconds"] == 0
    assert totals["internal_seconds"] is None
    assert totals["overall_seconds"] == 0

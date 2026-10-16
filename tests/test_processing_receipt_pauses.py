from datetime import date, datetime
from uuid import uuid4

import pytest
from django.utils import timezone

from apps.core import analytics
from apps.tracking.models import RoutingStep, Status, TrackingRecord

pytestmark = pytest.mark.django_db
HOUR = 3600


def stamp(hour, minute=0, day=10, month=8):
    return timezone.make_aware(datetime(2026, month, day, hour, minute))


def finished(users, offices, *, first=None, end=None, created=None):
    record = TrackingRecord.objects.create(
        tracking_number=f"PAUSE-{uuid4().hex[:16]}", subject="Receipt pause calculation",
        instructions="For action", originating_office=offices["MED"], created_by=users["med"],
        status=Status.COMPLETED, first_received_at=first or stamp(9), completed_at=end or stamp(16),
        current_batch=2,
    )
    TrackingRecord.objects.filter(pk=record.pk).update(created_at=created or stamp(8))
    record.refresh_from_db()
    return record


def endorse(record, users, offices, *, sent, received, batch=1, sequence=1, target="SUP", sender="MED"):
    return RoutingStep.objects.create(
        record=record, sequence=sequence, batch=batch, from_office=offices[sender],
        to_office=offices[target], sent_by=users["med"], sent_at=sent, received_at=received,
    )


def processing(record, *, month=date(2026, 8, 1)):
    result = analytics.turnaround(TrackingRecord.objects.filter(pk=record.pk), month=month)
    return next(stage for stage in result["stages"] if stage["key"] == "processing")


def test_forwarding_pauses_both_clocks_and_keeps_receipt_samples(users, offices):
    record = finished(users, offices)
    endorse(record, users, offices, sent=stamp(8), received=stamp(9))
    endorse(record, users, offices, sent=stamp(10), received=stamp(11), batch=2, sequence=2,
            sender="SUP", target="HR")
    result = analytics.turnaround(TrackingRecord.objects.filter(pk=record.pk), month=date(2026, 8, 1))
    stage = next(item for item in result["stages"] if item["key"] == "processing")
    assert stage["average_seconds"] == 5 * HOUR
    assert stage["average_calendar_seconds"] == 6 * HOUR
    assert result["receipt_samples"] == 2
    assert result["lifetime"] == "7 hrs"
    values = analytics.record_durations(record, list(record.routing_steps.all()))
    assert values["processing"] == 5 * HOUR
    assert values["receipt"] == 2 * HOUR


def test_received_idle_time_remains_processing(users, offices):
    record = finished(users, offices)
    endorse(record, users, offices, sent=stamp(8), received=stamp(9))
    assert processing(record)["average_seconds"] == 6 * HOUR


def test_parallel_waits_are_merged_and_wait_for_last_receipt(users, offices):
    record = finished(users, offices)
    endorse(record, users, offices, sent=stamp(8), received=stamp(9))
    endorse(record, users, offices, sent=stamp(10), received=stamp(11), batch=2, sequence=2,
            sender="SUP", target="HR")
    endorse(record, users, offices, sent=stamp(10), received=stamp(14), batch=2, sequence=3,
            sender="SUP", target="REC")
    stage = processing(record)
    assert stage["average_seconds"] == 3 * HOUR  # 9-10 and 14-16; one merged pause.
    assert stage["average_calendar_seconds"] == 3 * HOUR


def test_superseded_unconfirmed_wait_ends_when_next_batch_starts(users, offices):
    record = finished(users, offices)
    endorse(record, users, offices, sent=stamp(8), received=stamp(9))
    endorse(record, users, offices, sent=stamp(10), received=None, batch=2, sequence=2,
            sender="SUP", target="HR")
    endorse(record, users, offices, sent=stamp(11), received=stamp(13), batch=3, sequence=3,
            sender="SUP", target="REC")
    assert processing(record)["average_seconds"] == 4 * HOUR


def test_unreceived_final_endorsement_pauses_until_completion(users, offices):
    record = finished(users, offices)
    endorse(record, users, offices, sent=stamp(8), received=stamp(9))
    endorse(record, users, offices, sent=stamp(10), received=None, batch=2, sequence=2,
            sender="SUP", target="HR")
    assert processing(record)["average_seconds"] == HOUR


def test_initial_parallel_pending_tail_is_not_processing(users, offices):
    record = finished(users, offices)
    endorse(record, users, offices, sent=stamp(8), received=stamp(9))
    endorse(record, users, offices, sent=stamp(8), received=stamp(11), sequence=2, target="HR")
    assert processing(record)["average_seconds"] == 4 * HOUR


def test_historical_pause_outside_completion_month_is_still_removed(users, offices):
    record = finished(users, offices, first=stamp(9, day=31), end=stamp(16, day=1, month=9),
                      created=stamp(8, day=31))
    endorse(record, users, offices, sent=stamp(8, day=31), received=stamp(9, day=31))
    endorse(record, users, offices, sent=stamp(10, day=31), received=stamp(15, day=31),
            batch=2, sequence=2, sender="SUP", target="HR")
    stage = processing(record, month=date(2026, 9, 1))
    assert stage["average_seconds"] == 10 * HOUR
    monthly = analytics.turnaround_by_month(TrackingRecord.objects.filter(pk=record.pk), year=2026)
    daily = analytics.turnaround_by_day(TrackingRecord.objects.filter(pk=record.pk), date(2026, 9, 1))
    assert monthly["rows"][8]["processing_office_seconds"] == stage["average_seconds"]
    assert daily["rows"][0]["processing_office_seconds"] == stage["average_seconds"]


def test_reversed_confirmation_is_not_invented_as_a_missing_receipt(users, offices):
    record = finished(users, offices)
    endorse(record, users, offices, sent=stamp(8), received=stamp(9))
    endorse(record, users, offices, sent=stamp(11), received=stamp(10), batch=2, sequence=2,
            sender="SUP", target="HR")
    assert processing(record)["average_seconds"] == 6 * HOUR


def test_custody_clock_still_ends_when_office_sends_document_on(users, offices):
    record = finished(users, offices)
    first = endorse(record, users, offices, sent=stamp(8), received=stamp(9))
    endorse(record, users, offices, sent=stamp(10), received=stamp(11), batch=2, sequence=2,
            sender="SUP", target="HR")
    waits = analytics.record_waits(record, list(record.routing_steps.all()))
    assert waits[first.pk]["held"]["office_seconds"] == HOUR


@pytest.mark.parametrize("confirmation", [None, stamp(16, day=15, month=10)])
def test_receipt_after_completion_cannot_resume_processing(users, offices, confirmation):
    record = finished(users, offices)
    endorse(record, users, offices, sent=stamp(8), received=stamp(9))
    endorse(record, users, offices, sent=stamp(10), received=confirmation, batch=2, sequence=2,
            sender="SUP", target="HR")
    assert processing(record)["average_seconds"] == HOUR


def test_future_endorsement_cannot_change_completed_processing(users, offices):
    record = finished(users, offices)
    endorse(record, users, offices, sent=stamp(8), received=stamp(9))
    endorse(record, users, offices, sent=stamp(10, day=15, month=10), received=None,
            batch=2, sequence=2, sender="SUP", target="HR")
    assert processing(record)["average_seconds"] == 6 * HOUR


def test_future_intermediate_batch_cannot_hide_valid_successor(users, offices):
    record = finished(users, offices)
    endorse(record, users, offices, sent=stamp(8), received=stamp(9))
    endorse(record, users, offices, sent=stamp(10), received=None, batch=2, sequence=2,
            sender="SUP", target="HR")
    endorse(record, users, offices, sent=stamp(10, day=11), received=None,
            batch=3, sequence=3, sender="SUP", target="REC")
    endorse(record, users, offices, sent=stamp(11), received=stamp(13),
            batch=4, sequence=4, sender="SUP", target="REC")
    stage = processing(record)
    assert stage["average_seconds"] == 4 * HOUR  # 9-10 and 13-16, despite the invalid future batch.
    assert stage["average_calendar_seconds"] == 4 * HOUR
    assert analytics.record_durations(record, list(record.routing_steps.all()))["processing"] == 4 * HOUR

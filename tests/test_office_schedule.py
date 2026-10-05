"""Saved schedules must be validated, restricted and used consistently by reports."""
from datetime import date, datetime, time

import pytest
from django.test import Client
from django.utils import timezone

from apps.core import analytics
from apps.core.business_time import business_seconds_between, current_schedule, office_schedule_context
from apps.core.models import AuditLog, Holiday, OfficeSchedule
from apps.tracking.models import Status, TrackingRecord
from apps.tracking.services import create_draft_record

URL = "/administration/office-schedule/"
VALID = {"working_days": "5", "opens_at": "09:00", "closes_at": "16:00",
         "break_enabled": "on", "break_start": "12:00", "break_end": "12:30"}


def at(day, hour):
    return timezone.make_aware(datetime(2026, 10, day, hour))


@pytest.mark.django_db
def test_admin_can_save_schedule_and_change_is_audited(client, users):
    client.force_login(users["admin"])
    assert client.get(URL).status_code == 200
    assert not OfficeSchedule.objects.exists(), "Opening the editor must not change defaults."
    response = client.post(URL, VALID)
    assert response.status_code == 302
    schedule = OfficeSchedule.objects.get()
    assert schedule.pk == 1 and schedule.opens_at == time(9) and schedule.break_end == time(12, 30)
    audit = AuditLog.objects.get(target_type="OfficeSchedule")
    assert audit.actor == users["admin"]
    assert audit.extra["before"]["break_end"] == "13:00:00"
    assert audit.extra["after"]["break_end"] == "12:30:00"
    assert URL in client.get("/administration/").content.decode()
    assert "/administration/holidays/" in client.get(URL).content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["med_admin", "med", "viewer"])
def test_only_system_admins_can_read_or_write_schedule(client, users, role):
    client.force_login(users[role])
    assert client.get(URL).status_code == 403
    assert client.post(URL, VALID).status_code == 403
    assert not OfficeSchedule.objects.exists()
    assert not AuditLog.objects.filter(target_type="OfficeSchedule").exists()


@pytest.mark.django_db
def test_anonymous_and_csrf_requests_cannot_write(users):
    client = Client(enforce_csrf_checks=True)
    assert client.get(URL, secure=True).status_code == 302
    client.force_login(users["admin"])
    assert client.post(URL, VALID, secure=True).status_code == 403
    assert not OfficeSchedule.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("changes", [
    {"closes_at": "09:00"}, {"closes_at": "08:00"}, {"working_days": "0"}, {"working_days": "8"},
    {"break_start": "08:00"}, {"break_end": "17:00"}, {"break_end": "11:00"},
    {"break_start": "09:00", "break_end": "16:00"}, {"break_start": ""}, {"break_end": ""},
    {"opens_at": "broken"},
])
def test_invalid_schedules_do_not_change_the_database(client, users, changes):
    client.force_login(users["admin"])
    assert client.post(URL, {**VALID, **changes}).status_code == 400
    assert not OfficeSchedule.objects.exists()


@pytest.mark.django_db
def test_break_can_be_disabled_and_reenabled(client, users):
    client.force_login(users["admin"])
    data = {key: value for key, value in VALID.items() if key not in {"break_enabled", "break_start", "break_end"}}
    assert client.post(URL, data).status_code == 302
    assert OfficeSchedule.objects.get().break_start == OfficeSchedule.objects.get().break_end == time(9)
    assert business_seconds_between(at(5, 8), at(5, 17)) == 7 * 3600
    assert client.post(URL, VALID).status_code == 302
    assert business_seconds_between(at(5, 8), at(5, 17)) == 6.5 * 3600


@pytest.mark.django_db
def test_each_calculation_uses_a_snapshot_and_next_calculation_sees_saved_changes():
    schedule = OfficeSchedule.objects.create()
    with office_schedule_context():
        assert current_schedule()["break_end"] == time(13)
        schedule.break_end = time(12, 30)
        schedule.save()
        assert current_schedule()["break_end"] == time(13)
    with office_schedule_context():
        assert current_schedule()["break_end"] == time(12, 30)


@pytest.mark.django_db
def test_saved_break_and_holiday_agree_across_cards_daily_and_yearly_graphs(client, users, memo_type):
    record = create_draft_record(user=users["med"], subject="Measured schedule", instructions="x", document_type=memo_type)
    TrackingRecord.objects.filter(pk=record.pk).update(status=Status.COMPLETED, created_at=at(5, 8),
        first_received_at=at(5, 8), completed_at=at(6, 17))
    records = TrackingRecord.objects.filter(pk=record.pk)
    client.force_login(users["admin"])
    assert client.post(URL, VALID).status_code == 302
    Holiday.objects.create(date=date(2026, 10, 6), name="Work suspension")
    month = date(2026, 10, 1)
    now = at(7, 17)
    summary = analytics.turnaround(records, month, now=now)
    daily = analytics.turnaround_by_day(records, month, now=now)
    yearly = analytics.turnaround_by_month(records, year=2026, now=now)
    lifetime = next(stage for stage in summary["stages"] if stage["key"] == "lifetime")
    assert lifetime["average_seconds"] == 6.5 * 3600
    assert lifetime["average_calendar_seconds"] == 33 * 3600
    assert lifetime["average_outside_seconds"] == 26.5 * 3600
    assert summary["working_day_hours"] == daily["working_day_hours"] == yearly["working_day_hours"] == 6.5
    day = next(row for row in daily["rows"] if row["month"].day == 6)
    assert day["lifetime_office_seconds"] == yearly["rows"][9]["lifetime_office_seconds"] == lifetime["average_seconds"]
    assert day["lifetime"] == 1
    assert "12:30 PM" in summary["office_schedule"]["lunch"]

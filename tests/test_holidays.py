"""Holidays, kept by system administrators, excluded from office hours.

Every office's turnaround figures move when this table changes, so it is a
system administrator's screen, like Offices.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from django.utils import timezone

from apps.core import analytics
from apps.core.models import AuditLog, Holiday
from apps.tracking.models import Status, TrackingRecord

LIST = "/administration/holidays/"
NEW = "/administration/holidays/new/"


def _local(day, hour):
    return timezone.make_aware(datetime.combine(day, datetime.min.time().replace(hour=hour)))


@pytest.mark.django_db
def test_a_system_administrator_adds_a_holiday(client, users):
    client.force_login(users["admin"])
    response = client.post(
        NEW, {"date": "2026-12-30", "name": "Rizal Day", "recurring": "on", "is_active": "on"}
    )

    assert response.status_code == 302
    holiday = Holiday.objects.get()
    assert (holiday.date, holiday.recurring) == (date(2026, 12, 30), True)
    assert AuditLog.objects.filter(summary__contains="Rizal Day").exists(), "the change is audited"
    assert "Rizal Day" in client.get(LIST).content.decode()


@pytest.mark.django_db
def test_the_form_uses_the_browsers_date_picker(client, users):
    client.force_login(users["admin"])
    assert 'type="date"' in client.get(NEW).content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["med_admin", "med", "viewer"])
def test_nobody_else_can_open_or_change_them(client, users, role):
    client.force_login(users[role])

    assert client.get(LIST).status_code in (302, 403)
    client.post(NEW, {"date": "2026-12-30", "name": "Sneaky", "is_active": "on"})
    assert not Holiday.objects.exists()


@pytest.mark.django_db
def test_the_tab_is_offered_only_to_system_administrators(client, users):
    client.force_login(users["admin"])
    assert LIST in client.get("/administration/document-types/").content.decode()

    client.force_login(users["med_admin"])
    assert LIST not in client.get("/administration/document-types/").content.decode()


@pytest.mark.django_db
def test_a_holiday_shortens_the_turnaround_it_falls_inside(users, offices, memo_type):
    """Created Tuesday 8AM, completed Thursday 5PM: three working days, or two
    once Wednesday is a holiday."""
    from apps.tracking.services import create_draft_record

    record = create_draft_record(
        user=users["med"], subject="Across a holiday", instructions="x", document_type=memo_type,
    )
    tuesday, thursday = date(2026, 8, 25), date(2026, 8, 27)
    TrackingRecord.objects.filter(pk=record.pk).update(
        status=Status.COMPLETED,
        created_at=_local(tuesday, 8),
        first_received_at=_local(tuesday, 8),
        completed_at=_local(thursday, 17),
    )
    records = TrackingRecord.objects.filter(pk=record.pk)

    assert analytics.turnaround(records)["lifetime"] == "3 days"
    Holiday.objects.create(date=date(2026, 8, 26), name="Suspension")
    assert analytics.turnaround(records)["lifetime"] == "2 days"

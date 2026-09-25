"""Picking the month a turnaround figure answers for.

`?month=YYYY-MM` on the dashboard, Reports and the memo, one of the twelve
months the trend charts. The three read one service for one month, so they
print one figure for it; a value that is not one of those months is refused
with a note, never an error.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest
from django.contrib.messages import get_messages
from django.utils import timezone

from apps.core.analytics import month_window
from apps.tracking.models import TrackingRecord
from apps.tracking.services import complete_record, confirm_receipt, create_draft_record, route_record


def _month_start(moment):
    return timezone.localtime(moment).date().replace(day=1)


@pytest.fixture
def two_months(users, offices, memo_type):
    """One document finished two months ago in two working days, one this month."""
    made = []
    for subject in ("Earlier", "This month"):
        record = create_draft_record(
            user=users["med"], subject=subject, instructions="x", document_type=memo_type,
        )
        route_record(record, [offices["SUP"]], user=users["med"])
        confirm_receipt(record, user=users["sup"])
        record.refresh_from_db()
        complete_record(record, user=users["sup"])
        made.append(record)
    earlier = timezone.localtime().replace(day=10, hour=8, minute=0, second=0, microsecond=0)
    earlier = (earlier.replace(day=1) - timedelta(days=40)).replace(day=10)
    while earlier.weekday() > 2:  # a Monday to Wednesday, so two working days fit
        earlier += timedelta(days=1)
    TrackingRecord.objects.filter(pk=made[0].pk).update(
        created_at=earlier, first_received_at=earlier,
        completed_at=earlier + timedelta(days=1, hours=9),
    )
    return {"earlier": made[0], "month": _month_start(earlier), "now": made[1]}


def _param(month):
    return f"{month:%Y-%m}"


@pytest.mark.django_db
def test_the_dashboard_answers_for_the_month_picked(client, users, two_months):
    client.force_login(users["admin"])
    response = client.get(f"/?month={_param(two_months['month'])}")
    figures = response.context["turnaround"]
    body = " ".join(response.content.decode().split())

    assert figures["month"] == two_months["month"]
    assert figures["lifetime_samples"] == 1
    assert figures["lifetime"] == "2 days"
    assert f'{two_months["month"]:%B %Y}' in body
    assert f'{two_months["month"]:%B %Y} so far' not in body, "a finished month is not 'so far'"


@pytest.mark.django_db
def test_no_month_is_the_current_month(client, users, two_months):
    client.force_login(users["admin"])
    figures = client.get("/").context["turnaround"]

    assert figures["month"] == timezone.localdate().replace(day=1)
    assert figures["is_current"] is True


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/", "/reports/", "/memo/print/"])
@pytest.mark.parametrize("value", ["not-a-month", "2026-13", "1999-01", "2026-9-1"])
def test_a_month_that_is_not_charted_is_refused_with_a_note(client, users, path, value):
    client.force_login(users["admin"])
    response = client.get(f"{path}?month={value}")

    assert response.status_code == 200
    assert response.context["turnaround"]["month"] == timezone.localdate().replace(day=1)
    notes = [str(message) for message in get_messages(response.wsgi_request)]
    assert any("is not one of the 12 months" in note for note in notes), notes


@pytest.mark.django_db
def test_dashboard_reports_and_memo_print_one_figure_for_one_month(client, users, two_months):
    client.force_login(users["admin"])
    query = f"?office=all&month={_param(two_months['month'])}"
    dashboard = client.get(f"/{query}").context["turnaround"]
    reports = client.get(f"/reports/{query}").context["turnaround"]
    memo = client.get(f"/memo/print/{query}")

    for key in ("receipt", "processing", "lifetime"):
        assert dashboard[key] == reports[key], key
        assert dashboard[f"{key}_samples"] == reports[f"{key}_samples"], key
    lines = [line for section in memo.context["memo"] for line in section["lines"]]
    lifetime = next(line for line in lines if line["label"].startswith("Average lifetime"))
    assert lifetime["label"] == f'Average lifetime, {two_months["month"]:%B %Y}'
    assert lifetime["value"].startswith(dashboard["lifetime"])


@pytest.mark.django_db
def test_the_picker_offers_the_charted_months_and_keeps_the_office(client, users, offices):
    client.force_login(users["admin"])
    office = offices["SUP"].pk
    response = client.get(f"/?office={office}")
    picker = response.context["month_picker"]
    body = response.content.decode()

    months, _since = month_window()
    assert [option["value"] for option in picker["options"]] == [f"{m:%Y-%m}" for m in reversed(months)]
    assert [option["selected"] for option in picker["options"]][0] is True
    assert ("office", str(office)) in picker["keep"]
    assert f'<input type="hidden" name="office" value="{office}">' in body
    assert 'name="month" data-auto-submit' in body


@pytest.mark.django_db
def test_the_memo_print_link_carries_the_month(client, users, two_months):
    client.force_login(users["admin"])
    body = client.get(f"/?month={_param(two_months['month'])}").content.decode()

    assert f"/memo/print/?month={_param(two_months['month'])}" in body


# --- fastest and slowest, named ---------------------------------------------------
@pytest.fixture
def three_in_a_past_month(users, offices, memo_type):
    """Three documents of one, two and three working days, two months back."""
    first = (timezone.localdate().replace(day=1) - timedelta(days=40)).replace(day=1)
    monday = first + timedelta(days=(7 - first.weekday()) % 7)
    made = []
    for index, days in enumerate((2, 1, 3)):
        start = monday + timedelta(days=7 * index)
        record = create_draft_record(
            user=users["med"], subject=f"{days} days", instructions="x", document_type=memo_type,
        )
        route_record(record, [offices["SUP"]], user=users["med"])
        confirm_receipt(record, user=users["sup"])
        record.refresh_from_db()
        complete_record(record, user=users["sup"])
        began = timezone.make_aware(datetime.combine(start, time(8)))
        TrackingRecord.objects.filter(pk=record.pk).update(
            created_at=began, first_received_at=began,
            completed_at=began + timedelta(days=days - 1, hours=9),
        )
        made.append(record)
    return {"month": first, "one_day": made[1], "three_days": made[2]}


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/", "/reports/"])
def test_the_page_names_the_fastest_and_slowest_document(client, users, three_in_a_past_month, path):
    client.force_login(users["admin"])
    body = client.get(f"{path}?month={_param(three_in_a_past_month['month'])}").content.decode()

    fastest, slowest = three_in_a_past_month["one_day"], three_in_a_past_month["three_days"]
    assert "Fastest" in body and "Slowest" in body
    assert f'href="{fastest.get_absolute_url()}">{fastest.tracking_number}</a>' in body
    assert f'href="{slowest.get_absolute_url()}">{slowest.tracking_number}</a>' in body


@pytest.mark.django_db
def test_the_memo_names_the_fastest_and_slowest_lifetime(client, users, three_in_a_past_month):
    client.force_login(users["admin"])
    month = three_in_a_past_month["month"]
    memo = client.get(f"/memo/print/?month={_param(month)}").context["memo"]
    lines = {line["label"]: line["value"] for section in memo for line in section["lines"]}

    assert lines[f"Fastest lifetime, {month:%B %Y}"] == f'1 day ({three_in_a_past_month["one_day"].tracking_number})'
    assert lines[f"Slowest lifetime, {month:%B %Y}"] == f'3 days ({three_in_a_past_month["three_days"].tracking_number})'


@pytest.mark.django_db
def test_one_document_has_no_fastest_to_name(client, users, two_months):
    """With one, fastest and slowest would both repeat the average."""
    client.force_login(users["admin"])
    body = client.get(f"/reports/?month={_param(two_months['month'])}").content.decode()

    assert "Slowest" not in body

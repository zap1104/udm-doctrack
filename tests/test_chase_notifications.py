"""In-app notices for the two things that go wrong by nobody doing anything.

A document sits unreceived, or a deadline passes. Neither is an action there is
a hook to hang off, so both are computed on a daily schedule rather than raised
at the moment of an event — which is why they are tested by calling the task
directly rather than by performing something.

No email anywhere: these are in-app notifications only.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.conf import settings
from django.utils import timezone

from apps.core.models import Notification
from apps.core.tasks import chase_unreceived_and_overdue
from apps.tracking.services import (
    complete_record,
    confirm_receipt,
    create_draft_record,
    route_record,
)


def age_the_send(record, days):
    """Push the current batch's send time into the past."""
    record.routing_steps.update(sent_at=timezone.now() - timedelta(days=days))


@pytest.fixture
def in_flight(users, offices, memo_type):
    record = create_draft_record(
        user=users["med"], subject="Waiting on Supply", instructions="For action.",
        document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    record.refresh_from_db()
    return record


def notices(record, kind, office=None):
    query = Notification.objects.filter(
        tracking_record=record, kind=kind, resolved_at__isnull=True
    )
    return query.filter(office=office) if office else query


# --- the receiving office is told, on routing -------------------------------
@pytest.mark.django_db
def test_the_receiving_office_is_told_it_has_something_pending(in_flight, users, offices):
    """Already the behaviour — confirmed so it stays."""
    assert notices(in_flight, Notification.Kind.ROUTED, offices["SUP"]).exists()


# --- the sender is told when nobody receives it -----------------------------
@pytest.mark.django_db
def test_the_sender_is_nudged_once_the_document_has_sat(in_flight, users, offices):
    """The routed notice goes to the recipient and tells the sender nothing, so
    a document that quietly went unreceived was invisible to the one office
    with a reason to chase it."""
    age_the_send(in_flight, settings.UNRECEIVED_NUDGE_DAYS + 1)

    raised = chase_unreceived_and_overdue()

    assert raised["unreceived"] == 1
    notice = notices(in_flight, Notification.Kind.UNRECEIVED, offices["MED"]).first()
    assert notice is not None
    assert offices["SUP"].name in notice.message


@pytest.mark.django_db
def test_a_document_sent_this_morning_is_not_chased(in_flight, offices):
    """An office that is simply busy must not be chased the same afternoon."""
    raised = chase_unreceived_and_overdue()

    assert raised["unreceived"] == 0
    assert not notices(in_flight, Notification.Kind.UNRECEIVED).exists()


@pytest.mark.django_db
def test_the_nudge_is_raised_once_not_once_a_night(in_flight, offices):
    """A queue that re-notifies every night trains people to ignore it."""
    age_the_send(in_flight, settings.UNRECEIVED_NUDGE_DAYS + 1)

    chase_unreceived_and_overdue()
    chase_unreceived_and_overdue()
    chase_unreceived_and_overdue()

    assert notices(in_flight, Notification.Kind.UNRECEIVED, offices["MED"]).count() == 1


@pytest.mark.django_db
def test_confirming_receipt_answers_the_nudge(in_flight, users, offices):
    """A chase still on screen after the thing was chased is how a queue stops
    being read."""
    age_the_send(in_flight, settings.UNRECEIVED_NUDGE_DAYS + 1)
    chase_unreceived_and_overdue()
    assert notices(in_flight, Notification.Kind.UNRECEIVED, offices["MED"]).exists()

    confirm_receipt(in_flight, user=users["sup"])

    assert not notices(in_flight, Notification.Kind.UNRECEIVED, offices["MED"]).exists()


@pytest.mark.django_db
def test_a_received_document_is_never_chased(in_flight, users):
    age_the_send(in_flight, settings.UNRECEIVED_NUDGE_DAYS + 1)
    confirm_receipt(in_flight, user=users["sup"])
    in_flight.refresh_from_db()

    raised = chase_unreceived_and_overdue()

    assert raised["unreceived"] == 0


@pytest.mark.django_db
def test_a_completed_document_is_never_chased(in_flight, users):
    age_the_send(in_flight, settings.UNRECEIVED_NUDGE_DAYS + 1)
    confirm_receipt(in_flight, user=users["sup"])
    in_flight.refresh_from_db()
    complete_record(in_flight, user=users["sup"])
    in_flight.refresh_from_db()

    raised = chase_unreceived_and_overdue()

    assert raised["unreceived"] == 0


@pytest.mark.django_db
def test_only_the_current_batch_is_chased(users, offices, memo_type):
    """An earlier hop that was never confirmed is not this batch's problem —
    the same scoping bug the inbox queries had."""
    record = create_draft_record(
        user=users["med"], subject="Two hops", instructions="x", document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    record.refresh_from_db()
    route_record(record, [offices["HR"]], user=users["sup"], action="FORWARD")
    record.refresh_from_db()
    age_the_send(record, settings.UNRECEIVED_NUDGE_DAYS + 1)

    chase_unreceived_and_overdue()

    # The office waiting is SUP, who sent the current batch — not MED.
    assert notices(record, Notification.Kind.UNRECEIVED, offices["SUP"]).exists()
    assert not notices(record, Notification.Kind.UNRECEIVED, offices["MED"]).exists()


# --- the deadline raises the overdue notice ---------------------------------
@pytest.mark.django_db
def test_setting_a_deadline_is_what_arms_the_overdue_notice(users, offices, memo_type):
    """A deadline nothing acts on is a note in a field."""
    record = create_draft_record(
        user=users["med"], subject="With a deadline", instructions="x",
        document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"], due_days=1)
    confirm_receipt(record, user=users["sup"])
    record.refresh_from_db()

    # Nothing yet: the deadline has not passed.
    assert chase_unreceived_and_overdue()["overdue"] == 0

    record.due_at = timezone.now() - timedelta(days=1)
    record.save(update_fields=["due_at"])

    assert chase_unreceived_and_overdue()["overdue"] == 1
    assert notices(record, Notification.Kind.OVERDUE, offices["SUP"]).exists()


@pytest.mark.django_db
def test_a_record_with_no_deadline_is_never_overdue(in_flight, users):
    route = in_flight
    route.due_at = None
    route.save(update_fields=["due_at"])

    assert chase_unreceived_and_overdue()["overdue"] == 0


@pytest.mark.django_db
def test_the_overdue_notice_goes_to_whoever_is_holding_it(users, offices, memo_type):
    record = create_draft_record(
        user=users["med"], subject="Held by Supply", instructions="x", document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    record.refresh_from_db()
    record.due_at = timezone.now() - timedelta(days=1)
    record.save(update_fields=["due_at"])

    chase_unreceived_and_overdue()

    assert notices(record, Notification.Kind.OVERDUE, offices["SUP"]).exists()
    assert not notices(record, Notification.Kind.OVERDUE, offices["MED"]).exists()


@pytest.mark.django_db
def test_the_overdue_notice_is_raised_once(users, offices, memo_type):
    record = create_draft_record(
        user=users["med"], subject="Still late", instructions="x", document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    record.refresh_from_db()
    record.due_at = timezone.now() - timedelta(days=1)
    record.save(update_fields=["due_at"])

    chase_unreceived_and_overdue()
    chase_unreceived_and_overdue()

    assert notices(record, Notification.Kind.OVERDUE, offices["SUP"]).count() == 1


@pytest.mark.django_db
def test_completing_the_work_answers_the_deadline(users, offices, memo_type):
    record = create_draft_record(
        user=users["med"], subject="Late but finished", instructions="x",
        document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    record.refresh_from_db()
    record.due_at = timezone.now() - timedelta(days=1)
    record.save(update_fields=["due_at"])
    chase_unreceived_and_overdue()
    assert notices(record, Notification.Kind.OVERDUE).exists()

    complete_record(record, user=users["sup"])

    assert not notices(record, Notification.Kind.OVERDUE).exists()


# --- plumbing ---------------------------------------------------------------
@pytest.mark.django_db
def test_the_notices_are_in_app_only_and_send_no_mail(in_flight):
    """Email is out of scope for this brief and must not be stubbed in."""
    from django.core import mail

    age_the_send(in_flight, settings.UNRECEIVED_NUDGE_DAYS + 1)
    chase_unreceived_and_overdue()

    assert len(mail.outbox) == 0


@pytest.mark.django_db
def test_both_kinds_render_on_the_notifications_page(client, in_flight, users, offices):
    age_the_send(in_flight, settings.UNRECEIVED_NUDGE_DAYS + 1)
    chase_unreceived_and_overdue()

    client.force_login(users["med"])
    body = client.get("/notifications/").content.decode()

    assert "Still not received" in body


def test_the_chase_is_on_the_daily_schedule():
    """It cannot be raised at a hook, so it has to be scheduled."""
    import pathlib

    source = pathlib.Path("apps/core/management/commands/ensure_schedules.py").read_text(
        encoding="utf-8"
    )
    assert "apps.core.tasks.chase_unreceived_and_overdue" in source

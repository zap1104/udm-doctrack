"""A partial receipt must keep the remaining offices visible in reminders."""

from datetime import timedelta

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models.query import QuerySet
from django.utils import timezone

from apps.core.models import Notification
from apps.core.tasks import chase_unreceived_and_overdue
from apps.tracking.models import RoutingStep, Status, TrackingRecord
from apps.tracking.services import complete_record, confirm_receipt, create_draft_record, route_record


@pytest.fixture
def multi_office_record(users, offices, memo_type):
    record = create_draft_record(
        user=users["med"], subject="Receipt needed from two offices",
        instructions="For action.", document_type=memo_type,
    )
    route_record(record, [offices["SUP"], offices["HR"]], user=users["med"])
    record.current_step_queryset.update(
        sent_at=timezone.now() - timedelta(days=settings.UNRECEIVED_NUDGE_DAYS + 1)
    )
    return record


def unresolved(record, kind):
    return Notification.objects.filter(
        tracking_record=record, kind=kind, resolved_at__isnull=True,
    )


def _interleave_after_candidates(monkeypatch, candidate_model, record, transition):
    """Apply an action after the task loads candidates, before any row lock.

    Keep the real database fetch and service action. The one-shot hook also
    accepts candidate ID tuples with the record ID first instead of model rows.
    """
    fetch_all = QuerySet._fetch_all
    state = {"ran": False}

    def record_id(candidate):
        if isinstance(candidate, RoutingStep):
            return candidate.record_id
        if isinstance(candidate, TrackingRecord):
            return candidate.pk
        if isinstance(candidate, dict):
            return candidate.get("record_id", candidate.get("pk"))
        if isinstance(candidate, (tuple, list)) and candidate:
            return candidate[0]
        return candidate if isinstance(candidate, int) else None

    def fetch_then_transition(queryset):
        uncached = queryset._result_cache is None
        fetch_all(queryset)
        if (state["ran"] or not uncached or queryset.model is not candidate_model
                or queryset.query.select_for_update):
            return
        if not any(record_id(candidate) == record.pk for candidate in queryset._result_cache):
            return
        state["ran"] = True
        transition()

    monkeypatch.setattr(QuerySet, "_fetch_all", fetch_then_transition)
    return state


@pytest.mark.django_db
def test_sender_nudge_names_every_outstanding_recipient(multi_office_record, offices):
    raised = chase_unreceived_and_overdue()

    assert raised["unreceived"] == 1
    notice = unresolved(multi_office_record, Notification.Kind.UNRECEIVED).get()
    assert notice.office == offices["MED"]
    assert offices["SUP"].name in notice.message
    assert offices["HR"].name in notice.message


@pytest.mark.django_db
def test_partial_receipt_does_not_clear_the_sender_nudge(multi_office_record, users, offices):
    chase_unreceived_and_overdue()
    notice = unresolved(multi_office_record, Notification.Kind.UNRECEIVED).get()

    confirm_receipt(multi_office_record, user=users["sup"])

    notice.refresh_from_db()
    assert notice.resolved_at is None
    assert offices["HR"].name in notice.message
    assert offices["SUP"].name not in notice.message


@pytest.mark.django_db
def test_sender_nudge_updates_remaining_offices_without_another_notification(
    multi_office_record, users, offices,
):
    chase_unreceived_and_overdue()
    notice = unresolved(multi_office_record, Notification.Kind.UNRECEIVED).get()
    confirm_receipt(multi_office_record, user=users["sup"])

    raised = chase_unreceived_and_overdue()

    assert raised["unreceived"] == 0
    remaining = unresolved(multi_office_record, Notification.Kind.UNRECEIVED).get()
    assert remaining.pk == notice.pk
    assert offices["HR"].name in remaining.message
    assert offices["SUP"].name not in remaining.message


@pytest.mark.django_db
def test_final_receipt_clears_the_sender_nudge(multi_office_record, users):
    chase_unreceived_and_overdue()
    confirm_receipt(multi_office_record, user=users["sup"])
    confirm_receipt(multi_office_record, user=users["hr"])

    assert not unresolved(multi_office_record, Notification.Kind.UNRECEIVED).exists()
    assert chase_unreceived_and_overdue()["unreceived"] == 0


@pytest.mark.django_db
def test_overdue_before_receipt_notifies_recipients_who_owe_receipt(
    multi_office_record, offices,
):
    multi_office_record.due_at = timezone.now() - timedelta(days=1)
    multi_office_record.save(update_fields=["due_at"])

    raised = chase_unreceived_and_overdue()

    assert raised["overdue"] == 2
    assert set(unresolved(multi_office_record, Notification.Kind.OVERDUE).values_list(
        "office_id", flat=True,
    )) == {offices["SUP"].pk, offices["HR"].pk}
    for notice in unresolved(multi_office_record, Notification.Kind.OVERDUE):
        assert "receipt" in notice.message.lower()


@pytest.mark.django_db
def test_partial_receipt_notifies_pending_recipient_and_confirmed_holder(
    multi_office_record, users, offices,
):
    confirm_receipt(multi_office_record, user=users["sup"])
    multi_office_record.due_at = timezone.now() - timedelta(days=1)
    multi_office_record.save(update_fields=["due_at"])

    assert chase_unreceived_and_overdue()["overdue"] == 2
    notices = {
        notice.office_id: notice
        for notice in unresolved(multi_office_record, Notification.Kind.OVERDUE)
    }
    assert set(notices) == {offices["SUP"].pk, offices["HR"].pk}
    assert "receipt" in notices[offices["HR"].pk].message.lower()
    assert "receipt" not in notices[offices["SUP"].pk].message.lower()


@pytest.mark.django_db
def test_receipt_changes_existing_overdue_notice_to_work_without_duplicate(
    multi_office_record, users, offices,
):
    multi_office_record.due_at = timezone.now() - timedelta(days=1)
    multi_office_record.save(update_fields=["due_at"])
    chase_unreceived_and_overdue()
    notice = unresolved(multi_office_record, Notification.Kind.OVERDUE).get(
        office=offices["SUP"],
    )
    confirm_receipt(multi_office_record, user=users["sup"])

    notice.refresh_from_db()
    assert notice.resolved_at is None
    assert "receipt" not in notice.title.lower()
    assert "receipt" not in notice.message.lower()
    assert unresolved(multi_office_record, Notification.Kind.OVERDUE).count() == 2

    assert chase_unreceived_and_overdue()["overdue"] == 0
    notice.refresh_from_db()
    assert notice.resolved_at is None
    assert "receipt" not in notice.title.lower()
    assert "receipt" not in notice.message.lower()
    assert unresolved(multi_office_record, Notification.Kind.OVERDUE).count() == 2


@pytest.mark.django_db
def test_overdue_work_remains_visible_to_every_confirmed_recipient(
    multi_office_record, users, offices,
):
    confirm_receipt(multi_office_record, user=users["sup"])
    confirm_receipt(multi_office_record, user=users["hr"])
    multi_office_record.due_at = timezone.now() - timedelta(days=1)
    multi_office_record.save(update_fields=["due_at"])

    assert chase_unreceived_and_overdue()["overdue"] == 2
    assert set(unresolved(multi_office_record, Notification.Kind.OVERDUE).values_list(
        "office_id", flat=True,
    )) == {offices["SUP"].pk, offices["HR"].pk}
    for notice in unresolved(multi_office_record, Notification.Kind.OVERDUE):
        assert "receipt" not in notice.message.lower()


@pytest.mark.django_db
def test_overdue_ignores_old_batch_and_notifies_current_recipient(
    multi_office_record, users, offices,
):
    confirm_receipt(multi_office_record, user=users["sup"])
    route_record(
        multi_office_record, [offices["REC"]], user=users["sup"], action="FORWARD",
    )
    multi_office_record.due_at = timezone.now() - timedelta(days=1)
    multi_office_record.save(update_fields=["due_at"])

    assert chase_unreceived_and_overdue()["overdue"] == 1
    assert set(unresolved(multi_office_record, Notification.Kind.OVERDUE).values_list(
        "office_id", flat=True,
    )) == {offices["REC"].pk}


@pytest.mark.django_db
def test_forwarding_resolves_obsolete_batch_reminders(
    multi_office_record, users, offices,
):
    multi_office_record.due_at = timezone.now() - timedelta(days=1)
    multi_office_record.save(update_fields=["due_at"])
    chase_unreceived_and_overdue()
    previous_notices = list(unresolved(
        multi_office_record, Notification.Kind.UNRECEIVED,
    )) + list(unresolved(multi_office_record, Notification.Kind.OVERDUE))
    assert previous_notices
    confirm_receipt(multi_office_record, user=users["sup"])
    route_record(
        multi_office_record, [offices["REC"]], user=users["sup"], action="FORWARD",
    )

    chase_unreceived_and_overdue()

    for notice in previous_notices:
        notice.refresh_from_db()
        assert notice.resolved_at is not None


@pytest.mark.django_db
def test_partial_global_completion_resolves_remaining_receipt_notices(
    multi_office_record, users, offices,
):
    confirm_receipt(multi_office_record, user=users["sup"])
    assert unresolved(multi_office_record, Notification.Kind.ROUTED).filter(
        office=offices["HR"],
    ).exists()

    complete_record(multi_office_record, user=users["sup"])

    assert multi_office_record.status == Status.COMPLETED_PENDING_UPLOAD
    with pytest.raises(ValidationError):
        confirm_receipt(multi_office_record, user=users["hr"])
    assert not unresolved(multi_office_record, Notification.Kind.ROUTED).exists()


@pytest.mark.django_db
def test_chase_clears_legacy_receipt_notices_for_completed_work(multi_office_record, users, offices):
    confirm_receipt(multi_office_record, user=users["sup"])
    complete_record(multi_office_record, user=users["sup"])
    legacy = Notification.objects.create(
        office=offices["HR"], tracking_record=multi_office_record,
        kind=Notification.Kind.ROUTED, title="A document is waiting for your office",
        message="Your office still needs to confirm receipt.",
    )

    assert chase_unreceived_and_overdue() == {"unreceived": 0, "overdue": 0}

    legacy.refresh_from_db()
    assert legacy.resolved_at is not None


@pytest.mark.django_db
@pytest.mark.parametrize("transition", ["partial_receipt", "final_receipt", "forward"])
def test_chase_rechecks_sender_candidates_after_receipt_or_new_batch(
    multi_office_record, users, offices, monkeypatch, transition,
):
    if transition != "partial_receipt":
        confirm_receipt(multi_office_record, user=users["sup"])
    chase_unreceived_and_overdue()
    notice = unresolved(multi_office_record, Notification.Kind.UNRECEIVED).get()

    def act_before_chase_writes():
        if transition == "partial_receipt":
            confirm_receipt(multi_office_record, user=users["sup"])
        elif transition == "final_receipt":
            confirm_receipt(multi_office_record, user=users["hr"])
        else:
            route_record(
                multi_office_record, [offices["REC"]], user=users["sup"],
                action="FORWARD", due_at=None,
            )

    interleaved = _interleave_after_candidates(
        monkeypatch, RoutingStep, multi_office_record, act_before_chase_writes,
    )

    raised = chase_unreceived_and_overdue()

    assert interleaved["ran"], "The action must occur after sender candidates are loaded."
    assert raised["unreceived"] == 0
    if transition == "partial_receipt":
        remaining = unresolved(multi_office_record, Notification.Kind.UNRECEIVED).get()
        assert remaining.pk == notice.pk
        assert offices["HR"].name in remaining.message
        assert offices["SUP"].name not in remaining.message
    else:
        assert not unresolved(multi_office_record, Notification.Kind.UNRECEIVED).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("transition", ["partial_receipt", "completion", "forward"])
def test_chase_rechecks_overdue_candidates_after_receipt_or_transition(
    multi_office_record, users, offices, monkeypatch, transition,
):
    if transition != "partial_receipt":
        confirm_receipt(multi_office_record, user=users["sup"])
    multi_office_record.due_at = timezone.now() - timedelta(days=1)
    multi_office_record.save(update_fields=["due_at"])
    chase_unreceived_and_overdue()
    notice = unresolved(multi_office_record, Notification.Kind.OVERDUE).get(
        office=offices["SUP"],
    )

    def act_before_chase_writes():
        if transition == "partial_receipt":
            confirm_receipt(multi_office_record, user=users["sup"])
        elif transition == "completion":
            complete_record(multi_office_record, user=users["sup"])
        else:
            route_record(
                multi_office_record, [offices["REC"]], user=users["sup"],
                action="FORWARD", due_at=None,
            )

    interleaved = _interleave_after_candidates(
        monkeypatch, TrackingRecord, multi_office_record, act_before_chase_writes,
    )

    raised = chase_unreceived_and_overdue()

    assert interleaved["ran"], "The action must occur after overdue candidates are loaded."
    assert raised["overdue"] == 0
    if transition == "partial_receipt":
        notices = {
            alert.office_id: alert
            for alert in unresolved(multi_office_record, Notification.Kind.OVERDUE)
        }
        assert set(notices) == {offices["SUP"].pk, offices["HR"].pk}
        holder = notices[offices["SUP"].pk]
        assert holder.pk == notice.pk
        assert "receipt" not in holder.title.lower()
        assert "receipt" not in holder.message.lower()
        assert "receipt" in notices[offices["HR"].pk].message.lower()
    else:
        assert not unresolved(multi_office_record, Notification.Kind.OVERDUE).exists()
        assert not unresolved(multi_office_record, Notification.Kind.UNRECEIVED).exists()

"""Concurrent daily chases must raise only one unresolved sender reminder."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event, local

import pytest
from django.db import close_old_connections, connection, connections
from django.utils import timezone

from apps.core import notifications
from apps.core.models import Notification
from apps.core.tasks import chase_unreceived_and_overdue
from apps.tracking import services
from apps.tracking.models import TrackingRecord


@pytest.mark.django_db(transaction=True)
def test_simultaneous_chases_create_one_unresolved_sender_nudge(users, offices, settings, monkeypatch):
    settings.ENABLE_BACKGROUND_TASKS = False
    record = services.create_draft_record(
        user=users["med"], subject="Concurrent sender reminder", instructions="Review",
    )
    services.route_record(record, [offices["SUP"]], user=users["med"], due_at=None)
    record.current_step_queryset.update(
        sent_at=timezone.now() - timedelta(days=settings.UNRECEIVED_NUDGE_DAYS + 1),
    )
    first_ready_to_insert = Event()
    release_first_insert = Event()
    worker_state = local()
    real_notify_office = notifications.notify_office

    def synchronized_notify(office, **kwargs):
        if (kwargs["kind"] == Notification.Kind.UNRECEIVED
                and kwargs["tracking_record"].pk == record.pk):
            if worker_state.second:
                # Without a record lock, both runs pass the empty-notice check.
                # Release the first run here so that regression creates two real
                # notifications instead of leaving the first run waiting.
                release_first_insert.set()
            else:
                first_ready_to_insert.set()
                assert release_first_insert.wait(10), "Second chase never reached its lock or notification insert."
        return real_notify_office(office, **kwargs)

    monkeypatch.setattr(notifications, "notify_office", synchronized_notify)

    def chase(*, second):
        close_old_connections()
        worker_state.second = second

        def synchronize(execute, sql, params, many, context):
            if (second and "FOR UPDATE" in sql.upper()
                    and f'"{TrackingRecord._meta.db_table}"' in sql):
                # The first chase owns this record until its insert commits.
                # Let it finish immediately before the second waits for the lock.
                release_first_insert.set()
            return execute(sql, params, many, context)

        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '8s'")
            with connection.execute_wrapper(synchronize):
                return chase_unreceived_and_overdue()
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(chase, second=False)
        try:
            assert first_ready_to_insert.wait(10), "First chase never reached the sender notification insert."
            second = workers.submit(chase, second=True)
            outcomes = [first.result(timeout=20), second.result(timeout=20)]
        finally:
            release_first_insert.set()

    assert sum(outcome["unreceived"] for outcome in outcomes) == 1
    assert Notification.objects.filter(
        tracking_record=record,
        office=offices["MED"],
        kind=Notification.Kind.UNRECEIVED,
        resolved_at__isnull=True,
    ).count() == 1

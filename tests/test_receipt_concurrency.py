"""Receipt requests must serialize without reversing account and record locks."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections

from apps.accounts.models import User
from apps.tracking import services
from apps.tracking.models import RecordActivity, TrackingRecord


@pytest.mark.django_db(transaction=True)
def test_bulk_and_individual_receipts_by_same_user_do_not_deadlock(users, offices):
    record = services.create_draft_record(
        user=users["med"], subject="Concurrent bulk receipt", instructions="",
    )
    services.route_record(record, [offices["SUP"]], user=users["med"])
    actor_id = users["sup"].pk
    single_has_account = Event()
    bulk_reached_first_lock = Event()

    def receive(*, bulk):
        close_old_connections()
        first_lock = True

        def synchronize(execute, sql, params, many, context):
            nonlocal first_lock
            row_lock = "FOR UPDATE" in sql.upper() or "FOR NO KEY UPDATE" in sql.upper()
            account_lock = row_lock and f'"{User._meta.db_table}"' in sql
            record_lock = row_lock and f'"{TrackingRecord._meta.db_table}"' in sql
            if bulk and first_lock and account_lock:
                # The correct order waits for the account before acquiring any
                # record. Let the individual receipt finish and release it.
                first_lock = False
                bulk_reached_first_lock.set()
            result = execute(sql, params, many, context)
            if bulk and first_lock and record_lock:
                # The old bulk path owns the record before asking for the
                # account. Release the individual request at this exact point
                # to reproduce the opposite-lock cycle without timing sleeps.
                first_lock = False
                bulk_reached_first_lock.set()
            elif not bulk and first_lock and account_lock:
                first_lock = False
                single_has_account.set()
                assert bulk_reached_first_lock.wait(10), "Bulk receipt never reached its first lock."
            return result

        try:
            actor = User.objects.get(pk=actor_id)
            current = TrackingRecord.objects.get(pk=record.pk)
            # A regression must fail promptly even if database deadlock
            # detection is configured with a longer delay on the test server.
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '8s'")
            with connection.execute_wrapper(synchronize):
                try:
                    if bulk:
                        services.bulk_confirm_receipts([current], user=actor)
                    else:
                        services.confirm_receipt(current, user=actor)
                except ValidationError:
                    return "already received"
            return "received"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as workers:
        single = workers.submit(receive, bulk=False)
        try:
            assert single_has_account.wait(10), "Individual receipt never locked its account."
            bulk = workers.submit(receive, bulk=True)
            outcomes = [single.result(timeout=20), bulk.result(timeout=20)]
        finally:
            bulk_reached_first_lock.set()

    assert sorted(outcomes) == ["already received", "received"]
    assert record.activities.filter(event=RecordActivity.Event.RECEIVED).count() == 1
    step = record.routing_steps.get(batch=record.current_batch)
    assert step.received_at is not None
    assert step.received_by_id == actor_id

"""Deadlines come from an editable table, with the setting as the last word.

`settings.DEFAULT_ACTION_DUE_DAYS` was the only source of a default deadline, so
every office had three days for everything — a purchase request and a memo, a
department that signs same-day and one that meets weekly. `RoutingSLA` supplies
the number instead; the setting stays as the terminal fallback, so an empty
table behaves exactly as before.

`resolve_sla_due_days` is the single reader of that table. A second reader is a
second precedence order, and the two drift.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.conf import settings
from django.utils import timezone

from apps.core.models import DocumentType
from apps.tracking.models import RoutingSLA, Status
from apps.tracking.services import (
    create_draft_record,
    resolve_sla_due_days,
    route_record,
    sla_due_days_for,
)


@pytest.fixture
def purchase_request(db):
    return DocumentType.objects.create(code="PR", name="Purchase request", retention_years=5)


# --- precedence --------------------------------------------------------------
def test_an_empty_table_falls_back_to_the_setting(db, offices, memo_type):
    """The setting is not retired. A fresh installation with no rules behaves
    exactly as it did before the table existed."""
    assert resolve_sla_due_days(offices["MED"], memo_type) == settings.DEFAULT_ACTION_DUE_DAYS


def test_an_exact_pair_wins(db, offices, memo_type):
    RoutingSLA.objects.create(office=None, document_type=memo_type, due_days=9)
    RoutingSLA.objects.create(office=offices["MED"], document_type=None, due_days=7)
    RoutingSLA.objects.create(office=offices["MED"], document_type=memo_type, due_days=2)

    assert resolve_sla_due_days(offices["MED"], memo_type) == 2


def test_an_office_house_rule_covers_a_type_it_does_not_name(db, offices, memo_type, purchase_request):
    """"MED gets 7 days for anything" should not need a row per document type."""
    RoutingSLA.objects.create(office=offices["MED"], document_type=None, due_days=7)

    assert resolve_sla_due_days(offices["MED"], purchase_request) == 7


def test_a_university_rule_covers_an_office_it_does_not_name(db, offices, memo_type):
    """"A memo is nine days everywhere" should not need a row per office."""
    RoutingSLA.objects.create(office=None, document_type=memo_type, due_days=9)

    assert resolve_sla_due_days(offices["SUP"], memo_type) == 9


def test_the_office_rule_beats_the_university_rule(db, offices, memo_type):
    """More specific wins, and specificity is office before type: a department
    that has set its own pace has said something about itself, where the
    university-wide row has only said something about the paperwork."""
    RoutingSLA.objects.create(office=None, document_type=memo_type, due_days=9)
    RoutingSLA.objects.create(office=offices["MED"], document_type=None, due_days=7)

    assert resolve_sla_due_days(offices["MED"], memo_type) == 7


def test_an_inactive_rule_falls_through_rather_than_removing_the_deadline(
    db, offices, memo_type
):
    """Unticking a rule removes the exception, not the deadline. Treating it as
    "no deadline" would silently uncouple that office from every clock."""
    RoutingSLA.objects.create(office=offices["MED"], document_type=None, due_days=7)
    RoutingSLA.objects.create(
        office=offices["MED"], document_type=memo_type, due_days=2, is_active=False
    )

    assert resolve_sla_due_days(offices["MED"], memo_type) == 7


def test_zero_days_means_no_deadline(db, offices, memo_type):
    """An office that genuinely works to no clock can say so, rather than being
    given three days it never agreed to."""
    RoutingSLA.objects.create(office=offices["MED"], document_type=memo_type, due_days=0)

    assert resolve_sla_due_days(offices["MED"], memo_type) == 0


def test_a_record_with_no_type_still_resolves(db, offices):
    """`document_type` is nullable on a record, and a draft raised without one
    must not crash the deadline."""
    RoutingSLA.objects.create(office=offices["MED"], document_type=None, due_days=7)

    assert resolve_sla_due_days(offices["MED"], None) == 7
    assert resolve_sla_due_days(None, None) == settings.DEFAULT_ACTION_DUE_DAYS


# --- one deadline for a batch that goes to several offices -------------------
def test_the_strictest_rule_wins_across_a_batch(db, offices, memo_type):
    """`route_record` writes one `due_at` for the whole batch, so when two
    recipients have different rules one number has to win. The shortest does:
    the record is late the moment the first office is late, and `record.due_at`
    is what the overdue queue and the dashboard read. The longest would let a
    slow office's rule hide a fast office's breach."""
    RoutingSLA.objects.create(office=offices["MED"], document_type=None, due_days=7)
    RoutingSLA.objects.create(office=offices["SUP"], document_type=None, due_days=2)

    assert sla_due_days_for([offices["MED"], offices["SUP"]], memo_type) == 2


def test_an_empty_batch_still_answers(db, memo_type):
    assert sla_due_days_for([], memo_type) == settings.DEFAULT_ACTION_DUE_DAYS


# --- what route_record actually writes ---------------------------------------
def _days_out(record):
    return round((record.due_at - timezone.now()).total_seconds() / 86400)


def test_routing_uses_the_receiving_office_rule(db, users, offices, memo_type):
    RoutingSLA.objects.create(office=offices["SUP"], document_type=None, due_days=10)
    record = create_draft_record(
        user=users["med"], subject="Supplies", instructions="x", document_type=memo_type,
    )

    route_record(record, [offices["SUP"]], user=users["med"])
    record.refresh_from_db()

    assert _days_out(record) == 10


def test_every_hop_is_measured_against_the_office_receiving_it(
    db, users, offices, memo_type
):
    """A document forwarded to a slow office gets that office's clock, not the
    one the fast office was working to."""
    RoutingSLA.objects.create(office=offices["SUP"], document_type=None, due_days=2)
    RoutingSLA.objects.create(office=offices["HR"], document_type=None, due_days=12)
    record = create_draft_record(
        user=users["med"], subject="Supplies", instructions="x", document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    record.refresh_from_db()
    assert _days_out(record) == 2

    route_record(record, [offices["HR"]], user=users["sup"], action="FORWARD")
    record.refresh_from_db()

    assert _days_out(record) == 12


def test_an_explicit_deadline_beats_the_table(db, users, offices, memo_type):
    RoutingSLA.objects.create(office=offices["SUP"], document_type=None, due_days=10)
    record = create_draft_record(
        user=users["med"], subject="Supplies", instructions="x", document_type=memo_type,
    )
    chosen = timezone.now() + timedelta(days=4)

    route_record(record, [offices["SUP"]], user=users["med"], due_at=chosen)
    record.refresh_from_db()

    assert record.due_at == chosen


def test_an_explicit_no_deadline_beats_the_table(db, users, offices, memo_type):
    """"No deadline" on the form reaches `route_record` as an explicit None. An
    SLA existing for that office does not overrule somebody who said, in as many
    words, that this document has no clock."""
    RoutingSLA.objects.create(office=offices["SUP"], document_type=None, due_days=10)
    record = create_draft_record(
        user=users["med"], subject="Supplies", instructions="x", document_type=memo_type,
    )

    route_record(record, [offices["SUP"]], user=users["med"], due_at=None)
    record.refresh_from_db()

    assert record.due_at is None


def test_an_explicit_due_days_still_beats_the_table(db, users, offices, memo_type):
    """The argument predates the table and callers still pass it."""
    RoutingSLA.objects.create(office=offices["SUP"], document_type=None, due_days=10)
    record = create_draft_record(
        user=users["med"], subject="Supplies", instructions="x", document_type=memo_type,
    )

    route_record(record, [offices["SUP"]], user=users["med"], due_days=1)
    record.refresh_from_db()

    assert _days_out(record) == 1


def test_a_zero_day_rule_routes_without_a_deadline(db, users, offices, memo_type):
    RoutingSLA.objects.create(office=offices["SUP"], document_type=None, due_days=0)
    record = create_draft_record(
        user=users["med"], subject="Supplies", instructions="x", document_type=memo_type,
    )

    route_record(record, [offices["SUP"]], user=users["med"])
    record.refresh_from_db()

    assert record.due_at is None
    assert record.status == Status.PENDING_RECEIPT


# --- the table cannot hold a second global default ---------------------------
def test_a_rule_scoped_to_nothing_is_refused(db):
    """Both columns blank would be a second global default sitting beside the
    setting, and the resolver would have to decide which of them wins."""
    from django.db.utils import IntegrityError

    with pytest.raises(IntegrityError):
        RoutingSLA.objects.create(office=None, document_type=None, due_days=5)


def test_the_same_scope_cannot_be_written_twice(db, offices):
    """Postgres treats NULLs as distinct, so without `nulls_distinct=False`
    "MED, any type" could exist twice and the resolver would pick between
    duplicates by primary key."""
    from django.db.utils import IntegrityError

    RoutingSLA.objects.create(office=offices["MED"], document_type=None, due_days=7)

    with pytest.raises(IntegrityError):
        RoutingSLA.objects.create(office=offices["MED"], document_type=None, due_days=4)

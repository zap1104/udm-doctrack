"""Regular users see only what was routed to, originated by, assigned to, or
explicitly shared with them. This is the rule most likely to be broken by a
careless queryset change, so it gets its own test."""

from __future__ import annotations

import pytest

from apps.tracking.models import TrackingRecord
from apps.tracking.services import confirm_receipt, create_draft_record, grant_access, route_record


@pytest.fixture
def record_med_to_sup(users, offices, memo_type):
    record = create_draft_record(
        user=users["med"], subject="Confidential supply request", instructions="For action.",
        document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    return record


@pytest.mark.django_db
def test_originating_office_sees_its_own_record(record_med_to_sup, users):
    visible = TrackingRecord.objects.visible_to(users["med"])
    assert record_med_to_sup in visible


@pytest.mark.django_db
def test_receiving_office_sees_the_record(record_med_to_sup, users):
    visible = TrackingRecord.objects.visible_to(users["sup"])
    assert record_med_to_sup in visible


@pytest.mark.django_db
def test_unrelated_office_cannot_see_it(record_med_to_sup, users):
    visible = TrackingRecord.objects.visible_to(users["hr"])
    assert record_med_to_sup not in visible
    assert record_med_to_sup.can_user_view(users["hr"]) is False


@pytest.mark.django_db
def test_explicit_grant_opens_access(record_med_to_sup, users, offices):
    grant_access(record_med_to_sup, user=users["med"], office=offices["HR"], reason="Needs to comment")
    visible = TrackingRecord.objects.visible_to(users["hr"])
    assert record_med_to_sup in visible


@pytest.mark.django_db
def test_administrator_sees_everything(record_med_to_sup, users):
    assert record_med_to_sup in TrackingRecord.objects.visible_to(users["admin"])


@pytest.mark.django_db
def test_a_draft_is_private_until_routed(users, memo_type):
    draft = create_draft_record(
        user=users["med"], subject="Unsent draft", instructions="", document_type=memo_type
    )
    assert draft in TrackingRecord.objects.visible_to(users["med"])
    assert draft not in TrackingRecord.objects.visible_to(users["sup"])


@pytest.mark.django_db
def test_only_the_holding_office_can_act(record_med_to_sup, users):
    confirm_receipt(record_med_to_sup, user=users["sup"])
    record_med_to_sup.refresh_from_db()
    assert record_med_to_sup.can_user_act(users["sup"]) is True
    assert record_med_to_sup.can_user_act(users["med"]) is False

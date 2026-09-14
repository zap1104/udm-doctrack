"""Who owes the next move on an overdue document.

The report read "MED holds an overdue document" when MED had already sent it to
SUP and HR and neither had confirmed receipt. MED cannot clear that document.
Only SUP or HR can.

`recalculate_status()` sets `current_office` to the *sending* office while a
batch is unreceived, and that is correct — the last office with confirmed
custody is the sender, and showing `to_office` there would claim a handover
nobody has acknowledged. The bug was a panel captioned "who to chase" grouping
by a field that answers "where is the paper". Two questions, two functions.

The same conflation scoped the report's office filter, which is the worse half:
an office filtering by its own name could not see the documents sitting
unreceived in its own inbox, so the whole page lied for that office.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.views import apply_report_filters
from apps.tracking.models import TrackingRecord
from apps.tracking.services import (
    DIRECTION_INCOMING,
    DIRECTION_OUTGOING,
    confirm_receipt,
    create_draft_record,
    direction_annotation,
    overdue_accountability,
    overdue_unattributed,
    route_record,
)

REPORTS = "/reports/"


@pytest.fixture
def med_to_sup_and_hr(users, offices, memo_type):
    """MED routes one document to SUP and HR. Deadline passed, nobody confirms.

    The panel is never asserted against an empty set: this record is overdue and
    unreceived for every test below.
    """
    record = create_draft_record(
        user=users["med"],
        subject="Overdue purchase request",
        instructions="For action.",
        document_type=memo_type,
    )
    route_record(record, [offices["SUP"], offices["HR"]], user=users["med"])
    record.refresh_from_db()
    record.due_at = timezone.now() - timedelta(days=3)
    record.save(update_fields=["due_at"])
    record.routing_steps.update(due_at=record.due_at)
    return record


def _rows(user):
    return {
        row["code"]: row
        for row in overdue_accountability(TrackingRecord.objects.visible_to(user))
    }


# --- 1. the regression this branch exists for --------------------------------
@pytest.mark.django_db
def test_the_sender_is_not_charged_for_a_document_nobody_has_received(
    med_to_sup_and_hr, users, offices
):
    """MED sent it. MED cannot confirm receipt of it. Only SUP and HR can.

    `current_office` is MED here — correctly, as the last office with confirmed
    custody — which is exactly why the chase-list cannot be grouped by it.
    """
    med_to_sup_and_hr.refresh_from_db()
    assert med_to_sup_and_hr.current_office == offices["MED"], "custody is unchanged"

    rows = _rows(users["admin"])

    assert rows[offices["SUP"].code]["awaiting"] == 1
    assert rows[offices["HR"].code]["awaiting"] == 1
    assert offices["MED"].code not in rows, "the office that already did its part"


# --- 2. a partial confirmation splits the debt -------------------------------
@pytest.mark.django_db
def test_a_confirmed_office_holds_while_the_other_still_owes_a_receipt(
    med_to_sup_and_hr, users, offices
):
    confirm_receipt(med_to_sup_and_hr, user=users["sup"])

    rows = _rows(users["admin"])

    assert rows[offices["SUP"].code]["holding"] == 1
    assert rows[offices["SUP"].code]["awaiting"] == 0
    assert rows[offices["HR"].code]["awaiting"] == 1
    assert offices["MED"].code not in rows


# --- 3. everyone has signed for it, and it is still late ---------------------
@pytest.mark.django_db
def test_once_everybody_has_received_it_the_holder_owes_the_work(
    med_to_sup_and_hr, users, offices
):
    confirm_receipt(med_to_sup_and_hr, user=users["sup"])
    confirm_receipt(med_to_sup_and_hr, user=users["hr"])
    med_to_sup_and_hr.refresh_from_db()

    rows = _rows(users["admin"])
    holders = {code: row for code, row in rows.items() if row["holding"]}

    assert list(holders) == [med_to_sup_and_hr.current_office.code]
    assert sum(row["awaiting"] for row in rows.values()) == 0


# --- 4. the office filter, which is the worse half ---------------------------
@pytest.mark.django_db
def test_an_office_can_see_what_is_sitting_unreceived_in_its_own_inbox(
    med_to_sup_and_hr, users, offices
):
    """Fails on main. The filter matched `originating_office | current_office`
    and both read MED for an unreceived batch, so filtering the report by SUP
    returned nothing at all."""
    visible = TrackingRecord.objects.visible_to(users["admin"])

    filtered = apply_report_filters(visible, {"office": offices["SUP"]})

    assert med_to_sup_and_hr in filtered.distinct()


@pytest.mark.django_db
def test_the_sender_still_sees_it_too(med_to_sup_and_hr, users, offices):
    """Widening the filter must not narrow it: MED raised this and still has to
    find it under MED."""
    visible = TrackingRecord.objects.visible_to(users["admin"])

    filtered = apply_report_filters(visible, {"office": offices["MED"]})

    assert med_to_sup_and_hr in filtered.distinct()


# --- 5. direction is relative ------------------------------------------------
@pytest.mark.django_db
def test_the_same_record_is_incoming_for_one_office_and_outgoing_for_the_other(
    med_to_sup_and_hr, users, offices
):
    visible = TrackingRecord.objects.visible_to(users["admin"])

    def tag(office):
        return (
            visible.annotate(_direction=direction_annotation(office))
            .values_list("_direction", flat=True)
            .get(pk=med_to_sup_and_hr.pk)
        )

    assert tag(offices["SUP"]) == DIRECTION_INCOMING
    assert tag(offices["MED"]) == DIRECTION_OUTGOING


def test_direction_needs_an_office_to_be_measured_from():
    """None rather than a default, so a caller branches once instead of every
    panel inventing a point of view it does not have."""
    assert direction_annotation(None) is None


# --- 6. a system administrator with no office --------------------------------
@pytest.mark.django_db
def test_an_admin_with_no_office_is_asked_for_one_rather_than_shown_zeroes(
    client, med_to_sup_and_hr, users
):
    """A zeroed split reads as "nothing is moving", which is a different claim
    from "there is nobody to measure this from"."""
    admin = users["admin"]
    admin.office = None
    admin.save(update_fields=["office"])
    client.force_login(admin)

    response = client.get(REPORTS)
    body = response.content.decode()

    assert response.status_code == 200
    assert response.context["scope_office"] is None
    assert "Pick an office above to split these by incoming and outgoing" in body


@pytest.mark.django_db
def test_an_admin_who_can_pick_and_has_not_is_asked_rather_than_assumed(
    client, med_to_sup_and_hr, users
):
    """The fallback is for accounts that cannot pick, whose report *is* their
    office. A system administrator based in Records, viewing every office,
    measured direction from Records and read "Passed on 30" of 40 — records that
    had never been near Records, under a label claiming Records handed them on.
    """
    admin = users["admin"]
    assert admin.office is not None, "the point of the test: they do have one"
    client.force_login(admin)

    response = client.get(REPORTS)

    assert response.context["scope_office"] is None
    assert "Pick an office above to split these by incoming and outgoing" in (
        response.content.decode()
    )


@pytest.mark.django_db
@pytest.mark.parametrize("who", ["med", "viewer"])
def test_an_account_without_the_picker_keeps_its_own_point_of_view(
    client, med_to_sup_and_hr, users, offices, who
):
    """Their report is their office, so that is what direction is measured
    from — there is nothing for them to pick."""
    client.force_login(users[who])

    response = client.get(REPORTS)

    assert response.context["scope_office"] == offices["MED"]


def test_the_longest_status_label_is_not_clipped():
    """`.pill` is uppercase at 0.06em tracking with `white-space:nowrap`, so
    "Completed - pending upload" ran about 210px into a 152px column that also
    clipped its overflow: the row read "COMPLETED - PENDIN". It wraps now rather
    than the column widening, which would take that width off the bar."""
    import pathlib

    css = pathlib.Path("static/css/doctrack.css").read_text(encoding="utf-8")

    assert ".status-row-label .pill { white-space:normal" in css
    assert ".status-row-label { min-width:0; }" in css, "no overflow:hidden"


@pytest.mark.django_db
def test_picking_an_office_gives_the_page_a_point_of_view(
    client, med_to_sup_and_hr, users, offices
):
    client.force_login(users["admin"])

    response = client.get(f"{REPORTS}?office={offices['SUP'].pk}")

    assert response.context["scope_office"] == offices["SUP"]
    assert "Pick an office above" not in response.content.decode()


# --- 7. nothing falls between the total and the panel ------------------------
@pytest.mark.django_db
@pytest.mark.parametrize("confirmations", [0, 1, 2])
def test_every_overdue_record_has_somebody_accountable(
    med_to_sup_and_hr, users, confirmations
):
    """Should be zero in every arrangement above. Asserted rather than assumed:
    a silent gap between the headline count and this panel is the thing a
    defence panel asks about."""
    for user in [users["sup"], users["hr"]][:confirmations]:
        confirm_receipt(med_to_sup_and_hr, user=user)

    assert overdue_unattributed(TrackingRecord.objects.visible_to(users["admin"])) == 0

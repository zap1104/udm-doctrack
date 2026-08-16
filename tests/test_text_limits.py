"""Ceilings on the free-text boxes.

The bug these guard against is not "somebody typed too much". It is that the
service layer used to cut a long note down to size on save and say nothing —
so a completion note written at 3000 characters was stored at 2000, with the
missing third never mentioned to the person who wrote it, in a record the rest
of the system treats as permanent.
"""

from __future__ import annotations

import pytest

from apps.tracking.forms import CompleteForm, ConfirmReceiptForm, CreateRecordForm, RemarkForm, RouteForm
from apps.tracking.models import (
    MAX_INSTRUCTIONS_CHARS,
    MAX_NOTE_CHARS,
    MAX_REMARK_CHARS,
    RecordActivity,
)
from apps.tracking.services import add_remark, confirm_receipt, create_draft_record, route_record


# ---------------------------------------------------------------------------
# The boxes announce their ceiling
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "form,field,limit",
    [
        (RemarkForm(), "remark", MAX_REMARK_CHARS),
        (ConfirmReceiptForm(), "note", MAX_NOTE_CHARS),
        (CompleteForm(), "note", MAX_NOTE_CHARS),
    ],
)
def test_a_textarea_carries_its_maxlength(form, field, limit):
    """Without the attribute the browser lets the typing run on and the server
    is the first thing to object, after the text is written."""
    assert form.fields[field].max_length == limit
    assert str(limit) in form[field].as_widget()


@pytest.mark.django_db
def test_the_instructions_box_is_capped_even_though_the_column_is_not(users):
    """It comes from a model TextField, which has no max_length of its own, so
    the cap has to be applied by hand or it silently is not there."""
    form = CreateRecordForm(user=users["med"])
    assert form.fields["instructions"].max_length == MAX_INSTRUCTIONS_CHARS
    assert f'maxlength="{MAX_INSTRUCTIONS_CHARS}"' in form["instructions"].as_widget()


# ---------------------------------------------------------------------------
# Too much text is refused, not quietly trimmed
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_an_over_long_remark_is_rejected_with_a_message(users, offices):
    form = RemarkForm(data={"remark": "x" * (MAX_REMARK_CHARS + 1)})

    assert not form.is_valid()
    assert "remark" in form.errors


@pytest.mark.django_db
def test_an_over_long_completion_note_is_rejected(users):
    form = CompleteForm(data={"note": "x" * (MAX_NOTE_CHARS + 1)})

    assert not form.is_valid()
    assert "note" in form.errors


@pytest.mark.django_db
def test_a_note_exactly_on_the_limit_is_accepted(users):
    """An off-by-one here would reject the longest legitimate note."""
    form = CompleteForm(data={"note": "x" * MAX_NOTE_CHARS})

    assert form.is_valid(), form.errors


@pytest.mark.django_db
def test_an_over_long_route_instruction_is_rejected(users, offices, memo_type):
    record = create_draft_record(
        user=users["med"], subject="Limits probe", instructions="For action."
    )
    form = RouteForm(
        data={
            "action": "FORWARD",
            "offices": [offices["SUP"].pk],
            "instructions": "x" * (MAX_INSTRUCTIONS_CHARS + 1),
            "deadline_choice": "none",
        },
        record=record,
        user=users["med"],
    )

    assert not form.is_valid()
    assert "instructions" in form.errors


# ---------------------------------------------------------------------------
# The service layer keeps a backstop for callers that are not forms
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_the_service_caps_a_remark_from_a_non_form_caller(users, offices):
    """Seed data and the self check call this directly. The detail column is a
    TextField, so without the cap a remark had no ceiling at all."""
    record = create_draft_record(
        user=users["med"], subject="Backstop probe", instructions="For action."
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])

    add_remark(record, user=users["sup"], remark="y" * (MAX_REMARK_CHARS + 500))

    stored = record.activities.filter(event=RecordActivity.Event.REMARK).latest("id")
    assert len(stored.detail) == MAX_REMARK_CHARS


@pytest.mark.django_db
def test_a_receipt_note_within_the_limit_is_stored_whole(users, offices):
    """The point of matching the form ceiling to the service one: text that the
    form accepted must survive the save intact."""
    record = create_draft_record(
        user=users["med"], subject="Round trip probe", instructions="For action."
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    note = "z" * MAX_NOTE_CHARS

    step = confirm_receipt(record, user=users["sup"], note=note)

    assert step.receipt_note == note

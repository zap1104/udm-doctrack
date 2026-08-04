"""The tracking number is the promise the whole system makes: unique, readable,
and never reused. These tests hold that promise honest."""

from __future__ import annotations

import pytest
from django.utils import timezone

from apps.tracking.services import create_draft_record, generate_tracking_number


@pytest.mark.django_db
def test_number_follows_the_documented_format(offices):
    number = generate_tracking_number(offices["MED"])
    parts = number.split("-")

    assert parts[0] == "UDM"
    assert parts[1] == "OVPA"
    assert parts[2] == "MED"
    assert parts[3] == str(timezone.localtime().year)
    assert parts[4] == f"{timezone.localtime().month:02d}"
    assert parts[5].isdigit() and len(parts[5]) == 4


@pytest.mark.django_db
def test_sequence_increments_per_office(offices):
    first = generate_tracking_number(offices["MED"])
    second = generate_tracking_number(offices["MED"])
    other_office = generate_tracking_number(offices["SUP"])

    assert int(first.split("-")[-1]) + 1 == int(second.split("-")[-1])
    # A different office starts its own count rather than sharing one pool.
    assert other_office.split("-")[-1] == "0001"


@pytest.mark.django_db
def test_numbers_are_unique_across_many_records(users, memo_type):
    numbers = {
        create_draft_record(
            user=users["med"], subject=f"Test document {index}", instructions="For action.",
            document_type=memo_type,
        ).tracking_number
        for index in range(25)
    }
    assert len(numbers) == 25

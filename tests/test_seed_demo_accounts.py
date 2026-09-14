"""The demo seed has to be able to show what each role may do.

Four roles exist — SYSTEM_ADMIN, ADMIN, USER, VIEWER — and three of them are
defined by a boundary rather than a capability: an office administrator reaches
one office, a viewer reads and prints, a user cannot administer anybody. A seed
carrying one of each proves the roles exist and cannot demonstrate a single one
of those boundaries, because with one ADMIN there is nobody for them to fail to
reach.

`seed_demo` is run per test, inside the transaction pytest-django rolls back.
A module-scoped fixture that seeded once through `django_db_blocker.unblock()`
was faster and wrong: it writes outside that transaction, so the offices, users
and records stayed in the shared test database and 159 tests in other files
failed behind it. `--records 0` skips the generated year of traffic, which is
what made the per-test cost affordable — the eight sample records and the
archive are still seeded, and they are what these assertions read.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from apps.documents.models import Document
from apps.tracking.models import TrackingRecord

User = get_user_model()


@pytest.fixture
def seeded(db):
    call_command("seed_demo", records=0, verbosity=0)


def test_every_role_is_represented(seeded):
    seeded_roles = set(User.objects.values_list("role", flat=True))

    assert seeded_roles == {"SYSTEM_ADMIN", "ADMIN", "USER", "VIEWER"}


def test_office_administrators_span_more_than_one_office(seeded):
    """The point of splitting ADMIN from SYSTEM_ADMIN is that an office head
    administers *their* office. With a single ADMIN account there is nobody for
    them to fail to reach, so the boundary is invisible: open Administration and
    every account on screen is theirs, which looks the same as no scoping."""
    offices = set(
        User.objects.filter(role="ADMIN").values_list("office__code", flat=True)
    )

    assert len(offices) >= 2, offices


def test_viewers_span_more_than_one_office(seeded):
    """Read-only is office-scoped too, and one account cannot show that."""
    offices = set(
        User.objects.filter(role="VIEWER").values_list("office__code", flat=True)
    )

    assert len(offices) >= 2, offices


def test_an_office_has_an_administrator_a_user_and_a_viewer(seeded):
    """Several pages decide what to *offer* by role, not only what to permit —
    Reports gives the office picker to administrators alone and sends everybody
    else to their own office. Showing that needs all three in one office, or the
    comparison is confounded by them being in different ones."""
    med = User.objects.filter(office__code="MED")

    assert med.filter(role="ADMIN").exists()
    assert med.filter(role="USER").exists()
    assert med.filter(role="VIEWER").exists()


def test_only_the_system_administrator_reaches_the_django_admin_site(seeded):
    """An office administrator administers this application. /admin/ is a
    different power and the seed keeps them apart."""
    staff = set(User.objects.filter(is_superuser=True).values_list("role", flat=True))

    assert staff == {"SYSTEM_ADMIN"}
    assert not User.objects.filter(role="ADMIN", is_staff=True).exists()


def test_there_is_a_suspended_account_to_reactivate(seeded):
    """So the Administration screen has a real reactivate case rather than a
    row nobody can act on."""
    assert User.objects.filter(is_active=False).exists()


def test_nothing_was_authored_by_an_account_that_could_not_have_done_it(seeded):
    """A viewer has no create button and a suspended account cannot sign in, so
    seeding either as an author puts a row in the demo that the demo itself says
    is impossible.

    It was two loops with two different rules — one skipped viewers, one took
    whoever came first — so whether this held came down to the order of USERS.
    """
    assert not TrackingRecord.objects.filter(created_by__role="VIEWER").exists()
    assert not TrackingRecord.objects.filter(created_by__is_active=False).exists()
    assert not Document.objects.filter(uploaded_by__role="VIEWER").exists()
    assert not Document.objects.filter(uploaded_by__is_active=False).exists()


def test_every_office_that_holds_records_has_somebody_who_could_raise_them(seeded):
    """A seeded office with only viewers would be a demo dead end: documents
    arrive there and nobody on screen can act on them."""
    holders = set(
        TrackingRecord.objects.exclude(current_office__isnull=True)
        .values_list("current_office__code", flat=True)
    )
    able = set(
        User.objects.filter(is_active=True)
        .exclude(role="VIEWER")
        .exclude(office__isnull=True)
        .values_list("office__code", flat=True)
    )

    assert holders <= able, holders - able

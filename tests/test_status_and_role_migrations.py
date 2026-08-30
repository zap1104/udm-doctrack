"""The two data migrations, exercised against real rows.

These are the parts of this change that cannot be corrected by editing code
afterwards: they rewrite live data once, and a mistake in either is a silent
wrong answer in every screen built on top of it. The status one decides whether
a finished-but-unfiled record claims to be in a repository it never reached; the
role one decides whether the only accounts that can administer the system still
can.

They are driven directly rather than through a migration runner: the part worth
testing is the rewriting logic, and calling `forwards()` with a registry that
serves the current models exercises exactly that. Module names begin with a
digit, so they are loaded by `import_module` rather than a plain import.
"""

from __future__ import annotations

from importlib import import_module

import pytest

role_migration = import_module("apps.accounts.migrations.0005_split_admin_from_system_admin")
status_migration = import_module(
    "apps.tracking.migrations.0004_collapse_statuses_and_pending_upload"
)


class FakeApps:
    """Serves the real models under the historical-model interface."""

    def __init__(self, mapping):
        self._mapping = mapping

    def get_model(self, app_label, model_name):
        return self._mapping[(app_label.lower(), model_name.lower())]


@pytest.fixture
def apps_registry():
    from apps.accounts.models import User
    from apps.documents.models import Document
    from apps.tracking.models import TrackingRecord

    return FakeApps(
        {
            ("tracking", "trackingrecord"): TrackingRecord,
            ("documents", "document"): Document,
            ("accounts", "user"): User,
        }
    )


# --- 1.1 / 1.2 -------------------------------------------------------------
@pytest.mark.django_db
def test_forwarded_and_returned_rows_become_pending_receipt(apps_registry, users, offices):
    from apps.tracking.models import Status, TrackingRecord

    for index, legacy in enumerate(("FORWARDED", "RETURNED")):
        TrackingRecord.objects.create(
            tracking_number=f"LEGACY-{index}", subject="Old row", instructions="x",
            originating_office=offices["MED"], created_by=users["med"], status=legacy,
        )

    status_migration.forwards(apps_registry, None)

    assert TrackingRecord.objects.filter(status__in=("FORWARDED", "RETURNED")).count() == 0
    assert TrackingRecord.objects.filter(status=Status.PENDING_RECEIPT).count() == 2


@pytest.mark.django_db
def test_a_completed_record_with_no_document_becomes_pending_upload(
    apps_registry, users, offices
):
    """The case the migration exists for: finished, never filed."""
    from apps.tracking.models import Status, TrackingRecord

    record = TrackingRecord.objects.create(
        tracking_number="LEGACY-UNFILED", subject="Finished but never filed", instructions="x",
        originating_office=offices["MED"], created_by=users["med"], status="COMPLETED",
    )

    status_migration.forwards(apps_registry, None)

    record.refresh_from_db()
    assert record.status == Status.COMPLETED_PENDING_UPLOAD


@pytest.mark.django_db
def test_a_completed_record_that_was_filed_stays_completed(apps_registry, users, offices):
    """It really is in the repository, so it must not be dragged back."""
    from apps.documents.models import Document
    from apps.tracking.models import Status, TrackingRecord

    record = TrackingRecord.objects.create(
        tracking_number="LEGACY-FILED", subject="Finished and filed", instructions="x",
        originating_office=offices["MED"], created_by=users["med"], status="COMPLETED",
        is_archived=True,
    )
    Document.objects.create(
        title="Finished and filed", office=offices["MED"], year=2026,
        uploaded_by=users["med"], tracking_record=record,
    )

    status_migration.forwards(apps_registry, None)

    record.refresh_from_db()
    assert record.status == Status.COMPLETED


@pytest.mark.django_db
def test_the_status_migration_reverses_the_part_that_can_be_reversed(
    apps_registry, users, offices
):
    from apps.tracking.models import TrackingRecord

    record = TrackingRecord.objects.create(
        tracking_number="LEGACY-REVERSE", subject="Round trip", instructions="x",
        originating_office=offices["MED"], created_by=users["med"], status="COMPLETED",
    )
    status_migration.forwards(apps_registry, None)
    status_migration.backwards(apps_registry, None)

    record.refresh_from_db()
    assert record.status == "COMPLETED"


# --- 2.1 -------------------------------------------------------------------
@pytest.mark.django_db
def test_existing_admins_keep_their_global_reach(apps_registry, offices):
    """Silently demoting the only accounts that can fix it is not a migration
    anybody notices until they cannot fix it."""
    from apps.accounts.models import User

    User.objects.create_user(
        username="legacy-admin", password="x", office=offices["REC"], role="ADMIN"
    )

    role_migration.forwards(apps_registry, None)

    promoted = User.objects.get(username="legacy-admin")
    assert promoted.role == User.Role.SYSTEM_ADMIN
    assert promoted.is_system_admin is True


@pytest.mark.django_db
def test_secretaries_become_office_administrators(apps_registry, offices):
    from apps.accounts.models import User

    User.objects.create_user(
        username="legacy-secretary", password="x", office=offices["REC"], role="SECRETARY"
    )

    role_migration.forwards(apps_registry, None)

    mapped = User.objects.get(username="legacy-secretary")
    assert mapped.role == User.Role.ADMIN
    assert mapped.is_office_admin is True
    assert mapped.is_system_admin is False


@pytest.mark.django_db
def test_a_secretary_is_not_swept_up_into_system_admin(apps_registry, offices):
    """Order matters: run SECRETARY -> ADMIN first and the new office
    administrators are caught by the ADMIN -> SYSTEM_ADMIN rule behind them."""
    from apps.accounts.models import User

    User.objects.create_user(
        username="s1", password="x", office=offices["REC"], role="SECRETARY"
    )
    User.objects.create_user(
        username="a1", password="x", office=offices["REC"], role="ADMIN"
    )

    role_migration.forwards(apps_registry, None)

    assert User.objects.get(username="s1").role == User.Role.ADMIN
    assert User.objects.get(username="a1").role == User.Role.SYSTEM_ADMIN


@pytest.mark.django_db
def test_ordinary_users_are_left_alone(apps_registry, offices):
    from apps.accounts.models import User

    User.objects.create_user(username="u1", password="x", office=offices["MED"], role="USER")

    role_migration.forwards(apps_registry, None)

    assert User.objects.get(username="u1").role == User.Role.USER

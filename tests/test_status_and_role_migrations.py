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
def test_secretaries_become_ordinary_users_not_administrators(apps_registry, offices):
    """A migration must never be the thing that widens a permission.

    SECRETARY sounds like an administrator and was not one: every
    user-administration screen was gated on ADMIN alone, so mapping it to the
    new ADMIN would have handed every secretary the ability to create accounts,
    reset passwords and suspend colleagues — granted silently, by a data
    migration nobody would think to audit.
    """
    from apps.accounts.models import User

    User.objects.create_user(
        username="legacy-secretary", password="x", office=offices["REC"], role="SECRETARY"
    )

    role_migration.forwards(apps_registry, None)

    mapped = User.objects.get(username="legacy-secretary")
    assert mapped.role == User.Role.USER
    assert mapped.is_office_admin is False
    assert mapped.is_system_admin is False


@pytest.mark.django_db
def test_a_secretary_keeps_every_power_it_actually_had(apps_registry, offices, users, memo_type):
    """Nothing is lost by the demotion, because the powers came from
    `is_records_staff`, which USER now satisfies. All four call sites:
    acting on the office's drafts, sharing a record, editing the office's
    repository entries, and seeing the office columns on reports."""
    from apps.accounts.models import User
    from apps.documents.models import AccessLevel, Document
    from apps.tracking.services import create_draft_record

    secretary = User.objects.create_user(
        username="legacy-secretary", password="x", office=offices["MED"], role="SECRETARY"
    )
    role_migration.forwards(apps_registry, None)
    secretary.refresh_from_db()

    assert secretary.is_records_staff is True

    # 1. may act on a colleague's draft raised by their own office
    draft = create_draft_record(
        user=users["med"], subject="Somebody else's draft", instructions="For action.",
        document_type=memo_type,
    )
    assert draft.can_user_act(secretary) is True

    # 2. may edit their own office's repository entries
    document = Document.objects.create(
        title="MED filing", office=offices["MED"], year=2026,
        uploaded_by=users["med"], access_level=AccessLevel.OFFICE,
    )
    assert document.can_user_edit(secretary) is True


@pytest.mark.django_db
def test_a_migrated_secretary_can_still_forward_and_share(
    apps_registry, offices, users, memo_type, client
):
    from apps.accounts.models import User
    from apps.tracking.models import RoutingStep, Status
    from apps.tracking.services import confirm_receipt, create_draft_record, route_record

    secretary = User.objects.create_user(
        username="legacy-secretary", password="TestPass123!", office=offices["SUP"],
        role="SECRETARY",
    )
    role_migration.forwards(apps_registry, None)
    secretary.refresh_from_db()

    record = create_draft_record(
        user=users["med"], subject="Needs forwarding", instructions="For action.",
        document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=secretary)
    record.refresh_from_db()

    # 3. may forward it onward
    route_record(record, [offices["HR"]], user=secretary, action=RoutingStep.Action.FORWARD)
    record.refresh_from_db()
    assert record.status == Status.PENDING_RECEIPT

    # 4. and may share it — the endpoint gated on is_records_staff
    client.force_login(secretary)
    response = client.post(
        f"/tracking/{record.pk}/share/", {"office": offices["HR"].pk, "reason": "fyi"}
    )
    assert response.status_code == 302
    assert record.grants.filter(office=offices["HR"]).exists()


@pytest.mark.django_db
def test_a_migrated_secretary_still_sees_the_office_columns(apps_registry, offices, client):
    """The fourth call site: the dashboard shows the destination columns to
    records staff and hides them from everyone else."""
    from apps.accounts.models import User

    User.objects.create_user(
        username="legacy-secretary", password="TestPass123!", office=offices["REC"],
        role="SECRETARY",
    )
    role_migration.forwards(apps_registry, None)
    secretary = User.objects.get(username="legacy-secretary")

    client.force_login(secretary)
    response = client.get("/")

    assert response.status_code == 200
    assert response.context["show_office_columns"] is True


@pytest.mark.django_db
def test_a_secretary_gains_no_administration_access(apps_registry, offices, client):
    """The whole reason for mapping to USER rather than ADMIN."""
    from apps.accounts.models import User

    User.objects.create_user(
        username="legacy-secretary", password="TestPass123!", office=offices["REC"],
        role="SECRETARY",
    )
    role_migration.forwards(apps_registry, None)

    client.force_login(User.objects.get(username="legacy-secretary"))
    assert client.get("/accounts/users/").status_code == 403
    assert client.get("/administration/").status_code == 403


@pytest.mark.django_db
def test_nobody_is_left_on_a_retired_role(apps_registry, offices):
    from apps.accounts.models import User

    for index, role in enumerate(("ADMIN", "SECRETARY", "USER")):
        User.objects.create_user(
            username=f"legacy-{index}", password="x", office=offices["REC"], role=role
        )

    role_migration.forwards(apps_registry, None)

    assert not User.objects.filter(role__in=("SECRETARY",)).exists()
    assert set(User.objects.values_list("role", flat=True)) <= {
        "SYSTEM_ADMIN", "ADMIN", "USER", "VIEWER",
    }


@pytest.mark.django_db
def test_ordinary_users_are_left_alone(apps_registry, offices):
    from apps.accounts.models import User

    User.objects.create_user(username="u1", password="x", office=offices["MED"], role="USER")

    role_migration.forwards(apps_registry, None)

    assert User.objects.get(username="u1").role == User.Role.USER

"""Responsibility follows confirmed custody, while directory ownership stays fixed."""

from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.accounts.models import Office, User
from apps.core.models import AuditLog, Notification
from apps.core.tasks import chase_external_followups
from apps.core.utils import log_action
from apps.documents.models import Collection, Document
from apps.tracking import external_services as services
from apps.tracking.models import ExternalOffice, ExternalTrackingRecord, Status

pytestmark = pytest.mark.django_db


def act(function, record, user, **kwargs):
    return function(record, user=user, expected_revision=record.revision, **kwargs)


@pytest.fixture
def transferred(users, offices):
    external = services.create_external_office(user=users["med_admin"], name="Registrar")
    record = services.create_record(user=users["med_admin"], title="Transferred paper")
    act(services.route_record, record, users["med_admin"], to_office=offices["SUP"])
    act(services.confirm_receipt, record, users["sup"])
    return record, external


def test_responsibility_changes_only_on_internal_receipt_and_survives_external_loops(users, offices):
    external = services.create_external_office(user=users["med_admin"], name="Registrar")
    other = services.create_external_office(user=users["sup_admin"], name="Dean")
    record = services.create_record(user=users["med_admin"], title="Receipt boundary", from_external_office=external)
    assert record.responsible_office_id == offices["MED"].pk
    act(services.route_record, record, users["med_admin"], to_office=offices["SUP"])
    assert record.responsible_office_id == offices["MED"].pk
    assert not record.can_act(users["sup_admin"])
    act(services.confirm_receipt, record, users["sup"])
    assert record.responsible_office == offices["SUP"]
    assert not record.can_manage(users["med_admin"])
    assert record.can_manage(users["sup_admin"])
    act(services.route_record, record, users["sup"], to_external_office=external)
    assert record.responsible_office_id == offices["SUP"].pk
    act(services.record_external_receipt, record, users["sup_admin"])
    act(services.route_record, record, users["sup_admin"], to_external_office=other)
    act(services.record_external_receipt, record, users["sup_admin"])
    assert record.responsible_office_id == offices["SUP"].pk
    act(services.route_record, record, users["sup_admin"], to_office=offices["MED"])
    assert record.responsible_office_id == offices["SUP"].pk
    act(services.confirm_receipt, record, users["med"])
    assert record.responsible_office_id == offices["MED"].pk
    assert record.tracking_number.startswith("UDM-EXT-MED-")
    assert record.managing_office_id == offices["MED"].pk


def test_previous_admin_loses_manual_receipt_and_current_admin_gains_it(transferred, users):
    record, external = transferred
    act(services.route_record, record, users["sup"], to_external_office=external)
    with pytest.raises(PermissionDenied):
        act(services.record_external_receipt, record, users["med_admin"])
    act(services.record_external_receipt, record, users["sup_admin"])
    assert record.current_step.receipt_recorded_by_id == users["sup_admin"].pk
    assert record.current_step.received_by_id is None


@pytest.mark.parametrize("operation", ["work", "remark", "attachment", "route", "complete", "correct", "annotate"])
def test_current_responsible_admin_controls_manual_external_actions(transferred, users, offices, settings, tmp_path, operation):
    record, external = transferred
    settings.MEDIA_ROOT = tmp_path
    act(services.route_record, record, users["sup"], to_external_office=external)
    # Establish the received fixture directly so each action isolates its authorization.
    step = record.current_step
    step.received_at = timezone.now()
    step.save(update_fields=["received_at"])
    record.current_office = None
    record.current_external_office = external
    record.status = Status.RECEIVED
    record.save()
    operations = {
        "work": (services.record_external_work, {"message": "Signed"}),
        "remark": (services.add_remark, {"message": "Paper checked"}),
        "attachment": (services.add_attachment, {"uploaded_file": SimpleUploadedFile("paper.pdf", b"%PDF-1.4")}),
        "route": (services.route_record, {"to_office": offices["HR"]}),
        "complete": (services.complete_record, {}),
        "correct": (services.correct_step, {"step": step, "reason": "Paper note", "changes": {"receipt_note": "Correct note"}}),
        "annotate": (services.annotate_record, {"message": "Clarification"}),
    }
    function, kwargs = operations[operation]
    with pytest.raises(PermissionDenied):
        act(function, record, users["med_admin"], **kwargs)
    before = record.revision
    act(function, record, users["sup_admin"], **kwargs)
    assert record.revision == before + 1


def test_current_admin_reopens_completed_internal_custody(transferred, users):
    record, _ = transferred
    act(services.complete_record, record, users["sup"])
    with pytest.raises(PermissionDenied):
        act(services.reopen_record, record, users["med_admin"])
    act(services.reopen_record, record, users["sup_admin"])
    assert record.status == Status.RECEIVED


def test_receipt_wait_still_blocks_external_work_and_endorsement(transferred, users, offices):
    record, external = transferred
    act(services.route_record, record, users["sup"], to_external_office=external)
    with pytest.raises(ValidationError):
        act(services.record_external_work, record, users["sup_admin"], message="Too early")
    with pytest.raises(PermissionDenied):
        act(services.route_record, record, users["sup_admin"], to_office=offices["HR"])


def test_system_admin_can_dispatch_chosen_office_draft_but_cannot_impersonate_receiver(users, offices):
    record = services.create_record(user=users["admin"], title="Selected office", managing_office=offices["MED"])
    act(services.route_record, record, users["admin"], to_office=offices["SUP"])
    with pytest.raises(PermissionDenied):
        act(services.confirm_receipt, record, users["admin"])
    act(services.confirm_receipt, record, users["sup"])
    assert record.current_step.received_by_id == users["sup"].pk


@pytest.mark.parametrize("role", ["med", "sup", "sup_admin", "viewer", "admin"])
def test_directory_is_shared_but_editing_stays_with_original_admin(users, role):
    external = services.create_external_office(user=users["med_admin"], name="Registrar")
    assert ExternalOffice.objects.visible_to(users[role]).filter(pk=external.pk).exists()
    assert external.can_edit(users[role]) == (role == "admin")
    assert external.can_edit(users["med_admin"])
    with pytest.raises(PermissionDenied):
        services.save_external_office(external, user=users["sup_admin"], expected_revision=1, name="Renamed")


def test_inactive_directory_entries_are_not_selectable_and_stale_endpoints_are_rejected(users):
    external = services.create_external_office(user=users["sup_admin"], name="Closed office")
    ExternalOffice.objects.filter(pk=external.pk).update(is_active=False)
    assert ExternalOffice.objects.visible_to(users["med_admin"]).filter(pk=external.pk).exists()
    with pytest.raises(ValidationError):
        services.create_record(user=users["med_admin"], title="Closed source", from_external_office=external)


@pytest.mark.parametrize("operation", ["record", "directory"])
@pytest.mark.parametrize("invalid", ["inactive", "missing"])
def test_creation_reloads_and_requires_an_active_recording_office(users, offices, operation, invalid):
    office = offices["MED"]
    if invalid == "inactive":
        Office.objects.filter(pk=office.pk).update(is_active=False)
    else:
        office = Office(pk=987654321, code="MISSING", name="Missing office")
    function = services.create_record if operation == "record" else services.create_external_office
    kwargs = {"title": "Paper"} if operation == "record" else {"name": "Registrar"}
    with pytest.raises(ValidationError):
        function(user=users["admin"], managing_office=office, **kwargs)


@pytest.mark.parametrize("change", [{"role": "VIEWER"}, {"is_active": False}, {"office_id": None}])
def test_creation_reloads_actor_permissions(users, change):
    User.objects.filter(pk=users["med_admin"].pk).update(**change)
    with pytest.raises(PermissionDenied):
        services.create_record(user=users["med_admin"], title="Stale account")


def test_mutation_reloads_actor_office_and_record_revision(transferred, users, offices):
    record, _ = transferred
    stale = ExternalTrackingRecord.objects.get(pk=record.pk)
    User.objects.filter(pk=users["sup_admin"].pk).update(office=offices["HR"])
    with pytest.raises(PermissionDenied):
        act(services.annotate_record, record, users["sup_admin"], message="Old office")
    act(services.annotate_record, record, users["admin"], message="Current revision")
    with pytest.raises(ValidationError):
        act(services.annotate_record, stale, users["admin"], message="Stale revision")


def test_existing_filed_document_office_overrides_custody_without_cached_authority(transferred, users, offices):
    record, _ = transferred
    record.status = Status.COMPLETED
    record.save(update_fields=["status"])
    assert record.responsible_office_id == offices["SUP"].pk
    document = Document.objects.create(
        title="Legacy filing", office=offices["MED"], year=2026, collection=Collection.EXTERNAL,
        external_tracking_record=record, uploaded_by=users["med_admin"],
    )
    assert record.responsible_office_id == offices["MED"].pk
    assert record.can_manage(users["med_admin"])
    assert not record.can_manage(users["sup_admin"])
    act(services.annotate_record, record, users["med_admin"], message="Legacy owner annotation")
    document.refresh_from_db()
    assert document.office_id == offices["MED"].pk


def test_new_audit_snapshots_follow_responsibility_without_rewriting_old_entries(transferred, users, offices):
    record, _ = transferred
    original = AuditLog.objects.filter(target_type="ExternalTrackingRecord", target_id=str(record.pk)).order_by("pk").first()
    assert original.extra["audit_target_office_id"] == offices["MED"].pk
    latest = log_action("UPDATE", "Current paper", actor=users["admin"], target=record)
    assert latest.extra["audit_target_office_id"] == offices["SUP"].pk
    original.refresh_from_db()
    assert original.extra["audit_target_office_id"] == offices["MED"].pk


def test_completion_and_followups_use_responsible_office(transferred, users, offices, monkeypatch):
    record, external = transferred
    act(services.route_record, record, users["sup"], to_external_office=external, due_at=timezone.now() + timedelta(days=1))
    later = timezone.now() + timedelta(days=20)
    monkeypatch.setattr(timezone, "now", lambda: later)
    assert chase_external_followups() == {"unreceived": 1, "overdue": 1}
    notices = Notification.objects.filter(external_record=record, resolved_at__isnull=True)
    assert set(notices.values_list("office_id", flat=True)) == {offices["SUP"].pk}
    assert chase_external_followups() == {"unreceived": 0, "overdue": 0}
    act(services.record_external_receipt, record, users["sup_admin"])
    act(services.complete_record, record, users["sup_admin"])
    notice = Notification.objects.get(external_record=record, kind="COMPLETED", resolved_at__isnull=True)
    assert notice.office_id == offices["SUP"].pk

"""A recipient's stage follows its own receipt and subsequent work."""

import importlib

import pytest
from django.apps import apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection

from apps.tracking import services
from apps.tracking.models import RecordActivity, RoutingStep, Status, TrackingRecord

pytestmark = pytest.mark.django_db


@pytest.fixture
def parallel(users, offices, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_BACKGROUND_TASKS = False
    record = services.create_draft_record(user=users["sup"], subject="Parallel review", instructions="Review")
    services.route_record(record, [offices["HR"], offices["MED"]], user=users["sup"])
    return record


def stage(record, office=None):
    return TrackingRecord.objects.with_stage_for(office).get(pk=record.pk).display_status


def test_partial_receipt_and_work_do_not_advance_other_recipient(parallel, users, offices):
    services.confirm_receipt(parallel, user=users["hr"])
    assert parallel.status == Status.PENDING_RECEIPT
    assert stage(parallel, offices["HR"]) == Status.RECEIVED
    assert stage(parallel, offices["MED"]) == Status.PENDING_RECEIPT
    services.add_remark(parallel, user=users["hr"], remark="Reviewed the draft")
    assert stage(parallel, offices["HR"]) == Status.IN_PROCESS
    assert stage(parallel, offices["MED"]) == Status.PENDING_RECEIPT
    assert stage(parallel, offices["SUP"]) == Status.PENDING_RECEIPT
    assert stage(parallel) == Status.PENDING_RECEIPT
    services.confirm_receipt(parallel, user=users["med"], note="Received at office")
    assert stage(parallel, offices["MED"]) == Status.RECEIVED
    assert stage(parallel, offices["HR"]) == Status.IN_PROCESS
    assert stage(parallel) == Status.IN_PROCESS


def test_each_recipient_can_independently_start_work(parallel, users, offices):
    services.confirm_receipt(parallel, user=users["hr"])
    services.confirm_receipt(parallel, user=users["med"])
    services.mark_in_process(parallel, user=users["hr"])
    assert stage(parallel, offices["MED"]) == Status.RECEIVED
    services.mark_in_process(parallel, user=users["med"])
    assert stage(parallel, offices["MED"]) == Status.IN_PROCESS
    assert parallel.activities.filter(event=RecordActivity.Event.REMARK, actor_office=offices["MED"]).exists()


def test_attachment_alone_starts_work_after_receipt(parallel, users, offices):
    services.confirm_receipt(parallel, user=users["hr"])
    services.confirm_receipt(parallel, user=users["med"])
    services.attach_files(parallel, [SimpleUploadedFile("note.txt", b"Office update")], user=users["med"])
    assert parallel.status == Status.IN_PROCESS
    assert stage(parallel, offices["MED"]) == Status.IN_PROCESS
    assert stage(parallel, offices["HR"]) == Status.RECEIVED


def test_activity_before_receipt_does_not_become_processing(parallel, users, offices):
    services.add_remark(parallel, user=users["admin"], remark="Sender clarification before delivery")
    services.attach_files(parallel, [SimpleUploadedFile("note.txt", b"Original attachment")], user=users["admin"])
    services.confirm_receipt(parallel, user=users["hr"])
    services.confirm_receipt(parallel, user=users["med"])
    assert parallel.status == Status.RECEIVED
    assert stage(parallel, offices["HR"]) == stage(parallel, offices["MED"]) == Status.RECEIVED


def test_administrator_work_preserves_actor_snapshot_and_marks_actual_custody(parallel, users, offices):
    services.confirm_receipt(parallel, user=users["hr"])
    activity = services.add_remark(parallel, user=users["admin"], remark="Work for the holding office")
    assert activity.actor_office == offices["REC"]
    assert activity.work_office == offices["HR"]
    assert stage(parallel, offices["HR"]) == Status.IN_PROCESS
    assert stage(parallel, offices["MED"]) == Status.PENDING_RECEIPT


def test_reendorsement_resets_stages_and_ignores_old_work(parallel, users, offices):
    services.confirm_receipt(parallel, user=users["hr"])
    services.add_remark(parallel, user=users["hr"], remark="Ready to endorse")
    services.route_record(parallel, [offices["MED"]], user=users["hr"], action=RoutingStep.Action.FORWARD)
    assert stage(parallel, offices["MED"]) == Status.PENDING_RECEIPT
    services.confirm_receipt(parallel, user=users["med"])
    assert stage(parallel, offices["MED"]) == Status.RECEIVED
    assert parallel.status == Status.RECEIVED


def test_reopening_is_not_processing_work_and_completion_stays_global(parallel, users, offices):
    services.confirm_receipt(parallel, user=users["hr"])
    services.complete_record(parallel, user=users["hr"])
    assert stage(parallel, offices["MED"]) == Status.COMPLETED_PENDING_UPLOAD
    services.reopen_record(parallel, user=users["admin"], reason="Recheck receipt")
    parallel.recalculate_status()
    assert stage(parallel, offices["HR"]) == Status.RECEIVED
    assert stage(parallel, offices["MED"]) == Status.PENDING_RECEIPT


def test_view_and_print_never_start_work(parallel, users, offices):
    services.confirm_receipt(parallel, user=users["hr"])
    for event in (RecordActivity.Event.VIEWED, RecordActivity.Event.PRINTED):
        services.add_activity(parallel, event, "Read only", actor=users["hr"])
    assert stage(parallel, offices["HR"]) == Status.RECEIVED


def test_stage_filters_match_same_local_stage(parallel, users, offices):
    services.confirm_receipt(parallel, user=users["hr"])
    services.add_remark(parallel, user=users["hr"], remark="Work started")
    for office, expected in [(offices["HR"], Status.IN_PROCESS), (offices["MED"], Status.PENDING_RECEIPT)]:
        records = TrackingRecord.objects.with_stage_for(office).filter(pk=parallel.pk)
        for candidate in (Status.PENDING_RECEIPT, Status.RECEIVED, Status.IN_PROCESS):
            assert services.filter_records(records, status=candidate).exists() == (candidate == expected)


def test_reconciliation_is_preview_first_and_idempotent(parallel, users):
    services.confirm_receipt(parallel, user=users["hr"])
    TrackingRecord.objects.filter(pk=parallel.pk).update(status=Status.IN_PROCESS)
    events = parallel.activities.count()
    call_command("reconcile_tracking_stages", record=parallel.tracking_number)
    parallel.refresh_from_db()
    assert parallel.status == Status.IN_PROCESS
    for _ in range(2):
        call_command("reconcile_tracking_stages", record=parallel.tracking_number, apply=True)
        parallel.refresh_from_db()
        assert parallel.status == Status.PENDING_RECEIPT
        assert parallel.activities.count() == events


def test_migration_retains_historical_admin_work_using_receipt_at_event_time(parallel, users, offices):
    before = services.add_remark(parallel, user=users["admin"], remark="Before any receipt")
    services.confirm_receipt(parallel, user=users["hr"])
    work = services.add_remark(parallel, user=users["admin"], remark="Administrative work for HR")
    # An old event did not store its work office. MED receives later; current
    # custody cannot be used to reinterpret the earlier HR work.
    RecordActivity.objects.filter(pk=work.pk).update(work_office=None)
    services.confirm_receipt(parallel, user=users["med"])
    migration = importlib.import_module("apps.tracking.migrations.0011_recipient_work_stage")
    with connection.schema_editor() as editor:
        migration.backfill_work_offices(apps, editor)
    before.refresh_from_db()
    work.refresh_from_db()
    assert before.work_office is None
    assert work.actor_office == offices["REC"]
    assert work.work_office == offices["HR"]
    assert stage(parallel, offices["HR"]) == Status.IN_PROCESS
    assert stage(parallel, offices["MED"]) == Status.RECEIVED


def test_mutation_refreshes_an_annotated_recipient_stage(parallel, users, offices):
    services.confirm_receipt(parallel, user=users["hr"])
    annotated = TrackingRecord.objects.with_stage_for(offices["HR"]).get(pk=parallel.pk)
    assert annotated.display_status == Status.RECEIVED
    services.add_remark(annotated, user=users["hr"], remark="Recipient started work")
    assert annotated.display_status == Status.IN_PROCESS
    assert annotated.status == Status.PENDING_RECEIPT

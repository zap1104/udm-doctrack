from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.tracking import external_services as services
from apps.tracking.models import (
    ExternalActionRevision,
    ExternalOffice,
    ExternalRoutingStep,
    ExternalTrackingRecord,
    Status,
    TrackingRecord,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def case(users, offices):
    ext = services.create_external_office(user=users["med_admin"], name="Registrar", code="REG")
    record = services.create_record(user=users["med_admin"], title="External paper", from_external_office=ext)
    return record, ext


def test_directory_and_draft_are_office_scoped(case, users):
    record, ext = case
    assert ExternalOffice.objects.visible_to(users["sup_admin"]).count() == 0
    assert not ExternalTrackingRecord.objects.visible_to(users["sup"]).filter(pk=record.pk).exists()
    assert not TrackingRecord.objects.exists()
    with pytest.raises(PermissionDenied):
        services.create_external_office(user=users["med"], name="No")


def test_mixed_custody_revision_and_internal_confirmation(case, users, offices):
    record, ext = case
    past = timezone.now() - timedelta(days=3)
    services.route_record(record, user=users["med_admin"], expected_revision=1, to_office=offices["SUP"], sent_at=past)
    assert record.tracking_number.startswith("UDM-EXT-MED-")
    assert record.current_external_office_id == ext.pk
    assert record.status == Status.PENDING_RECEIPT
    with pytest.raises(PermissionDenied):
        services.confirm_receipt(record, user=users["med_admin"], expected_revision=2)
    services.confirm_receipt(record, user=users["sup"], expected_revision=2)
    assert record.current_office_id == offices["SUP"].pk
    assert record.current_step.received_at >= record.current_step.sent_recorded_at
    with pytest.raises(ValidationError):
        services.add_remark(record, user=users["sup"], expected_revision=2, message="Stale")
    services.add_remark(record, user=users["sup"], expected_revision=3, message="Working")
    assert record.status == Status.IN_PROCESS
    services.route_record(record, user=users["sup"], expected_revision=4, to_external_office=ext)
    services.record_external_receipt(record, user=users["med_admin"], expected_revision=5)
    assert record.current_external_office_id == ext.pk
    assert record.current_office_id is None


def test_endpoint_constraint_and_external_directory_ownership(case, users, offices):
    record, ext = case
    other = services.create_external_office(user=users["sup_admin"], name="Other")
    with pytest.raises(ValidationError):
        services.route_record(record, user=users["med_admin"], expected_revision=1, to_external_office=other)
    with transaction.atomic(), pytest.raises(IntegrityError):
        ExternalRoutingStep.objects.create(
            record=record,
            sequence=1,
            from_external_office=ext,
            to_office=offices["SUP"],
            to_external_office=ext,
            sent_by=users["med_admin"],
        )


def test_correction_is_audited_and_internal_facts_protected(case, users, offices):
    record, ext = case
    sent = timezone.now() - timedelta(days=3)
    services.route_record(record, user=users["med_admin"], expected_revision=1, to_office=offices["SUP"], sent_at=sent)
    step = record.current_step
    corrected = sent + timedelta(hours=1)
    services.correct_step(
        record,
        step=step,
        user=users["med_admin"],
        expected_revision=2,
        reason="Paper register corrected",
        changes={"sent_at": corrected},
    )
    step.refresh_from_db()
    assert step.sent_at == corrected
    assert ExternalActionRevision.objects.get(record=record).reason == "Paper register corrected"
    services.confirm_receipt(record, user=users["sup"], expected_revision=3)
    with pytest.raises(ValidationError):
        services.correct_step(
            record,
            step=step,
            user=users["med_admin"],
            expected_revision=4,
            reason="Forbidden internal edit",
            changes={"received_at": sent},
        )


def test_completion_and_frozen_history_allow_annotation(case, users, offices):
    record, _ = case
    services.route_record(record, user=users["med_admin"], expected_revision=1, to_office=offices["MED"])
    services.confirm_receipt(record, user=users["med"], expected_revision=2)
    services.complete_record(record, user=users["med"], expected_revision=3)
    assert record.status == Status.COMPLETED_PENDING_UPLOAD
    record.status = Status.COMPLETED
    record.save(update_fields=["status"])
    with pytest.raises(ValidationError):
        services.route_record(record, user=users["med_admin"], expected_revision=4, to_office=offices["SUP"])
    services.annotate_record(record, user=users["med_admin"], expected_revision=4, message="Archive annotation")
    assert record.activities.filter(event="ANNOTATION").exists()


def test_stale_actor_and_viewer_cannot_write(case, users, offices):
    record, _ = case
    users["med_admin"].__class__.objects.filter(pk=users["med_admin"].pk).update(is_active=False)
    with pytest.raises(PermissionDenied):
        services.route_record(record, user=users["med_admin"], expected_revision=1, to_office=offices["MED"])
    with pytest.raises(PermissionDenied):
        services.annotate_record(record, user=users["viewer"], expected_revision=1, message="No")


def test_drafts_hidden_from_staff_and_viewers(case, users):
    record, _ = case
    assert not ExternalTrackingRecord.objects.visible_to(users["med"]).filter(pk=record.pk).exists()
    assert not ExternalTrackingRecord.objects.visible_to(users["viewer"]).filter(pk=record.pk).exists()
    assert ExternalTrackingRecord.objects.visible_to(users["med_admin"]).filter(pk=record.pk).exists()


def test_directory_names_normalized_and_case_unique(case, users):
    with pytest.raises(ValidationError):
        services.create_external_office(user=users["med_admin"], name="  REGISTRAR  ")


def test_timing_keeps_external_time_and_receipt_wait_separate(case, users, offices):
    from apps.tracking.external_analytics import timing_for_record

    record, _ = case
    sent = timezone.now() - timedelta(days=3)
    services.route_record(record, user=users["med_admin"], expected_revision=1, to_office=offices["MED"], sent_at=sent)
    timing = timing_for_record(record)
    assert timing["receipt_wait_seconds"] > 0
    assert timing["internal_seconds"] is None
    assert timing["overall_seconds"] >= timing["receipt_wait_seconds"]
    services.confirm_receipt(record, user=users["med"], expected_revision=2)
    timing = timing_for_record(record, now=timezone.now() + timedelta(days=1))
    assert timing["internal_seconds"] > 0


def test_external_completion_retains_actual_time(case, users, offices):
    record, ext = case
    sent = timezone.now() - timedelta(days=4)
    services.route_record(record, user=users["med_admin"], expected_revision=1, to_office=offices["MED"], sent_at=sent)
    services.confirm_receipt(record, user=users["med"], expected_revision=2)
    services.route_record(record, user=users["med"], expected_revision=3, to_external_office=ext)
    services.record_external_receipt(record, user=users["med_admin"], expected_revision=4)
    when = timezone.now()
    services.complete_record(record, user=users["med_admin"], expected_revision=5, completed_at=when)
    assert record.completed_at == when


def test_corrections_cannot_move_receipt_after_known_external_work(case, users, offices):
    record, ext = case
    services.route_record(record, user=users["med_admin"], expected_revision=1, to_office=offices["MED"])
    services.confirm_receipt(record, user=users["med"], expected_revision=2)
    services.route_record(record, user=users["med"], expected_revision=3, to_external_office=ext)
    services.record_external_receipt(record, user=users["med_admin"], expected_revision=4)
    services.record_external_work(record, user=users["med_admin"], expected_revision=5, message="Signed")
    with pytest.raises(ValidationError):
        services.correct_step(
            record,
            step=record.current_step,
            user=users["med_admin"],
            expected_revision=6,
            reason="Wrong time",
            changes={"received_at": timezone.now()},
        )


def test_upload_requires_custody_and_valid_signature(case, users, offices):
    from django.core.files.uploadedfile import SimpleUploadedFile

    record, _ = case
    with pytest.raises(ValidationError):
        services.add_attachment(
            record,
            user=users["med_admin"],
            expected_revision=1,
            uploaded_file=SimpleUploadedFile("bad.pdf", b"not a PDF"),
        )
    services.route_record(record, user=users["med_admin"], expected_revision=1, to_office=offices["SUP"])
    with pytest.raises(PermissionDenied):
        services.add_attachment(
            record, user=users["sup"], expected_revision=2, uploaded_file=SimpleUploadedFile("good.pdf", b"%PDF-1.4")
        )


def test_lifecycle_starts_at_earliest_known_external_history(users, offices):
    from apps.tracking.external_analytics import timing_for_record

    ext = services.create_external_office(user=users["med_admin"], name="Registrar")
    origin = timezone.now() - timedelta(days=5)
    record = services.create_record(
        user=users["med_admin"], title="Earlier origin", from_external_office=ext, actual_at=origin
    )
    services.route_record(
        record,
        user=users["med_admin"],
        expected_revision=1,
        to_office=offices["MED"],
        sent_at=origin + timedelta(days=1),
    )
    assert timing_for_record(record)["started_at"] == origin


def test_multiple_document_types_require_a_main(users, memo_type):
    from apps.core.models import DocumentType

    other = DocumentType.objects.create(code="LET", name="Letter")
    with pytest.raises(ValidationError):
        services.create_record(user=users["med_admin"], title="Mixed", document_types=[memo_type, other])
    record = services.create_record(
        user=users["med_admin"], title="Mixed", document_types=[memo_type, other], document_type=memo_type
    )
    assert record.document_types.count() == 2
    assert record.document_type_id == memo_type.pk


@pytest.mark.django_db(transaction=True)
def test_simultaneous_same_revision_only_one_route(users, offices):
    from concurrent.futures import ThreadPoolExecutor

    from django.db import close_old_connections

    ext = services.create_external_office(user=users["med_admin"], name="Registrar")
    record = services.create_record(user=users["med_admin"], title="Race", from_external_office=ext)

    def attempt():
        close_old_connections()
        try:
            candidate = ExternalTrackingRecord.objects.get(pk=record.pk)
            services.route_record(candidate, user=users["med_admin"], expected_revision=1, to_office=offices["MED"])
            return "ok"
        except ValidationError:
            return "stale"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(outcomes) == ["ok", "stale"]
    assert record.routing_steps.count() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("operation", ["create", "rename"])
def test_concurrent_directory_names_show_validation_error_instead_of_database_error(
    users, offices, monkeypatch, operation
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import connections

    actors = [users["med_admin"], users["admin"]]
    entries = []
    if operation == "rename":
        entries = [
            services.create_external_office(user=actors[0], name=name)
            for name in ("Original registrar", "Original college")
        ]
    validated = Barrier(2)
    original_clean = ExternalOffice.full_clean

    def clean_then_wait(entry, *args, **kwargs):
        original_clean(entry, *args, **kwargs)
        validated.wait(timeout=10)

    monkeypatch.setattr(ExternalOffice, "full_clean", clean_then_wait)

    def attempt(index):
        try:
            name = "Concurrent Registrar" if index == 0 else "  concurrent registrar  "
            if operation == "create":
                services.create_external_office(user=actors[index], managing_office=offices["MED"], name=name)
            else:
                services.save_external_office(entries[index], user=actors[index], expected_revision=1, name=name)
            return "saved"
        except ValidationError:
            return "duplicate"
        except IntegrityError:
            return "database error"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, range(2)))
    assert sorted(outcomes) == ["duplicate", "saved"]
    assert (
        ExternalOffice.objects.filter(managing_office=offices["MED"], name__iexact="Concurrent Registrar").count() == 1
    )
    if operation == "rename":
        for entry in entries:
            entry.refresh_from_db()
        assert sorted(entry.revision for entry in entries) == [1, 2]
        loser = entries[outcomes.index("duplicate")]
        assert loser.name in {"Original registrar", "Original college"}


@pytest.mark.parametrize("constraint", [None, "unrelated_directory_constraint"])
def test_directory_name_error_handling_preserves_unrelated_database_failures(users, monkeypatch, constraint):
    from types import SimpleNamespace

    def unrelated_failure(entry, *args, **kwargs):
        cause = Exception("Unrelated database failure")
        if constraint:
            cause.diag = SimpleNamespace(constraint_name=constraint)
        raise IntegrityError("Unrelated database failure") from cause

    monkeypatch.setattr(ExternalOffice, "save", unrelated_failure)
    with pytest.raises(IntegrityError, match="Unrelated database failure"):
        services.create_external_office(user=users["med_admin"], name="Registrar")


def test_sent_and_receipt_recording_times_cannot_be_corrected(case, users, offices):
    record, _ = case
    services.route_record(record, user=users["med_admin"], expected_revision=1, to_office=offices["MED"])
    with pytest.raises(ValidationError):
        services.correct_step(
            record,
            step=record.current_step,
            user=users["med_admin"],
            expected_revision=2,
            reason="Backdate recording",
            changes={"sent_recorded_at": timezone.now() - timedelta(days=1)},
        )


def test_directory_rename_does_not_change_custody_history(case, users, offices):
    record, ext = case
    services.route_record(record, user=users["med_admin"], expected_revision=1, to_office=offices["MED"])
    services.save_external_office(ext, user=users["med_admin"], expected_revision=1, name="Renamed Registrar")
    assert record.current_step.from_name_snapshot == "Registrar"
    assert record.current_office_snapshot == "Registrar"


def test_unknown_movement_times_remain_unmeasured(case):
    from apps.tracking.external_analytics import timing_for_record

    record, _ = case
    timing = timing_for_record(record)
    assert timing["internal_seconds"] is None
    assert timing["external_seconds"] is None
    assert timing["receipt_wait_seconds"] is None
    assert timing["overall_seconds"] is None


def test_external_custody_still_reported_for_internal_office_perspective(case, users, offices):
    from apps.tracking.external_analytics import timing_for_record

    record, ext = case
    services.route_record(record, user=users["med_admin"], expected_revision=1, to_office=offices["MED"])
    services.confirm_receipt(record, user=users["med"], expected_revision=2)
    services.route_record(record, user=users["med"], expected_revision=3, to_external_office=ext)
    services.record_external_receipt(record, user=users["med_admin"], expected_revision=4)
    later = timezone.now() + timedelta(days=1)
    assert timing_for_record(record, office=offices["MED"], now=later)["external_seconds"] > 0


def test_sent_action_cannot_precede_known_external_work(case, users, offices):
    record, ext = case
    services.route_record(record, user=users["med_admin"], expected_revision=1, to_office=offices["MED"])
    services.confirm_receipt(record, user=users["med"], expected_revision=2)
    services.route_record(record, user=users["med"], expected_revision=3, to_external_office=ext)
    services.record_external_receipt(record, user=users["med_admin"], expected_revision=4)
    received = record.current_step.received_at
    services.record_external_work(record, user=users["med_admin"], expected_revision=5, message="Known later work")
    with pytest.raises(ValidationError):
        services.route_record(
            record, user=users["med_admin"], expected_revision=6, to_office=offices["SUP"], sent_at=received
        )


def test_external_endpoint_correction_is_typed_scoped_and_unconsumed(case, users, offices):
    record, first = case
    second = services.create_external_office(user=users["med_admin"], name="Corrected Registrar")
    services.route_record(record, user=users["med_admin"], expected_revision=1, to_office=offices["MED"])
    services.correct_step(
        record,
        step=record.current_step,
        user=users["med_admin"],
        expected_revision=2,
        reason="Wrong paper sender",
        changes={"from_external_office": second},
    )
    assert record.current_external_office_id == second.pk
    assert record.current_step.from_name_snapshot == "Corrected Registrar"
    revision = record.revisions.get()
    assert revision.old_values["from_external_office"]["id"] == first.pk
    assert revision.new_values["from_external_office"]["id"] == second.pk
    services.confirm_receipt(record, user=users["med"], expected_revision=3)
    with pytest.raises(ValidationError):
        services.correct_step(
            record,
            step=record.current_step,
            user=users["med_admin"],
            expected_revision=4,
            reason="Consumed internal receipt",
            changes={"from_external_office": first},
        )


def test_external_attachment_is_evidence_until_work_explicitly_recorded(case, users, offices, settings, tmp_path):
    from django.core.files.uploadedfile import SimpleUploadedFile

    settings.MEDIA_ROOT = tmp_path
    record, ext = case
    services.route_record(record, user=users["med_admin"], expected_revision=1, to_office=offices["MED"])
    services.confirm_receipt(record, user=users["med"], expected_revision=2)
    services.route_record(record, user=users["med"], expected_revision=3, to_external_office=ext)
    services.record_external_receipt(record, user=users["med_admin"], expected_revision=4)
    services.add_attachment(
        record,
        user=users["med_admin"],
        expected_revision=5,
        uploaded_file=SimpleUploadedFile("receipt.pdf", b"%PDF-1.4"),
    )
    assert record.status == Status.RECEIVED
    services.record_external_work(record, user=users["med_admin"], expected_revision=6, message="Action completed")
    assert record.status == Status.IN_PROCESS


def test_only_one_unreceived_recipient_per_record_database_constraint(case, users, offices):
    record, ext = case
    services.route_record(record, user=users["med_admin"], expected_revision=1, to_office=offices["MED"])
    with transaction.atomic(), pytest.raises(IntegrityError):
        ExternalRoutingStep.objects.create(
            record=record, sequence=2, from_external_office=ext, to_office=offices["SUP"], sent_by=users["med_admin"]
        )


def test_external_receipt_encoder_is_not_actual_internal_confirmer(case, users, offices):
    record, ext = case
    services.route_record(record, user=users["med_admin"], expected_revision=1, to_office=offices["MED"])
    services.confirm_receipt(record, user=users["med"], expected_revision=2)
    internal = record.current_step
    assert internal.received_by_id == users["med"].pk
    assert internal.receipt_recorded_by_id == users["med"].pk
    services.route_record(record, user=users["med"], expected_revision=3, to_external_office=ext)
    services.record_external_receipt(record, user=users["med_admin"], expected_revision=4)
    external = record.current_step
    assert external.received_by_id is None
    assert external.receipt_recorded_by_id == users["med_admin"].pk


def test_external_remark_is_annotation_without_fabricated_work(case, users, offices):
    record, ext = case
    services.route_record(record, user=users["med_admin"], expected_revision=1, to_office=offices["MED"])
    services.confirm_receipt(record, user=users["med"], expected_revision=2)
    services.route_record(record, user=users["med"], expected_revision=3, to_external_office=ext)
    services.record_external_receipt(record, user=users["med_admin"], expected_revision=4)
    services.add_remark(record, user=users["med_admin"], expected_revision=5, message="Evidence note")
    assert record.status == Status.RECEIVED
    assert record.activities.get(event="REMARK").actual_at is None


def test_manual_external_dispatch_distinguishes_encoder_from_internal_sender(case, users, offices):
    record, _ = case
    services.route_record(record, user=users["med_admin"], expected_revision=1, to_office=offices["MED"])
    external = record.current_step
    assert external.sent_by_id is None
    assert external.sent_recorded_by_id == users["med_admin"].pk
    services.confirm_receipt(record, user=users["med"], expected_revision=2)
    services.route_record(record, user=users["med"], expected_revision=3, to_office=offices["SUP"])
    internal = record.current_step
    assert internal.sent_by_id == users["med"].pk
    assert internal.sent_recorded_by_id == users["med"].pk


def test_external_evidence_upload_does_not_invent_unknown_actual_history(case, users, settings, tmp_path):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.tracking.external_analytics import timing_for_record

    settings.MEDIA_ROOT = tmp_path
    record, _ = case
    services.add_attachment(
        record, user=users["med_admin"], expected_revision=1, uploaded_file=SimpleUploadedFile("paper.pdf", b"%PDF-1.4")
    )
    assert record.activities.get(event="ATTACHMENT").actual_at is None
    assert timing_for_record(record)["started_at"] is None


def test_corrected_dispatch_cannot_precede_work_in_previous_custody(case, users):
    record, first = case
    second = services.create_external_office(user=users["med_admin"], name="Second external office")
    start = timezone.now() - timedelta(days=10)
    services.route_record(
        record, user=users["med_admin"], expected_revision=1, to_external_office=second, sent_at=start
    )
    services.record_external_receipt(
        record, user=users["med_admin"], expected_revision=2, received_at=start + timedelta(days=1)
    )
    services.record_external_work(
        record, user=users["med_admin"], expected_revision=3, actual_at=start + timedelta(days=3), message="Known work"
    )
    services.route_record(
        record,
        user=users["med_admin"],
        expected_revision=4,
        to_external_office=first,
        sent_at=start + timedelta(days=4),
    )
    with pytest.raises(ValidationError):
        services.correct_step(
            record,
            step=record.current_step,
            user=users["med_admin"],
            expected_revision=5,
            reason="Before recorded work",
            changes={"sent_at": start + timedelta(days=2)},
        )


def test_corrected_dispatch_controls_current_lifecycle_without_rewriting_audit(case, users, offices):
    from apps.tracking.external_analytics import timing_for_record

    record, _ = case
    sent = timezone.now() - timedelta(days=5)
    corrected = sent + timedelta(days=2)
    services.route_record(record, user=users["med_admin"], expected_revision=1, to_office=offices["MED"], sent_at=sent)
    services.correct_step(
        record,
        step=record.current_step,
        user=users["med_admin"],
        expected_revision=2,
        reason="Actual dispatch was later",
        changes={"sent_at": corrected},
    )
    assert record.activities.get(event="SENT").actual_at == sent
    assert timing_for_record(record)["started_at"] == corrected


def test_corrected_receipt_allows_valid_historical_completion_without_old_audit_date(case, users):
    record, _ = case
    target = services.create_external_office(user=users["med_admin"], name="Recipient external office")
    start = timezone.now() - timedelta(days=10)
    services.route_record(
        record, user=users["med_admin"], expected_revision=1, to_external_office=target, sent_at=start
    )
    services.record_external_receipt(
        record, user=users["med_admin"], expected_revision=2, received_at=start + timedelta(days=4)
    )
    services.correct_step(
        record,
        step=record.current_step,
        user=users["med_admin"],
        expected_revision=3,
        reason="Actual receipt was earlier",
        changes={"received_at": start + timedelta(days=1)},
    )
    services.complete_record(
        record, user=users["med_admin"], expected_revision=4, completed_at=start + timedelta(days=2)
    )
    assert record.completed_at == start + timedelta(days=2)
    assert record.activities.get(event="RECEIVED").actual_at == start + timedelta(days=4)


def test_later_sender_correction_cannot_invent_transfer_from_confirmed_custodian(case, users, offices):
    record, first = case
    second = services.create_external_office(user=users["med_admin"], name="Other claimed custodian")
    services.route_record(record, user=users["med_admin"], expected_revision=1, to_office=offices["MED"])
    services.confirm_receipt(record, user=users["med"], expected_revision=2)
    services.route_record(record, user=users["med"], expected_revision=3, to_external_office=first)
    services.record_external_receipt(record, user=users["med_admin"], expected_revision=4)
    services.route_record(record, user=users["med_admin"], expected_revision=5, to_office=offices["SUP"])
    with pytest.raises(ValidationError):
        services.correct_step(
            record,
            step=record.current_step,
            user=users["med_admin"],
            expected_revision=6,
            reason="Unrecorded transfer",
            changes={"from_external_office": second},
        )
    record.refresh_from_db()
    assert record.current_external_office_id == first.pk


def test_external_work_cannot_publish_draft_without_a_route_or_real_number(case, users):
    record, _ = case
    draft_number = record.tracking_number
    with pytest.raises(ValidationError):
        services.record_external_work(
            record, user=users["med_admin"], expected_revision=1, message="Attempt to publish draft"
        )
    record.refresh_from_db()
    assert record.status == Status.DRAFT
    assert record.tracking_number == draft_number
    assert not ExternalTrackingRecord.objects.visible_to(users["med"]).filter(pk=record.pk).exists()


def test_corrected_dispatch_cannot_precede_anchored_reopening_after_pending_filing(case, users):
    record, first = case
    second = services.create_external_office(user=users["med_admin"], name="External reopening recipient")
    start = timezone.now() - timedelta(days=5)
    services.route_record(
        record, user=users["med_admin"], expected_revision=1, to_external_office=second, sent_at=start
    )
    services.record_external_receipt(
        record, user=users["med_admin"], expected_revision=2, received_at=start + timedelta(days=1)
    )
    services.complete_record(
        record, user=users["med_admin"], expected_revision=3, completed_at=start + timedelta(days=2)
    )
    services.reopen_record(record, user=users["med_admin"], expected_revision=4, note="Further action required")
    reopened_at = record.activities.get(event="REOPENED").actual_at
    services.route_record(record, user=users["med_admin"], expected_revision=5, to_external_office=first)
    with pytest.raises(ValidationError):
        services.correct_step(
            record,
            step=record.current_step,
            user=users["med_admin"],
            expected_revision=6,
            reason="Dispatch during pending filing",
            changes={"sent_at": reopened_at - timedelta(hours=1)},
        )


def test_external_work_cannot_be_backdated_into_a_completed_pause_after_reopening(case, users):
    record, _ = case
    recipient = services.create_external_office(user=users["med_admin"], name="External paused work recipient")
    start = timezone.now() - timedelta(days=5)
    services.route_record(
        record, user=users["med_admin"], expected_revision=record.revision, to_external_office=recipient, sent_at=start
    )
    services.record_external_receipt(
        record, user=users["med_admin"], expected_revision=record.revision, received_at=start + timedelta(days=1)
    )
    services.complete_record(
        record, user=users["med_admin"], expected_revision=record.revision, completed_at=start + timedelta(days=2)
    )
    services.reopen_record(
        record, user=users["med_admin"], expected_revision=record.revision, note="Additional work required"
    )
    revision = record.revision
    with pytest.raises(ValidationError):
        services.record_external_work(
            record,
            user=users["med_admin"],
            expected_revision=revision,
            actual_at=start + timedelta(days=3),
            message="Work during the closed pause",
        )
    record.refresh_from_db()
    assert record.status == Status.RECEIVED
    assert record.revision == revision
    assert not record.activities.filter(event="EXTERNAL_WORK").exists()

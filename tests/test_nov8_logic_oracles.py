"""Independent timeline oracles and fully populated direction/stage evidence."""

import csv
import io
import random
from datetime import date, datetime, timedelta

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.core.business_time import Holidays, business_seconds_between
from apps.core.processing_time import processing_seconds
from apps.documents.external_services import archive_external_record
from apps.documents.models import Document
from apps.tracking import external_services as external
from apps.tracking import services as internal
from apps.tracking.external_analytics import timing_for_record
from apps.tracking.models import ExternalTrackingRecord, Status

pytestmark = pytest.mark.django_db
MINUTE = timedelta(minutes=1)
HOUR = 3600


def at(day, hour, minute=0, *, month=10, year=2026):
    return timezone.make_aware(datetime(year, month, day, hour, minute))


def minute_oracle(start, end, *, holidays=(), recurring=(), blocked=()):
    """Count a bounded minute grid; no production calendar/filter helpers."""
    work = calendar = 0
    cursor = start
    while cursor < end:
        stop = min(cursor + MINUTE, end)
        active = not any(left <= cursor < right for left, right in blocked)
        seconds = int((stop - cursor).total_seconds())
        if active:
            calendar += seconds
            local = timezone.localtime(cursor)
            if (local.weekday() < 5 and local.date() not in holidays
                    and (local.month, local.day) not in recurring
                    and (8 <= local.hour < 12 or 13 <= local.hour < 17)):
                work += seconds
        cursor = stop
    return work, calendar


@pytest.mark.parametrize("seed", range(24))
def test_generated_calendar_matches_independent_grid_across_year_boundary(seed):
    randomizer = random.Random(seed)
    start = at(28, 6, month=12) + timedelta(minutes=randomizer.randrange(3000))
    end = start + timedelta(minutes=randomizer.randrange(1, 6000))
    closed = {date(2026, 12, 30)}
    recurring = {(1, 1)}
    expected, _ = minute_oracle(start, end, holidays=closed, recurring=recurring)
    assert business_seconds_between(start, end, Holidays(frozenset(closed), frozenset(recurring))) == expected


@pytest.mark.parametrize("seed", range(24))
def test_generated_receipt_wait_union_matches_independent_grid(seed):
    randomizer = random.Random(seed)
    start, end = at(12, 8), at(14, 17)
    # Three batches, including overlapping parallel receipt waits and an
    # unresolved earlier recipient superseded by a later endorsement.
    steps = [(1, at(12, 8), at(12, 9))]
    for batch, day in ((2, 12), (3, 13), (4, 14)):
        sent = at(day, 9) + timedelta(minutes=randomizer.randrange(60))
        steps.extend((batch, sent, sent + timedelta(minutes=randomizer.randrange(1, 240))) for _ in range(2))
        steps.append((batch, sent, None))
    starts = {batch: min(sent for b, sent, _ in steps if b == batch) for batch in (1, 2, 3, 4)}
    blocked = []
    for batch, sent, received in steps:
        successor = starts.get(batch + 1, end)
        blocked.append((max(start, sent), min(received or end, successor, end)))
    expected = minute_oracle(start, end, blocked=blocked)
    assert processing_seconds(start, end, steps, Holidays()) == expected
    # Permuting rows and duplicate evidence must not increase or reduce time.
    randomizer.shuffle(steps)
    assert processing_seconds(start, end, steps + steps[:2], Holidays()) == expected


def act(function, record, user, **kwargs):
    return function(record, user=user, expected_revision=record.revision, **kwargs)


def facts(record):
    record.refresh_from_db()
    return (record.status, record.revision, record.current_office_id, record.current_external_office_id,
            record.completed_at, list(record.routing_steps.values_list(
                "sequence", "sent_at", "received_at", "from_office_id", "to_office_id",
                "from_external_office_id", "to_external_office_id")), record.activities.count())


def test_external_full_custody_trace_has_hand_calculated_partition_and_immutable_refusals(
    users, offices, monkeypatch, settings, tmp_path,
):
    settings.MEDIA_ROOT = tmp_path
    clock = [at(12, 8)]  # Monday.
    monkeypatch.setattr(timezone, "now", lambda: clock[0])
    outside = external.create_external_office(user=users["med_admin"], name="Trace Registrar")
    record = external.create_record(user=users["med_admin"], title="Independent custody trace",
                                    from_external_office=outside, actual_at=clock[0])
    act(external.add_attachment, record, users["med_admin"],
        uploaded_file=SimpleUploadedFile("trace.txt", b"Original trace evidence"))
    assert record.status == Status.DRAFT
    clock[0] = at(12, 9)
    act(external.route_record, record, users["med_admin"], to_office=offices["MED"], sent_at=clock[0])
    assert record.status == Status.PENDING_RECEIPT
    assert record.responsible_office_id == offices["MED"].pk
    before = facts(record)
    with pytest.raises(PermissionDenied):
        act(external.add_remark, record, users["med_admin"], message="Cannot work before receipt")
    assert facts(record) == before
    clock[0] = at(12, 10)
    act(external.confirm_receipt, record, users["med"])
    assert record.status == Status.RECEIVED
    clock[0] = at(12, 11)
    act(external.add_remark, record, users["med"], message="Checked the papers")
    assert record.status == Status.IN_PROCESS
    clock[0] = at(12, 14)
    act(external.route_record, record, users["med"], to_external_office=outside)
    assert record.status == Status.PENDING_RECEIPT
    clock[0] = at(12, 15)
    act(external.record_external_receipt, record, users["med_admin"], received_at=clock[0])
    assert record.current_external_office_id == outside.pk
    clock[0] = at(12, 16)
    act(external.record_external_work, record, users["med_admin"], actual_at=clock[0], message="Signed outside")
    clock[0] = at(13, 9)
    act(external.route_record, record, users["med_admin"], to_office=offices["MED"], sent_at=clock[0])
    clock[0] = at(13, 10)
    act(external.confirm_receipt, record, users["med"])
    clock[0] = at(13, 11)
    act(external.complete_record, record, users["med"])
    assert record.status == Status.COMPLETED_PENDING_UPLOAD
    before = facts(record)
    with pytest.raises(ValidationError):
        act(external.add_attachment, record, users["med_admin"],
            uploaded_file=SimpleUploadedFile("late.txt", b"Must reopen first"))
    assert facts(record) == before
    clock[0] = at(13, 14)
    act(external.reopen_record, record, users["med_admin"])
    clock[0] = at(13, 15)
    act(external.add_remark, record, users["med"], message="Additional check after return")
    clock[0] = at(13, 16)
    act(external.complete_record, record, users["med"])
    timing = timing_for_record(record, now=at(14, 17))
    assert timing["internal_seconds"] == 6 * HOUR
    assert timing["external_seconds"] == 4 * HOUR
    assert timing["receipt_wait_seconds"] == 3 * HOUR
    assert timing["overall_seconds"] == 15 * HOUR
    assert timing_for_record(record, office=offices["SUP"])["internal_seconds"] is None
    document = archive_external_record(record, user=users["med_admin"], expected_revision=record.revision)
    assert document.collection == "EXTERNAL" and document.office_id == offices["MED"].pk
    assert Document.objects.filter(external_tracking_record=record).count() == 1
    assert Document.objects.filter(collection="OVPA").count() == 0
    assert list(record.routing_steps.values_list("sequence", flat=True)) == [1, 2, 3]


@pytest.fixture
def external_matrix(users, offices):
    """Names and expected memberships are literal, not production query results."""
    now = timezone.now()
    outside = external.create_external_office(user=users["med_admin"], name="Matrix Registrar")
    rows = {}
    for direction, sender, receiver, external_sender in (
        ("incoming", None, "MED", outside), ("outgoing", "MED", "SUP", None),
        ("other", "SUP", "HR", None),
    ):
        for index, stage in enumerate((Status.PENDING_RECEIPT, Status.RECEIVED, Status.IN_PROCESS)):
            record = ExternalTrackingRecord.objects.create(
                managing_office=offices["MED"], title=f"Matrix {direction} {stage}",
                tracking_number=f"MATRIX-{direction}-{index}", created_by=users["med_admin"],
                current_office=offices[receiver] if stage != Status.PENDING_RECEIPT else (
                    offices[sender] if sender else None),
                current_external_office=external_sender if stage == Status.PENDING_RECEIPT else None,
                current_office_snapshot="Fixture confirmed holder", status=stage,
                due_at=now - timedelta(hours=1) if index != 1 else now + timedelta(hours=1),
            )
            record.routing_steps.create(
                sequence=1, from_office=offices[sender] if sender else None, from_external_office=external_sender,
                to_office=offices[receiver], from_name_snapshot="Fixture sender", to_name_snapshot="Fixture recipient",
                sent_recorded_by=users["med_admin"], sent_at=now - timedelta(hours=3),
                received_at=now - timedelta(hours=2) if stage != Status.PENDING_RECEIPT else None,
            )
            rows[direction, stage] = record.pk
    # An unrelated OVPA record must never appear in any External response.
    internal.create_draft_record(user=users["med"], subject="Separate internal draft", instructions="Review")
    return rows


@pytest.mark.parametrize("actor", ["admin", "med_admin", "med", "viewer"])
@pytest.mark.parametrize("direction", ["incoming", "outgoing", "other"])
@pytest.mark.parametrize("stage", [Status.PENDING_RECEIPT, Status.RECEIVED, Status.IN_PROCESS])
def test_external_explicit_direction_stage_ids_list_report_and_export_agree(
    client, users, offices, external_matrix, actor, direction, stage,
):
    client.force_verified_login(users[actor])
    expected = {external_matrix[direction, stage]}
    params = {"office": offices["MED"].pk, "direction": direction, "status": stage}
    for endpoint in ("tracking:external_list", "tracking:external_reports"):
        response = client.get(reverse(endpoint), params)
        assert response.status_code == 200
        assert {record.pk for record in response.context["page_obj"]} == expected
        assert response.context["total_count"] == 1
        assert sum(response.context["direction_counts"].values()) == 1
        assert response.context["direction_counts"][direction] == 1
        assert sum(count for _, count in response.context["status_counts"]) == 1
    response = client.get(reverse("tracking:external_export"), params)
    assert response.status_code == 200
    exported = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    expected_number = f"MATRIX-{direction}-{[Status.PENDING_RECEIPT, Status.RECEIVED, Status.IN_PROCESS].index(stage)}"
    assert [row["External tracking number"] for row in exported] == [expected_number]
    for overdue in ("yes", "no"):
        response = client.get(reverse("tracking:external_list"), params | {"overdue": overdue})
        wanted = expected if ((stage != Status.RECEIVED) == (overdue == "yes")) else set()
        assert {record.pk for record in response.context["page_obj"]} == wanted

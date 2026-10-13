"""A multi-office slip has the same office stage in reports and its workspace."""

import csv
import io
from datetime import timedelta
from urllib.parse import urlencode

import pytest
from django.utils import timezone

from apps.tracking import services
from apps.tracking.models import Status, TrackingRecord


@pytest.fixture
def partial_office_work(users, offices):
    record = services.create_draft_record(
        user=users["admin"], subject="Two recipients, only HR has started", instructions="For action",
    )
    services.route_record(record, [offices["HR"], offices["MED"]], user=users["admin"])
    services.confirm_receipt(record, user=users["hr"])
    services.add_remark(record, user=users["hr"], remark="HR has started its review.")
    return record


def _stage_counts(rows):
    return {row["status"]: row["total"] for row in rows}


def _csv_records(response):
    rows = list(csv.reader(io.StringIO(response.content.decode())))
    heading = next(index for index, row in enumerate(rows) if row and row[0] == "Tracking number")
    return [dict(zip(rows[heading], row, strict=True)) for row in rows[heading + 1:]]


@pytest.mark.django_db
@pytest.mark.parametrize("office_code,direction,stage", [
    ("MED", "incoming", Status.PENDING_RECEIPT),
    ("HR", "incoming", Status.IN_PROCESS),
    ("REC", "outgoing", Status.PENDING_RECEIPT),
])
def test_each_office_stage_matches_dashboard_reports_memo_and_export(
    client, users, offices, partial_office_work, office_code, direction, stage,
):
    client.force_login(users["admin"])
    office = offices[office_code]
    dashboard = client.get(f"/?office={office.pk}")
    reports = client.get(f"/tracking/reports/?office={office.pk}")
    memo = client.get(f"/memo/print/?office={office.pk}")
    assert dashboard.status_code == reports.status_code == memo.status_code == 200

    group = next(group for group in dashboard.context["desk_direction_groups"] if group["key"] == direction)
    report_group = next(group for group in reports.context["direction_stages"] if group["key"] == direction)
    assert group["count"] == report_group["total"] == 1
    assert {item["status"]: item["count"] for item in group["stages"]}[stage] == 1
    assert _stage_counts(report_group["stages"])[stage] == 1
    assert sum(_stage_counts(reports.context["by_status"]).values()) == reports.context["total_records"] == 1

    ring = next(ring for ring in dashboard.context["tracking_rings"]["rings"] if ring["key"] == direction)
    assert ring["status"]["total"] == 1
    assert next(slice_["total"] for slice_ in ring["status"]["slices"] if slice_["label"] == stage.label) == 1
    expected_key = {Status.PENDING_RECEIPT: "pending_receipt", Status.IN_PROCESS: "in_process"}[stage]
    for context in (dashboard.context, memo.context):
        tracking = {item["key"]: item["total"] for item in context["breakdown"]["slices"] if item["group"] == "tracking"}
        assert tracking[expected_key] == 1
        assert sum(tracking.values()) == 1

    child = next(item for item in group["stages"] if item["status"] == stage)
    picked = client.get(child["href"])
    assert next(record.stage_url for record in picked.context["attention_records"] if record.pk == partial_office_work.pk) == (
        partial_office_work.get_absolute_url() + f"?office={office.pk}"
    )
    assert next(record.stage_url for record in dashboard.context["recent_records"] if record.pk == partial_office_work.pk) == (
        partial_office_work.get_absolute_url() + f"?office={office.pk}"
    )
    workspace = client.get(child["tracking_url"])
    assert {record.pk for record in workspace.context["page_obj"]} == {partial_office_work.pk}
    assert next(iter(workspace.context["page_obj"])).display_status == stage

    for query in (
        {"office": office.pk, "record_status": stage},
        {"source": "tracking", "office": office.pk, "scope": direction, "status": stage},
    ):
        exported = client.get("/tracking/reports/export/?" + urlencode(query))
        assert exported.status_code == 200
        rows = _csv_records(exported)
        assert len(rows) == 1
        assert rows[0]["Status"] == stage.label
        assert rows[0]["Direction"] == direction.title()


@pytest.mark.django_db
def test_all_offices_counts_a_partial_batch_once_as_pending(client, users, partial_office_work):
    client.force_login(users["admin"])
    dashboard = client.get("/?office=all")
    reports = client.get("/tracking/reports/?office=all")
    memo = client.get("/memo/print/?office=all")
    assert dashboard.status_code == reports.status_code == memo.status_code == 200
    assert _stage_counts(reports.context["all_office_stages"]) == {
        Status.DRAFT: 0, Status.PENDING_RECEIPT: 1, Status.RECEIVED: 0, Status.IN_PROCESS: 0,
    }
    assert reports.context["incoming_records"] is reports.context["outgoing_records"] is None
    assert reports.context["total_records"] == 1
    for context in (dashboard.context, memo.context):
        counts = {item["key"]: item["total"] for item in context["breakdown"]["slices"]}
        assert counts["pending_receipt"] == 1
        assert counts["received"] == counts["in_process"] == 0
    rows = _csv_records(client.get("/tracking/reports/export/?office=all&record_status=PENDING_RECEIPT"))
    assert len(rows) == 1
    assert rows[0]["Status"] == Status.PENDING_RECEIPT.label


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["med", "med_admin", "viewer"])
def test_non_admin_stage_uses_own_office_even_with_a_foreign_office_parameter(
    client, users, offices, partial_office_work, role,
):
    client.force_login(users[role])
    for query in ("", f"?office={offices['HR'].pk}"):
        dashboard = client.get("/" + query)
        reports = client.get("/tracking/reports/" + query)
        assert dashboard.status_code == reports.status_code == 200
        incoming = next(group for group in dashboard.context["desk_direction_groups"] if group["key"] == "incoming")
        assert next(stage["count"] for stage in incoming["stages"] if stage["status"] == Status.PENDING_RECEIPT) == 1
        assert _stage_counts(reports.context["by_status"])[Status.PENDING_RECEIPT] == 1
        counts = {item["key"]: item["total"] for item in dashboard.context["breakdown"]["slices"]}
        assert counts["pending_receipt"] == 1
        assert counts["in_process"] == 0


@pytest.mark.django_db
def test_late_recipient_receipt_does_not_inherit_hr_work_in_reports(client, users, offices, partial_office_work):
    services.confirm_receipt(partial_office_work, user=users["med"])
    client.force_login(users["admin"])
    for code, expected in (("HR", Status.IN_PROCESS), ("MED", Status.RECEIVED), ("REC", Status.IN_PROCESS)):
        response = client.get(f"/tracking/reports/?office={offices[code].pk}")
        assert response.status_code == 200
        assert _stage_counts(response.context["by_status"]) == {expected: 1}
        assert response.context["total_records"] == 1


@pytest.mark.django_db
@pytest.mark.parametrize("stored_stage", [Status.RECEIVED, Status.IN_PROCESS])
def test_legacy_whole_record_stage_cannot_hide_pending_recipient_from_report(
    client, users, offices, partial_office_work, stored_stage,
):
    TrackingRecord.objects.filter(pk=partial_office_work.pk).update(status=stored_stage)
    client.force_login(users["admin"])
    for office in (offices["MED"].pk, "all"):
        report = client.get(f"/tracking/reports/?office={office}")
        assert report.status_code == 200
        assert _stage_counts(report.context["by_status"]) == {Status.PENDING_RECEIPT: 1}
        exported = client.get(f"/tracking/reports/export/?office={office}&record_status=PENDING_RECEIPT")
        assert len(_csv_records(exported)) == 1


@pytest.mark.django_db
def test_every_confirmed_recipient_owes_its_current_overdue_work(client, users, offices, partial_office_work):
    services.confirm_receipt(partial_office_work, user=users["med"])
    TrackingRecord.objects.filter(pk=partial_office_work.pk).update(due_at=timezone.now() - timedelta(days=2))
    client.force_login(users["admin"])
    for code in ("HR", "MED"):
        response = client.get(f"/tracking/reports/?office={offices[code].pk}")
        assert response.status_code == 200
        assert response.context["overdue"] == response.context["overdue_all"] == 1
        row = next(row for row in response.context["overdue_accountability"]["rows"] if row["code"] == code)
        assert row["holding"] == row["total"] == 1

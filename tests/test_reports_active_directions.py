"""Report cards and direction bars count current work, not completion history."""

import re

import pytest

from apps.documents.services import archive_tracking_record
from apps.tracking.models import Status, TrackingRecord
from apps.tracking.services import (
    complete_record,
    confirm_receipt,
    create_draft_record,
    mark_in_process,
    route_record,
)
from tests.test_filter_agreement import page_records


@pytest.fixture
def report_direction_work(users, offices, memo_type):
    made = {"incoming": {}, "outgoing": {}}
    for direction, creator, receiver, quantities in (
        ("incoming", "sup", "med", (2, 3, 5)),
        ("outgoing", "med", "hr", (4, 1, 2)),
    ):
        for status, quantity in zip((Status.PENDING_RECEIPT, Status.RECEIVED, Status.IN_PROCESS), quantities, strict=True):
            ids = set()
            for index in range(quantity):
                record = create_draft_record(user=users[creator], subject=f"{direction} {status} {index}",
                                             instructions="Review", document_type=memo_type)
                route_record(record, [users[receiver].office], user=users[creator])
                if status != Status.PENDING_RECEIPT:
                    confirm_receipt(record, user=users[receiver])
                if status == Status.IN_PROCESS:
                    mark_in_process(record, user=users[receiver])
                ids.add(record.pk)
            made[direction][status] = ids
    # Both completion stages must disappear from active totals and stage bars.
    for filed in (False, True):
        record = create_draft_record(user=users["med"], subject=f"Finished {filed}",
                                     instructions="Review", document_type=memo_type)
        route_record(record, [offices["SUP"]], user=users["med"])
        confirm_receipt(record, user=users["sup"])
        complete_record(record, user=users["sup"])
        if filed:
            archive_tracking_record(record, user=users["admin"])
    draft = create_draft_record(user=users["med"], subject="Unsent draft", instructions="Review", document_type=memo_type)
    made["draft"] = draft.pk
    # A record involving MED only in an older hop is neither current direction.
    earlier = create_draft_record(user=users["med"], subject="Already passed on", instructions="Review", document_type=memo_type)
    route_record(earlier, [offices["SUP"]], user=users["med"])
    confirm_receipt(earlier, user=users["sup"])
    route_record(earlier, [offices["HR"]], user=users["sup"], action="FORWARD")
    made["earlier"] = earlier.pk
    return made


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["admin", "med_admin", "med", "viewer"])
def test_reports_cards_and_direction_stages_open_exact_current_records(client, users, offices, report_direction_work, role):
    client.force_login(users[role])
    response = client.get("/tracking/reports/", {"office": offices["MED"].pk})
    context, body = response.context, response.content.decode()
    # Total also includes the draft and a visible record passed on by MED.
    expected_total = 19 if role in {"admin", "med"} else 18
    assert context["total_records"] == expected_total
    listed = page_records(client, context["report_workspace_url"])
    assert len(listed) == expected_total
    assert not TrackingRecord.objects.filter(pk__in=listed, status__in=[Status.COMPLETED, Status.COMPLETED_PENDING_UPLOAD]).exists()
    assert "office_activity" not in context
    assert "received and sent</h2>" not in body
    stages = context["direction_stages"]
    assert [(row["key"], row["total"]) for row in stages] == [("incoming", 10), ("outgoing", 7)]
    for direction in stages:
        expected = report_direction_work[direction["key"]]
        assert page_records(client, direction["workspace_url"]) == set().union(*expected.values())
        assert [row["status"] for row in direction["stages"]] == [Status.PENDING_RECEIPT, Status.RECEIVED, Status.IN_PROCESS]
        for row in direction["stages"]:
            assert row["total"] == len(expected[row["status"]])
            assert page_records(client, row["workspace_url"]) == expected[row["status"]]
    incoming = stages[0]
    assert [row["percent"] for row in incoming["stages"]] == [20, 30, 50]
    metrics = re.search(r'<div class="report-metrics[^\"]*">(.*?)<div class="row g-4">', body, re.S).group(1)
    assert re.findall(r'<div class="label">(.*?)</div>', metrics) == ["Total", "Incoming", "Outgoing"]
    chart = re.search(r'<section[^>]*id="report-document-stages".*?</section>', body, re.S).group()
    assert ">Completed</span>" not in chart and ">Completed - Pending Filing</span>" not in chart
    assert ">Draft</span>" not in chart
    assert "report-direction-bar" in chart
    assert "var(--status-process)" in chart


@pytest.mark.django_db
@pytest.mark.parametrize("params", [{}, {"office": "all"}])
def test_all_offices_reports_show_one_chart_with_exact_stage_destinations(client, users, report_direction_work, params):
    client.force_login(users["admin"])
    response = client.get("/tracking/reports/", params)
    assert response.context["total_records"] == 19
    assert response.context["direction_stages"] == []
    assert response.context["incoming_records"] is None
    assert response.context["outgoing_records"] is None
    stages = response.context["all_office_stages"]
    assert [(row["status"], row["total"]) for row in stages] == [
        (Status.DRAFT, 1), (Status.PENDING_RECEIPT, 7), (Status.RECEIVED, 4), (Status.IN_PROCESS, 7),
    ]
    assert sum(row["total"] for row in stages) == 19
    expected = {
        Status.DRAFT: {report_direction_work["draft"]},
        Status.PENDING_RECEIPT: (report_direction_work["incoming"][Status.PENDING_RECEIPT]
                                | report_direction_work["outgoing"][Status.PENDING_RECEIPT]
                                | {report_direction_work["earlier"]}),
        Status.RECEIVED: (report_direction_work["incoming"][Status.RECEIVED]
                         | report_direction_work["outgoing"][Status.RECEIVED]),
        Status.IN_PROCESS: (report_direction_work["incoming"][Status.IN_PROCESS]
                           | report_direction_work["outgoing"][Status.IN_PROCESS]),
    }
    for stage in stages:
        assert page_records(client, stage["workspace_url"]) == expected[stage["status"]]
    assert page_records(client, response.context["report_workspace_url"]) == set().union(*expected.values())
    body = response.content.decode()
    assert 'id="report-all-offices-chart"' in body
    assert 'id="report-direction-incoming"' not in body
    assert 'id="report-direction-outgoing"' not in body
    assert '<div class="label">Incoming</div>' not in body
    assert '<div class="label">Outgoing</div>' not in body
    assert "Choose an office" not in body


@pytest.mark.django_db
def test_all_offices_chart_counts_a_multi_recipient_document_once(client, users, offices, memo_type):
    record = create_draft_record(user=users["med"], subject="Shared across offices", instructions="Review",
                                 document_type=memo_type)
    route_record(record, [offices["SUP"], offices["HR"]], user=users["med"])
    client.force_login(users["admin"])
    response = client.get("/tracking/reports/", {"office": "all"})
    stages = {row["status"]: row for row in response.context["all_office_stages"]}
    assert response.context["total_records"] == 1
    assert stages[Status.PENDING_RECEIPT]["total"] == 1
    assert page_records(client, stages[Status.PENDING_RECEIPT]["workspace_url"]) == {record.pk}


@pytest.mark.django_db
def test_empty_all_offices_chart_is_readable(client, users):
    client.force_login(users["admin"])
    response = client.get("/tracking/reports/", {"office": "all"})
    assert response.context["total_records"] == 0
    stages = response.context["all_office_stages"]
    assert len(stages) == 4
    assert all(row["total"] == 0 and row["share"] == "0%" for row in stages)
    assert "No current documents across all offices." in response.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["med_admin", "med", "viewer"])
@pytest.mark.parametrize("office", ["all", "HR"])
def test_non_system_admin_cannot_switch_to_the_combined_chart(client, users, offices, role, office):
    client.force_login(users[role])
    response = client.get("/tracking/reports/", {"office": "all" if office == "all" else offices[office].pk})
    assert response.context["university_wide"] is False
    assert response.context["scope_office"] == offices["MED"]
    assert not response.context.get("all_office_stages")
    assert 'id="report-all-offices-chart"' not in response.content.decode()
    assert 'id="report-direction-incoming"' in response.content.decode()


@pytest.mark.django_db
def test_empty_direction_bars_have_three_zero_stages(client, users, offices):
    client.force_login(users["admin"])
    response = client.get("/tracking/reports/", {"office": offices["MED"].pk})
    assert response.context["total_records"] == 0
    for direction in response.context["direction_stages"]:
        assert direction["total"] == 0
        assert [row["total"] for row in direction["stages"]] == [0, 0, 0]
        assert [row["percent"] for row in direction["stages"]] == [0, 0, 0]

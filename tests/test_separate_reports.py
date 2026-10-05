"""Reports show statistics; record links open the existing scoped workspaces."""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core import analytics
from apps.documents.models import Document
from apps.documents.services import archive_tracking_record
from apps.tracking.models import ACTIVE_STATUSES, Status, TrackingRecord
from apps.tracking.services import (
    complete_record,
    confirm_receipt,
    create_draft_record,
    route_record,
)
from tests.test_turnaround_period import two_months  # noqa: F401


@pytest.fixture
def report_data(users, offices, memo_type):
    for office in ("MED", "SUP", "HR"):
        record = create_draft_record(user=users[office.lower()], subject=f"{office} report document", instructions="Review", document_type=memo_type)
        route_record(record, [offices["SUP" if office == "MED" else "MED"]], user=users[office.lower()])
        Document.objects.create(title=f"{office} filed document", office=offices[office], document_type=memo_type,
                                uploaded_by=users[office.lower()], year=timezone.localdate().year)


@pytest.mark.django_db
@pytest.mark.parametrize("who", ["admin", "med_admin", "sup", "viewer"])
@pytest.mark.parametrize("office", ["all", "MED", "HR"])
def test_dashboard_total_opens_the_same_scoped_tracking_list(client, users, offices, report_data, who, office):
    client.force_login(users[who])
    chosen = office if office == "all" else offices[office].pk
    context = client.get("/", {"office": chosen}).context
    listed = client.get(context["tracking_total_url"]).context["page_obj"]
    assert context["tracking_total_count"] == listed.paginator.count
    assert set(listed.object_list.values_list("pk", flat=True)) <= set(TrackingRecord.objects.visible_to(users[who]).values_list("pk", flat=True))


@pytest.mark.django_db
@pytest.mark.parametrize("who", ["admin", "med_admin", "sup", "viewer"])
@pytest.mark.parametrize("domain", ["tracking", "documents"])
def test_reports_show_statistics_and_open_their_own_workspace(client, users, offices, report_data, who, domain):
    client.force_login(users[who])
    response = client.get(f"/{domain}/reports/", {"office": offices["MED"].pk})
    assert response.status_code == 200
    context, body = response.context, response.content.decode()
    other = "tracking" if domain == "documents" else "documents"
    assert f'data-report-panel="{domain}"' in body
    assert f'data-report-panel="{other}"' not in body
    assert 'id="report-records"' not in body
    assert "#report-records" not in body
    assert "page_obj" not in context
    assert "report_records_count" not in context
    assert "Export records CSV" not in body
    for name in ("record_q", "record_status", "record_type", "record_month", "record_overdue"):
        assert f'name="{name}"' not in body
    target = context["report_workspace_url"]
    assert target.split("?", 1)[0] == f"/{domain}/"
    listed = client.get(target).context
    assert listed["total"] == context["total_documents" if domain == "documents" else "total_records"]
    model = Document if domain == "documents" else TrackingRecord
    assert {row.pk for row in listed["page_obj"]} <= set(model.objects.visible_to(users[who]).values_list("pk", flat=True))


@pytest.mark.django_db
@pytest.mark.parametrize("who", ["admin", "med_admin", "sup", "viewer"])
@pytest.mark.parametrize("office", ["all", "MED", "HR"])
def test_live_stage_links_open_tracking_with_the_same_count(client, users, offices, report_data, who, office):
    client.force_login(users[who])
    response = client.get("/tracking/reports/", {"office": office if office == "all" else offices[office].pk})
    for row in response.context["by_status"]:
        if row["status"] not in ACTIVE_STATUSES:
            assert row["workspace_url"] is None
            continue
        selected = client.get(row["workspace_url"])
        assert selected.status_code == 200
        assert selected.context["total"] == row["total"]
        assert all(record.status == row["status"] for record in selected.context["page_obj"])


@pytest.mark.django_db
def test_repository_month_statistics_still_match_the_scoped_documents(client, users, offices, report_data):
    client.force_login(users["admin"])
    query = {"office": offices["MED"].pk}
    report = client.get("/documents/reports/", query)
    visible = Document.objects.visible_to(users["admin"]).filter(is_active=True, office=offices["MED"])
    for row in report.context["document_months"]["rows"]:
        assert visible.filter(created_at__year=row["month"].year, created_at__month=row["month"].month).count() == row["total"]


@pytest.mark.django_db
@pytest.mark.parametrize("who", ["admin", "med_admin", "sup", "viewer"])
def test_repository_type_links_open_the_same_scoped_documents(client, users, report_data, who):
    client.force_login(users[who])
    report = client.get("/documents/reports/")
    for row in report.context["document_types"]:
        if not row.get("document_type_id"):
            assert row["workspace_url"] is None
            continue
        selected = client.get(row["workspace_url"])
        assert selected.context["total"] == row["total"]
        assert all(document.document_type_id == row["document_type_id"] for document in selected.context["documents"])


@pytest.mark.django_db
def test_filed_history_stays_in_the_report_without_a_misleading_live_link(client, users, report_data):
    record = TrackingRecord.objects.get(subject="MED report document")
    confirm_receipt(record, user=users["sup"])
    record.refresh_from_db()
    complete_record(record, user=users["sup"])
    record.refresh_from_db()
    archive_tracking_record(record, user=users["admin"])
    create_draft_record(user=users["admin"], subject="Draft for this report", instructions="Review")
    client.force_login(users["admin"])
    report = client.get("/tracking/reports/").context
    listed = client.get(report["report_workspace_url"]).context
    assert report["total_records"] == listed["total"] + 1
    completed = next(row for row in report["by_status"] if row["status"] == Status.COMPLETED)
    assert completed["total"] == 1
    assert completed["workspace_url"] is None
    draft = next(row for row in report["by_status"] if row["status"] == Status.DRAFT)
    assert client.get(draft["workspace_url"]).context["total"] == draft["total"] == 1


@pytest.mark.django_db
@pytest.mark.parametrize("domain", ["tracking", "documents"])
def test_old_list_filters_do_not_change_report_statistics(client, users, report_data, domain):
    client.force_login(users["admin"])
    before = client.get(f"/{domain}/reports/").context
    after = client.get(f"/{domain}/reports/", {
        "record_status": "PENDING_RECEIPT", "record_type": "none", "record_q": "missing",
        "record_month": "2000-01", "record_overdue": "yes", "page": "999",
    }).context
    for key in ("total_records", "total_documents", "pending_receipt", "overdue_all", "completed_records"):
        assert before[key] == after[key]
    assert "page_obj" not in after


@pytest.mark.django_db
def test_year_picker_includes_years_without_new_records(client, users, memo_type):
    current = timezone.localdate().year
    record = TrackingRecord.objects.create(subject="Completed in a later year", originating_office=users["med"].office,
                                           created_by=users["med"], document_type=memo_type)
    TrackingRecord.objects.filter(pk=record.pk).update(created_at=timezone.now().replace(year=current - 2))
    client.force_login(users["admin"])
    response = client.get("/tracking/reports/", {"trend_year": current - 1})
    assert response.context["trend_year"] == current - 1
    assert {row["month"].year for row in response.context["turnaround_trend"]["rows"]} == {current - 1}


@pytest.mark.django_db
def test_daily_samples_reconcile_with_monthly_summary_and_annual_chart(client, users, two_months):  # noqa: F811
    client.force_login(users["admin"])
    month = two_months["month"]
    response = client.get("/", {"month": month.strftime("%Y-%m")})
    daily = response.context["turnaround_trend"]
    summary = response.context["turnaround"]
    assert all(row["month"].replace(day=1) == month for row in daily["rows"])
    for stage in summary["stages"]:
        assert sum(row[f'{stage["key"]}_samples'] for row in daily["rows"]) == stage["samples"]
    annual = client.get("/tracking/reports/", {"trend_year": month.year}).context["turnaround_trend"]
    assert len(annual["rows"]) == 12
    assert {row["month"].year for row in annual["rows"]} == {month.year}
    selected = next(row for row in annual["rows"] if row["month"] == month)
    assert selected["lifetime_samples"] == summary["lifetime_samples"]
    assert selected["lifetime_label"] == summary["lifetime"]


@pytest.mark.django_db
def test_daily_chart_does_not_include_another_month(users, memo_type):
    month = (timezone.localdate().replace(day=1) - timedelta(days=1)).replace(day=1)
    record = TrackingRecord.objects.create(subject="Outside selected month", originating_office=users["med"].office,
                                           created_by=users["med"], status=Status.COMPLETED_PENDING_UPLOAD,
                                           completed_at=timezone.now(), document_type=memo_type)
    chart = analytics.turnaround_by_day(TrackingRecord.objects.filter(pk=record.pk), month)
    assert not chart["has_data"]
    assert all(row["lifetime"] is None for row in chart["rows"])

"""Every report drilldown is a subset of its scoped, counted population."""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core import analytics
from apps.documents.models import Document
from apps.tracking.models import Status, TrackingRecord
from apps.tracking.services import create_draft_record, route_record
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
def test_reports_have_only_their_own_charts_and_matching_records(client, users, offices, report_data, who, domain):
    client.force_login(users[who])
    response = client.get(f"/{domain}/reports/", {"office": offices["MED"].pk})
    assert response.status_code == 200
    context, body = response.context, response.content.decode()
    total = context["total_documents" if domain == "documents" else "total_records"]
    assert context["report_records_count"] == total
    other = "tracking" if domain == "documents" else "documents"
    assert f'data-report-panel="{domain}"' in body
    assert f'data-report-panel="{other}"' not in body
    assert 'id="report-records"' in body
    assert 'href="#report-records">View report records</a>' in body
    assert 'per_page=10#report-records' in body
    model = Document if domain == "documents" else TrackingRecord
    assert {row.pk for row in context["page_obj"]} <= set(model.objects.visible_to(users[who]).values_list("pk", flat=True))


@pytest.mark.django_db
def test_stage_drilldown_matches_its_chart_count(client, users, report_data):
    client.force_login(users["admin"])
    response = client.get("/tracking/reports/")
    for row in response.context["by_status"]:
        selected = client.get("/tracking/reports/", {"record_status": row["status"]})
        assert selected.context["report_records_count"] == row["total"]
        assert all(record.status == row["status"] for record in selected.context["page_obj"])


@pytest.mark.django_db
def test_repository_month_drilldown_matches_its_chart_count(client, users, offices, report_data):
    client.force_login(users["admin"])
    query = {"office": offices["MED"].pk}
    report = client.get("/documents/reports/", query)
    for row in report.context["document_months"]["rows"]:
        selected = client.get("/documents/reports/", {**query, "record_month": row["month"].strftime("%Y-%m")})
        assert selected.context["report_records_count"] == row["total"]


@pytest.mark.django_db
def test_repository_type_drilldown_matches_its_chart_count(client, users, report_data):
    client.force_login(users["admin"])
    report = client.get("/documents/reports/")
    for row in report.context["document_types"]:
        selected = client.get("/documents/reports/", {"record_type": row["type_value"]})
        assert selected.context["report_records_count"] == row["total"]


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

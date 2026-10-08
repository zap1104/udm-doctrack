"""Report breakdowns must retain diagnostic rows and agree with their totals."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.models import DocumentType
from apps.core.views import ReportsView
from apps.documents.models import Document
from apps.tracking.models import TrackingRecord
from apps.tracking.services import create_draft_record


@pytest.mark.django_db
@pytest.mark.parametrize("unclassified_count", [1, 30])
def test_unclassified_documents_remain_separate_from_type_remainder(users, offices, unclassified_count):
    """Unclassified is actionable even when it falls below the type cutoff."""
    for index in range(13):
        kind = DocumentType.objects.create(code=f"RECON{index:02}", name=f"Type {index:02}")
        for copy in range(2):
            Document.objects.create(
                title=f"Typed {index} {copy}", office=offices["REC"],
                document_type=kind, uploaded_by=users["admin"],
            )
    for index in range(unclassified_count):
        Document.objects.create(
            title=f"Unclassified {index}", office=offices["REC"], uploaded_by=users["admin"],
        )

    rows = ReportsView()._document_types(Document.objects.all())

    unclassified = [row for row in rows if row["label"] == "Unclassified"]
    assert len(unclassified) == 1
    assert unclassified[0]["total"] == unclassified_count
    assert sum(row["total"] for row in rows) == 26 + unclassified_count
    remainder = next(row for row in rows if row.get("is_remainder"))
    assert None not in remainder["type_ids"]


@pytest.mark.django_db
def test_overdue_records_without_accountable_offices_do_not_show_all_clear(client, users):
    """An unattributed overdue record must not appear beside 'Nothing is overdue'."""
    record = create_draft_record(user=users["med"], subject="Unattributed deadline", instructions="")
    TrackingRecord.objects.filter(pk=record.pk).update(due_at=timezone.now() - timedelta(days=1))
    client.force_login(users["admin"])

    response = client.get("/tracking/reports/")

    assert response.context["overdue_accountability"]["whole"] == 1
    assert response.context["overdue_accountability"]["rows"] == []
    assert response.context["overdue_accountability"]["unattributed"] == 1
    assert "Nothing is overdue" not in response.content.decode()


@pytest.mark.django_db
def test_report_still_shows_all_clear_when_no_records_are_overdue(client, users):
    client.force_login(users["admin"])

    response = client.get("/tracking/reports/")

    assert response.context["overdue_accountability"]["whole"] == 0
    assert "Nothing is overdue" in response.content.decode()

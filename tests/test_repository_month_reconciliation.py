"""Every visible repository document belongs to exactly one chart bucket."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.documents.models import Document, Source


@pytest.mark.django_db
@pytest.mark.parametrize("scope,expected", [("all", 6), ("MED", 5)])
def test_repository_months_reconcile_older_current_and_future_documents(client, users, offices, scope, expected):
    now = timezone.now()
    for index, (offset, source, office, active) in enumerate([
        (-500, Source.DTS, "MED", True),
        (-500, Source.SCAN, "MED", True),
        (0, Source.DTS, "MED", True),
        (0, Source.UPLOAD, "MED", True),
        (500, Source.UPLOAD, "MED", True),
        (-500, Source.UPLOAD, "SUP", True),
        (-500, Source.UPLOAD, "MED", False),
    ]):
        document = Document.objects.create(title=f"Chart reconciliation {index}", office=offices[office],
                                           source=source, is_active=active)
        Document.objects.filter(pk=document.pk).update(created_at=now + timedelta(days=offset))
    client.force_login(users["admin"])

    response = client.get("/documents/reports/", {"office": "all" if scope == "all" else offices[scope].pk})

    assert response.status_code == 200
    chart = response.context["document_months"]
    assert response.context["total_documents"] == expected
    assert sum(row["total"] for row in chart["rows"]) == expected
    assert chart["total"] == expected
    assert chart["window_total"] == 2
    assert chart["earlier_total"] == expected - 3
    assert chart["later_total"] == 1
    assert sum(row["completed"] for row in chart["rows"]) == 2
    assert all(row["completed"] + row["historical"] == row["total"] for row in chart["rows"])
    assert "Earlier" in response.content.decode()
    assert "Later dates" in response.content.decode()

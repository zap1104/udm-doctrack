"""Time ties must not leave pagination order up to the database."""

import pytest
from django.utils import timezone

from apps.core.models import AuditLog, Notification


@pytest.mark.django_db
@pytest.mark.parametrize("surface", ["notifications", "audit"])
def test_event_pages_partition_tied_timestamps(client, users, offices, surface):
    if surface == "notifications":
        model = Notification
        rows = model.objects.bulk_create([
            Notification(office=offices["MED"], kind=Notification.Kind.SHARED,
                         title=f"Tie {index}", message="Pagination audit") for index in range(31)
        ])
        user, url, key, extra = users["med"], "/notifications/", "notifications", {}
    else:
        model = AuditLog
        rows = model.objects.bulk_create([
            AuditLog(action=AuditLog.Action.UPDATE, summary=f"Pagination audit {index}") for index in range(31)
        ])
        user, url, key, extra = users["admin"], "/administration/audit-log/", "page_obj", {"q": "Pagination audit"}
    model.objects.filter(pk__in=[row.pk for row in rows]).update(created_at=timezone.now())
    client.force_login(user)
    seen = []
    for page in range(1, 5):
        response = client.get(url, {**extra, "per_page": 10, "page": page})
        assert response.status_code == 200
        seen.extend(row.pk for row in response.context[key])
    assert seen == sorted((row.pk for row in rows), reverse=True)
    assert len(set(seen)) == 31

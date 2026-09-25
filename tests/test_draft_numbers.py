"""A draft never shows, prints, logs or exports its placeholder number.

A draft's number is issued when it is sent, so an abandoned draft leaves no gap
in the office's series. Until then it carries a `DRAFT-…` placeholder, which is
not a number — and anywhere it leaked, somebody could quote it as one.
"""

from __future__ import annotations

import pytest

from apps.core.models import AuditLog
from apps.tracking.models import RecordActivity
from apps.tracking.services import create_draft_record, log_view, route_record

PLACEHOLDER = "DRAFT-"


@pytest.fixture
def draft(users, memo_type):
    return create_draft_record(
        user=users["med"], subject="Still a draft", instructions="x", document_type=memo_type,
    )


@pytest.mark.django_db
def test_creating_a_draft_logs_its_subject_not_its_placeholder(draft):
    entry = AuditLog.objects.filter(target_id=str(draft.pk)).latest("pk")

    assert draft.tracking_number.startswith(PLACEHOLDER), "the fixture is a real draft"
    assert PLACEHOLDER not in entry.summary
    assert "Still a draft" in entry.summary


@pytest.mark.django_db
def test_the_audit_log_names_a_draft_without_its_placeholder(client, users, draft):
    log_view(draft, user=users["med"])
    client.force_login(users["admin"])
    body = client.get("/administration/audit-log/").content.decode()

    assert PLACEHOLDER not in body
    assert "Not yet assigned" in body


@pytest.mark.django_db
def test_a_draft_exports_without_its_placeholder(client, users, draft):
    client.force_login(users["med"])
    body = client.get("/reports/export/").content.decode()

    assert "Still a draft" in body
    assert PLACEHOLDER not in body


@pytest.mark.django_db
def test_a_draft_has_no_routing_slip_and_prints_nothing(client, users, draft):
    client.force_login(users["med"])
    detail = client.get(draft.get_absolute_url()).content.decode()
    response = client.get(f"/tracking/{draft.pk}/slip/")

    assert "Print routing slip" not in detail
    assert response.status_code == 302 and response["Location"] == draft.get_absolute_url()
    assert not AuditLog.objects.filter(action=AuditLog.Action.PRINT).exists()
    assert not draft.activities.filter(event=RecordActivity.Event.PRINTED).exists()


@pytest.mark.django_db
def test_once_sent_the_slip_prints_the_real_number(client, users, offices, draft):
    route_record(draft, [offices["SUP"]], user=users["med"])
    draft.refresh_from_db()
    client.force_login(users["med"])
    response = client.get(f"/tracking/{draft.pk}/slip/")

    assert response.status_code == 200
    assert draft.tracking_number in response.content.decode()
    printed = AuditLog.objects.get(action=AuditLog.Action.PRINT)
    assert draft.tracking_number in printed.summary and PLACEHOLDER not in printed.summary


@pytest.mark.django_db
def test_a_draft_reads_as_not_yet_assigned_wherever_it_is_named(draft):
    assert str(draft).startswith("Not yet assigned")

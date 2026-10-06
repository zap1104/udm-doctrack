"""Audit office filters use the office recorded when an event happened."""

import pytest

from apps.core.models import AuditLog
from apps.core.utils import log_action

pytestmark = pytest.mark.django_db

AUDIT_URL = "/administration/audit-log/"


def entries_for_office(client, office):
    response = client.get(AUDIT_URL, {"office": office.pk})
    assert response.status_code == 200
    return {entry.pk for entry in response.context["entries"]}


def test_audit_office_filter_preserves_attribution_after_actor_transfer(client, users, offices):
    actor = users["med"]
    entry = log_action(AuditLog.Action.UPDATE, "Work before office transfer", actor=actor)
    actor.office = offices["SUP"]
    actor.save(update_fields=["office"])
    client.force_login(users["admin"])

    assert entry.pk in entries_for_office(client, offices["MED"])
    assert entry.pk not in entries_for_office(client, offices["SUP"])


def test_audit_office_filter_preserves_attribution_after_actor_deletion(client, users, offices):
    actor = users["med"]
    entry = log_action(AuditLog.Action.UPDATE, "Work before account deletion", actor=actor)
    actor.delete()
    client.force_login(users["admin"])

    assert entry.pk in entries_for_office(client, offices["MED"])


def test_audit_office_filter_leaves_unknown_legacy_attribution_unassigned(client, users, offices):
    entry = AuditLog.objects.create(
        actor=users["med"], actor_label="Old MED account", action=AuditLog.Action.UPDATE,
        summary="Legacy event without an office snapshot",
    )
    client.force_login(users["admin"])

    assert entry.pk not in entries_for_office(client, offices["MED"])
    assert entry.pk in {entry.pk for entry in client.get(AUDIT_URL).context["entries"]}


def test_office_admin_audit_scope_cannot_be_changed_by_office_filter(client, users, offices):
    own_entry = log_action(AuditLog.Action.UPDATE, "Own office work", actor=users["med"])
    other_entry = log_action(AuditLog.Action.UPDATE, "Other office work", actor=users["sup"])
    client.force_login(users["med_admin"])

    entries = entries_for_office(client, offices["SUP"])
    assert own_entry.pk in entries
    assert other_entry.pk not in entries

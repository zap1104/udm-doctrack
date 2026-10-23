"""Defense audit: preserve filed ownership and refuse ambiguous scope filters."""

import pytest
from django.http import QueryDict
from django.urls import reverse

from apps.accounts.models import User
from apps.documents.models import Document
from apps.tracking import external_services
from apps.tracking.models import ExternalTrackingRecord, Status

pytestmark = pytest.mark.django_db


def test_unsaved_external_case_cannot_inherit_an_unrelated_repository_office(users, offices):
    Document.objects.create(title="Unrelated repository entry", office=offices["HR"], year=2026)
    record = ExternalTrackingRecord(managing_office=offices["MED"], created_by=users["med_admin"])
    assert record.responsible_office_id == offices["MED"].pk


def test_existing_filing_owner_can_access_history_without_a_routing_participation(users, offices):
    owner = User.objects.create_user(username="legacy_filing_admin", office=offices["HR"], role="ADMIN")
    record = external_services.create_record(user=users["med_admin"], title="Legacy filed ownership")
    record.status = Status.COMPLETED
    record.save(update_fields=["status"])
    Document.objects.create(
        title=record.title, collection="EXTERNAL", office=offices["HR"], year=2026,
        external_tracking_record=record, uploaded_by=users["admin"],
    )
    assert record.responsible_office_id == offices["HR"].pk
    assert record.can_manage(owner)
    assert type(record).objects.visible_to(owner).filter(pk=record.pk).exists()
    external_services.annotate_record(
        record, user=owner, expected_revision=record.revision, message="Preserve the original filing owner"
    )
    assert record.activities.filter(event="ANNOTATION", actor=owner).exists()
    assert not record.can_manage(users["med_admin"])


def test_explicit_filing_office_scope_matches_list_report_and_export(client, users, offices):
    owner = User.objects.create_user(username="legacy_report_admin", office=offices["HR"], role="ADMIN")
    record = external_services.create_record(user=users["med_admin"], title="Legacy filing scope parity")
    record.status = Status.COMPLETED
    record.tracking_number = "UDM-EXT-MED-2026-01-0700"
    record.save(update_fields=["status", "tracking_number"])
    Document.objects.create(title=record.title, collection="EXTERNAL", office=offices["HR"], year=2026,
                            external_tracking_record=record)
    client.force_login(owner)
    for route in ("tracking:external_list", "tracking:external_reports"):
        response = client.get(reverse(route), {"office": offices["HR"].pk, "status": Status.COMPLETED})
        assert response.status_code == 200
        assert response.context["total_count"] == 1
        assert [item.pk for item in response.context["page_obj"]] == [record.pk]
    response = client.get(reverse("tracking:external_export"), {
        "office": offices["HR"].pk, "source": "reports", "status": Status.COMPLETED,
    })
    assert response.status_code == 200
    import csv
    import io
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert len(rows) == 2 and rows[1][0] == record.tracking_number


@pytest.mark.parametrize("field", ["office", "external_office", "q", "status", "direction", "overdue"])
def test_repeated_external_filters_return_explicit_error_and_no_rows(client, users, offices, field):
    external_services.create_record(user=users["med_admin"], title="Scope guard record")
    values = {
        "office": [str(offices["MED"].pk)] * 2,
        "external_office": ["1", "2"],
        "q": ["Scope", "guard"],
        "status": [Status.DRAFT, Status.RECEIVED],
        "direction": ["incoming", "outgoing"],
        "overdue": ["yes", "no"],
    }
    params = QueryDict(mutable=True)
    params.setlist(field, values[field])
    client.force_login(users["med_admin"])
    response = client.get(reverse("tracking:external_list"), params)
    assert response.status_code == 400
    assert response.context["filter_errors"]
    assert response.context["total_count"] == 0


@pytest.mark.django_db(transaction=True)
def test_opposite_external_endpoint_corrections_do_not_deadlock(users, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, local

    from django.db import DatabaseError, connections

    entries = [external_services.create_external_office(user=users["med_admin"], name=f"Lock office {n}")
               for n in range(4)]
    actors = [users["med_admin"], users["sup_admin"]]
    cases = []
    for actor in actors:
        record = external_services.create_record(user=actor, title="Independent correction", from_external_office=entries[2])
        external_services.route_record(record, user=actor, expected_revision=record.revision, to_external_office=entries[3])
        cases.append(record)
    first_locked = Barrier(2)
    state = local()
    old_external = external_services._external

    def lock_then_synchronize(*args, **kwargs):
        result = old_external(*args, **kwargs)
        state.calls = getattr(state, "calls", 0) + 1
        if state.calls == 1:
            first_locked.wait(timeout=15)
        return result

    # Force the formerly unsafe per-endpoint path into opposite lock ordering.
    # Batch locking must bypass that path and use a consistent database order.
    monkeypatch.setattr(external_services, "_external", lock_then_synchronize)

    def attempt(index):
        try:
            candidate = ExternalTrackingRecord.objects.get(pk=cases[index].pk)
            source, target = entries[:2] if index == 0 else entries[1::-1]
            external_services.correct_step(candidate, step=candidate.current_step, user=actors[index],
                expected_revision=candidate.revision, reason="Correct paper endpoints",
                changes={"from_external_office": source, "to_external_office": target})
            return "saved"
        except DatabaseError as exc:
            return getattr(exc.__cause__, "sqlstate", type(exc).__name__)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, range(2)))
    assert outcomes == ["saved", "saved"]
    for index, record in enumerate(cases):
        step = record.routing_steps.get()
        assert (step.from_external_office_id, step.to_external_office_id) == (
            entries[index].pk, entries[1 - index].pk,
        )
        assert record.revisions.count() == 1

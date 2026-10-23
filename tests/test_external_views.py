"""HTTP boundaries for the separate External tracking workspace."""

import csv
import io
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from tests.upload_helpers import follow_upload_recovery

pytestmark = pytest.mark.django_db


def test_external_workspace_has_its_own_route(client, users):
    client.force_login(users["med_admin"])
    assert client.get("/tracking/external/").status_code == 200


@pytest.mark.parametrize(
    "route",
    [
        "tracking:external_list",
        "tracking:external_reports",
        "documents:external_repository",
        "documents:external_reports",
    ],
)
def test_external_filters_use_existing_in_place_updates(client, users, route):
    client.force_login(users["med_admin"])
    url = reverse(route)
    response = client.get(url, {"q": "Paper reference"}, HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    content = response.content.decode()
    assert "data-auto-filter" in content
    assert f'hx-get="{url}"' in content
    assert 'hx-target="#main-content"' in content
    assert 'value="Paper reference"' in content
    assert "data-filter-reset" in content
    assert ">Apply filters</button>" not in content


@pytest.fixture
def external_cases(users, offices):
    from apps.tracking.external_services import create_external_office, create_record

    med = create_external_office(user=users["med_admin"], name="College of Science", code="SCI")
    sup = create_external_office(user=users["sup_admin"], name="College of Nursing", code="NUR")
    own = create_record(user=users["med_admin"], title="MED external draft", from_external_office=med)
    other = create_record(user=users["sup_admin"], title="SUP private draft", from_external_office=sup)
    return med, sup, own, other


@pytest.mark.parametrize("role", ["med", "viewer", "med_admin", "admin"])
def test_external_list_does_not_disclose_another_offices_draft(client, users, external_cases, role):
    client.force_login(users[role])
    response = client.get(reverse("tracking:external_list"))
    assert response.status_code == 200
    if role != "admin":
        assert b"SUP private draft" not in response.content


@pytest.mark.parametrize(
    "filters",
    [
        {"office": "invalid"},
        {"office": "99999999"},
        {"external_office": "99999999"},
        {"status": "invented"},
        {"direction": "invented"},
    ],
)
def test_external_invalid_filters_never_widen_results(client, users, external_cases, filters):
    client.force_login(users["med_admin"])
    response = client.get(reverse("tracking:external_list"), filters)
    assert response.status_code == 400
    assert b"MED external draft" not in response.content
    assert b"SUP private draft" not in response.content


@pytest.mark.parametrize("role", ["med", "viewer"])
def test_only_admins_create_directory_entries_and_external_cases(client, users, role):
    client.force_login(users[role])
    assert client.post(reverse("tracking:external_office_create"), {"name": "Forged"}).status_code == 403
    assert client.post(reverse("tracking:external_create"), {"title": "Forged"}).status_code == 403


def test_forged_directory_id_is_not_editable(client, users, external_cases):
    _own, other, _record, _foreign = external_cases
    client.force_login(users["med_admin"])
    response = client.post(
        reverse("tracking:external_office_edit", args=[other.pk]), {"name": "Taken", "revision": other.revision}
    )
    assert response.status_code == 404
    other.refresh_from_db()
    assert other.name == "College of Nursing"


def test_viewer_cannot_write_even_with_a_valid_visible_record(client, users, external_cases):
    from apps.tracking.external_services import route_record

    _own, _other, record, _foreign = external_cases
    route_record(
        record, user=users["med_admin"], expected_revision=record.revision, to_office=users["med"].office, action="SEND"
    )
    record.refresh_from_db()
    client.force_login(users["viewer"])
    assert client.get(reverse("tracking:external_detail", args=[record.pk])).status_code == 200
    response = client.post(
        reverse("tracking:external_action", args=[record.pk, "remark"]),
        {"revision": record.revision, "message": "Forged viewer note"},
    )
    assert response.status_code == 403


def test_review_and_writes_require_revision(client, users, external_cases):
    _own, _other, record, _foreign = external_cases
    client.force_login(users["med_admin"])
    response = client.get(reverse("tracking:external_review", args=[record.pk]))
    assert response.status_code == 200
    assert f'name="revision" value="{record.revision}"'.encode() in response.content
    response = client.post(
        reverse("tracking:external_action", args=[record.pk, "remark"]), {"message": "Missing revision"}
    )
    assert response.status_code == 400


def test_external_csv_neutralizes_spreadsheet_formula_cells(client, users, external_cases):
    _own, _other, record, _foreign = external_cases
    record.title = '=HYPERLINK("evil")'
    record.save(update_fields=["title"])
    client.force_login(users["med_admin"])
    response = client.get(reverse("tracking:external_export"))
    assert response.status_code == 200
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert any('\'=HYPERLINK("evil")' in row for row in rows)
    assert not any("SUP private draft" in row for row in rows)


def test_create_preserves_multiple_types_and_requires_selected_main(client, users, offices, memo_type):
    from apps.core.models import DocumentType
    from apps.tracking.external_models import ExternalTrackingRecord

    second = DocumentType.objects.create(code="LETTER-EXT", name="Letter for external packet", retention_years=3)
    client.force_login(users["med_admin"])
    data = {"title": "Mixed packet", "from_office": offices["MED"].pk, "document_types": [memo_type.pk, second.pk]}
    response = client.post(reverse("tracking:external_create"), data)
    response = follow_upload_recovery(client, response)
    assert response.status_code == 200
    assert not ExternalTrackingRecord.objects.filter(title="Mixed packet").exists()
    data["document_type"] = second.pk
    response = client.post(reverse("tracking:external_create"), data)
    assert response.status_code == 302
    record = ExternalTrackingRecord.objects.get(title="Mixed packet")
    assert record.document_type == second
    assert set(record.document_types.values_list("pk", flat=True)) == {memo_type.pk, second.pk}


def test_internal_route_does_not_offer_manual_dispatch_time(client, users, offices):
    from apps.tracking.external_services import create_record

    record = create_record(user=users["med_admin"], title="Internal source")
    client.force_login(users["med_admin"])
    body = client.get(reverse("tracking:external_review", args=[record.pk])).content
    assert b'name="sent_at"' not in body


def test_mixed_internal_external_http_flow_preserves_separate_repository(client, users, offices, external_cases):
    from apps.documents.models import Document
    from apps.tracking.models import Status

    external, _other, record, _foreign = external_cases
    client.force_login(users["med_admin"])
    response = client.post(
        reverse("tracking:external_review", args=[record.pk]),
        {
            "revision": record.revision,
            "action": "SEND",
            "to_office": offices["MED"].pk,
            "sent_at": (timezone.now() - timedelta(hours=1)).isoformat(),
        },
    )
    assert response.status_code == 302
    record.refresh_from_db()
    assert record.status == Status.PENDING_RECEIPT
    client.force_login(users["med"])
    response = client.post(
        reverse("tracking:external_action", args=[record.pk, "receipt"]),
        {"revision": record.revision, "note": "Paper received"},
    )
    assert response.status_code == 302
    record.refresh_from_db()
    assert record.current_office == offices["MED"]
    response = client.post(
        reverse("tracking:external_action", args=[record.pk, "remark"]),
        {"revision": record.revision, "message": "Internal review finished"},
    )
    assert response.status_code == 302
    record.refresh_from_db()
    response = client.post(
        reverse("tracking:external_action", args=[record.pk, "route"]),
        {"revision": record.revision, "action": "RETURN", "to_external_office": external.pk},
    )
    assert response.status_code == 302
    record.refresh_from_db()
    client.force_login(users["med_admin"])
    response = client.post(
        reverse("tracking:external_action", args=[record.pk, "external-receipt"]),
        {"revision": record.revision, "received_at": timezone.now().isoformat(), "note": "Office signed the paper"},
    )
    assert response.status_code == 302
    record.refresh_from_db()
    response = client.post(
        reverse("tracking:external_action", args=[record.pk, "external-work"]),
        {"revision": record.revision, "actual_at": timezone.now().isoformat(), "message": "External approval recorded"},
    )
    assert response.status_code == 302
    record.refresh_from_db()
    response = client.post(
        reverse("tracking:external_action", args=[record.pk, "complete"]),
        {"revision": record.revision, "completed_at": timezone.now().isoformat()},
    )
    assert response.status_code == 302
    record.refresh_from_db()
    assert record.status == Status.COMPLETED_PENDING_UPLOAD
    response = client.post(reverse("tracking:external_action", args=[record.pk, "file"]), {"revision": record.revision})
    assert response.status_code == 302
    record.refresh_from_db()
    assert record.status == Status.COMPLETED
    document = Document.objects.get(external_tracking_record=record)
    assert document.collection == "EXTERNAL"
    assert Document.objects.filter(collection="OVPA").count() == 0


def test_private_attachment_links_are_signed_and_user_bound(client, users, external_cases, settings, tmp_path):
    from apps.tracking.external_services import add_attachment

    settings.MEDIA_ROOT = tmp_path
    _own, _other, record, _foreign = external_cases
    uploaded = SimpleUploadedFile(
        "evidence.pdf", b"%PDF-1.4\n% External attachment test\n%%EOF", content_type="application/pdf"
    )
    attachment = add_attachment(
        record, user=users["med_admin"], expected_revision=record.revision, uploaded_file=uploaded
    )
    client.force_login(users["med_admin"])
    response = client.get(reverse("tracking:external_detail", args=[record.pk]))
    url = response.context["attachments"][0][1]
    missing_token = client.get(reverse("tracking:external_attachment", args=[attachment.pk]))
    assert missing_token.status_code == 403
    download = client.get(url)
    assert download.status_code == 200
    assert download["Cache-Control"] == "private, no-store"
    assert b"External attachment test" in b"".join(download.streaming_content)
    client.force_login(users["admin"])
    assert client.get(url).status_code == 403


def test_stale_revision_does_not_duplicate_a_movement(client, users, offices, external_cases):
    _external, _other, record, _foreign = external_cases
    client.force_login(users["med_admin"])
    data = {
        "revision": record.revision,
        "action": "SEND",
        "to_office": offices["MED"].pk,
        "sent_at": (timezone.now() - timedelta(hours=1)).isoformat(),
    }
    endpoint = reverse("tracking:external_review", args=[record.pk])
    assert client.post(endpoint, data).status_code == 302
    assert client.post(endpoint, data).status_code == 400
    assert record.routing_steps.count() == 1


def test_reports_count_current_work_with_another_office_without_losing_totals(client, users, offices, external_cases):
    from apps.tracking.external_services import route_record

    _external, _other, record, _foreign = external_cases
    route_record(
        record,
        user=users["med_admin"],
        expected_revision=record.revision,
        to_office=offices["SUP"],
        sent_at=timezone.now() - timedelta(hours=1),
    )
    client.force_login(users["med_admin"])
    response = client.get(reverse("tracking:external_reports"))
    assert response.status_code == 200
    assert response.context["total_count"] == 1
    assert response.context["direction_counts"] == {"incoming": 0, "outgoing": 0, "other": 1}
    assert b"With another office" in response.content


def test_viewer_can_print_authorized_external_slip_but_not_foreign_draft(client, users, offices, external_cases):
    from apps.tracking.external_services import route_record

    _external, _other, record, foreign = external_cases
    route_record(
        record,
        user=users["med_admin"],
        expected_revision=record.revision,
        to_office=offices["MED"],
        sent_at=timezone.now() - timedelta(hours=1),
    )
    client.force_login(users["viewer"])
    response = client.get(reverse("tracking:external_slip", args=[record.pk]))
    assert response.status_code == 200
    assert b"data-print-trigger" in response.content
    assert b'data-print-log-target-type="ExternalTrackingRecord"' in response.content
    assert record.tracking_number.encode() in response.content
    assert client.get(reverse("tracking:external_slip", args=[foreign.pk])).status_code == 404


def test_directory_create_edit_and_deactivate_use_current_revision(client, users):
    from apps.tracking.external_models import ExternalOffice

    client.force_login(users["med_admin"])
    assert (
        client.post(
            reverse("tracking:external_office_create"), {"name": "College of Arts", "code": "ART", "is_active": "on"}
        ).status_code
        == 302
    )
    entry = ExternalOffice.objects.get(name="College of Arts")
    edit_url = reverse("tracking:external_office_edit", args=[entry.pk])
    assert (
        client.post(
            edit_url,
            {"name": "College of Arts and Sciences", "code": "CAS", "is_active": "on", "revision": entry.revision},
        ).status_code
        == 302
    )
    entry.refresh_from_db()
    response = client.post(
        reverse("tracking:external_office_deactivate", args=[entry.pk]), {"revision": entry.revision - 1}
    )
    assert response.status_code == 400
    entry.refresh_from_db()
    assert entry.is_active
    assert (
        client.post(
            reverse("tracking:external_office_deactivate", args=[entry.pk]), {"revision": entry.revision}
        ).status_code
        == 302
    )
    entry.refresh_from_db()
    assert not entry.is_active


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("operation", ["create", "rename"])
def test_concurrent_directory_requests_render_a_duplicate_message(client, users, offices, monkeypatch, operation):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import connections

    from apps.tracking import external_services
    from apps.tracking.models import ExternalOffice

    actors = [users["med_admin"], users["admin"]]
    browsers = [type(client)(), type(client)()]
    for browser, actor in zip(browsers, actors, strict=True):
        browser.force_login(actor)
    entries = []
    if operation == "rename":
        entries = [
            external_services.create_external_office(user=actors[0], name=name)
            for name in ("Original registrar", "Original college")
        ]
    validated = Barrier(2)
    original_clean = ExternalOffice.full_clean
    original_recording = external_services.recording_office

    def recording_then_lock(*args, **kwargs):
        validated.wait(timeout=30)
        return original_recording(*args, **kwargs)

    def clean_then_wait(entry, *args, **kwargs):
        original_clean(entry, *args, **kwargs)
        # ModelForm validates first; synchronize only the service's final
        # validation to reproduce a change arriving after both forms passed.
        if operation == "rename" and kwargs.get("validate_constraints") is False:
            validated.wait(timeout=30)

    monkeypatch.setattr(ExternalOffice, "full_clean", clean_then_wait)
    if operation == "create":
        monkeypatch.setattr(external_services, "recording_office", recording_then_lock)

    def submit(index):
        try:
            url = reverse("tracking:external_office_create") + f"?office={offices['MED'].pk}"
            data = {"name": "Concurrent Registrar", "managing_office": offices["MED"].pk, "is_active": "on"}
            if operation == "rename":
                url = reverse("tracking:external_office_edit", args=[entries[index].pk])
                data["revision"] = 1
            return browsers[index].post(url, data)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, range(2)))
    assert sorted(response.status_code for response in responses) == [302, 400]
    rejected = next(response for response in responses if response.status_code == 400)
    assert b"already exists" in rejected.content
    assert ExternalOffice.objects.filter(managing_office=offices["MED"], name="Concurrent Registrar").count() == 1


def test_involved_staff_can_filter_a_counterpart_from_another_managing_office(client, users, offices, external_cases):
    from apps.tracking.external_services import route_record

    counterpart, _other, record, _foreign = external_cases
    route_record(
        record,
        user=users["med_admin"],
        expected_revision=record.revision,
        to_office=offices["SUP"],
        sent_at=timezone.now() - timedelta(hours=1),
    )
    client.force_login(users["sup"])
    response = client.get(reverse("tracking:external_list"), {"external_office": counterpart.pk})
    assert response.status_code == 200
    assert record.title.encode() in response.content


def test_other_direction_filter_and_count_link_match_reports(client, users, offices, external_cases):
    from apps.tracking.external_services import route_record

    _counterpart, _other, record, _foreign = external_cases
    route_record(
        record,
        user=users["med_admin"],
        expected_revision=record.revision,
        to_office=offices["SUP"],
        sent_at=timezone.now() - timedelta(hours=1),
    )
    client.force_login(users["med_admin"])
    response = client.get(reverse("tracking:external_list"), {"direction": "other"})
    assert response.status_code == 200
    assert response.context["total_count"] == 1
    assert record.title.encode() in response.content
    report = client.get(reverse("tracking:external_reports"))
    assert b"direction=other" in report.content


def test_filed_history_is_explicit_in_tracking_default(client, users, external_cases):
    from apps.tracking.models import Status

    _own, _other, record, _foreign = external_cases
    record.status = Status.COMPLETED
    record.save(update_fields=["status"])
    client.force_login(users["med_admin"])
    assert record.title.encode() not in client.get(reverse("tracking:external_list")).content
    response = client.get(reverse("tracking:external_list"), {"status": Status.COMPLETED})
    assert record.title.encode() in response.content


def test_overdue_filter_matches_csv_and_does_not_include_completed(client, users, external_cases):
    from apps.tracking.models import Status

    _own, _other, record, _foreign = external_cases
    record.due_at = timezone.now() - timedelta(days=1)
    record.status = Status.IN_PROCESS
    record.save(update_fields=["due_at", "status"])
    client.force_login(users["med_admin"])
    response = client.get(reverse("tracking:external_list"), {"overdue": "yes"})
    assert response.context["total_count"] == 1
    assert record.title.encode() in client.get(reverse("tracking:external_export"), {"overdue": "yes"}).content
    assert client.get(reverse("tracking:external_list"), {"overdue": "no"}).context["total_count"] == 0
    assert client.get(reverse("tracking:external_list"), {"overdue": "invalid"}).status_code == 400
    record.status = Status.COMPLETED_PENDING_UPLOAD
    record.save(update_fields=["status"])
    assert client.get(reverse("tracking:external_list"), {"overdue": "yes"}).context["total_count"] == 0


def test_note_only_correction_preserves_seconds_and_original_microseconds():
    from apps.tracking.external_forms import ExternalCorrectionForm

    sent = timezone.now().replace(second=20, microsecond=123456) - timedelta(days=1)
    received = sent + timedelta(seconds=10, microseconds=111111)
    step = SimpleNamespace(from_external_office_id=1, to_external_office_id=2, received_at=received)
    initial = {
        "revision": 1,
        "sent_at": sent,
        "received_at": received,
        "instructions": "Original",
        "receipt_note": "Signed",
    }
    unbound = ExternalCorrectionForm(step=step, initial=initial)
    assert 'step="1"' in str(unbound["sent_at"])
    payload = {"revision": 1, "reason": "Corrected note", "instructions": "Updated", "receipt_note": "Signed"}
    for field in ("sent_at", "received_at"):
        form_field = unbound.fields[field]
        payload[field] = form_field.widget.format_value(form_field.prepare_value(initial[field]))
    bound = ExternalCorrectionForm(payload, step=step, initial=initial)
    assert bound.is_valid(), bound.errors
    assert bound.cleaned_data["sent_at"] == sent
    assert bound.cleaned_data["received_at"] == received


def test_draft_cannot_record_external_work_before_publication(client, users, external_cases):
    from apps.tracking.models import Status

    _own, _other, record, _foreign = external_cases
    client.force_login(users["med_admin"])
    detail = client.get(reverse("tracking:external_detail", args=[record.pk]))
    endpoint = reverse("tracking:external_action", args=[record.pk, "external-work"])
    assert endpoint.encode() not in detail.content
    assert client.get(endpoint).status_code == 403
    assert (
        client.post(
            endpoint,
            {"revision": record.revision, "message": "Premature work", "actual_at": timezone.now().isoformat()},
        ).status_code
        == 403
    )
    record.refresh_from_db()
    assert record.status == Status.DRAFT

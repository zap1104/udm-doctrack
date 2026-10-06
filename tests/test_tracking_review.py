"""Deadline round trips and editing the draft that the review page belongs to."""

from __future__ import annotations

import html
import re
from datetime import datetime, time, timedelta

import pytest
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory
from django.utils import timezone

from apps.accounts.models import User
from apps.tracking.models import Attachment, Status, TrackingRecord
from apps.tracking.services import confirm_receipt, create_draft_record, route_record
from apps.tracking.views import RecordCreateView

pytestmark = pytest.mark.django_db


def _payload(offices, memo_type, **changes):
    data = {
        "subject": "Repair request for the main building",
        "instructions": "Please inspect the affected room.",
        "document_type": memo_type.pk,
        "classification": "INTERNAL",
        "priority": "NORMAL",
        "requested_action": "2",
        "receiving_offices": [offices["SUP"].pk],
        "deadline_choice": "date",
        "due_date": (timezone.localdate() + timedelta(days=2)).isoformat(),
        "due_time": "10:00",
    }
    data.update(changes)
    return data


def _create(client, users, offices, memo_type, **changes):
    client.force_login(users["med"])
    payload = _payload(offices, memo_type, **changes)
    response = client.post("/tracking/new/", payload)
    assert response.status_code == 302
    record = TrackingRecord.objects.get()
    assert response["Location"] == f"/tracking/{record.pk}/review/"
    return record, payload


def _input_value(body, name):
    """Read the value a browser submits from the actual rendered input."""
    tag = next(
        tag for tag in re.findall(r"<input\b[^>]*>", body)
        if re.search(rf'\bname="{re.escape(name)}"', tag)
    )
    match = re.search(r'\bvalue="([^"]*)"', tag)
    return html.unescape(match.group(1)) if match else ""


def _input_attributes(body, name):
    tag = next(
        tag for tag in re.findall(r"<input\b[^>]*>", body)
        if re.search(rf'\bname="{re.escape(name)}"', tag)
    )
    return dict(re.findall(r'([\w:-]+)="([^"]*)"', tag))


def _deadline_cell(body):
    match = re.search(r"<th[^>]*>Deadline</th>\s*<td[^>]*>(.*?)</td>", body, re.DOTALL)
    assert match is not None
    return match.group(1)


def _deadline_text(body):
    return " ".join(html.unescape(re.sub(r"<[^>]*>", "", _deadline_cell(body))).split())


def _review_payload(body, offices):
    return {
        "receiving_offices": [offices["SUP"].pk],
        "deadline_choice": "date",
        "due_date": _input_value(body, "due_date"),
        "due_time": _input_value(body, "due_time"),
    }


@pytest.mark.parametrize(
    "entered_time,want_time",
    [("10:00", time(10, 0)), ("", time(23, 59, 59)), ("23:59", time(23, 59))],
)
def test_send_keeps_the_deadline_chosen_before_review(client, users, offices, memo_type, entered_time, want_time):
    record, payload = _create(client, users, offices, memo_type, due_time=entered_time)
    expected = timezone.make_aware(datetime.combine(datetime.fromisoformat(payload["due_date"]).date(), want_time))
    assert record.due_at == expected
    body = client.get(f"/tracking/{record.pk}/review/").content.decode()

    response = client.post(f"/tracking/{record.pk}/review/", _review_payload(body, offices))

    assert response.status_code == 302
    record.refresh_from_db()
    assert record.status == Status.PENDING_RECEIPT
    assert record.due_at == expected
    assert record.routing_steps.get().due_at == expected


def test_review_uses_the_persisted_deadline_when_session_choices_are_lost(client, users, offices, memo_type):
    record, payload = _create(client, users, offices, memo_type, due_time="15:30")
    session = client.session
    session.pop(f"draft_offices_{record.pk}", None)
    session.pop(f"draft_deadline_{record.pk}", None)
    session.save()

    body = client.get(f"/tracking/{record.pk}/review/").content.decode()

    assert _input_value(body, "due_date") == payload["due_date"]
    assert _input_value(body, "due_time") == "15:30"
    response = client.post(f"/tracking/{record.pk}/review/", _review_payload(body, offices))
    assert response.status_code == 302
    record.refresh_from_db()
    assert timezone.localtime(record.due_at).time() == time(15, 30)


def test_an_old_date_only_session_value_cannot_override_the_saved_deadline(client, users, offices, memo_type):
    record, payload = _create(client, users, offices, memo_type)
    session = client.session
    session[f"draft_deadline_{record.pk}"] = (timezone.localdate() + timedelta(days=8)).isoformat()
    session.save()

    body = client.get(f"/tracking/{record.pk}/review/").content.decode()

    assert _input_value(body, "due_date") == payload["due_date"]
    assert _input_value(body, "due_time") == "10:00"


@pytest.mark.parametrize("entered_time,want_text", [("10:00", "10:00 AM"), ("", "end of day"), ("23:59", "11:59 PM")])
def test_review_and_detail_show_the_chosen_deadline_time(client, users, offices, memo_type, entered_time, want_text):
    record, _payload_data = _create(client, users, offices, memo_type, due_time=entered_time)

    for url in (f"/tracking/{record.pk}/review/", record.get_absolute_url()):
        text = _deadline_text(client.get(url).content.decode())
        assert want_text.casefold() in text.casefold(), (url, text)


@pytest.mark.parametrize("day_offset,entered_time", [(3, "15:30"), (-1, "09:00")])
def test_invalid_review_summarizes_the_submitted_deadline_without_saving_it(
    client, users, offices, memo_type, day_offset, entered_time,
):
    record, _payload_data = _create(client, users, offices, memo_type)
    previous_due = record.due_at
    submitted_date = timezone.localdate() + timedelta(days=day_offset)

    response = client.post(f"/tracking/{record.pk}/review/", {
        "receiving_offices": [],
        "deadline_choice": "date",
        "due_date": submitted_date.isoformat(),
        "due_time": entered_time,
    })

    assert response.status_code == 200
    assert response.context["form"].errors
    text = _deadline_text(response.content.decode())
    assert submitted_date.strftime("%d %B %Y") in text
    assert ("3:30 PM" if entered_time == "15:30" else "9:00 AM") in text
    record.refresh_from_db()
    assert record.due_at == previous_due
    assert record.status == Status.DRAFT
    assert not record.routing_steps.exists()


def test_invalid_review_can_show_a_submitted_no_deadline_choice(client, users, offices, memo_type):
    record, _payload_data = _create(client, users, offices, memo_type)

    response = client.post(f"/tracking/{record.pk}/review/", {
        "receiving_offices": [], "deadline_choice": "none", "due_date": "", "due_time": "",
    })

    assert response.status_code == 200
    assert "no deadline" in _deadline_text(response.content.decode()).casefold()
    record.refresh_from_db()
    assert record.due_at is not None


def test_invalid_review_summarizes_the_submitted_receiving_offices(client, users, offices, memo_type):
    record, _payload_data = _create(client, users, offices, memo_type)

    response = client.post(f"/tracking/{record.pk}/review/", {
        "receiving_offices": [offices["HR"].pk], "deadline_choice": "date",
        "due_date": (timezone.localdate() + timedelta(days=2)).isoformat(), "due_time": "not-a-time",
    })

    assert response.status_code == 200
    assert "due_time" in response.context["form"].errors
    cell = re.search(r"<th>Receiving office\(s\)</th>\s*<td[^>]*>(.*?)</td>", response.content.decode(), re.DOTALL).group(1)
    assert offices["HR"].name in cell
    assert offices["SUP"].name not in cell
    assert client.session[f"draft_offices_{record.pk}"] == [offices["SUP"].pk]
    assert not record.routing_steps.exists()


@pytest.mark.parametrize("session_available", [True, False])
def test_invalid_review_recipients_request_correction_without_claiming_a_lost_session(
    client, users, offices, memo_type, session_available,
):
    record, payload = _create(client, users, offices, memo_type)
    if not session_available:
        session = client.session
        session.pop(f"draft_offices_{record.pk}", None)
        session.save()

    response = client.post(f"/tracking/{record.pk}/review/", {
        "receiving_offices": ["not-an-office"], "deadline_choice": "date",
        "due_date": payload["due_date"], "due_time": "10:00",
    })

    assert response.status_code == 200
    assert "receiving_offices" in response.context["form"].errors
    body = response.content.decode()
    cell = re.search(r"<th>Receiving office\(s\)</th>\s*<td[^>]*>(.*?)</td>", body, re.DOTALL).group(1)
    assert offices["SUP"].name not in cell
    # The editor now sits in the Send Document panel beside the summary on
    # desktop and below it on phones. Keep the instruction valid in both.
    assert "correct the receiving offices before sending" in cell.casefold()
    assert "started in an earlier session" not in body
    assert not record.routing_steps.exists()


def test_malformed_submitted_deadline_is_visible_and_escaped_in_review(client, users, offices, memo_type):
    record, _payload_data = _create(client, users, offices, memo_type)
    malformed = '<script>alert("deadline")</script>'

    response = client.post(f"/tracking/{record.pk}/review/", {
        "receiving_offices": [offices["SUP"].pk], "deadline_choice": "date",
        "due_date": malformed, "due_time": "15:30",
    })

    assert response.status_code == 200
    cell = _deadline_cell(response.content.decode())
    assert html.escape(malformed) in cell
    assert malformed not in cell
    assert "15:30" in cell
    record.refresh_from_db()
    assert record.status == Status.DRAFT


def test_sending_without_a_deadline_stays_unscheduled(client, users, offices, memo_type):
    record, _payload_data = _create(client, users, offices, memo_type, deadline_choice="none", due_date="", due_time="")

    response = client.post(f"/tracking/{record.pk}/review/", {
        "receiving_offices": [offices["SUP"].pk], "deadline_choice": "none", "due_date": "", "due_time": "",
    })

    assert response.status_code == 302
    record.refresh_from_db()
    assert record.status == Status.PENDING_RECEIPT
    assert record.due_at is None
    assert record.routing_steps.get().due_at is None


def test_back_to_edit_opens_the_same_draft_with_its_saved_values(client, users, offices, memo_type):
    record, payload = _create(client, users, offices, memo_type)
    review_body = client.get(f"/tracking/{record.pk}/review/").content.decode()
    link = re.search(r'<a\b[^>]*href="([^"]+)"[^>]*>Back to Edit</a>', review_body)
    assert link is not None, "Back to Edit must navigate to this saved draft."

    response = client.get(html.unescape(link.group(1)))

    assert response.status_code == 200
    form = response.context["form"]
    assert form["subject"].value() == record.subject
    assert form["instructions"].value() == record.instructions
    assert str(offices["SUP"].pk) in {str(pk) for pk in form["receiving_offices"].value()}
    body = response.content.decode()
    assert _input_value(body, "due_date") == payload["due_date"]
    assert _input_value(body, "due_time") == "10:00"
    assert TrackingRecord.objects.count() == 1


def test_editing_a_draft_updates_it_without_losing_files_history_or_identity(client, users, offices, memo_type):
    record, _payload_data = _create(client, users, offices, memo_type)
    attachment = Attachment.objects.create(record=record, file="tracking/existing.pdf", original_name="existing.pdf")
    identity = (record.pk, record.tracking_number, record.created_at, record.created_by_id, record.originating_office_id)
    old_history = list(record.activities.values("pk", "event", "message", "detail", "created_at"))
    edited = _payload(offices, memo_type, subject="Updated repair request", instructions="Please inspect both rooms.",
                      classification="CONFIDENTIAL", priority="URGENT", requested_action="6", due_time="15:30")
    edited["receiving_offices"] = [offices["HR"].pk]

    response = client.post(f"/tracking/new/?draft={record.pk}", edited)

    assert response.status_code == 302
    assert response["Location"] == f"/tracking/{record.pk}/review/"
    assert TrackingRecord.objects.count() == 1
    record.refresh_from_db()
    assert (record.pk, record.tracking_number, record.created_at, record.created_by_id, record.originating_office_id) == identity
    assert record.subject == "Updated repair request"
    assert record.instructions == "Please inspect both rooms."
    assert (record.classification, record.priority, record.requested_action) == ("CONFIDENTIAL", "URGENT", "6")
    assert timezone.localtime(record.due_at).time() == time(15, 30)
    assert (record.status, record.current_batch, record.current_office_id, record.current_holder_id) == (
        Status.DRAFT, 0, offices["MED"].pk, users["med"].pk,
    )
    assert list(record.activities.filter(pk__in=[row["pk"] for row in old_history]).values(
        "pk", "event", "message", "detail", "created_at",
    )) == old_history
    assert record.attachments.get().pk == attachment.pk
    assert not record.routing_steps.exists()
    assert client.session[f"draft_offices_{record.pk}"] == [offices["HR"].pk]
    assert "existing.pdf" in client.get(f"/tracking/new/?draft={record.pk}").content.decode()


def test_editing_a_draft_can_add_a_file_without_replacing_existing_attachments(client, users, offices, memo_type, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    record, _payload_data = _create(client, users, offices, memo_type)
    existing = Attachment.objects.create(record=record, file="tracking/existing.pdf", original_name="existing.pdf")
    data = _payload(offices, memo_type)
    data["attachments"] = SimpleUploadedFile("added.pdf", b"%PDF-1.4\nadditional file\n%%EOF", content_type="application/pdf")

    response = client.post(f"/tracking/new/?draft={record.pk}", data)

    assert response.status_code == 302
    assert TrackingRecord.objects.count() == 1
    assert record.attachments.filter(pk=existing.pk).exists()
    added = record.attachments.get(original_name="added.pdf")
    assert added.uploaded_by_id == users["med"].pk
    assert added.routing_step_id is None


def test_draft_upload_content_rejection_rolls_back_metadata_and_keeps_existing_files(
    client, users, offices, memo_type, settings, tmp_path,
):
    settings.MEDIA_ROOT = tmp_path
    record, _payload_data = _create(client, users, offices, memo_type)
    existing = Attachment.objects.create(record=record, file="tracking/existing.pdf", original_name="existing.pdf")
    before = TrackingRecord.objects.values().get(pk=record.pk)
    history = list(record.activities.values())
    data = _payload(offices, memo_type, subject="Must not survive rejected upload", due_time="15:30")
    data["receiving_offices"] = [offices["HR"].pk]
    # FileField accepts this nonempty file; the real signature validator refuses
    # its contents later in attach_files, after metadata would have been saved.
    data["attachments"] = SimpleUploadedFile("pretend.pdf", b"This is not a PDF.", content_type="application/pdf")

    response = client.post(f"/tracking/new/?draft={record.pk}", data)

    assert response.status_code == 200
    assert response.context["form"].is_valid()
    assert "do not match" in response.content.decode()
    assert TrackingRecord.objects.values().get(pk=record.pk) == before
    assert list(record.activities.values()) == history
    assert record.attachments.get().pk == existing.pk
    assert client.session[f"draft_offices_{record.pk}"] == [offices["SUP"].pk]
    assert TrackingRecord.objects.count() == 1


@pytest.mark.parametrize("editing", [False, True])
def test_tracking_attachment_picker_has_a_label_and_linked_help(client, users, offices, memo_type, editing):
    record, _payload_data = _create(client, users, offices, memo_type)
    url = f"/tracking/new/?draft={record.pk}" if editing else "/tracking/new/"

    response = client.get(url)

    assert response.status_code == 200
    body = response.content.decode()
    field = _input_attributes(body, "attachments")
    label = re.search(rf'<label\b[^>]*for="{re.escape(field["id"])}"[^>]*>(.*?)</label>', body, re.DOTALL)
    assert label is not None
    assert "Attachments" in label.group(1)
    help_id = f'{field["id"]}_helptext'
    assert help_id in field.get("aria-describedby", "").split()
    assert f'id="{help_id}"' in body


@pytest.mark.parametrize("editing", [False, True])
def test_tracking_attachment_picker_links_its_validation_error(client, users, offices, memo_type, editing):
    record, _payload_data = _create(client, users, offices, memo_type)
    url = f"/tracking/new/?draft={record.pk}" if editing else "/tracking/new/"
    data = _payload(offices, memo_type)
    data["attachments"] = SimpleUploadedFile("empty.pdf", b"", content_type="application/pdf")

    response = client.post(url, data)

    assert response.status_code == 200
    assert "attachments" in response.context["form"].errors
    body = response.content.decode()
    field = _input_attributes(body, "attachments")
    error_id = f'{field["id"]}_error'
    assert field.get("aria-invalid") == "true"
    assert error_id in field.get("aria-describedby", "").split()
    assert f'id="{error_id}"' in body
    assert f'id="{field["id"]}_helptext"' in body


def test_editing_as_system_admin_labels_the_drafts_originating_office(client, users, offices):
    record = create_draft_record(user=users["med"], subject="Original office display", instructions="For action.")
    client.force_login(users["admin"])

    response = client.get(f"/tracking/new/?draft={record.pk}")

    assert response.status_code == 200
    body = response.content.decode()
    office_input = next(
        tag for tag in re.findall(r"<input\b[^>]*>", body)
        if f'value="{offices["MED"].name}"' in tag and "disabled" in tag
    )
    input_id = re.search(r'\bid="([^"]+)"', office_input)
    assert input_id is not None
    label = re.search(rf'<label\b[^>]*for="{re.escape(input_id.group(1))}"[^>]*>(.*?)</label>', body, re.DOTALL)
    assert label is not None
    assert "Originating office" in label.group(1)


def test_invalid_draft_edit_leaves_the_persisted_record_untouched(client, users, offices, memo_type):
    record, _payload_data = _create(client, users, offices, memo_type)
    previous = (record.subject, record.instructions, record.due_at)

    response = client.post(f"/tracking/new/?draft={record.pk}", _payload(offices, memo_type, subject="x"))

    assert response.status_code == 200
    assert "subject" in response.context["form"].errors
    record.refresh_from_db()
    assert (record.subject, record.instructions, record.due_at) == previous
    assert TrackingRecord.objects.count() == 1


@pytest.mark.parametrize("actor", ["sup", "med_admin", "viewer"])
def test_draft_edit_cannot_open_or_change_another_users_draft(client, users, offices, memo_type, actor):
    record = create_draft_record(user=users["med"], subject="Private draft", instructions="For appropriate action.")
    client.force_login(users[actor])
    url = f"/tracking/new/?draft={record.pk}"

    assert client.get(url).status_code in (403, 404)
    assert client.post(url, _payload(offices, memo_type)).status_code in (403, 404)
    assert TrackingRecord.objects.count() == 1
    record.refresh_from_db()
    assert record.subject == "Private draft"


@pytest.mark.parametrize("draft_id", ["0", "not-an-id"])
def test_invalid_draft_reference_never_creates_a_new_record(client, users, offices, memo_type, draft_id):
    client.force_login(users["med"])
    url = f"/tracking/new/?draft={draft_id}"

    assert client.get(url).status_code == 404
    assert client.post(url, _payload(offices, memo_type)).status_code == 404
    assert not TrackingRecord.objects.exists()


def test_a_sent_draft_cannot_be_overwritten_or_recreated_by_the_edit_form(client, users, offices, memo_type):
    record, _payload_data = _create(client, users, offices, memo_type)
    route_record(record, [offices["SUP"]], user=users["med"])
    previous = (record.subject, record.status, record.tracking_number, record.due_at, record.current_batch)
    url = f"/tracking/new/?draft={record.pk}"

    for response in (client.get(url), client.post(url, _payload(offices, memo_type, subject="Overwrite attempt"))):
        assert response.status_code == 302
        assert response["Location"] == record.get_absolute_url()
    assert TrackingRecord.objects.count() == 1
    record.refresh_from_db()
    assert (record.subject, record.status, record.tracking_number, record.due_at, record.current_batch) == previous
    assert record.routing_steps.count() == 1


def test_draft_edit_rechecks_write_permission_for_a_stale_account(users, offices, memo_type):
    actor = users["med"]
    record = create_draft_record(user=actor, subject="Permission check", instructions="For appropriate action.")
    request = RequestFactory().post(f"/tracking/new/?draft={record.pk}", _payload(offices, memo_type), secure=True)
    request.user = actor
    request.session = {}
    request._messages = FallbackStorage(request)
    User.objects.filter(pk=actor.pk).update(role="VIEWER")

    with pytest.raises(PermissionDenied):
        RecordCreateView.as_view()(request)

    assert TrackingRecord.objects.count() == 1
    record.refresh_from_db()
    assert record.subject == "Permission check"


def test_detail_action_tabs_name_their_panels_and_initial_selection(client, users, offices):
    record = create_draft_record(user=users["med"], subject="Action tab relationships", instructions="For action.")
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    client.force_login(users["sup"])

    response = client.get(record.get_absolute_url())

    assert response.status_code == 200
    assert response.context["can_act"]
    body = response.content.decode()

    def attributes(tag):
        return dict(re.findall(r'([\w:-]+)="([^"]*)"', tag))

    tabs = [attributes(tag) for tag in re.findall(r"<button\b[^>]*>", body) if attributes(tag).get("role") == "tab"]
    panels = {
        attributes(tag)["id"]: attributes(tag)
        for tag in re.findall(r"<div\b[^>]*>", body)
        if attributes(tag).get("role") == "tabpanel"
    }
    assert len(tabs) == 4
    assert len({tab.get("id") for tab in tabs}) == 4
    assert set(panels) == {"tab-remark", "tab-route", "tab-complete", "tab-share"}
    for tab in tabs:
        assert tab.get("id")
        assert tab.get("aria-controls") in panels
        panel = panels[tab["aria-controls"]]
        assert tab["data-bs-target"] == f'#{tab["aria-controls"]}'
        assert panel.get("aria-labelledby") == tab["id"]
        assert tab.get("aria-selected") == ("true" if "active" in panel["class"].split() else "false")
    assert sum(tab.get("aria-selected") == "true" for tab in tabs) == 1
    tab_items = re.findall(r'<li\b[^>]*>\s*<button\b[^>]*\brole="tab"[^>]*>', body)
    assert len(tab_items) == 4
    assert all(attributes(item.split(">")[0]).get("role") == "presentation" for item in tab_items)


def test_detail_attachment_pickers_have_distinct_ids_and_their_own_labels(client, users, offices):
    record = create_draft_record(user=users["med"], subject="Response attachment labels", instructions="For action.")
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    client.force_login(users["sup"])

    response = client.get(record.get_absolute_url())

    assert response.context["can_act"]
    body = response.content.decode()
    pickers = [
        dict(re.findall(r'([\w:-]+)="([^"]*)"', tag))
        for tag in re.findall(r"<input\b[^>]*>", body)
        if 'name="attachments"' in tag
    ]
    assert len(pickers) == 2
    assert len({picker["id"] for picker in pickers}) == 2
    for picker, expected_label in zip(pickers, ["Attach files", "Attach a response or revision"], strict=True):
        labels = re.findall(rf'<label\b[^>]*for="{re.escape(picker["id"])}"[^>]*>(.*?)</label>', body, re.DOTALL)
        assert labels == [expected_label]

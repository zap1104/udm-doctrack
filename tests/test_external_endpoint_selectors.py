"""One visible office selector must preserve External routing boundaries."""

from datetime import timedelta

import pytest
from django.http import QueryDict
from django.urls import reverse
from django.utils import timezone

from apps.tracking.external_forms import ExternalCreateForm, ExternalRouteForm
from apps.tracking.external_models import ExternalOffice, ExternalTrackingRecord
from apps.tracking.external_services import create_record
from apps.tracking.models import Status
from tests.upload_helpers import follow_upload_recovery

pytestmark = pytest.mark.django_db


@pytest.fixture
def counterparts(offices):
    return {
        "own": ExternalOffice.objects.create(managing_office=offices["MED"], name="College of Science"),
        "other": ExternalOffice.objects.create(managing_office=offices["SUP"], name="College of Nursing"),
        "inactive": ExternalOffice.objects.create(managing_office=offices["MED"], name="Retired college", is_active=False),
    }


def test_external_create_has_one_visible_from_office_and_grouped_choices(users, offices, counterparts):
    form = ExternalCreateForm(user=users["med_admin"])
    names = [field.name for field in form.visible_fields()]
    assert "from_endpoint" in names
    assert "from_office" not in names
    assert "from_external_office" not in names
    assert form.fields["from_endpoint"].label == "From office"
    choices = dict(form.fields["from_endpoint"].choices)
    assert [value for value, _ in choices["Internal offices"]] == [f"internal:{offices['MED'].pk}"]
    assert [value for value, _ in choices["External UDM offices"]] == [f"external:{counterparts['own'].pk}"]


@pytest.mark.parametrize("kind", ["internal", "external"])
def test_combined_source_decodes_to_existing_service_arguments(users, offices, counterparts, kind):
    office = offices["MED"] if kind == "internal" else counterparts["own"]
    form = ExternalCreateForm({"title": "Paper packet", "from_endpoint": f"{kind}:{office.pk}"}, user=users["med_admin"])
    assert form.is_valid(), form.errors
    assert "from_endpoint" not in form.cleaned_data
    assert form.cleaned_data["from_office"] == (office if kind == "internal" else None)
    assert form.cleaned_data["from_external_office"] == (office if kind == "external" else None)


@pytest.mark.parametrize("kind", ["internal", "external"])
def test_legacy_source_posts_still_select_the_same_office(users, offices, counterparts, kind):
    office = offices["MED"] if kind == "internal" else counterparts["own"]
    key = "from_office" if kind == "internal" else "from_external_office"
    form = ExternalCreateForm({"title": "Legacy paper", key: office.pk}, user=users["med_admin"])
    assert form.is_valid(), form.errors
    assert form["from_endpoint"].value() == f"{kind}:{office.pk}"
    assert form.cleaned_data[key] == office


@pytest.mark.parametrize("bad", ["malformed", "unknown:1", "internal:99999999", "external:99999999", ""])
def test_invalid_combined_source_is_a_visible_field_error(users, bad):
    form = ExternalCreateForm({"title": "Rejected paper", "from_endpoint": bad}, user=users["med_admin"])
    assert not form.is_valid()
    assert "from_endpoint" in form.errors


@pytest.mark.parametrize("entry", ["other", "inactive"])
def test_source_dropdown_rejects_foreign_and_inactive_external_entries(users, counterparts, entry):
    form = ExternalCreateForm(
        {"title": "Rejected paper", "from_endpoint": f"external:{counterparts[entry].pk}"}, user=users["med_admin"],
    )
    assert not form.is_valid()
    assert "from_endpoint" in form.errors


def test_conflicting_new_and_legacy_source_is_rejected(users, offices, counterparts):
    form = ExternalCreateForm(
        {"title": "Conflicting source", "from_endpoint": f"external:{counterparts['own'].pk}", "from_office": offices["MED"].pk},
        user=users["med_admin"],
    )
    assert not form.is_valid()
    assert "from_endpoint" in form.errors


def test_repeated_combined_source_is_rejected(users, offices, counterparts):
    data = QueryDict("", mutable=True)
    data["title"] = "Ambiguous source"
    data.setlist("from_endpoint", [f"internal:{offices['MED'].pk}", f"external:{counterparts['own'].pk}"])
    form = ExternalCreateForm(data, user=users["med_admin"])
    assert not form.is_valid()
    assert "from_endpoint" in form.errors


def test_invalid_creation_keeps_the_selected_source(client, users, counterparts):
    client.force_login(users["med_admin"])
    token = f"external:{counterparts['own'].pk}"
    response = client.post(reverse("tracking:external_create"), {"from_endpoint": token})
    response = follow_upload_recovery(client, response)
    assert response.status_code == 200
    assert response.context["form"]["from_endpoint"].value() == token
    assert f'value="{token}" selected'.encode() in response.content


@pytest.mark.parametrize("kind", ["internal", "external"])
def test_combined_destination_decodes_without_changing_current_custody(users, offices, counterparts, kind):
    record = create_record(user=users["med_admin"], title="Destination selection", from_office=offices["MED"])
    office = offices["HR"] if kind == "internal" else counterparts["own"]
    form = ExternalRouteForm(
        {"revision": record.revision, "action": "SEND", "to_endpoint": f"{kind}:{office.pk}"},
        user=users["med_admin"], record=record,
    )
    assert form.is_valid(), form.errors
    assert "to_endpoint" not in form.cleaned_data
    assert form.cleaned_data["to_office"] == (office if kind == "internal" else None)
    assert form.cleaned_data["to_external_office"] == (office if kind == "external" else None)
    assert [field.name for field in form.visible_fields()].count("to_endpoint") == 1
    assert "to_office" not in form.fields and "to_external_office" not in form.fields
    record.refresh_from_db()
    assert record.status == Status.DRAFT
    assert record.current_office_id == offices["MED"].pk


def test_external_http_create_and_review_route_uses_new_selectors(client, users, offices, counterparts):
    client.force_login(users["med_admin"])
    response = client.post(reverse("tracking:external_create"), {
        "title": "Single selector round trip", "from_endpoint": f"external:{counterparts['own'].pk}",
    })
    assert response.status_code == 302
    record = ExternalTrackingRecord.objects.get(title="Single selector round trip")
    assert record.status == Status.DRAFT
    review = client.get(response.url)
    assert review.status_code == 200
    assert counterparts["own"].name.encode() in review.content
    assert b'id="external-from-office"' in review.content
    assert "readonly" in review.content.decode()
    response = client.post(response.url, {
        "revision": record.revision, "action": "SEND", "to_endpoint": f"internal:{offices['HR'].pk}",
        "sent_at": (timezone.now() - timedelta(minutes=5)).isoformat(),
    })
    assert response.status_code == 302
    record.refresh_from_db()
    assert record.status == Status.PENDING_RECEIPT
    assert record.current_step.to_office_id == offices["HR"].pk
    assert record.current_step.to_external_office_id is None
    assert record.current_step.from_external_office_id == counterparts["own"].pk


def test_error_redisplay_can_change_source_without_javascript(client, users, offices, counterparts):
    from tests.test_external_ui_contract import Controls

    client.force_login(users["med_admin"])
    response = client.post(reverse("tracking:external_create"), {"from_office": offices["MED"].pk})
    response = follow_upload_recovery(client, response)
    assert response.status_code == 200
    controls = Controls(response.content.decode())
    assert controls.names["from_office"] == controls.names["from_external_office"] == 0
    response = client.post(reverse("tracking:external_create"), {
        "title": "Source changed after validation", "from_endpoint": f"external:{counterparts['own'].pk}",
    })
    assert response.status_code == 302
    record = ExternalTrackingRecord.objects.get(title="Source changed after validation")
    assert record.current_external_office == counterparts["own"]
    assert record.current_office_id is None


def test_equal_internal_and_external_ids_do_not_confuse_endpoint_types(users, offices):
    office = offices["MED"]
    external = ExternalOffice.objects.create(pk=office.pk, managing_office=office, name="College with overlapping ID")
    for token, expected_internal, expected_external in [
        (f"internal:{office.pk}", office, None), (f"external:{external.pk}", None, external),
    ]:
        form = ExternalCreateForm({"title": "Separate namespaces", "from_endpoint": token}, user=users["med_admin"])
        assert form.is_valid(), form.errors
        assert form.cleaned_data["from_office"] == expected_internal
        assert form.cleaned_data["from_external_office"] == expected_external


@pytest.mark.parametrize("change", ["inactive", "owner"])
def test_office_changed_after_form_initialization_is_rechecked(users, offices, counterparts, change):
    external = counterparts["own"]
    form = ExternalCreateForm({"title": "Changed entry", "from_endpoint": f"external:{external.pk}"}, user=users["med_admin"])
    ExternalOffice.objects.filter(pk=external.pk).update(**(
        {"is_active": False} if change == "inactive" else {"managing_office": offices["SUP"]}
    ))
    assert not form.is_valid()
    assert "from_endpoint" in form.errors


@pytest.mark.parametrize("kind", ["internal", "external"])
def test_legacy_destination_posts_still_decode(users, offices, counterparts, kind):
    record = create_record(user=users["med_admin"], title="Legacy recipient", from_office=offices["MED"])
    recipient = offices["HR"] if kind == "internal" else counterparts["own"]
    key = "to_office" if kind == "internal" else "to_external_office"
    form = ExternalRouteForm(
        {"revision": record.revision, "action": "SEND", key: recipient.pk}, user=users["med_admin"], record=record,
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data[key] == recipient


@pytest.mark.parametrize("entry", ["other", "inactive"])
def test_forged_destination_is_refused_without_mutating_the_record(client, users, offices, counterparts, entry):
    admin = users["med_admin"]
    record = create_record(user=admin, title="Protected recipient", from_office=offices["MED"])
    before_revision = record.revision
    client.force_login(admin)
    response = client.post(reverse("tracking:external_review", args=[record.pk]), {
        "revision": record.revision, "action": "SEND", "to_endpoint": f"external:{counterparts[entry].pk}",
    })
    assert response.status_code == 400
    assert "to_endpoint" in response.context["form"].errors
    record.refresh_from_db()
    assert record.revision == before_revision and record.status == Status.DRAFT
    assert record.routing_steps.count() == 0


def test_readonly_from_office_cannot_forge_the_actual_sender(client, users, offices, counterparts):
    admin = users["med_admin"]
    record = create_record(user=admin, title="Protected sender", from_office=offices["MED"])
    client.force_login(admin)
    response = client.post(reverse("tracking:external_review", args=[record.pk]), {
        "revision": record.revision, "action": "SEND", "to_endpoint": f"external:{counterparts['own'].pk}",
        "from_endpoint": f"internal:{offices['HR'].pk}", "external-from-office": offices["HR"].name,
    })
    assert response.status_code == 302
    record.refresh_from_db()
    assert record.current_step.from_office_id == offices["MED"].pk
    assert record.current_step.from_external_office_id is None

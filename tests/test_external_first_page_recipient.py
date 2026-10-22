"""External recipients are chosen before draft review, without sending early."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.tracking.external_models import ExternalOffice, ExternalTrackingRecord
from apps.tracking.models import Status

pytestmark = pytest.mark.django_db


@pytest.fixture
def paper_offices(offices):
    return {
        "own": ExternalOffice.objects.create(name="College of Science", managing_office=offices["MED"]),
        "foreign": ExternalOffice.objects.create(name="College of Nursing", managing_office=offices["SUP"]),
        "inactive": ExternalOffice.objects.create(name="Retired college", managing_office=offices["MED"], is_active=False),
    }


def test_first_page_displays_from_and_to_once(client, users):
    from tests.test_external_ui_contract import Controls

    client.force_login(users["med_admin"])
    response = client.get(reverse("tracking:external_create"))
    controls = Controls(response.content.decode())
    assert controls.names["from_endpoint"] == controls.names["to_endpoint"] == 1
    assert response.context["form"].fields["to_endpoint"].required


@pytest.mark.parametrize("external_source", [False, True])
def test_recipient_survives_review_without_dispatching(client, users, offices, paper_offices, external_source):
    client.force_login(users["med_admin"])
    source = f"external:{paper_offices['own'].pk}" if external_source else f"internal:{offices['MED'].pk}"
    destination = f"internal:{offices['HR'].pk}" if external_source else f"external:{paper_offices['own'].pk}"
    response = client.post(reverse("tracking:external_create"), {
        "title": "Choose recipient before review", "from_endpoint": source, "to_endpoint": destination,
    })
    assert response.status_code == 302
    record = ExternalTrackingRecord.objects.get(title="Choose recipient before review")
    assert record.status == Status.DRAFT
    assert record.tracking_number.startswith("DRAFT-")
    assert record.routing_steps.count() == 0
    assert record.current_external_office_id == (paper_offices["own"].pk if external_source else None)
    assert record.current_office_id == (None if external_source else offices["MED"].pk)
    review = client.get(response.url)
    assert review.status_code == 200
    assert review.context["form"]["to_endpoint"].value() == destination
    reopened = client.get(reverse("tracking:external_review", args=[record.pk]))
    assert reopened.context["form"]["to_endpoint"].value() == destination
    payload = {"revision": record.revision, "action": "SEND", "to_endpoint": destination}
    if external_source:
        payload["sent_at"] = (timezone.now() - timedelta(minutes=1)).isoformat()
    sent = client.post(response.url, payload)
    assert sent.status_code == 302
    record.refresh_from_db()
    assert record.status == Status.PENDING_RECEIPT
    assert record.routing_steps.count() == 1
    assert f"external_draft_recipient_{record.pk}" not in client.session


def test_required_destination_error_preserves_source(client, users, offices):
    client.force_login(users["med_admin"])
    source = f"internal:{offices['MED'].pk}"
    response = client.post(reverse("tracking:external_create"), {
        "title": "Recipient missing", "from_endpoint": source, "to_endpoint": "",
    })
    assert response.status_code == 400
    assert "to_endpoint" in response.context["form"].errors
    assert response.context["form"]["from_endpoint"].value() == source
    assert not ExternalTrackingRecord.objects.exists()


def test_validation_redisplay_retains_both_offices(client, users, offices, paper_offices):
    client.force_login(users["med_admin"])
    source = f"external:{paper_offices['own'].pk}"
    destination = f"internal:{offices['HR'].pk}"
    response = client.post(reverse("tracking:external_create"), {"from_endpoint": source, "to_endpoint": destination})
    assert response.status_code == 400
    assert response.context["form"]["from_endpoint"].value() == source
    assert response.context["form"]["to_endpoint"].value() == destination
    assert not ExternalTrackingRecord.objects.exists()


@pytest.mark.parametrize("entry", ["foreign", "inactive"])
def test_invalid_external_destination_does_not_create_a_draft(client, users, offices, paper_offices, entry):
    client.force_login(users["med_admin"])
    response = client.post(reverse("tracking:external_create"), {
        "title": "Invalid recipient", "from_endpoint": f"internal:{offices['MED'].pk}",
        "to_endpoint": f"external:{paper_offices[entry].pk}",
    })
    assert response.status_code == 400
    assert "to_endpoint" in response.context["form"].errors
    assert not ExternalTrackingRecord.objects.exists()


@pytest.mark.parametrize("external_source", [False, True])
def test_initial_recipient_cannot_already_be_the_source(client, users, offices, paper_offices, external_source):
    client.force_login(users["med_admin"])
    endpoint = f"external:{paper_offices['own'].pk}" if external_source else f"internal:{offices['MED'].pk}"
    response = client.post(reverse("tracking:external_create"), {
        "title": "Same source and recipient", "from_endpoint": endpoint, "to_endpoint": endpoint,
    })
    assert response.status_code == 400
    assert "to_endpoint" in response.context["form"].errors
    assert not ExternalTrackingRecord.objects.exists()


def test_retired_remembered_recipient_requires_a_new_choice(client, users, offices, paper_offices):
    client.force_login(users["med_admin"])
    response = client.post(reverse("tracking:external_create"), {
        "title": "Recipient retired during review", "from_endpoint": f"internal:{offices['MED'].pk}",
        "to_endpoint": f"external:{paper_offices['own'].pk}",
    })
    assert response.status_code == 302
    record = ExternalTrackingRecord.objects.get(title="Recipient retired during review")
    ExternalOffice.objects.filter(pk=paper_offices["own"].pk).update(is_active=False)
    response = client.get(response.url)
    assert response.status_code == 200
    assert not response.context["form"]["to_endpoint"].value()
    assert b"no longer available" in response.content
    assert record.routing_steps.count() == 0


def test_expired_recipient_memory_does_not_send_or_widen_choices(client, users, offices, paper_offices):
    client.force_login(users["med_admin"])
    response = client.post(reverse("tracking:external_create"), {
        "title": "Session lost before review", "from_endpoint": f"internal:{offices['MED'].pk}",
        "to_endpoint": f"external:{paper_offices['own'].pk}",
    })
    assert response.status_code == 302
    record = ExternalTrackingRecord.objects.get(title="Session lost before review")
    session = client.session
    session.pop(f"external_draft_recipient_{record.pk}", None)
    session.save()
    review = client.get(response.url)
    assert review.status_code == 200
    assert not review.context["form"]["to_endpoint"].value()
    record.refresh_from_db()
    assert record.status == Status.DRAFT and record.routing_steps.count() == 0

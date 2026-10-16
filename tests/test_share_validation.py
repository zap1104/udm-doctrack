"""Sharing must choose one recipient and report invalid input without a 500."""

import pytest
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.accounts.models import Office, User
from apps.tracking.forms import GrantAccessForm
from apps.tracking.models import RecordAccessGrant
from apps.tracking.services import create_draft_record, route_record


@pytest.mark.parametrize("office,user", [(True, True), (False, False)])
def test_clean_rejects_ambiguous_or_missing_recipient(office, user):
    form = GrantAccessForm()
    form.cleaned_data = {
        "office": Office(pk=1) if office else None,
        "user": User(pk=2) if user else None,
    }
    with pytest.raises(ValidationError):
        form.clean()


@pytest.mark.django_db
@pytest.mark.parametrize("actor", ["med", "med_admin", "admin"])
def test_share_with_both_recipients_shows_error_without_granting_access(client, users, offices, actor):
    record = create_draft_record(user=users["med"], subject="Share validation", instructions="")
    route_record(record, [offices["SUP"]], user=users["med"])
    client.force_login(users[actor])
    response = client.post(reverse("tracking:grant_access", args=[record.pk]), {
        "office": offices["MED"].pk, "user": users["med"].pk, "reason": "Review",
    })
    assert response.status_code == 302
    assert response.url == record.get_absolute_url()
    assert not RecordAccessGrant.objects.filter(record=record).exists()
    assert [str(message) for message in get_messages(response.wsgi_request)] == [
        "Choose either an office or a user, not both."
    ]


@pytest.mark.django_db
@pytest.mark.parametrize("recipient", ["office", "user"])
def test_share_with_one_recipient_still_grants_access(client, users, offices, recipient):
    record = create_draft_record(user=users["med"], subject="Valid share", instructions="")
    route_record(record, [offices["SUP"]], user=users["med"])
    client.force_login(users["med"])
    selected = offices["HR"].pk if recipient == "office" else users["hr"].pk
    response = client.post(reverse("tracking:grant_access", args=[record.pk]), {recipient: selected})
    assert response.status_code == 302
    grant = RecordAccessGrant.objects.get(record=record)
    assert getattr(grant, f"{recipient}_id") == selected
    other = "user" if recipient == "office" else "office"
    assert getattr(grant, f"{other}_id") is None

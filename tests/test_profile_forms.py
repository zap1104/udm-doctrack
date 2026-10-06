"""Saving one profile-page form must never overwrite the other form's data."""

from html.parser import HTMLParser

import pytest
from django.contrib.messages import ERROR, SUCCESS, get_messages
from django.urls import reverse

from apps.core.models import NotificationPreference

pytestmark = pytest.mark.django_db

PERSONAL_DETAILS = {
    "first_name": "Maria",
    "last_name": "Santos",
    "email": "maria@example.edu",
    "phone": "09123456789",
    "position": "Records officer",
}


class ProfileFormMarkup(HTMLParser):
    """Read native submission targets and fields from the rendered page."""

    def __init__(self):
        super().__init__()
        self.forms = []
        self.current_form = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form":
            self.current_form = {"attributes": attributes, "inputs": {}}
            self.forms.append(self.current_form)
        elif tag == "input" and self.current_form is not None and "name" in attributes:
            self.current_form["inputs"][attributes["name"]] = attributes

    def handle_endtag(self, tag):
        if tag == "form":
            self.current_form = None


@pytest.fixture
def profile_user(users):
    user = users["med"]
    for field, value in PERSONAL_DETAILS.items():
        setattr(user, field, value)
    user.verified_email = "maria@example.edu"
    user.save()
    return user


def test_native_profile_forms_submit_separate_actions_and_preserve_verification_target(client, profile_user):
    profile_user.verified_email = ""
    profile_user.save(update_fields=["verified_email"])
    client.force_login(profile_user)

    response = client.get(reverse("accounts:profile"))

    assert response.status_code == 200
    markup = ProfileFormMarkup()
    markup.feed(response.content.decode())
    profile_form = next(form for form in markup.forms if "first_name" in form["inputs"])
    preferences_form = next(form for form in markup.forms if "in_app_enabled" in form["inputs"])
    verification_form = next(
        form for form in markup.forms
        if form["attributes"].get("action") == reverse("accounts:verify_email")
    )
    for form, action in [(profile_form, "profile"), (preferences_form, "preferences")]:
        assert form["attributes"]["method"].lower() == "post"
        assert form["attributes"].get("action", "") in {"", reverse("accounts:profile")}
        assert form["inputs"]["action"]["type"] == "hidden"
        assert form["inputs"]["action"]["value"] == action
        assert "csrfmiddlewaretoken" in form["inputs"]
    assert "in_app_enabled" not in profile_form["inputs"]
    assert "first_name" not in preferences_form["inputs"]
    assert verification_form["attributes"]["method"].lower() == "post"
    assert "csrfmiddlewaretoken" in verification_form["inputs"]
    assert "action" not in verification_form["inputs"]


@pytest.mark.parametrize("enabled", [True, False])
def test_profile_save_preserves_notification_preference(client, profile_user, enabled):
    preference = NotificationPreference.objects.create(user=profile_user, in_app_enabled=enabled)
    client.force_login(profile_user)

    response = client.post(reverse("accounts:profile"), {
        "action": "profile", **PERSONAL_DETAILS, "first_name": "Mariana",
    })

    assert response.status_code == 302
    assert response["Location"] == reverse("accounts:profile")
    profile_user.refresh_from_db()
    preference.refresh_from_db()
    assert profile_user.first_name == "Mariana"
    assert preference.in_app_enabled is enabled
    assert profile_user.verified_email == "maria@example.edu"
    assert profile_user.email_is_verified
    successes = [str(message) for message in get_messages(response.wsgi_request) if message.level == SUCCESS]
    assert any("profile" in message.lower() and "notification" not in message.lower() for message in successes)


@pytest.mark.parametrize("enabled", [True, False])
def test_preferences_save_preserves_personal_details_and_verified_email(client, profile_user, enabled):
    preference = NotificationPreference.objects.create(user=profile_user, in_app_enabled=not enabled)
    client.force_login(profile_user)
    data = {"action": "preferences"}
    if enabled:
        data["in_app_enabled"] = "on"

    response = client.post(reverse("accounts:profile"), data)

    assert response.status_code == 302
    assert response["Location"] == reverse("accounts:profile")
    profile_user.refresh_from_db()
    preference.refresh_from_db()
    assert {field: getattr(profile_user, field) for field in PERSONAL_DETAILS} == PERSONAL_DETAILS
    assert profile_user.verified_email == "maria@example.edu"
    assert profile_user.email_is_verified
    assert preference.in_app_enabled is enabled
    successes = [str(message) for message in get_messages(response.wsgi_request) if message.level == SUCCESS]
    assert any("notification" in message.lower() and "profile" not in message.lower() for message in successes)


def test_invalid_profile_save_keeps_preference_form_unbound_and_all_saved_values(client, profile_user):
    preference = NotificationPreference.objects.create(user=profile_user, in_app_enabled=True)
    client.force_login(profile_user)

    response = client.post(reverse("accounts:profile"), {
        "action": "profile", **PERSONAL_DETAILS, "first_name": "Unsaved", "email": "invalid-address",
    })

    assert response.status_code in {200, 400}
    assert response.context["form"].is_bound
    assert "email" in response.context["form"].errors
    assert response.context["form"]["first_name"].value() == "Unsaved"
    assert not response.context["preferences_form"].is_bound
    assert response.context["preferences_form"]["in_app_enabled"].value() is True
    profile_user.refresh_from_db()
    preference.refresh_from_db()
    assert {field: getattr(profile_user, field) for field in PERSONAL_DETAILS} == PERSONAL_DETAILS
    assert profile_user.verified_email == "maria@example.edu"
    assert preference.in_app_enabled is True


@pytest.mark.parametrize("action", [None, "unknown"])
@pytest.mark.parametrize("existing_preference", [True, False])
def test_missing_or_unknown_profile_action_changes_nothing(client, profile_user, action, existing_preference):
    if existing_preference:
        NotificationPreference.objects.create(user=profile_user, in_app_enabled=True)
    client.force_login(profile_user)
    data = {**PERSONAL_DETAILS, "first_name": "Should not save", "email": "other@example.edu"}
    if action is not None:
        data["action"] = action

    response = client.post(reverse("accounts:profile"), data)

    assert response.status_code == 400
    assert not response.context["form"].is_bound
    assert not response.context["preferences_form"].is_bound
    assert any(message.level >= ERROR for message in get_messages(response.wsgi_request))
    profile_user.refresh_from_db()
    assert {field: getattr(profile_user, field) for field in PERSONAL_DETAILS} == PERSONAL_DETAILS
    assert profile_user.verified_email == "maria@example.edu"
    preferences = NotificationPreference.objects.filter(user=profile_user)
    assert preferences.count() == int(existing_preference)
    if existing_preference:
        assert preferences.get().in_app_enabled is True


def test_profile_email_change_requires_reverification_without_overwriting_verified_address(client, profile_user):
    NotificationPreference.objects.create(user=profile_user, in_app_enabled=True)
    client.force_login(profile_user)

    response = client.post(reverse("accounts:profile"), {
        "action": "profile", **PERSONAL_DETAILS, "email": "new-address@example.edu",
        "verified_email": "new-address@example.edu",
    })

    assert response.status_code == 302
    profile_user.refresh_from_db()
    assert profile_user.email == "new-address@example.edu"
    assert profile_user.verified_email == "maria@example.edu"
    assert not profile_user.email_is_verified


@pytest.mark.parametrize("role", ["med", "viewer", "med_admin", "admin"])
def test_profile_form_split_remains_available_for_each_account_role(client, users, role):
    user = users[role]
    preference = NotificationPreference.objects.create(user=user, in_app_enabled=True)
    client.force_login(user)

    response = client.post(reverse("accounts:profile"), {"action": "preferences"})

    assert response.status_code == 302
    preference.refresh_from_db()
    assert preference.in_app_enabled is False

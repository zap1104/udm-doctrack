"""Personal settings persist without granting account or document permissions."""
import pytest
from django.test import Client

from apps.accounts.models import ACCESSIBILITY_DEFAULTS, AccessibilityPreference
from tests.test_template_accessibility import RenderedHTML

URL = "/accounts/accessibility/"
CHOICES = {"text_size": "18", "high_contrast": "true", "motion": "reduce",
           "underline_links": "true", "spacing": "roomy", "larger_targets": "true"}


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["get", "post"])
def test_accessibility_requires_sign_in(client, method):
    response = getattr(client, method)(URL, CHOICES)
    assert response.status_code == 302
    assert "/accounts/login/" in response.url
    assert not AccessibilityPreference.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["admin", "med_admin", "sup", "viewer"])
def test_every_role_can_change_only_its_own_display(client, second_client, users, role):
    user = users[role]
    other = users["hr"]
    client.force_login(user)
    response = client.post(URL, {**CHOICES, "user": other.pk, "role": "SYSTEM_ADMIN"}, HTTP_ACCEPT="application/json")
    assert response.status_code == 200
    preference = AccessibilityPreference.objects.get(user=user)
    assert response.json()["preferences"] == preference.as_dict()
    assert preference.text_size == "18" and preference.high_contrast and preference.larger_targets
    assert preference.motion == "reduce" and preference.underline_links and preference.spacing == "roomy"
    assert not AccessibilityPreference.objects.filter(user=other).exists()
    old_role = user.role
    user.refresh_from_db()
    assert user.role == old_role
    # A separate session for the same account receives saved settings on first paint.
    second_client.force_login(user)
    root = RenderedHTML(second_client.get("/").content.decode()).nodes[0]
    assert root["tag"] == "html"
    assert root["attrs"]["data-a11y-text-size"] == "18"
    assert root["attrs"]["data-a11y-links"] == "underline"
    assert root["attrs"]["data-a11y-targets"] == "large"
    second_client.force_login(other)
    assert 'data-a11y-text-size="14"' in second_client.get("/").content.decode()


@pytest.mark.django_db
def test_defaults_do_not_create_records_and_controls_have_names_and_labels(client, users):
    client.force_login(users["sup"])
    for url in ["/", URL]:
        page = RenderedHTML(client.get(url).content.decode())
        for name in ACCESSIBILITY_DEFAULTS:
            assert page.label(page.field(name))["tag"] == "label"
        assert page.by_id("accessibility-text_size")["attrs"]["name"] == "text_size"
        assert 'data-a11y-contrast="standard"' in client.get(url).content.decode()
    assert not AccessibilityPreference.objects.exists()


@pytest.mark.django_db
def test_reset_restores_every_default_and_ignores_other_submitted_values(client, users):
    client.force_login(users["sup"])
    client.post(URL, CHOICES)
    response = client.post(URL, {**CHOICES, "action": "reset"}, HTTP_ACCEPT="application/json")
    assert response.status_code == 200
    assert response.json()["preferences"] == ACCESSIBILITY_DEFAULTS
    assert AccessibilityPreference.objects.get(user=users["sup"]).as_dict() == ACCESSIBILITY_DEFAULTS


@pytest.mark.django_db
@pytest.mark.parametrize("field,value", [("text_size", "40"), ("motion", "disable-security"), ("spacing", "unknown")])
def test_invalid_options_do_not_replace_saved_settings(client, users, field, value):
    client.force_login(users["sup"])
    client.post(URL, CHOICES)
    response = client.post(URL, {**CHOICES, field: value}, HTTP_ACCEPT="application/json")
    assert response.status_code == 400 and field in response.json()["errors"]
    assert AccessibilityPreference.objects.get(user=users["sup"]).text_size == "18"


@pytest.mark.django_db
def test_native_form_fallback_can_save_unchecked_options_and_reset(client, users):
    client.force_login(users["sup"])
    client.post(URL, CHOICES)
    response = client.post(URL, {"text_size": "16", "motion": "system", "spacing": "standard", "action": "save"})
    assert response.status_code == 302 and response.url == URL
    saved = AccessibilityPreference.objects.get(user=users["sup"])
    assert saved.text_size == "16" and not saved.high_contrast and not saved.underline_links
    assert not saved.larger_targets
    response = client.post(URL, {"action": "reset"})
    saved.refresh_from_db()
    assert response.status_code == 302 and saved.as_dict() == ACCESSIBILITY_DEFAULTS


@pytest.mark.django_db
def test_saving_is_csrf_protected_and_safe_get_does_not_reset(client, users):
    secured = Client(enforce_csrf_checks=True)
    secured.force_login(users["sup"])
    assert secured.post(URL, CHOICES, secure=True).status_code == 403
    assert not AccessibilityPreference.objects.exists()
    client.force_login(users["sup"])
    client.post(URL, CHOICES)
    assert client.get(URL, {"action": "reset"}).status_code == 200
    assert AccessibilityPreference.objects.get(user=users["sup"]).text_size == "18"


@pytest.mark.django_db
def test_admin_shell_receives_the_same_account_preferences(client, users):
    client.force_login(users["admin"])
    client.post(URL, CHOICES)
    html = client.get("/django-admin/").content.decode()
    assert 'id="accessibility-seed"' in html and '"text_size": "18"' in html
    assert "js/accessibility-init.js" in html and "css/accessibility.css" in html


@pytest.mark.django_db
def test_smaller_text_and_native_switches_keep_saved_choice_values(client, users):
    client.force_login(users["sup"])
    response = client.post(URL, {**CHOICES, "text_size": "12"})
    assert response.status_code == 302
    preference = AccessibilityPreference.objects.get(user=users["sup"])
    assert preference.text_size == "12" and preference.motion == "reduce" and preference.spacing == "roomy"
    page = RenderedHTML(client.get(URL).content.decode())
    assert [node["attrs"]["name"] for node in page.nodes
            if node["tag"] == "select" and "name" in node["attrs"]] == ["text_size"]
    for name, on_value in [("motion", "reduce"), ("spacing", "roomy")]:
        switch = page.field(name)
        assert switch["tag"] == "input" and switch["attrs"]["type"] == "checkbox"
        assert switch["attrs"]["role"] == "switch" and "checked" in switch["attrs"]
        assert switch["attrs"]["value"] == on_value
    # Unchecked native switches are omitted by a browser without JavaScript.
    response = client.post(URL, {"text_size": "12"})
    assert response.status_code == 302
    preference.refresh_from_db()
    assert preference.motion == "system" and preference.spacing == "standard"
    assert preference.text_size == "12"
    root = RenderedHTML(client.get(URL).content.decode()).nodes[0]
    assert root["attrs"]["data-a11y-text-size"] == "12"

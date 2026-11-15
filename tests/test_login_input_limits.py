import pytest
from axes.models import AccessAttempt
from django.contrib.auth import get_user_model
from django.urls import reverse


@pytest.mark.parametrize("route", ["accounts:login", "admin:login"])
@pytest.mark.parametrize("length", [151, 256])
def test_oversized_username_is_rejected_before_recording_failed_attempts(client, settings, db, route, length):
    settings.EMAIL_OTP_REQUIRED = False
    client.raise_request_exception = False
    username = "u" * length

    response = client.post(reverse(route), {"username": username, "password": "invalid"})

    assert response.status_code == 200
    assert response.context["form"].has_error("username", code="max_length")
    assert not AccessAttempt.objects.filter(username=username).exists()


@pytest.mark.parametrize("route", ["accounts:login", "admin:login"])
def test_username_at_length_limit_can_sign_in(client, settings, db, route):
    settings.EMAIL_OTP_REQUIRED = False
    user_model = get_user_model()
    user = user_model.objects.create_user(
        username="u" * 150,
        password="ValidFixturePassword!",
        is_staff=True,
        role=user_model.Role.SYSTEM_ADMIN,
    )

    response = client.post(reverse(route), {"username": user.username, "password": "ValidFixturePassword!"})

    assert response.status_code == 302
    assert client.session["_auth_user_id"] == str(user.pk)

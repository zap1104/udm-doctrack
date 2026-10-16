"""Password writes must respect newer account administration and credentials."""

import pytest
from django.contrib.auth import HASH_SESSION_KEY, SESSION_KEY
from django.contrib.auth.hashers import make_password
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import PasswordResetConfirmView
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import RequestFactory
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.accounts.forms import AccountPasswordChangeForm, AccountSetPasswordForm
from apps.accounts.models import User
from apps.accounts.views import PasswordChangeViewCustom

pytestmark = pytest.mark.django_db

OLD_PASSWORD = "TestPass123!"
NEW_PASSWORD = "ChangedAccountPassword789!"
ADMIN_PASSWORD = "AdministratorResetPassword456!"


def password_form(form_class, user):
    data = {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD}
    if form_class is AccountPasswordChangeForm:
        data["old_password"] = OLD_PASSWORD
    form = form_class(user, data)
    assert form.is_valid(), form.errors
    return form


@pytest.mark.parametrize("form_class", [AccountPasswordChangeForm, AccountSetPasswordForm])
@pytest.mark.parametrize("field", ["is_active", "role", "office_id", "email", "verified_email"])
def test_password_save_does_not_restore_stale_administrative_fields(users, offices, form_class, field):
    user = users["med"]
    form = password_form(form_class, user)
    newer_value = {
        "is_active": False,
        "role": User.Role.VIEWER,
        "office_id": offices["SUP"].pk,
        "email": "current-address@example.edu",
        "verified_email": "verified-address@example.edu",
    }[field]
    # Validation has finished with the request's original account instance.
    User.objects.filter(pk=user.pk).update(**{field: newer_value})

    try:
        form.save()
    except (PermissionDenied, ValidationError):
        # Refusing a newly suspended account is also a safe outcome.
        pass

    user.refresh_from_db()
    assert getattr(user, field) == newer_value


@pytest.mark.parametrize("form_class", [AccountPasswordChangeForm, AccountSetPasswordForm])
def test_a_validated_password_form_cannot_replace_a_newer_administrator_reset(users, form_class):
    user = users["med"]
    form = password_form(form_class, user)
    newer_hash = make_password(ADMIN_PASSWORD)
    User.objects.filter(pk=user.pk).update(password=newer_hash, must_change_password=True)

    with pytest.raises(ValidationError):
        form.save()

    user.refresh_from_db()
    assert user.password == newer_hash
    assert user.check_password(ADMIN_PASSWORD)
    assert user.must_change_password is True


def stale_password_request(user):
    request = RequestFactory().post(reverse("accounts:password_change"), {
        "old_password": OLD_PASSWORD,
        "new_password1": NEW_PASSWORD,
        "new_password2": NEW_PASSWORD,
    })
    request.user = user
    request.session = SessionStore()
    request.session[SESSION_KEY] = str(user.pk)
    request.session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    request._messages = FallbackStorage(request)
    # Exercise the view with the authenticated user already resolved, which is
    # precisely the gap before another administrator's transaction completes.
    request._dont_enforce_csrf_checks = True
    return request


def test_a_password_request_loaded_before_suspension_cannot_reactivate_the_account(users):
    user = users["med"]
    request = stale_password_request(user)
    User.objects.filter(pk=user.pk).update(is_active=False)

    try:
        response = PasswordChangeViewCustom.as_view()(request)
    except PermissionDenied:
        response = None

    user.refresh_from_db()
    assert user.is_active is False
    assert user.check_password(OLD_PASSWORD)
    assert response is None or response.status_code in {200, 302, 403}


def test_a_password_request_loaded_before_an_administrator_reset_cannot_undo_it(users):
    user = users["med"]
    request = stale_password_request(user)
    newer_hash = make_password(ADMIN_PASSWORD)
    User.objects.filter(pk=user.pk).update(password=newer_hash, must_change_password=True)

    response = PasswordChangeViewCustom.as_view()(request)

    user.refresh_from_db()
    assert user.password == newer_hash
    assert user.must_change_password is True
    assert response.status_code in {200, 302, 403}


def test_forced_password_change_rejects_reusing_the_administrator_password(client, users):
    user = users["med"]
    User.objects.filter(pk=user.pk).update(must_change_password=True)
    client.force_login(user)

    response = client.post(reverse("accounts:password_change"), {
        "old_password": OLD_PASSWORD,
        "new_password1": OLD_PASSWORD,
        "new_password2": OLD_PASSWORD,
    })

    user.refresh_from_db()
    assert response.status_code == 200
    assert response.context["form"].errors
    assert user.must_change_password is True
    assert user.check_password(OLD_PASSWORD)


def test_forced_password_change_accepts_a_different_password_and_preserves_the_session(client, users):
    user = users["med"]
    User.objects.filter(pk=user.pk).update(must_change_password=True)
    client.force_login(user)

    response = client.post(reverse("accounts:password_change"), {
        "old_password": OLD_PASSWORD,
        "new_password1": NEW_PASSWORD,
        "new_password2": NEW_PASSWORD,
    })

    user.refresh_from_db()
    assert response.status_code == 302
    assert response["Location"] == reverse("core:dashboard")
    assert user.must_change_password is False
    assert user.check_password(NEW_PASSWORD)
    assert not user.check_password(OLD_PASSWORD)
    assert client.session[SESSION_KEY] == str(user.pk)
    assert client.get(reverse("core:dashboard")).status_code == 200


def recovery_form_url(client, user, settings):
    settings.EMAIL_CONFIGURED = True
    user.email = user.verified_email = "recovery-owner@example.edu"
    user.save(update_fields=["email", "verified_email"])
    url = reverse("accounts:password_reset_confirm", kwargs={
        "uidb64": urlsafe_base64_encode(force_bytes(user.pk)),
        "token": default_token_generator.make_token(user),
    })
    response = client.get(url)
    assert response.status_code == 302
    return response["Location"]


@pytest.mark.parametrize("change", ["password", "suspension", "email"])
def test_recovery_rechecks_the_account_after_an_administrator_changes_the_loaded_snapshot(
    client, users, settings, monkeypatch, change,
):
    user = users["med"]
    url = recovery_form_url(client, user, settings)
    newer_hash = make_password(ADMIN_PASSWORD)
    updates = {
        "password": {"password": newer_hash, "must_change_password": True},
        "suspension": {"is_active": False},
        "email": {"email": "new-recovery-owner@example.edu"},
    }[change]
    original_get_user = PasswordResetConfirmView.get_user

    def load_before_admin_change(view, uidb64):
        loaded = original_get_user(view, uidb64)
        # The base implementation has read the user but has not yet checked
        # its recovery token. An administrator now commits a newer state.
        User.objects.filter(pk=loaded.pk).update(**updates)
        return loaded

    monkeypatch.setattr(PasswordResetConfirmView, "get_user", load_before_admin_change)
    response = client.post(url, {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})

    user.refresh_from_db()
    assert response.status_code == 200
    assert response.context["validlink"] is False
    assert all(getattr(user, field) == value for field, value in updates.items())
    assert user.check_password(ADMIN_PASSWORD if change == "password" else OLD_PASSWORD)


def test_recovery_still_accepts_a_current_verified_email_token(client, users, settings):
    user = users["med"]
    url = recovery_form_url(client, user, settings)

    response = client.post(url, {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})

    user.refresh_from_db()
    assert response.status_code == 302
    assert response["Location"] == reverse("accounts:password_reset_complete")
    assert user.check_password(NEW_PASSWORD)
    assert user.email_is_verified

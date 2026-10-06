"""Personal-detail writes must not undo concurrent account administration."""

import pytest
from django.contrib.auth.hashers import make_password
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory

from apps.accounts.models import User
from apps.accounts.views import ProfileView
from apps.core.models import NotificationPreference

pytestmark = pytest.mark.django_db


def stale_profile_request(user, data):
    request = RequestFactory().post("/accounts/profile/", data)
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


@pytest.mark.parametrize("field", ["is_active", "role", "office_id", "must_change_password", "password"])
def test_profile_save_does_not_restore_stale_administrative_fields(users, offices, field):
    user = users["med"]
    new_values = {
        "is_active": False, "role": User.Role.VIEWER, "office_id": offices["SUP"].pk,
        "must_change_password": True,
    }
    new_value = make_password("AdministratorReset789!") if field == "password" else new_values[field]
    request = stale_profile_request(user, {
        "action": "profile", "first_name": "Updated", "last_name": "Staff",
        "email": "updated@example.edu", "phone": "", "position": "Records officer",
    })
    # The request has loaded its user; an administrator now changes the row.
    User.objects.filter(pk=user.pk).update(**{field: new_value})

    response = ProfileView.as_view()(request)

    assert response.status_code == 302
    user.refresh_from_db()
    assert getattr(user, field) == new_value
    assert user.first_name == "Updated"
    assert user.email == "updated@example.edu"


def test_preferences_save_does_not_restore_stale_administrative_fields(users, offices):
    user = users["med"]
    NotificationPreference.objects.create(user=user, in_app_enabled=True)
    request = stale_profile_request(user, {"action": "preferences"})
    updates = {"is_active": False, "role": User.Role.VIEWER, "office_id": offices["SUP"].pk,
               "must_change_password": True, "password": make_password("AdministratorReset789!")}
    User.objects.filter(pk=user.pk).update(**updates)

    response = ProfileView.as_view()(request)

    assert response.status_code == 302
    user.refresh_from_db()
    assert all(getattr(user, field) == value for field, value in updates.items())
    assert NotificationPreference.objects.get(user=user).in_app_enabled is False

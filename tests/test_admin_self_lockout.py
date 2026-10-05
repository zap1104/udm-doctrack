"""An administrator must not be able to lock everyone out of administration.

The suspend button already refused to act on your own account. The edit form
could do the same damage two other ways — drop your own role to USER, or clear
"Account is active" — and nothing in the UI could undo either afterwards.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.contrib.messages.storage.fallback import FallbackStorage
from django.db import close_old_connections, connections
from django.test import RequestFactory

from apps.accounts.forms import AdminUserUpdateForm
from apps.accounts.models import User
from apps.accounts.views import UserUpdateView


def _payload(user, **overrides):
    data = {
        "first_name": user.first_name or "A",
        "last_name": user.last_name or "B",
        "email": user.email,
        "office": user.office_id,
        "role": user.role,
        "position": user.position,
        "phone": user.phone,
        "is_active": "on",
    }
    data.update(overrides)
    return data


@pytest.mark.django_db
def test_admin_cannot_remove_their_own_role(users):
    admin = users["admin"]
    form = AdminUserUpdateForm(
        _payload(admin, role=User.Role.USER), instance=admin, editing_self=True
    )
    assert form.is_valid() is False
    assert "role" in form.errors


@pytest.mark.django_db
def test_admin_cannot_deactivate_themselves(users):
    admin = users["admin"]
    data = _payload(admin)
    data.pop("is_active")  # an unchecked box is simply not posted
    form = AdminUserUpdateForm(data, instance=admin, editing_self=True)
    assert form.is_valid() is False
    assert "is_active" in form.errors


@pytest.mark.django_db
def test_admin_may_still_edit_their_own_details(users):
    admin = users["admin"]
    form = AdminUserUpdateForm(
        _payload(admin, first_name="Renamed"), instance=admin, editing_self=True
    )
    assert form.is_valid() is True, form.errors


@pytest.mark.django_db
def test_admin_may_still_demote_somebody_else(users):
    other = users["med"]
    form = AdminUserUpdateForm(
        _payload(other, role=User.Role.USER), instance=other, editing_self=False
    )
    assert form.is_valid() is True, form.errors


@pytest.mark.django_db
def test_the_view_blocks_self_demotion_end_to_end(client, users):
    admin = users["admin"]
    client.force_login(admin)
    client.post(f"/accounts/users/{admin.pk}/", _payload(admin, role=User.Role.USER))

    admin.refresh_from_db()
    assert admin.role == User.Role.SYSTEM_ADMIN


@pytest.mark.django_db
def test_the_last_system_admin_cannot_step_down_to_office_admin(users):
    """Demoting yourself one rung is still a demotion nobody is left to undo."""
    admin = users["admin"]
    form = AdminUserUpdateForm(
        _payload(admin, role=User.Role.ADMIN), instance=admin, editing_self=True, actor=admin
    )
    assert form.is_valid() is False
    assert "role" in form.errors


@pytest.mark.django_db
def test_stepping_down_is_allowed_once_somebody_else_can_take_over(users, offices):
    admin = users["admin"]
    User.objects.create_user(
        username="second-sysadmin", password="TestPass123!",
        office=offices["REC"], role=User.Role.SYSTEM_ADMIN,
    )
    form = AdminUserUpdateForm(
        _payload(admin, role=User.Role.ADMIN), instance=admin, editing_self=True, actor=admin
    )
    assert form.is_valid() is True, form.errors


@pytest.mark.django_db
def test_an_inactive_system_admin_cannot_take_over(users, offices):
    actor = users["admin"]
    User.objects.create_user(username="inactive-sysadmin", password="TestPass123!", office=offices["REC"],
                             role=User.Role.SYSTEM_ADMIN, is_active=False)
    form = AdminUserUpdateForm(_payload(actor, role=User.Role.ADMIN), instance=actor, editing_self=True, actor=actor)
    assert not form.is_valid()
    assert "role" in form.errors


@pytest.mark.django_db(transaction=True)
def test_two_system_admins_cannot_both_step_down_at_the_same_time(users, offices):
    second = User.objects.create_user(username="second-concurrent-admin", password="TestPass123!",
                                      office=offices["REC"], role=User.Role.SYSTEM_ADMIN)
    barrier = Barrier(2)

    def step_down(user_id):
        close_old_connections()
        try:
            actor = User.objects.get(pk=user_id)
            request = RequestFactory().post(f"/accounts/users/{user_id}/", _payload(actor, role=User.Role.ADMIN))
            request.user = actor
            request.session = {}
            request._messages = FallbackStorage(request)
            barrier.wait(timeout=10)
            return UserUpdateView.as_view()(request, pk=user_id).status_code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = list(workers.map(step_down, [users["admin"].pk, second.pk]))
    assert sorted(outcomes) == [200, 302]
    assert User.objects.filter(role=User.Role.SYSTEM_ADMIN, is_active=True).count() == 1

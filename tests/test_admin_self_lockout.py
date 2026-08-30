"""An administrator must not be able to lock everyone out of administration.

The suspend button already refused to act on your own account. The edit form
could do the same damage two other ways — drop your own role to USER, or clear
"Account is active" — and nothing in the UI could undo either afterwards.
"""

from __future__ import annotations

import pytest

from apps.accounts.forms import AdminUserUpdateForm
from apps.accounts.models import User


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

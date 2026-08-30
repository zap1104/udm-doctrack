"""An office administrator administers their own office and nobody else's.

This is a security fix, not a tidy-up. `UserListView` did
`User.objects.select_related("office")` with no office filter, and the edit,
password-reset and suspend views each did `get_object_or_404(User, pk=pk)` —
so every screen in the administration area was scoped by role and by nothing
else. Splitting ADMIN into an office role and a system role is what turns that
from latent into live: before the split, everyone who could reach these screens
was global by definition, so the missing filter never showed.

The password tests are about the reset *path*, not about revealing anything:
Argon2 means no screen can show an existing password, and nothing here should
ever make that untrue.
"""

from __future__ import annotations

import pytest

from apps.accounts.forms import AdminUserCreateForm, AdminUserUpdateForm
from apps.accounts.models import User


def _update_payload(user, **overrides):
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


# --- the list --------------------------------------------------------------
@pytest.mark.django_db
def test_an_office_admin_sees_only_their_own_offices_accounts(client, users):
    client.force_login(users["med_admin"])
    body = client.get("/accounts/users/").content.decode()

    assert "med" in body, "their own office's user"
    assert "viewer" in body, "also MED"
    assert ">sup<" not in body, "another office's user must not be listed"
    assert ">hr<" not in body


@pytest.mark.django_db
def test_a_system_admin_still_sees_everybody(client, users):
    client.force_login(users["admin"])
    body = client.get("/accounts/users/").content.decode()

    for username in ("med", "sup", "hr", "viewer"):
        assert username in body


@pytest.mark.django_db
def test_an_office_admin_with_no_office_administers_nobody(client, users):
    """`filter(office_id=None)` would have handed them every unassigned account."""
    stray = users["med_admin"]
    stray.office = None
    stray.save(update_fields=["office"])

    client.force_login(stray)
    body = client.get("/accounts/users/").content.decode()

    assert ">med<" not in body
    assert ">sup<" not in body


# --- the edit / reset / suspend endpoints ----------------------------------
@pytest.mark.django_db
def test_an_office_admin_cannot_open_another_offices_account(client, users):
    client.force_login(users["med_admin"])
    response = client.get(f"/accounts/users/{users['sup'].pk}/")

    assert response.status_code == 404


@pytest.mark.django_db
def test_an_office_admin_cannot_edit_another_offices_account(client, users):
    target = users["sup"]
    client.force_login(users["med_admin"])
    response = client.post(
        f"/accounts/users/{target.pk}/", _update_payload(target, first_name="Hijacked")
    )

    assert response.status_code == 404
    target.refresh_from_db()
    assert target.first_name != "Hijacked"


@pytest.mark.django_db
def test_an_office_admin_cannot_reset_another_offices_password(client, users):
    target = users["sup"]
    original = target.password
    client.force_login(users["med_admin"])
    response = client.post(
        f"/accounts/users/{target.pk}/",
        {"set_password": "1", "new_password1": "AnotherPass456!", "new_password2": "AnotherPass456!"},
    )

    assert response.status_code == 404
    target.refresh_from_db()
    assert target.password == original


@pytest.mark.django_db
def test_an_office_admin_cannot_suspend_another_offices_account(client, users):
    target = users["sup"]
    client.force_login(users["med_admin"])
    response = client.post(f"/accounts/users/{target.pk}/toggle/")

    assert response.status_code == 404
    target.refresh_from_db()
    assert target.is_active is True


@pytest.mark.django_db
def test_an_office_admin_can_still_do_all_of_that_in_their_own_office(client, users):
    target = users["med"]
    client.force_login(users["med_admin"])

    assert client.get(f"/accounts/users/{target.pk}/").status_code == 200
    assert client.post(f"/accounts/users/{target.pk}/toggle/").status_code == 302
    target.refresh_from_db()
    assert target.is_active is False


@pytest.mark.django_db
def test_a_reset_sets_a_new_password_and_never_shows_the_old_one(client, users):
    """Argon2 makes revealing one impossible; this guards the path around it."""
    target = users["med"]
    original = target.password
    client.force_login(users["med_admin"])
    response = client.post(
        f"/accounts/users/{target.pk}/",
        {"set_password": "1", "new_password1": "BrandNewPass789!", "new_password2": "BrandNewPass789!"},
    )

    assert response.status_code == 302
    target.refresh_from_db()
    assert target.password != original
    assert target.check_password("BrandNewPass789!")
    assert target.must_change_password is True

    body = client.get(f"/accounts/users/{target.pk}/").content.decode()
    assert "BrandNewPass789!" not in body
    assert target.password not in body


# --- privilege escalation --------------------------------------------------
@pytest.mark.django_db
def test_an_office_admin_cannot_mint_a_system_admin(users):
    form = AdminUserCreateForm(
        {
            "username": "sneaky", "first_name": "S", "last_name": "N", "email": "",
            "office": users["med_admin"].office_id, "role": User.Role.SYSTEM_ADMIN,
            "position": "", "phone": "",
            "password1": "TestPass123!", "password2": "TestPass123!",
        },
        actor=users["med_admin"],
    )
    assert form.is_valid() is False
    assert "role" in form.errors


@pytest.mark.django_db
def test_an_office_admin_cannot_promote_an_existing_account_to_system_admin(users):
    target = users["med"]
    form = AdminUserUpdateForm(
        _update_payload(target, role=User.Role.SYSTEM_ADMIN),
        instance=target, actor=users["med_admin"],
    )
    assert form.is_valid() is False
    assert "role" in form.errors


@pytest.mark.django_db
def test_an_office_admin_cannot_move_somebody_into_another_office(users):
    target = users["med"]
    form = AdminUserUpdateForm(
        _update_payload(target, office=users["sup"].office_id),
        instance=target, actor=users["med_admin"],
    )
    assert form.is_valid() is False
    assert "office" in form.errors


@pytest.mark.django_db
def test_a_system_admin_may_do_both(users):
    target = users["med"]
    form = AdminUserUpdateForm(
        _update_payload(target, role=User.Role.SYSTEM_ADMIN, office=users["sup"].office_id),
        instance=target, actor=users["admin"],
    )
    assert form.is_valid() is True, form.errors


# --- offices are system-admin territory ------------------------------------
@pytest.mark.django_db
def test_an_office_admin_cannot_reach_the_office_screens(client, users):
    client.force_login(users["med_admin"])

    assert client.get("/accounts/offices/").status_code == 403
    assert client.get("/administration/offices/").status_code == 403


@pytest.mark.django_db
def test_a_system_admin_can_create_an_office_without_a_code_change(client, users):
    from apps.accounts.models import Office

    client.force_login(users["admin"])
    assert client.get("/administration/offices/").status_code == 200

    response = client.post(
        "/administration/offices/new/",
        {"code": "LIB", "name": "University Library", "short_name": "", "cluster": "OVPA",
         "parent": "", "head_name": "", "email": "", "location": "", "colour": "",
         "sort_order": 100, "is_active": "on"},
    )

    assert response.status_code == 302
    assert Office.objects.filter(code="LIB").exists()


@pytest.mark.django_db
def test_the_offices_tile_is_not_offered_to_an_office_admin(client, users):
    """Showing a tile that answers with a 403 is its own small bug."""
    client.force_login(users["med_admin"])
    body = client.get("/administration/").content.decode()

    assert "/administration/offices/" not in body

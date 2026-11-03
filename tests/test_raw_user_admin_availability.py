"""Raw account administration must retain a usable administrator and current state."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import OperationalError, close_old_connections, transaction

from apps.accounts.admin import UserAdmin
from apps.accounts.models import User
from tests.auth_helpers import prepare_verified_user
from tests.conftest import HttpsClient


@pytest.fixture(autouse=True)
def required_otp(settings):
    settings.EMAIL_OTP_REQUIRED = True


def change_data(user, **changes):
    data = {
        'username': user.username, 'first_name': user.first_name,
        'last_name': user.last_name, 'office': user.office_id, 'role': user.role,
        'position': user.position, 'phone': user.phone,
        'is_active': 'on' if user.is_active else '',
        'is_staff': 'on' if user.is_staff else '',
        'is_superuser': 'on' if user.is_superuser else '',
        'date_joined_0': user.date_joined.strftime('%Y-%m-%d'),
        'date_joined_1': user.date_joined.strftime('%H:%M:%S'),
        '_save': 'Save',
    }
    data.update(changes)
    return data


def second_admin(users):
    other = users['med_admin']
    other.role = 'SYSTEM_ADMIN'
    other.is_staff = other.is_superuser = True
    other.save(update_fields=['role', 'is_staff', 'is_superuser'])
    prepare_verified_user(other)
    return other


@pytest.mark.django_db
@pytest.mark.parametrize('changes', [{'role': 'USER'}, {'role': 'VIEWER'}, {'is_active': ''}])
def test_raw_admin_cannot_remove_own_administrator_access(client, users, changes):
    actor = users['admin']
    second_admin(users)
    client.force_verified_login(actor)
    response = client.post(f'/django-admin/accounts/user/{actor.pk}/change/', change_data(actor, **changes))
    actor.refresh_from_db()
    assert actor.role == 'SYSTEM_ADMIN'
    assert actor.is_active
    assert response.status_code == 200
    assert response.context['adminform'].form.non_field_errors()


@pytest.mark.django_db
@pytest.mark.parametrize('changes', [{'role': 'ADMIN'}, {'is_staff': ''}])
@pytest.mark.parametrize('backup', ['absent', 'inactive', 'not_staff', 'unusable_password', 'unverified_email'])
def test_raw_admin_preserves_last_ready_system_administrator(client, users, changes, backup):
    actor = users['admin']
    if backup != 'absent':
        other = second_admin(users)
        if backup == 'inactive':
            other.is_active = False
        elif backup == 'not_staff':
            other.is_staff = False
        elif backup == 'unusable_password':
            other.set_unusable_password()
        else:
            other.verified_email = ''
        other.save()
    client.force_verified_login(actor)
    response = client.post(f'/django-admin/accounts/user/{actor.pk}/change/', change_data(actor, **changes))
    actor.refresh_from_db()
    assert actor.role == 'SYSTEM_ADMIN'
    assert actor.is_staff
    assert response.status_code == 200
    assert response.context['adminform'].form.non_field_errors()


@pytest.mark.django_db
def test_raw_admin_can_step_down_after_a_ready_replacement_exists(client, users):
    actor = users['admin']
    second_admin(users)
    client.force_verified_login(actor)
    response = client.post(f'/django-admin/accounts/user/{actor.pk}/change/', change_data(actor, role='ADMIN'))
    actor.refresh_from_db()
    assert response.status_code == 302
    assert actor.role == 'ADMIN'


@pytest.mark.django_db
def test_raw_admin_setup_mode_does_not_require_verified_replacement_email(client, users, settings):
    settings.EMAIL_OTP_REQUIRED = False
    actor = users['admin']
    other = second_admin(users)
    other.verified_email = ''
    other.save(update_fields=['verified_email'])
    client.force_login(actor)
    response = client.post(f'/django-admin/accounts/user/{actor.pk}/change/', change_data(actor, role='ADMIN'))
    actor.refresh_from_db()
    assert response.status_code == 302
    assert actor.role == 'ADMIN'


@pytest.mark.django_db
@pytest.mark.parametrize('bulk', [False, True])
def test_raw_admin_cannot_delete_own_account(client, users, bulk):
    actor = users['admin']
    second_admin(users)
    client.force_verified_login(actor)
    if bulk:
        response = client.post('/django-admin/accounts/user/', {
            'action': 'delete_selected', '_selected_action': [actor.pk, users['hr'].pk], 'post': 'yes',
        })
        assert User.objects.filter(pk=users['hr'].pk).exists()
    else:
        response = client.post(f'/django-admin/accounts/user/{actor.pk}/delete/', {'post': 'yes'})
    assert response.status_code == 403
    assert User.objects.filter(pk=actor.pk).exists()


@pytest.mark.django_db
@pytest.mark.parametrize('bulk', [False, True])
def test_raw_admin_can_delete_another_account(client, users, bulk):
    actor, target = users['admin'], users['hr']
    client.force_verified_login(actor)
    if bulk:
        response = client.post('/django-admin/accounts/user/', {
            'action': 'delete_selected', '_selected_action': [target.pk], 'post': 'yes',
        })
    else:
        response = client.post(f'/django-admin/accounts/user/{target.pk}/delete/', {'post': 'yes'})
    assert response.status_code == 302
    assert not User.objects.filter(pk=target.pk).exists()


@pytest.mark.django_db
def test_raw_admin_cannot_disable_the_last_ready_administrator_password(client, users):
    actor = users['admin']
    client.force_verified_login(actor)
    response = client.post(f'/django-admin/accounts/user/{actor.pk}/password/', {
        'usable_password': 'false', 'unset-password': 'Disable password-based authentication',
    })
    actor.refresh_from_db()
    assert actor.has_usable_password()
    assert response.status_code == 200
    assert response.context['adminForm'].form.non_field_errors()


@pytest.mark.django_db(transaction=True)
def test_concurrent_raw_admin_step_down_retains_one_ready_system_administrator(users):
    actors = [users['admin'], second_admin(users)]
    clients = [HttpsClient(), HttpsClient()]
    for browser, actor in zip(clients, actors, strict=True):
        browser.force_verified_login(actor)
    barrier = Barrier(2)

    def demote(index):
        close_old_connections()
        try:
            actor = actors[index]
            barrier.wait(timeout=10)
            return clients[index].post(
                f'/django-admin/accounts/user/{actor.pk}/change/', change_data(actor, role='ADMIN'),
            ).status_code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(demote, range(2)))
    assert sorted(statuses) == [200, 302]
    assert User.objects.filter(role='SYSTEM_ADMIN', is_active=True, is_staff=True).count() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('password_view', [False, True])
def test_raw_admin_locks_target_before_building_a_form(client, users, monkeypatch, password_view):
    actor, target = users['admin'], users['hr']
    client.force_verified_login(actor)
    observed = []

    def another_transaction_can_lock_target():
        close_old_connections()
        try:
            with transaction.atomic():
                User.objects.select_for_update(nowait=True).get(pk=target.pk)
            return True
        except OperationalError:
            return False
        finally:
            close_old_connections()

    original = UserAdmin.get_object

    def observe_target(self, request, object_id, from_field=None):
        result = original(self, request, object_id, from_field=from_field)
        if result is not None and result.pk == target.pk:
            with ThreadPoolExecutor(max_workers=1) as pool:
                observed.append(pool.submit(another_transaction_can_lock_target).result(timeout=10))
        return result

    monkeypatch.setattr(UserAdmin, 'get_object', observe_target)
    if password_view:
        response = client.post(f'/django-admin/accounts/user/{target.pk}/password/', {
            'password1': 'LongReplacementPassword!9', 'password2': 'LongReplacementPassword!9',
        })
    else:
        response = client.post(f'/django-admin/accounts/user/{target.pk}/change/', change_data(target, first_name='Updated'))
    assert response.status_code == 302
    assert observed and not any(observed)

"""Real password/email-code journeys; no authenticated-session shortcuts."""
import re
from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def otp_enabled(settings):
    settings.EMAIL_OTP_REQUIRED = True
    settings.EMAIL_CONFIGURED = True
    settings.EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'


def ready(user):
    user.email = f'{user.username}@example.test'
    user.verified_email = user.email
    user.save(update_fields=['email', 'verified_email'])
    return user


def last_code():
    return re.search(r'\b(\d{6})\b', mail.outbox[-1].body).group(1)


@pytest.mark.parametrize('role', ['admin', 'med_admin', 'med', 'viewer'])
def test_password_does_not_authenticate_before_email_code(client, users, role):
    actor = ready(users[role])
    before = actor.last_login
    response = client.post('/accounts/login/', {'username': actor.username, 'password': 'TestPass123!'})
    assert response.status_code == 302
    assert response.url.startswith('/accounts/otp/')
    assert '_auth_user_id' not in client.session
    actor.refresh_from_db()
    assert actor.last_login == before
    response = client.post('/accounts/otp/', {'code': last_code()})
    assert response.status_code == 302
    assert client.session['_auth_user_id'] == str(actor.pk)
    assert client.get('/').status_code == 200


def test_password_only_old_session_cannot_access_records(client, users):
    client.force_login(ready(users['admin']))
    assert client.get('/').status_code == 302
    assert client.get('/django-admin/').status_code == 302


def test_raw_admin_password_login_uses_same_otp_flow(client, users):
    actor = ready(users['admin'])
    response = client.post('/django-admin/login/', {'username': actor.username, 'password': 'TestPass123!', 'next': '/django-admin/'})
    assert response.status_code == 302
    assert '_auth_user_id' not in client.session
    # Raw POST must not be accepted as an alternative password-only login.
    assert response.url.startswith('/accounts/')


def test_unverified_email_does_not_send_code(client, users):
    actor = users['med']
    actor.email = 'unverified@example.test'
    actor.save(update_fields=['email'])
    response = client.post('/accounts/login/', {'username': actor.username, 'password': 'TestPass123!'})
    assert response.status_code == 200
    assert '_auth_user_id' not in client.session
    assert not getattr(mail, 'outbox', [])


def test_code_expires_at_boundary(client, users):
    from apps.accounts.security_models import EmailChallenge
    actor = ready(users['med'])
    client.post('/accounts/login/', {'username': actor.username, 'password': 'TestPass123!'})
    code = last_code()
    EmailChallenge.objects.filter(user=actor).update(expires_at=timezone.now())
    assert client.post('/accounts/otp/', {'code': code}).status_code == 200
    assert '_auth_user_id' not in client.session


def test_five_wrong_codes_exhaust_challenge(client, users):
    actor = ready(users['med'])
    client.post('/accounts/login/', {'username': actor.username, 'password': 'TestPass123!'})
    code = last_code()
    wrong = '000000' if code != '000000' else '111111'
    for _ in range(5):
        client.post('/accounts/otp/', {'code': wrong})
    client.post('/accounts/otp/', {'code': code})
    assert '_auth_user_id' not in client.session


def test_resend_invalidates_previous_code_without_resetting_attempts(client, users):
    from apps.accounts.security_models import EmailChallenge
    actor = ready(users['med'])
    client.post('/accounts/login/', {'username': actor.username, 'password': 'TestPass123!'})
    old = last_code()
    client.post('/accounts/otp/', {'code': 'bad'})
    EmailChallenge.objects.filter(user=actor).update(last_sent_at=timezone.now()-timedelta(seconds=61))
    client.post('/accounts/otp/resend/')
    current = last_code()
    challenge = EmailChallenge.objects.get(user=actor, purpose='login')
    assert challenge.attempts == 1
    assert old != current
    client.post('/accounts/otp/', {'code': old})
    assert '_auth_user_id' not in client.session
    client.post('/accounts/otp/', {'code': current})
    assert client.session['_auth_user_id'] == str(actor.pk)


def test_code_is_not_stored_in_database_session_or_audit(client, users):
    from apps.accounts.security_models import EmailChallenge
    from apps.core.models import AuditLog
    actor = ready(users['med'])
    client.post('/accounts/login/', {'username': actor.username, 'password': 'TestPass123!'})
    code = last_code()
    assert code not in str(list(EmailChallenge.objects.values()))
    assert code not in str(dict(client.session))
    assert code not in str(list(AuditLog.objects.values()))


def test_readiness_rejects_unverified_accounts(users):
    from django.core.management import call_command
    from django.core.management.base import CommandError
    with pytest.raises(CommandError, match='verified'):
        call_command('check_otp_readiness')


def test_account_creation_requires_email_when_otp_enabled(users):
    from apps.accounts.forms import AdminUserCreateForm
    form = AdminUserCreateForm({'username': 'new', 'first_name': 'New', 'last_name': 'User',
        'office': users['med'].office_id, 'role': 'USER', 'password1': 'LongNewPassword!9',
        'password2': 'LongNewPassword!9'}, actor=users['admin'])
    assert not form.is_valid()
    assert 'email' in form.errors


def test_profile_cannot_swap_otp_destination_without_verifying_new_address(client, users):
    actor = ready(users['med'])
    client.post('/accounts/login/', {'username': actor.username, 'password': 'TestPass123!'})
    client.post('/accounts/otp/', {'code': last_code()})
    response = client.post('/accounts/profile/', {'action': 'profile', 'first_name': 'New',
        'last_name': 'Name', 'email': 'attacker@example.test', 'phone': '', 'position': ''})
    actor.refresh_from_db()
    assert actor.email == 'med@example.test'
    assert response.status_code == 200


def test_delivery_failure_never_authenticates(client, users, monkeypatch):
    actor = ready(users['med'])
    def failed(*args, **kwargs):
        raise OSError('mail service unavailable')
    monkeypatch.setattr('apps.accounts.otp.send_mail', failed)
    response = client.post('/accounts/login/', {'username': actor.username, 'password': 'TestPass123!'})
    assert response.status_code == 200
    assert '_auth_user_id' not in client.session


def test_invitation_requires_post_and_does_not_sign_in(client, users, settings):
    from apps.accounts.otp import invitation
    settings.SITE_BASE_URL = 'https://testserver'
    actor = users['med']
    actor.email = 'med@example.test'
    actor.save(update_fields=['email'])
    invitation(actor, reason='operator enrollment')
    path = re.search(r'https://testserver(/accounts/email/invitation/\S+)', mail.outbox[-1].body).group(1)
    assert client.get(path).status_code == 200
    actor.refresh_from_db()
    assert not actor.email_is_verified
    assert client.post(path).status_code == 200
    actor.refresh_from_db()
    assert actor.email_is_verified
    assert '_auth_user_id' not in client.session
    assert client.post(path).status_code == 400


def test_account_changes_invalidate_pending_code(client, users):
    actor = ready(users['med'])
    client.post('/accounts/login/', {'username': actor.username, 'password': 'TestPass123!'})
    code = last_code()
    actor.role = 'VIEWER'
    actor.save(update_fields=['role'])
    client.post('/accounts/otp/', {'code': code})
    assert '_auth_user_id' not in client.session

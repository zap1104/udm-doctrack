"""Security edge cases use real challenge rows and simulated delivery."""
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import RequestFactory
from django.utils import timezone

from apps.accounts import otp
from apps.accounts.models import User
from apps.accounts.security_models import EmailChallenge
from tests.auth_helpers import prepare_verified_user, verified_request


@pytest.fixture(autouse=True)
def enabled(settings):
    settings.EMAIL_OTP_REQUIRED = True
    settings.EMAIL_CONFIGURED = True
    settings.EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
    settings.SITE_BASE_URL = 'https://testserver'


def request():
    req = RequestFactory().post('/accounts/otp/')
    req.session = {}
    from django.contrib.auth.models import AnonymousUser
    req.user = AnonymousUser()
    return req


def code():
    return re.search(r'\b(\d{6})\b', mail.outbox[-1].body).group(1)


@pytest.mark.django_db(transaction=True)
def test_concurrent_code_consumption_is_single_use(users):
    from django.contrib.sessions.backends.db import SessionStore

    user = users['med']
    prepare_verified_user(user)
    original = request()
    original.session = SessionStore()
    otp.begin(original, user)
    original.session.save()
    key, value = original.session.session_key, code()
    barrier = Barrier(2)

    def consume():
        close_old_connections()
        req = request()
        req.session = SessionStore(session_key=key)
        dict(req.session.items())  # Both requests have arrived with the same cookie.
        barrier.wait(timeout=10)
        try:
            otp.verify(req, value)
            req.session.save()
            return 'accepted'
        except ValidationError:
            return 'rejected'
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: consume(), range(2)))
    assert sorted(results) == ['accepted', 'rejected']
    assert EmailChallenge.objects.get(user=user).consumed_at is not None


@pytest.mark.django_db
def test_resend_cannot_extend_password_validation_window(users):
    user = users['med']
    prepare_verified_user(user)
    req = request()
    otp.begin(req, user)
    EmailChallenge.objects.filter(user=user).update(last_sent_at=timezone.now()-timedelta(seconds=61), pending_until=timezone.now())
    with pytest.raises(ValidationError):
        otp.resend(req)


@pytest.mark.django_db
def test_three_sends_share_account_budget_across_browsers(users):
    user = users['med']
    prepare_verified_user(user)
    for _ in range(3):
        otp.begin(request(), user)
    with pytest.raises(ValidationError):
        otp.begin(request(), user)


@pytest.mark.django_db
def test_self_email_change_keeps_old_address_until_confirmation(users):
    from apps.accounts.email_security import request_email_change
    user = users['med']
    req = verified_request(user)
    old = user.email
    request_email_change(req, email='replacement@example.test', password='TestPass123!')
    user.refresh_from_db()
    assert user.email == old
    invitation = next(message for message in mail.outbox if message.to == ['replacement@example.test'])
    path = re.search(r'/accounts/email/invitation/([^/]+)/([^/]+)/', invitation.body)
    assert otp.invitation_valid(path.group(1), path.group(2), consume=True)
    user.refresh_from_db()
    assert user.email == 'replacement@example.test'
    assert user.email_is_verified
    req.user = user
    assert not otp.verified(req)


@pytest.mark.django_db
def test_admin_recovery_leaves_replacement_unverified(users):
    from apps.accounts.email_security import recover_email
    actor = users['admin']
    target = users['med']
    prepare_verified_user(target)
    req = verified_request(actor)
    recover_email(target, email='recovered@example.test', reason='Identity checked in person', actor=actor, request=req)
    target.refresh_from_db()
    assert target.email == 'recovered@example.test'
    assert not target.email_is_verified


@pytest.mark.django_db
def test_stale_factor_change_request_does_not_enroll_new_email(users):
    user = users['med']
    req = verified_request(user)
    User.objects.filter(pk=user.pk).update(role='VIEWER')
    from django.core.exceptions import PermissionDenied
    with pytest.raises(PermissionDenied):
        otp.invitation(user, request=req, new_email='replacement@example.test', reason='Stale session')


@pytest.mark.django_db
def test_account_removed_after_password_check_has_readable_failure(users):
    user = users['med']
    prepare_verified_user(user)
    User.objects.filter(pk=user.pk).delete()
    with pytest.raises(ValidationError):
        otp.begin(request(), user)


@pytest.mark.django_db
def test_suspended_invitation_target_is_refused_cleanly(users):
    user = users['med']
    prepare_verified_user(user)
    User.objects.filter(pk=user.pk).update(is_active=False)
    with pytest.raises(ValidationError):
        otp.invitation(user, reason='Stale invitation request')


@pytest.mark.django_db
def test_failed_code_limits_survive_new_pending_sign_ins(users):
    user = users['med']
    prepare_verified_user(user)
    for _ in range(2):
        req = request()
        otp.begin(req, user)
        wrong = '000000' if code() != '000000' else '111111'
        for _ in range(5):
            with pytest.raises(ValidationError):
                otp.verify(req, wrong)
    req = request()
    otp.begin(req, user)
    with pytest.raises(ValidationError, match='Too many incorrect'):
        otp.verify(req, code())


@pytest.mark.django_db
def test_failed_code_ip_budget_is_shared_between_accounts(users):
    for user in list(users.values())[:6]:
        prepare_verified_user(user)
        req = request()
        otp.begin(req, user)
        wrong = '000000' if code() != '000000' else '111111'
        for _ in range(5):
            with pytest.raises(ValidationError):
                otp.verify(req, wrong)
    user = list(users.values())[6]
    prepare_verified_user(user)
    req = request()
    otp.begin(req, user)
    with pytest.raises(ValidationError, match='Too many incorrect'):
        otp.verify(req, code())


@pytest.mark.django_db
def test_late_delivery_cannot_reactivate_superseded_code(client, users):
    user = users['med']
    prepare_verified_user(user)
    client.post('/accounts/login/', {'username': user.username, 'password': 'TestPass123!'})
    old = EmailChallenge.objects.get(user=user)
    old_code = code()
    EmailChallenge.objects.filter(pk=old.pk).update(last_sent_at=timezone.now()-timedelta(seconds=61))
    client.post('/accounts/otp/resend/')
    current_code = code()
    assert not otp._deliver(old, old_code)
    latest = EmailChallenge.objects.get(pk=old.pk)
    assert latest.generation != old.generation
    assert client.post('/accounts/otp/', {'code': old_code}).status_code == 200
    assert '_auth_user_id' not in client.session
    assert client.post('/accounts/otp/', {'code': current_code}).status_code == 302
    assert client.session['_auth_user_id'] == str(user.pk)


@pytest.mark.django_db
def test_operator_recovery_does_not_verify_the_address(users):
    from io import StringIO

    from django.core.management import call_command

    from apps.core.models import AuditLog
    user = users['admin']
    prepare_verified_user(user)
    call_command('recover_account_email', user=user.username, email='operator-recovery@example.test',
        reason='Administrator identity confirmed offline', stdout=StringIO())
    user.refresh_from_db()
    assert not user.email_is_verified
    assert user.email == 'operator-recovery@example.test'
    assert EmailChallenge.objects.filter(user=user, purpose='enroll', delivered=True).exists()
    assert AuditLog.objects.filter(extra__operator_recovery=True).exists()


@pytest.mark.django_db
def test_resend_never_reuses_the_previous_digits(client, users, monkeypatch):
    user = users['med']
    prepare_verified_user(user)
    numbers = iter([123456, 123456, 654321])
    monkeypatch.setattr('apps.accounts.otp.secrets.randbelow', lambda _: next(numbers))
    client.post('/accounts/login/', {'username': user.username, 'password': 'TestPass123!'})
    EmailChallenge.objects.filter(user=user).update(last_sent_at=timezone.now()-timedelta(seconds=61))
    client.post('/accounts/otp/resend/')
    assert code() == '654321'

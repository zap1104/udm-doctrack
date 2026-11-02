"""Explicit authenticated-session setup for tests that exercise post-login behavior.

This does not replace Client.force_login: OTP bypass tests still use the original
password-only shortcut. End-to-end login tests use real password and emailed codes.
"""
import time

from django.contrib.auth import get_user_model
from django.test import Client


def prepare_verified_user(user):
    if not user.email_is_verified:
        user.email = user.email or f'{user.username}@example.test'
        user.verified_email = user.email
        get_user_model().objects.filter(pk=user.pk).update(email=user.email, verified_email=user.verified_email)


def verified_request(user):
    from django.test import RequestFactory

    from apps.accounts.otp import establish_proof

    prepare_verified_user(user)
    request = RequestFactory().post('/django-admin/accounts/permissionuser/')
    request.user = user
    request.session = {}
    establish_proof(request, user)
    return request


def force_verified_login(client, user, backend=None):
    # Test factories predate mandatory verified-email enrollment. Preparing this
    # disposable account is explicit in the caller's chosen helper name.
    from apps.accounts.otp import PROOF, required, stamp
    current = get_user_model().objects.get(pk=user.pk)
    if required():
        prepare_verified_user(current)
        user.email, user.verified_email = current.email, current.verified_email
    Client.force_login(client, current, backend=backend)
    session = client.session
    session[PROOF] = {'user': current.pk, 'stamp': stamp(current), 'at': time.time()}
    session.save()

"""Explicit server-side role simulation for trusted diagnostics, not an HTTP API.

The diagnostic caller owns the database connection and already bypasses passwords
using Django's test client. Mandatory OTP still requires a verified account. This
helper supplies a verified diagnostic session without sending real sign-in email;
it never edits an account or claims to test authentication or delivery.
"""
import time

from django.core.exceptions import ValidationError


def diagnostic_login(client, user):
    from apps.accounts import otp

    if otp.required() and not user.email_is_verified:
        raise ValidationError('Complete verified-email enrollment before running diagnostics with mandatory OTP. Authentication and mail delivery are tested separately.')
    client.force_login(user)
    session = client.session
    session[otp.PROOF] = {'user':user.pk, 'stamp':otp.stamp(user), 'at':time.time()}
    session.save()

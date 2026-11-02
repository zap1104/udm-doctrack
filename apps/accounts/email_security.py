"""Changing an email changes an authentication factor, not just contact metadata."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db import connection, transaction
from django.utils import timezone

from apps.core.models import AuditLog
from apps.core.utils import log_action

from . import otp
from .models import User
from .security_models import EmailChallenge


def _notify_old(address):
    if not address:
        return
    try:
        from django.conf import settings
        from django.core.mail import get_connection, send_mail
        send_mail('Your DocTrack email security settings changed',
            'Your registered email address was changed or a replacement was requested. If this was not you, contact your records administrator immediately.',
            settings.DEFAULT_FROM_EMAIL, [address], connection=get_connection(timeout=10))
    except Exception:
        otp.logger.warning('Email security notification could not be delivered')


def request_email_change(request, *, email, password):
    validate_email(email)
    with transaction.atomic():
        current = otp._locked_user(request.user.pk)
        request.user = current
        otp.require_recent(request)
        if not current.check_password(password):
            raise ValidationError('Your current password is incorrect.')
        if email.casefold() == current.email.casefold():
            raise ValidationError('Enter a different replacement email address.')
    challenge = otp.invitation(current, request=request, new_email=email, reason='User requested email replacement')
    _notify_old(current.email)
    return challenge


def recover_email(user, *, email, reason, actor=None, request=None, operator=False):
    validate_email(email)
    reason = str(reason or '').strip()
    if not reason or len(reason) > 2000:
        raise ValidationError('Record the identity-check or recovery reason (up to 2,000 characters).')
    if not operator and (actor is None or request is None):
        raise PermissionDenied('An authorized administrator is required.')
    with transaction.atomic():
        # Same account-wide serialization used by account administration.
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_xact_lock(%s)', [0x55444D414343])
        current = otp._locked_user(user.pk)
        if not operator:
            actual = User.objects.filter(pk=actor.pk, is_active=True).first()
            if actual is None:
                raise PermissionDenied('This administrator account is no longer available.')
            request.user = actual
            otp.require_recent(request)
            if not actual.can_administer(current):
                raise PermissionDenied('You may only recover accounts you administer.')
        old = current.email
        current.email = email
        current.verified_email = ''
        current.save(update_fields=['email', 'verified_email'])
        EmailChallenge.objects.filter(user=current, consumed_at__isnull=True).update(cancelled_at=timezone.now(), digest='')
        log_action(AuditLog.Action.PERMISSION, 'Registered email reset; verification required', actor=actor,
            target=current, request=request, extra={'reason': reason, 'operator_recovery': operator})
    _notify_old(old)
    # Persist the reset even if delivery is temporarily unavailable. It must never
    # restore a compromised address or mark an unproven replacement verified.
    return otp.invitation(current, reason=reason)

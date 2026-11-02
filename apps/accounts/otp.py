"""Password-bound email verification, shared by application and raw administration."""
import logging
import secrets
import time
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import login
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import get_connection, send_mail
from django.db import connection, transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.utils.http import url_has_allowed_host_and_scheme

from apps.core.models import AuditLog, SecurityThrottle
from apps.core.security import allow_sensitive_request
from apps.core.utils import client_ip, log_action

from .models import User
from .security_models import EmailChallenge

logger = logging.getLogger('doctrack')
PENDING = '_otp_pending'
BINDING = '_otp_browser'
PROOF = '_email_otp_verified'


def required():
    return getattr(settings, 'EMAIL_OTP_REQUIRED', True)


def stamp(user):
    # TextChoices and their database strings compare equal but repr() differs.
    # Normalize types so equivalent saved account state has one fingerprint.
    values = (str(user.pk), str(user.password), user.email.casefold(), user.verified_email.casefold(),
              str(user.role), str(user.office_id or ''), bool(user.is_active), bool(user.is_staff), bool(user.is_superuser))
    return salted_hmac('doctrack.otp.account', repr(values)).hexdigest()


def verified(request, *, recent=False):
    if not required():
        return True
    user = request.user
    proof = getattr(request, 'session', {}).get(PROOF, {})
    if not user.is_authenticated or not user.is_active or not user.email_is_verified:
        return False
    if not isinstance(proof, dict) or proof.get('user') != user.pk:
        return False
    if not constant_time_compare(str(proof.get('stamp', '')), stamp(user)):
        return False
    timestamp = proof.get('at')
    return isinstance(timestamp, (int, float)) and 0 <= time.time()-timestamp and (not recent or time.time()-timestamp < 300)


def establish_proof(request, user):
    request.session[PROOF] = {'user': user.pk, 'stamp': stamp(user), 'at': time.time()}


def safe_return(request, value):
    value = str(value or '')[:2048]
    if value.startswith('/') and not value.startswith('//') and url_has_allowed_host_and_scheme(
        value, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        if not value.startswith(('/accounts/login/', '/accounts/otp/', '/django-admin/login/')):
            return value
    return '/'


def masked(address):
    name, _, domain = address.partition('@')
    return f'{name[:1]}***@{domain}'


def _binding(request):
    nonce = request.session.get(BINDING)
    if not nonce:
        nonce = secrets.token_urlsafe(32)
        request.session[BINDING] = nonce
    return salted_hmac('doctrack.otp.browser', nonce).hexdigest()


def _digest(challenge, secret):
    return salted_hmac('doctrack.email.challenge', f'{challenge.pk}:{challenge.generation}:{secret}').hexdigest()


def _budget(request, user):
    # These writes occur under the account lock. No per-process caches.
    limits = [('otp-send-user-quarter', user.pk, 3, 900), ('otp-send-user-hour', user.pk, 10, 3600),
              ('otp-send-ip', client_ip(request), 20, 900)]
    for scope, identity, limit, seconds in limits:
        if not allow_sensitive_request(scope, identity, limit=limit, seconds=seconds):
            raise ValidationError('Too many code requests. Please wait before trying again.')


def _failed_budget(request, user, *, increment=False):
    import hashlib

    now = timezone.now()
    for scope, identity, limit in [('otp-fail-user', user.pk, 10), ('otp-fail-ip', client_ip(request), 30)]:
        key = hashlib.sha256(f'{scope}:{identity}'.encode()).hexdigest()
        counter, _ = SecurityThrottle.objects.select_for_update().get_or_create(
            key=key, defaults={'expires_at': now+timedelta(minutes=15)})
        if counter.expires_at <= now:
            counter.attempts = 0
            counter.expires_at = now+timedelta(minutes=15)
        if counter.attempts >= limit:
            return False
        if increment:
            counter.attempts += 1
        counter.save(update_fields=['attempts', 'expires_at'])
    return True


def _deliver(challenge, secret, *, invitation=False):
    if invitation:
        base = settings.SITE_BASE_URL.rstrip('/')
        if not base:
            raise ValidationError('Configure the public site address before sending a verification invitation.')
        path = reverse('accounts:email_invitation', args=[challenge.pk, secret])
        body = f'Confirm your registered DocTrack email address:\n{base}{path}\nThis link expires in 24 hours. It does not sign you in.'
        subject = 'Verify your DocTrack email address'
    else:
        body = f'Your DocTrack verification code is {secret}.\nIt expires in five minutes. Do not share this code. If you did not request it, ignore this email.'
        subject = 'Your DocTrack verification code'
    try:
        # Deliberately synchronous and bounded: login cannot depend on a running queue.
        connection = get_connection(timeout=10)
        sent = send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [challenge.destination],
                         connection=connection, fail_silently=False)
        if sent != 1:
            raise OSError('Delivery not accepted')
    except Exception:
        # Mail-provider exceptions can contain the message; never log their text.
        logger.warning('Email challenge delivery failed; no authentication granted')
        raise ValidationError('The email could not be sent. Please try again or contact your administrator.') from None
    return EmailChallenge.objects.filter(pk=challenge.pk, generation=challenge.generation,
        cancelled_at__isnull=True, consumed_at__isnull=True).update(delivered=True) == 1


def _current(challenge, user, *, now=None):
    now = now or timezone.now()
    return bool(user.is_active and user.has_usable_password()
        and challenge.consumed_at is None and challenge.cancelled_at is None
        and now < challenge.pending_until and challenge.attempts < 5
        and constant_time_compare(challenge.account_stamp, stamp(user)))


def _locked_user(pk):
    current = User.objects.select_for_update(no_key=True).filter(pk=pk, is_active=True).first()
    if current is None:
        raise ValidationError('This account is no longer available. Contact your administrator.')
    return current


def begin(request, user, *, return_url='/', purpose='login'):
    if not settings.EMAIL_CONFIGURED:
        raise ValidationError('Email sign-in is not configured. Contact your administrator.')
    with transaction.atomic():
        actor = _locked_user(user.pk)
        if not actor.is_active or not actor.email_is_verified or not constant_time_compare(actor.password, user.password):
            raise ValidationError('Your registered email must be verified before sign-in. Contact your administrator.')
        if purpose == 'reauth':
            request.user = actor
            if not verified(request):
                raise PermissionDenied('Sign in again before confirming your identity.')
        _budget(request, actor)
        now = timezone.now()
        binding = _binding(request)
        EmailChallenge.objects.filter(binding=binding, purpose=purpose, consumed_at__isnull=True).update(cancelled_at=now)
        challenge = EmailChallenge(user=actor, purpose=purpose, binding=binding, account_stamp=stamp(actor),
            destination=actor.email, expires_at=now+timedelta(minutes=5), pending_until=now+timedelta(minutes=10),
            last_sent_at=now, return_url=safe_return(request, return_url), backend=getattr(user, 'backend', 'django.contrib.auth.backends.ModelBackend'))
        secret = f'{secrets.randbelow(1000000):06d}'
        challenge.digest = _digest(challenge, secret)
        challenge.save()
    request.session[PENDING] = str(challenge.pk)
    _deliver(challenge, secret)
    return challenge


def pending(request):
    value = request.session.get(PENDING)
    try:
        pk = uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None
    return EmailChallenge.objects.select_related('user').filter(pk=pk, binding=_binding(request),
        purpose__in=['login', 'reauth'], consumed_at__isnull=True, cancelled_at__isnull=True).first()


def resend(request):
    previous = pending(request)
    if previous is None:
        raise ValidationError('Start sign-in again to request a new code.')
    error = None
    with transaction.atomic():
        user = _locked_user(previous.user_id)
        challenge = EmailChallenge.objects.select_for_update().get(pk=previous.pk)
        now = timezone.now()
        if not _current(challenge, user, now=now):
            error = 'This sign-in has expired. Start again.'
        elif challenge.last_sent_at and (now-challenge.last_sent_at).total_seconds() < 60:
            error = 'Please wait 60 seconds before requesting another code.'
        else:
            _budget(request, user)
            for _ in range(10):
                secret = f'{secrets.randbelow(1000000):06d}'
                if not constant_time_compare(challenge.digest, _digest(challenge, secret)):
                    break
            else:
                raise ValidationError('A fresh code could not be generated. Please try again.')
            challenge.generation = uuid.uuid4()
            challenge.digest = _digest(challenge, secret)
            challenge.last_sent_at = now
            challenge.expires_at = min(now+timedelta(minutes=5), challenge.pending_until)
            challenge.delivered = False
            challenge.save(update_fields=['generation', 'digest', 'last_sent_at', 'expires_at', 'delivered'])
    if error:
        raise ValidationError(error)
    _deliver(challenge, secret)


def verify(request, code):
    previous = pending(request)
    if previous is None:
        raise ValidationError('Start sign-in again. This code is no longer available.')
    error = None
    with transaction.atomic():
        user = _locked_user(previous.user_id)
        challenge = EmailChallenge.objects.select_for_update().get(pk=previous.pk)
        now = timezone.now()
        if not _current(challenge, user, now=now) or not user.email_is_verified:
            error = 'This sign-in has expired or the account changed. Start again.'
        elif not challenge.delivered or now >= challenge.expires_at:
            error = 'This code has expired or was not delivered. Request a new code.'
        elif not _failed_budget(request, user):
            error = 'Too many incorrect codes. Try signing in again after 15 minutes.'
        elif not constant_time_compare(challenge.digest, _digest(challenge, str(code).strip())):
            challenge.attempts += 1
            challenge.save(update_fields=['attempts'])
            _failed_budget(request, user, increment=True)
            error = 'That code is incorrect. Check the latest email and try again.'
        elif challenge.purpose == 'reauth' and (not request.user.is_authenticated or request.user.pk != user.pk):
            error = 'Sign in again before confirming your identity.'
        else:
            challenge.consumed_at = now
            challenge.digest = ''
            challenge.save(update_fields=['consumed_at', 'digest'])
            if challenge.purpose == 'login':
                login(request, user, backend=challenge.backend)
            else:
                request.session.cycle_key()
            establish_proof(request, user)
            request.session.pop(PENDING, None)
            return challenge.return_url
    raise ValidationError(error)


def cancel(request):
    challenge = pending(request)
    if challenge:
        EmailChallenge.objects.filter(pk=challenge.pk).update(cancelled_at=timezone.now(), digest='')
    request.session.pop(PENDING, None)


def require_recent(request):
    if not verified(request, recent=True):
        raise PermissionDenied('Confirm your identity with a fresh email code before changing security settings.')


def invitation(user, *, actor=None, request=None, new_email=None, reason=''):
    """An invited address proves mailbox ownership only; it never creates a session."""
    if not settings.EMAIL_CONFIGURED:
        raise ValidationError('Email delivery is not configured.')
    with transaction.atomic():
        if actor is not None:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_xact_lock(%s)', [0x55444D414343])
            # Lock both accounts in the same order as permission administration.
            list(User.objects.select_for_update(no_key=True).filter(pk__in=[user.pk, actor.pk]).order_by('pk'))
        current = _locked_user(user.pk)
        if new_email:
            if request is None or request.user.pk != current.pk:
                raise PermissionDenied('Confirm your identity before replacing your email.')
            request.user = current
            require_recent(request)
        if actor is not None:
            actor = User.objects.filter(pk=actor.pk, is_active=True).first()
            if actor is None or not actor.can_administer(current):
                raise PermissionDenied('You may not manage this account.')
            if request is None:
                raise PermissionDenied('An authenticated administrator request is required.')
            request.user = actor
            require_recent(request)
        target = new_email or current.email
        if not target:
            raise ValidationError('Add a registered email address first.')
        now = timezone.now()
        purpose = 'change' if new_email else 'enroll'
        EmailChallenge.objects.filter(user=current, purpose=purpose, consumed_at__isnull=True).update(cancelled_at=now)
        challenge = EmailChallenge(user=current, purpose=purpose, destination=target, account_stamp=stamp(current),
            expires_at=now+timedelta(hours=24), pending_until=now+timedelta(hours=24), last_sent_at=now)
        secret = secrets.token_urlsafe(32)
        challenge.digest = _digest(challenge, secret)
        challenge.save()
        log_action(AuditLog.Action.PERMISSION, 'Email verification invitation requested', actor=actor,
                   target=current, request=request, extra={'reason': reason, 'purpose': purpose})
    _deliver(challenge, secret, invitation=True)
    return challenge


def invitation_valid(pk, secret, *, consume=False):
    from django.core.exceptions import ObjectDoesNotExist
    try:
        with transaction.atomic():
            row = EmailChallenge.objects.get(pk=pk, purpose__in=['enroll', 'change'])
            user = User.objects.select_for_update(no_key=True).get(pk=row.user_id)
            row = EmailChallenge.objects.select_for_update().get(pk=pk)
            valid = (_current(row, user) and row.delivered and timezone.now() < row.expires_at
                     and constant_time_compare(row.digest, _digest(row, secret)))
            if valid and consume:
                row.consumed_at = timezone.now()
                row.digest = ''
                row.save(update_fields=['consumed_at', 'digest'])
                fields = ['verified_email']
                if row.purpose == 'change':
                    user.email = row.destination
                    fields.append('email')
                user.verified_email = row.destination
                user.save(update_fields=fields)
                EmailChallenge.objects.filter(user=user, consumed_at__isnull=True).update(cancelled_at=timezone.now())
                log_action(AuditLog.Action.PERMISSION, 'Registered email verified', actor=user, target=user)
            return valid
    except (ObjectDoesNotExist, ValueError, TypeError, ValidationError):
        return False

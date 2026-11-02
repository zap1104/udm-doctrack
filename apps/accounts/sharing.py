"""Read-only sharing with the same authorization in admin forms and service callers."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction

from apps.core.models import AuditLog
from apps.core.permissions import active_writer
from apps.core.utils import log_action

from .capabilities import area_for, require_capability


def grant_model(record):
    from apps.documents.models import DocumentAccessGrant
    from apps.tracking.external_models import ExternalRecordAccessGrant
    from apps.tracking.models import RecordAccessGrant

    area = area_for(record)
    return DocumentAccessGrant if 'repository' in area else ExternalRecordAccessGrant if area == 'external_tracking' else RecordAccessGrant


def _authorized(record, user):
    area = area_for(record)
    require_capability(user, area, 'share')
    if 'repository' in area:
        allowed = record.is_active and record.can_user_view(user) and user.is_office_admin and (user.is_system_admin or user.office_id == record.office_id)
    elif area == 'external_tracking':
        allowed = record.can_manage(user)
    else:
        allowed = record.can_user_grant_access(user)
    if not allowed:
        raise PermissionDenied('Only an authorized administrator or existing sharing authority may manage this record access.')


def _target(user, office, target_user):
    from .models import Office, User

    if (office is None) == (target_user is None):
        raise ValidationError('Choose exactly one internal office or active user.')
    if office is not None:
        office = Office.objects.select_for_update(no_key=True).filter(pk=office.pk, is_active=True).first()
        if office is None or (not user.is_system_admin and office.pk != user.office_id):
            raise PermissionDenied('This office is not an allowed share recipient.')
    else:
        target_user = User.objects.select_for_update(no_key=True).filter(pk=target_user.pk, is_active=True).first()
        if target_user is None or (not user.is_system_admin and not user.can_administer(target_user)):
            raise PermissionDenied('This account is not an allowed share recipient.')
    return office, target_user


@transaction.atomic
def grant_read_access(record, *, user, office=None, target_user=None, reason=''):
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(%s)', [0x55444D414343])
    user = active_writer(user)
    record = type(record).objects.select_for_update().get(pk=record.pk)
    _authorized(record, user)
    office, target_user = _target(user, office, target_user)
    reason = str(reason or '').strip()
    if not reason or len(reason) > 255:
        raise ValidationError('Record a sharing reason of up to 255 characters.')
    model = grant_model(record)
    key = 'document' if 'repository' in area_for(record) else 'record'
    # Parent lock serializes nullable legacy grant keys without rewriting history.
    matches = model.objects.filter(**{key: record, 'office': office, 'user': target_user})
    grant = matches.first()
    if grant is None:
        grant = model.objects.create(**{key: record, 'office': office, 'user': target_user, 'reason': reason, 'granted_by': user})
        log_action(AuditLog.Action.PERMISSION, 'Read access shared', actor=user, target=record,
            extra={'reason': reason, 'before': None, 'after': {'grant_id': grant.pk,
                'office_id': getattr(office, 'pk', None), 'user_id': getattr(target_user, 'pk', None)}})
    return grant


@transaction.atomic
def revoke_read_access(record, grant_id, *, user, reason):
    user = active_writer(user)
    record = type(record).objects.select_for_update().get(pk=record.pk)
    _authorized(record, user)
    reason = str(reason or '').strip()
    if not reason or len(reason) > 255:
        raise ValidationError('Record a revocation reason of up to 255 characters.')
    key = 'document' if 'repository' in area_for(record) else 'record'
    grant = grant_model(record).objects.filter(pk=grant_id, **{key: record}).first()
    if grant is None:
        raise ValidationError('This share changed or was already removed. Reload the table.')
    snapshot = {'grant_id': grant.pk, 'office_id': grant.office_id, 'user_id': grant.user_id, 'reason': reason}
    grant.delete()
    log_action(AuditLog.Action.PERMISSION, 'Explicit read share removed; inherited access unchanged', actor=user, target=record,
        extra={'reason': reason, 'before': snapshot, 'after': None})

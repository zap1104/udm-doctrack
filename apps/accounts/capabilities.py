"""A restrictive overlay. Object visibility, role and custody rules remain authoritative."""
from functools import wraps

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction

from apps.core.models import AuditLog
from apps.core.utils import log_action

from .models import User
from .permission_models import CapabilityRestriction, UserPermissionPolicy

AREAS = {'tracking': 'OVPA Tracking', 'external_tracking': 'External Tracking',
         'repository': 'OVPA Repository', 'external_repository': 'External Repository'}
ACTION_LABELS = {'view_record': 'View record details', 'view_content': 'View file contents',
    'download': 'Download originals', 'create': 'Create / upload', 'edit_metadata': 'Edit metadata',
    'edit_files': 'Add, update or discard Tracking files', 'receive': 'Confirm receipt', 'work': 'Add work updates',
    'endorse': 'Endorse', 'complete': 'Complete', 'reopen': 'Return to Tracking', 'file': 'Approve filing',
    'share': 'Share read access', 'export': 'Export', 'extract': 'Manage extraction',
    'deletion_review': 'Review deletion', 'restore': 'Restore deleted documents'}
COMMON = {'view_record', 'view_content', 'download', 'create', 'edit_metadata', 'share', 'export'}
TRACKING = COMMON | {'edit_files', 'receive', 'work', 'endorse', 'complete', 'reopen', 'file'}
REPOSITORY = COMMON | {'extract', 'deletion_review', 'restore'}
APPLICABLE = {area: (REPOSITORY if 'repository' in area else TRACKING) for area in AREAS}


def area_for(record):
    if record._meta.model_name == 'externaltrackingrecord':
        return 'external_tracking'
    if record._meta.model_name == 'trackingrecord':
        return 'tracking'
    if record._meta.model_name == 'document':
        return 'external_repository' if record.is_external else 'repository'
    raise ValueError('Unsupported permission area')


def role_default(user, area, action):
    if area not in AREAS or action not in APPLICABLE[area] or not user.is_active:
        return False
    if user.role not in {'USER', 'VIEWER', 'ADMIN', 'SYSTEM_ADMIN'}:
        return False
    if action in {'view_record', 'view_content', 'download', 'export'}:
        return True
    if not user.is_system_admin and not user.office_id:
        return False
    if user.is_viewer:
        return False
    if action in {'file', 'deletion_review', 'restore'} or (area.startswith('external_') and action == 'create'):
        return user.is_office_admin
    return user.is_records_staff


def _request_policy(user):
    from apps.core.middleware import get_current_request

    request = get_current_request()
    if request is None or request.method not in {'GET', 'HEAD', 'OPTIONS'} or not getattr(user, 'pk', None):
        return None
    if not request.user.is_authenticated or request.user.pk != user.pk:
        return None
    if not hasattr(request, '_record_capability_snapshot'):
        blocked = set(CapabilityRestriction.objects.filter(policy__user_id=user.pk).values_list('area', 'action'))
        request._record_capability_snapshot = (request.user, blocked)
    return request._record_capability_snapshot


def current_actor(user):
    if (not getattr(user, 'is_authenticated', False) or not getattr(user, 'pk', None)
            or not getattr(user, 'is_active', False) or getattr(user, 'role', '') not in {'USER','VIEWER','ADMIN','SYSTEM_ADMIN'}):
        return None
    snapshot = _request_policy(user)
    if snapshot is not None:
        return snapshot[0]
    return User.objects.filter(pk=user.pk, is_active=True).first()


def capability_allowed(user, area, action, *, fresh=False):
    if not getattr(user, 'is_authenticated', False) or not getattr(user, 'pk', None):
        return False
    snapshot = None if fresh else _request_policy(user)
    current = snapshot[0] if snapshot else User.objects.filter(pk=user.pk, is_active=True).first()
    if current is None or not role_default(current, area, action):
        return False
    actions = {action}
    if action != 'view_record':
        actions.add('view_record')
    if action in {'download', 'edit_files', 'extract'}:
        actions.add('view_content')
    if snapshot is not None:
        return not any((area, key) in snapshot[1] for key in actions)
    return not CapabilityRestriction.objects.filter(policy__user_id=current.pk, area=area, action__in=actions).exists()


def require_capability(user, area, action):
    if not capability_allowed(user, area, action, fresh=True):
        raise PermissionDenied(f'Your account cannot {ACTION_LABELS.get(action, action).lower()} in {AREAS.get(area, "this area")}.')


def requires_capability(area, action):
    """Recheck inside a write transaction, serialized against policy/account edits."""
    def decorate(function):
        @wraps(function)
        def call(*args, **kwargs):
            from apps.core.permissions import active_writer

            with transaction.atomic():
                actor = active_writer(kwargs.get('user'))
                actual_area = area_for(args[0]) if area is None else area
                if actual_area == 'repository' and kwargs.get('collection') == 'EXTERNAL':
                    actual_area = 'external_repository'
                require_capability(actor, actual_area, action)
                kwargs['user'] = actor
                return function(*args, **kwargs)
        return call
    return decorate


def set_restrictions(user, restrictions, *, actor, expected_revision, reason, request=None):
    from . import otp

    reason = str(reason or '').strip()
    proposed = set(tuple(item) for item in restrictions)
    if not reason or len(reason) > 2000:
        raise ValidationError('Record a reason of up to 2,000 characters.')
    if any(area not in APPLICABLE or action not in APPLICABLE[area] for area, action in proposed):
        raise ValidationError('Choose only supported permission settings.')
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_xact_lock(%s)', [0x55444D414343])
        people = {row.pk: row for row in User.objects.select_for_update(no_key=True).filter(pk__in=[actor.pk, user.pk]).order_by('pk')}
        current_actor = people.get(actor.pk)
        if not current_actor or not current_actor.is_active or not current_actor.is_system_admin:
            raise PermissionDenied('Only system administrators can change user permissions.')
        if user.pk not in people:
            raise ValidationError('This account no longer exists.')
        if otp.required():
            if request is None or request.user.pk != current_actor.pk:
                raise PermissionDenied('A recently verified administrator session is required.')
            request.user = current_actor
            otp.require_recent(request)
        policy, _ = UserPermissionPolicy.objects.select_for_update().get_or_create(user_id=user.pk)
        if expected_revision != policy.revision:
            raise ValidationError('These permissions changed while the form was open. Reload before saving.')
        previous = set(policy.restrictions.values_list('area', 'action'))
        policy.restrictions.all().delete()
        CapabilityRestriction.objects.bulk_create([CapabilityRestriction(policy=policy, area=area, action=action) for area, action in sorted(proposed)])
        policy.revision += 1
        policy.updated_by = current_actor
        policy.reason = reason
        policy.save()
        log_action(AuditLog.Action.PERMISSION, 'User capability restrictions updated', actor=current_actor,
            target=people[user.pk], request=request, extra={'reason': reason, 'before': sorted(previous), 'after': sorted(proposed), 'revision': policy.revision})
        return policy


def permission_snapshot(user):
    current = current_actor(user)
    if current is None:
        return {area: {} for area in AREAS}
    snapshot = _request_policy(user)
    blocked = snapshot[1] if snapshot is not None else set(CapabilityRestriction.objects.filter(policy__user_id=current.pk).values_list('area', 'action'))
    result = {}
    for area in AREAS:
        decisions = {}
        for action in APPLICABLE[area]:
            dependencies = {action, 'view_record'}
            if action in {'download', 'edit_files', 'extract'}:
                dependencies.add('view_content')
            decisions[action] = role_default(current, area, action) and not any((area, key) in blocked for key in dependencies)
        result[area] = decisions
    return result

"""Availability checks shared by account administration entry points."""
from . import otp
from .models import User


def is_ready_system_admin(user):
    return bool(user.is_active and user.is_system_admin and user.is_staff
        and user.has_usable_password() and (not otp.required() or user.email_is_verified))


def ready_system_admins(*, exclude_ids=()):
    candidates = User.objects.filter(is_active=True, role=User.Role.SYSTEM_ADMIN, is_staff=True).exclude(pk__in=exclude_ids)
    return [user for user in candidates if is_ready_system_admin(user)]

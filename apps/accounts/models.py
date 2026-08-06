"""Offices and user accounts.

There is no public self-registration anywhere in this project: accounts are
created by an administrator and always carry a role and an office.
"""

from __future__ import annotations

from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models

from apps.core.models import ActiveManager, TimeStampedModel


class Office(TimeStampedModel):
    """An OVPA office. Codes appear inside every tracking number."""

    code = models.CharField(
        max_length=12, unique=True, help_text="Short code used in tracking numbers, e.g. MED."
    )
    name = models.CharField(max_length=150, unique=True)
    short_name = models.CharField(max_length=60, blank=True)
    cluster = models.CharField(
        max_length=60, default="OVPA", help_text="Grouping, e.g. OVPA. Peer VP offices come later."
    )
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children"
    )
    head_name = models.CharField(max_length=150, blank=True)
    email = models.EmailField(blank=True)
    location = models.CharField(max_length=150, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    objects = models.Manager()
    active = ActiveManager()

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        if not self.short_name:
            self.short_name = self.code
        super().save(*args, **kwargs)

    @property
    def label(self) -> str:
        return self.short_name or self.code


class UserQuerySet(models.QuerySet):
    def in_office(self, office):
        return self.filter(office=office, is_active=True)


class UserManagerFromQuerySet(UserManager.from_queryset(UserQuerySet)):
    """UserManager (so create_user/create_superuser/get_by_natural_key work)
    combined with UserQuerySet (for .in_office() and other query helpers).

    This has to be a real, named class living at module level — not the
    result of calling UserManager.from_queryset(UserQuerySet)() inline.
    Django's makemigrations writes the default manager into the migration
    file by dotted import path, and an anonymous class created inline has no
    such path, which fails with:
        ValueError: Could not find manager UserManagerFromUserQuerySet in
        django.db.models.manager.
    Naming it here gives migrations `apps.accounts.models.UserManagerFromQuerySet`
    to import.
    """


class LoginLockout(TimeStampedModel):
    """How many times this username / IP has been locked out, so each new
    lockout can last longer than the last one.

    This has to live in the database rather than the cache. The project has no
    shared cache configured, so Django falls back to a per-process in-memory
    one: the dev server's auto-reloader wipes it on every code change and each
    Gunicorn worker in production would keep its own separate copy. Either way
    the escalation silently resets and every lockout looks like the first.
    """

    class Kind(models.TextChoices):
        USERNAME = "username", "Username"
        IP = "ip", "IP address"

    kind = models.CharField(max_length=10, choices=Kind.choices)
    key = models.CharField(max_length=255, help_text="The username or IP address this applies to.")
    stage = models.PositiveIntegerField(
        default=0, help_text="Number of lockouts so far. Each one doubles the wait."
    )
    locked_until = models.DateTimeField(
        null=True, blank=True, help_text="End of the current lockout, used to tell episodes apart."
    )
    last_lockout_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["kind", "key"], name="uniq_login_lockout_kind_key"),
        ]
        ordering = ["-last_lockout_at"]
        verbose_name = "sign-in lockout"

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {self.key} — stage {self.stage}"


class User(AbstractUser):
    """Custom user. `role` decides what the dashboard and menus show."""

    class Role(models.TextChoices):
        ADMIN = "ADMIN", "System administrator"
        SECRETARY = "SECRETARY", "Secretary / records personnel"
        USER = "USER", "Office user"

    office = models.ForeignKey(
        Office, null=True, blank=True, on_delete=models.PROTECT, related_name="members"
    )
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.USER)
    position = models.CharField(max_length=120, blank=True, help_text="Job title shown on routing slips.")
    phone = models.CharField(max_length=32, blank=True)
    must_change_password = models.BooleanField(
        default=False, help_text="Force a password change on the next sign-in."
    )
    last_seen_at = models.DateTimeField(null=True, blank=True)

    objects = UserManagerFromQuerySet()

    class Meta:
        ordering = ["first_name", "last_name", "username"]

    def __str__(self) -> str:
        return self.display_name

    @property
    def display_name(self) -> str:
        full = self.get_full_name().strip()
        return full or self.username

    @property
    def short_display(self) -> str:
        if self.first_name and self.last_name:
            return f"{self.first_name[0]}. {self.last_name}"
        return self.display_name

    @property
    def initials(self) -> str:
        parts = [part for part in (self.first_name, self.last_name) if part]
        if len(parts) >= 2:
            return (parts[0][0] + parts[1][0]).upper()
        return (self.username[:2] or "??").upper()

    @property
    def is_system_admin(self) -> bool:
        return self.role == self.Role.ADMIN or self.is_superuser

    @property
    def is_records_staff(self) -> bool:
        return self.role in {self.Role.ADMIN, self.Role.SECRETARY} or self.is_superuser

    @property
    def office_label(self) -> str:
        return self.office.name if self.office_id else "No office assigned"

    def can_act_for_office(self, office) -> bool:
        """Can this user confirm receipt / forward on behalf of `office`?"""
        if office is None:
            return False
        if self.is_system_admin:
            return True
        return self.office_id == getattr(office, "pk", office)

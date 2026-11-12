"""Offices and user accounts.

There is no public self-registration anywhere in this project: accounts are
created by an administrator and always carry a role and an office.
"""

from __future__ import annotations

from django.contrib.auth.models import AbstractUser, UserManager
from django.core.validators import RegexValidator
from django.db import models, router, transaction

from apps.core.models import ActiveManager, TimeStampedModel

#: Default badge colours, handed out one per office so a fresh install is
#: already colour-coded without anybody visiting the admin screen. Chosen to sit
#: with the UDM navy/teal/gold system and to stay apart from one another for the
#: common forms of colour blindness — though the office code is printed on every
#: badge regardless, so colour is never carrying the meaning on its own.
OFFICE_COLOURS = [
    "#0b315a",  # navy
    "#16697a",  # teal
    "#2e7d5b",  # green
    "#b4342b",  # red
    "#6b4c9a",  # violet
    "#c2611f",  # orange
    "#1f6fb2",  # blue
    "#a8336b",  # magenta
    "#8a6a12",  # gold, darkened to hold its own as text
    "#4a5a70",  # slate
    "#7a5230",  # brown
    "#3f7d3f",  # moss
]

# Monotonic invalidation survives a security setting being changed back to its
# original value; comparing only those values could resurrect an old OTP proof.
ACCOUNT_SECURITY_FIELDS = frozenset({
    "password", "email", "verified_email", "role", "office_id", "is_active", "is_staff", "is_superuser",
})


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
    colour = models.CharField(
        max_length=7,
        blank=True,
        verbose_name="badge colour",
        validators=[
            RegexValidator(
                r"^#[0-9A-Fa-f]{6}$",
                "Enter a colour as six hex digits, for example #16697A.",
            )
        ],
        help_text="Identifies this office at a glance wherever it appears. "
                  "Leave blank to be given an unused colour automatically.",
    )
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
        if not self.colour:
            self.colour = self.next_free_colour()
        super().save(*args, **kwargs)

    @classmethod
    def next_free_colour(cls) -> str:
        """The first palette colour no other office is using.

        Handed out by scarcity rather than by hashing the code: a hash gives
        the same office the same colour forever, but with a dozen colours and a
        dozen offices it also collides often enough that two offices on the
        same screen would share one — which is the one thing the colour is
        there to prevent.
        """
        taken = set(cls.objects.exclude(colour="").values_list("colour", flat=True))
        for candidate in OFFICE_COLOURS:
            if candidate not in taken:
                return candidate
        # More offices than colours. Reuse in order, so it is at least even.
        return OFFICE_COLOURS[cls.objects.count() % len(OFFICE_COLOURS)]

    @property
    def label(self) -> str:
        return self.short_name or self.code

    @property
    def badge(self) -> dict[str, str]:
        """Background and text colours for this office's badge.

        Derived rather than stored, because the pair has to stay readable for
        whatever colour an administrator picks — including the pale ones. See
        apps.core.utils.badge_palette.
        """
        from apps.core.utils import badge_palette, badge_palette_dark, normalise_hex

        tint, ink = badge_palette(self.colour)
        tint_dark, ink_dark = badge_palette_dark(self.colour)
        return {
            "base": normalise_hex(self.colour), "tint": tint, "ink": ink,
            "tint_dark": tint_dark, "ink_dark": ink_dark,
        }


class UserQuerySet(models.QuerySet):
    def update(self, **kwargs):
        # This manager is also used by historical migration models, before the
        # additive revision column exists. Their original migrations stay valid.
        if not any(field.name == 'security_revision' for field in self.model._meta.fields):
            return super().update(**kwargs)
        if 'security_revision' in kwargs:
            raise ValueError('The account security revision is managed automatically.')
        security = {key: value for key, value in kwargs.items() if (
            'office_id' if key == 'office' else key
        ) in ACCOUNT_SECURITY_FIELDS}
        if security:
            changed = models.Q()
            for key, value in security.items():
                changed |= ~models.Q(**{key: value})
            kwargs['security_revision'] = models.F('security_revision') + models.Case(
                models.When(changed, then=models.Value(1)), default=models.Value(0),
                output_field=models.PositiveBigIntegerField(),
            )
        return super().update(**kwargs)

    def in_office(self, office):
        return self.filter(office=office, is_active=True)

    def administrable_by(self, actor):
        if not actor.is_authenticated or not actor.is_active or not actor.is_office_admin:
            return self.none()
        if actor.is_system_admin:
            return self
        if not actor.office_id:
            return self.none()
        return self.filter(office_id=actor.office_id, is_superuser=False, is_staff=False).exclude(role="SYSTEM_ADMIN")


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

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("role", "SYSTEM_ADMIN")
        if extra_fields["role"] != "SYSTEM_ADMIN":
            raise ValueError("A superuser must have the system administrator role.")
        return super().create_superuser(username, email=email, password=password, **extra_fields)


class LoginLockout(TimeStampedModel):
    """Database record of successive lockouts for a username or IP address."""

    class Kind(models.TextChoices):
        USERNAME = "username", "Username"
        IP = "ip", "IP address"

    kind = models.CharField(max_length=10, choices=Kind.choices)
    key = models.CharField(max_length=255, help_text="The username or IP address this applies to.")
    stage = models.PositiveIntegerField(
        default=0, help_text="Number of lockouts recorded since this history last reset."
    )
    locked_until = models.DateTimeField(
        null=True, blank=True, help_text="Recorded end of the latest lockout when it began."
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
        """Four roles, on two axes: how much you may do, and where.

        SYSTEM_ADMIN is the only role whose reach crosses office boundaries.
        ADMIN used to be that role, which is why the migration promotes existing
        ADMIN accounts to SYSTEM_ADMIN rather than leaving them holding a name
        that has quietly shrunk underneath them.

        SECRETARY is gone, and its accounts become USER. The name suggested more
        than the role held: every user-administration screen was gated on ADMIN
        alone, so a secretary could not administer anybody. What a secretary
        could do — act on the office's documents, share them, edit the office's
        repository entries — is what USER does now. See migration 0005.

        What each role may do:

        - USER    own office: create and update routing slips, upload, update
                  status, print, forward, and initiate a document's move to the
                  repository (which an administrator then approves).
        - VIEWER  own office: read active documents and the repository, open a
                  history by tracking number, print the routing slip. Print is
                  the only button. No status changes, uploads, edits, forwards,
                  or timeline entries.
        - ADMIN   own office: everything USER may do, plus adding users, setting
                  access control, editing staff accounts, resetting passwords,
                  and suspending, reactivating or deleting accounts — and
                  approving completed documents into the repository.
        - SYSTEM_ADMIN  all of the above, across every office.
        """

        SYSTEM_ADMIN = "SYSTEM_ADMIN", "System administrator (all offices)"
        ADMIN = "ADMIN", "Office administrator"
        USER = "USER", "Office user"
        VIEWER = "VIEWER", "Viewer (read-only)"

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
    verified_email = models.EmailField(blank=True, editable=False)
    security_revision = models.PositiveBigIntegerField(default=0, editable=False)

    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            update_fields = set(update_fields)
            kwargs['update_fields'] = update_fields
            relevant = {'office_id' if field == 'office' else field for field in update_fields}
            if 'security_revision' in relevant:
                raise ValueError('The account security revision is managed automatically.')
            if not relevant.intersection(ACCOUNT_SECURITY_FIELDS):
                return super().save(*args, **kwargs)
        if self.pk is None:
            self.security_revision = 0
            return super().save(*args, **kwargs)
        using = kwargs.get('using') or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            current = type(self).objects.using(using).select_for_update(no_key=True).filter(pk=self.pk).first()
            if current is not None:
                watched = ACCOUNT_SECURITY_FIELDS if update_fields is None else ACCOUNT_SECURITY_FIELDS.intersection(relevant)
                changed = any(getattr(self, field) != getattr(current, field) for field in watched)
                self.security_revision = current.security_revision + int(changed)
                if changed and update_fields is not None:
                    kwargs['update_fields'] = update_fields | {'security_revision'}
            return super().save(*args, **kwargs)

    @property
    def email_is_verified(self) -> bool:
        return bool(self.email) and self.email.casefold() == self.verified_email.casefold()

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
        """Reach across every office. The one role that is not office-scoped."""
        return self.role == self.Role.SYSTEM_ADMIN

    @property
    def is_office_admin(self) -> bool:
        """Administrator powers — over their own office unless also a system
        administrator. True for SYSTEM_ADMIN too: everything an office
        administrator may do, a system administrator may do everywhere.

        This is only half a permission check. It says the user holds admin
        rights; it does not say over whom. Every caller must still compare
        offices unless `is_system_admin` is true.
        """
        return self.role == self.Role.ADMIN or self.is_system_admin

    @property
    def is_viewer(self) -> bool:
        """Read-only. May open records and print a routing slip, nothing else."""
        return self.role == self.Role.VIEWER

    @property
    def is_records_staff(self) -> bool:
        """May do records work on their own office's documents.

        Everyone except a viewer. This is the old {ADMIN, SECRETARY} set with
        USER added, which is not a widening so much as a renaming: the powers
        this gates — acting on the office's drafts, sharing a record, editing
        the office's repository entries, seeing the office columns — are the
        ones the brief's USER row lists, and they are what a SECRETARY account
        actually had. Migrated secretaries therefore keep every one of them.

        Deliberately *not* office-scoped on its own. It answers "may this person
        do records work at all", never "over whose documents" — every call site
        still compares offices, and the ones that matter do so on the line
        immediately after asking this.
        """
        return self.is_active and self.role in {self.Role.USER, self.Role.ADMIN, self.Role.SYSTEM_ADMIN}

    @property
    def can_start_work(self) -> bool:
        """Whether to offer a button that starts new tracking or a new upload.

        Mirrors the two gates the target views apply — `WriteAccessRequiredMixin`
        turns a viewer away, `OfficeAssignedMixin` turns away an account with no
        office — so no page offers a button that only answers with a redirect and
        a warning.

        This hides a control; it does not grant one. Both views still refuse the
        request on their own, which is the check that matters: a hidden button is
        not a permission, and the endpoints stay reachable to anyone who knows the
        URL.

        Lives here rather than in `apps.core.views` because three pages ask it —
        the dashboard, the tracking list and the repository — and two of them
        would otherwise be importing from another app's view module for a
        question that needs nothing but the user.
        """
        if not self.is_records_staff:
            return False
        return self.office_id is not None or self.is_system_admin

    def can_administer(self, other) -> bool:
        """May this user create or edit `other`'s account?

        The office comparison is the whole point: before this existed, any
        administrator could edit every account in the university, because the
        only check was "is an admin at all".
        """
        if not self.is_active or not self.is_office_admin:
            return False
        if self.is_system_admin:
            return True
        if other.is_system_admin or other.is_staff or other.is_superuser:
            return False
        other_office_id = getattr(other, "office_id", None)
        return bool(self.office_id) and self.office_id == other_office_id

    def assignable_roles(self):
        """Roles this user may hand out. Only a system administrator can mint
        another one — otherwise an office administrator could promote itself out
        of its own office scope.
        """
        if self.is_system_admin:
            return list(self.Role.choices)
        return [(value, label) for value, label in self.Role.choices if value != self.Role.SYSTEM_ADMIN]

    @property
    def office_label(self) -> str:
        return self.office.name if self.office_id else "No office assigned"

    def can_act_for_office(self, office) -> bool:
        """Can this user confirm receipt / forward on behalf of `office`?"""
        if office is None or not self.can_start_work:
            return False
        if self.is_system_admin:
            return True
        return self.office_id == getattr(office, "pk", office)


from .permission_models import CapabilityRestriction, UserPermissionPolicy  # noqa: E402,F401
from .security_models import EmailChallenge  # noqa: E402,F401


class PermissionUser(User):
    class Meta:
        proxy = True
        verbose_name = 'user permissions'
        verbose_name_plural = 'User permissions'

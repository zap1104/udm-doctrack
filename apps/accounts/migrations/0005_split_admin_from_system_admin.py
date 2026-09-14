"""Four roles: SYSTEM_ADMIN, ADMIN, USER, VIEWER.

ADMIN used to be the global, cross-office administrator. It now means head of
one office, so every existing ADMIN is promoted to SYSTEM_ADMIN rather than left
holding a name whose reach shrank underneath them — silently demoting the only
accounts that can administer the system is not a migration anyone would notice
until they could no longer fix it.

SECRETARY becomes USER, not ADMIN. It is tempting to read "records personnel"
as "office administrator", but the code is the authority on what the role could
actually do, and it could not administer anybody: `AdminRequiredMixin` was
`("ADMIN",)`, so every user-administration screen was closed to a secretary. A
secretary's powers came entirely from `RecordsStaffRequiredMixin` and
`is_records_staff` — acting on the office's documents, sharing them, editing the
office's repository entries — which is exactly what the new USER role covers.

Mapping them to ADMIN would therefore have handed every secretary the ability to
create accounts, reset passwords and suspend colleagues, granted silently by a
migration nobody would think to audit. A data migration must never be the thing
that widens a permission.

Office heads are promoted to ADMIN by hand afterwards, by somebody who knows
which of them is the head. That cannot be inferred from a role column.

VIEWER is new and nobody is migrated into it.
"""

from django.db import migrations, models

ROLE_MAP = {
    # was global admin  -> keep global reach under its new name
    "ADMIN": "SYSTEM_ADMIN",
    # was records staff -> ordinary office user, which is what it could do
    "SECRETARY": "USER",
}

NEW_CHOICES = [
    ("SYSTEM_ADMIN", "System administrator (all offices)"),
    ("ADMIN", "Office administrator"),
    ("USER", "Office user"),
    ("VIEWER", "Viewer (read-only)"),
]

OLD_CHOICES = [
    ("ADMIN", "System administrator"),
    ("SECRETARY", "Secretary / records personnel"),
    ("USER", "Office user"),
]


def forwards(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    for old, new in ROLE_MAP.items():
        User.objects.filter(role=old).update(role=new)
    # Nobody is left holding a role the field no longer offers. Worth asserting
    # rather than assuming: a stray value survives every screen silently, showing
    # as a raw code where a label belongs and matching no filter.
    assert not User.objects.exclude(
        role__in=["SYSTEM_ADMIN", "ADMIN", "USER", "VIEWER"]
    ).exists(), "a user was left on a role that no longer exists"


def backwards(apps, schema_editor):
    """Undo what can be undone.

    SYSTEM_ADMIN goes back to ADMIN. The secretaries cannot be picked back out
    of USER — after the forward pass they are indistinguishable from every other
    ordinary user, which is the point of the mapping — so reversing returns a
    working database in which nobody is a secretary. VIEWER has no pre-existing
    equivalent either, so those accounts become ordinary users: the nearest role
    that can still sign in.
    """
    User = apps.get_model("accounts", "User")
    User.objects.filter(role="VIEWER").update(role="USER")
    User.objects.filter(role="SYSTEM_ADMIN").update(role="ADMIN")


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_office_colour"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(choices=OLD_CHOICES, default="USER", max_length=16),
        ),
        migrations.RunPython(forwards, backwards),
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(choices=NEW_CHOICES, default="USER", max_length=16),
        ),
    ]

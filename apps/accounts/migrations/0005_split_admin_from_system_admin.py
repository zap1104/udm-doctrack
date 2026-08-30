"""Four roles: SYSTEM_ADMIN, ADMIN, USER, VIEWER.

ADMIN used to be the global, cross-office administrator. It now means head of
one office, so every existing ADMIN is promoted to SYSTEM_ADMIN rather than left
holding a name whose reach shrank underneath them — silently demoting the only
accounts that can administer the system is not a migration anyone would notice
until they could no longer fix it.

SECRETARY is folded into ADMIN. A records secretary is the office's records
person with elevated rights inside their own office, which is precisely what the
new ADMIN is; carrying both would have left two names for one set of powers.
This is the only mapping in this migration that grants anybody more than they
had — a secretary gains the office-scoped user-administration screens — and it
is deliberate: those screens are how an office head manages their own staff, and
the secretary is who does that work.

VIEWER is new and nobody is migrated into it.
"""

from django.db import migrations, models

ROLE_MAP = {
    # was global admin  -> keep global reach under its new name
    "ADMIN": "SYSTEM_ADMIN",
    # was records staff -> office-scoped administrator
    "SECRETARY": "ADMIN",
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
    # ADMIN -> SYSTEM_ADMIN must run before SECRETARY -> ADMIN, or the
    # secretaries promoted into ADMIN would be swept up by the first rule and
    # end up as system administrators.
    User.objects.filter(role="ADMIN").update(role="SYSTEM_ADMIN")
    User.objects.filter(role="SECRETARY").update(role="ADMIN")


def backwards(apps, schema_editor):
    """SYSTEM_ADMIN goes back to ADMIN; office administrators become
    secretaries again. VIEWER has no pre-existing equivalent, so those accounts
    are put back as ordinary users — the nearest role that can still sign in.
    """
    User = apps.get_model("accounts", "User")
    User.objects.filter(role="ADMIN").update(role="SECRETARY")
    User.objects.filter(role="SYSTEM_ADMIN").update(role="ADMIN")
    User.objects.filter(role="VIEWER").update(role="USER")


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

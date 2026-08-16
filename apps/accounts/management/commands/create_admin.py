"""Creates or resets the first administrator account without the interactive prompt.

    python manage.py create_admin --username admin --password "ChangeMe123!"

Used by the setup scripts so a new teammate gets a working login in one step.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.accounts.models import Office

User = get_user_model()


class Command(BaseCommand):
    help = "Create or reset a system administrator account."

    def add_arguments(self, parser):
        parser.add_argument("--username", default="admin")
        parser.add_argument("--password", required=True)
        parser.add_argument("--email", default="")
        parser.add_argument("--office", default="REC", help="Office code to attach the account to.")

    def handle(self, *args, **options):
        office = Office.objects.filter(code__iexact=options["office"]).first()
        if not office:
            office = Office.objects.create(
                code=options["office"].upper(),
                name="Records Management Office",
                cluster="OVPA",
            )
            self.stdout.write(f"Created office {office.code} for the administrator.")

        user, created = User.objects.get_or_create(
            username=options["username"],
            defaults={"email": options["email"], "office": office, "role": "ADMIN"},
        )
        user.email = options["email"] or user.email
        user.office = user.office or office
        user.role = "ADMIN"
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.must_change_password = False
        user.set_password(options["password"])
        user.save()

        verb = "Created" if created else "Reset"
        self.stdout.write(self.style.SUCCESS(f"OK  {verb} administrator '{user.username}'."))

"""Diagnoses and repairs sign-in problems in one command.

    python manage.py fix_login                      # check everything
    python manage.py fix_login --user admin         # check one account
    python manage.py fix_login --reset-password     # also reset to the demo password

Written because every sign-in failure we hit had a different cause but the same
symptom ("login details not working"): a missing account, a rolled-back seed, a
password mismatch, an inactive account, or a django-axes lockout left behind by
an earlier crash. This checks all five and fixes what it safely can.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

User = get_user_model()

DEMO_PASSWORD = "DocTrack2026!"


class Command(BaseCommand):
    help = "Diagnose and repair sign-in problems (lockouts, missing accounts, bad passwords)."

    def add_arguments(self, parser):
        parser.add_argument("--user", help="Check a single username instead of all accounts.")
        parser.add_argument(
            "--reset-password",
            action="store_true",
            help=f"Reset the checked account(s) to the demo password ({DEMO_PASSWORD}).",
        )
        parser.add_argument("--password", default=DEMO_PASSWORD, help="Password to set with --reset-password.")
        parser.add_argument("--keep-lockouts", action="store_true", help="Do not clear django-axes lockouts.")

    def handle(self, *args, **options):
        problems = 0

        # -- 1. Are there any accounts at all? --------------------------------
        self.stdout.write(self.style.MIGRATE_HEADING("1. Accounts"))
        total = User.objects.count()
        if total == 0:
            self.stdout.write(self.style.ERROR("   No user accounts exist at all."))
            self.stdout.write("   Fix: python manage.py seed_demo")
            self.stdout.write(
                "   If seed_demo printed a traceback earlier, nothing was saved — read that error first."
            )
            return
        self.stdout.write(f"   {total} account(s) found.")

        # -- 2. Per-account checks --------------------------------------------
        queryset = User.objects.all().order_by("username")
        if options["user"]:
            queryset = queryset.filter(username=options["user"])
            if not queryset.exists():
                self.stdout.write(self.style.ERROR(f"   No account named '{options['user']}'."))
                self.stdout.write("   Existing usernames: " + ", ".join(User.objects.values_list("username", flat=True)))
                return

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("2. Account health"))
        for user in queryset:
            notes = []
            if not user.is_active:
                notes.append(self.style.WARNING("SUSPENDED"))
                problems += 1
            if not user.office_id:
                notes.append(self.style.WARNING("no office — cannot send or receive"))
                problems += 1
            if not user.has_usable_password():
                notes.append(self.style.ERROR("no usable password"))
                problems += 1

            if options["reset_password"]:
                user.set_password(options["password"])
                user.must_change_password = False
                user.is_active = True
                user.save()
                notes.append(self.style.SUCCESS("password reset"))

            suffix = ("  " + ", ".join(notes)) if notes else ""
            self.stdout.write(f"   {user.username:<14} {user.get_role_display():<24}{suffix}")

        # -- 3. django-axes lockouts ------------------------------------------
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("3. Sign-in lockouts"))
        try:
            from axes.models import AccessAttempt

            attempts = AccessAttempt.objects.all()
            if options["user"]:
                attempts = attempts.filter(username=options["user"])

            count = attempts.count()
            if count == 0:
                self.stdout.write("   No lockouts recorded.")
            else:
                for attempt in attempts:
                    self.stdout.write(
                        f"   {attempt.username or '(unknown)':<14} "
                        f"{attempt.failures_since_start} failure(s) from {attempt.ip_address}"
                    )
                if options["keep_lockouts"]:
                    self.stdout.write(self.style.WARNING(f"   {count} lockout(s) left in place (--keep-lockouts)."))
                    problems += count
                else:
                    attempts.delete()
                    self.stdout.write(self.style.SUCCESS(f"   Cleared {count} lockout(s)."))
                    self.stdout.write(
                        "   Note: a crash during sign-in also counts as a failed attempt, "
                        "so lockouts often outlive the bug that caused them."
                    )
        except Exception as exc:  # axes not installed, or its tables are missing
            self.stdout.write(f"   Skipped — django-axes is not available ({exc}).")

        # -- 4. Verdict --------------------------------------------------------
        self.stdout.write("")
        if problems:
            self.stdout.write(self.style.WARNING(f"{problems} issue(s) need your attention — see above."))
            self.stdout.write("To force a known-good password:")
            self.stdout.write("   python manage.py fix_login --user admin --reset-password")
        else:
            self.stdout.write(self.style.SUCCESS("No sign-in problems found."))
            self.stdout.write(f"Try: admin / {DEMO_PASSWORD}  at  /accounts/login/")

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User


class Command(BaseCommand):
    help = 'Read-only email OTP readiness check. Does not send email or change accounts.'

    def handle(self, *args, **options):
        users = list(User.objects.filter(is_active=True))
        missing = [user.username for user in users if not user.email_is_verified]
        if missing:
            raise CommandError(f'{len(missing)} active accounts have missing or unverified email: ' + ', '.join(missing))
        if not any(user.is_system_admin and user.is_staff and user.has_usable_password() for user in users):
            raise CommandError('At least one active, verified system administrator with Django staff access is required.')
        backend = settings.EMAIL_BACKEND
        if not settings.EMAIL_CONFIGURED or any(part in backend for part in ('.console.', '.dummy.', '.locmem.', '.filebased.')):
            raise CommandError('Configure a real email-delivery backend before activation.')
        self.stdout.write(self.style.SUCCESS(f'{len(users)} active accounts have verified emails; administrator and email configuration checks passed.'))
        self.stdout.write('Actual delivery is not proved by this read-only check. Complete an email-code sign-in before rollout.')

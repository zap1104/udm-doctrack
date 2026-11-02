from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.email_security import recover_email
from apps.accounts.models import User


class Command(BaseCommand):
    help = 'Audited server-operator email recovery. Never bypasses OTP or verifies an email.'

    def add_arguments(self, parser):
        parser.add_argument('--user', required=True)
        parser.add_argument('--email', required=True)
        parser.add_argument('--reason', required=True)

    def handle(self, *args, **options):
        try:
            user = User.objects.get(username=options['user'], is_active=True)
            recover_email(user, email=options['email'], reason=options['reason'], operator=True)
        except (User.DoesNotExist, ValidationError) as exc:
            raise CommandError(' '.join(exc.messages) if isinstance(exc, ValidationError) else 'Active account not found.') from None
        self.stdout.write(self.style.SUCCESS('Email reset and verification invitation sent. Verify the address, then sign in with password and OTP.'))

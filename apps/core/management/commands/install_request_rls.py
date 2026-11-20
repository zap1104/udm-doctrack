from django.core.management.base import BaseCommand
from django.db import connection

from apps.core.row_policies import install_policies


class Command(BaseCommand):
    help = "Install DocTrack row policies for a separately provisioned restricted web login."

    def add_arguments(self, parser):
        parser.add_argument("--role", required=True)

    def handle(self, *, role, **options):
        install_policies(connection, role)
        self.stdout.write(self.style.SUCCESS("Request row policies installed."))

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Register recurring django-q2 maintenance schedules."

    def handle(self, *args, **options):
        if not settings.ENABLE_BACKGROUND_TASKS:
            self.stdout.write("Background tasks are disabled; no schedules were changed.")
            return

        from django_q.models import Schedule

        schedule, created = Schedule.objects.update_or_create(
            name="notification-pruning",
            defaults={
                "func": "apps.core.tasks.prune_notifications",
                "schedule_type": Schedule.DAILY,
                "repeats": -1,
            },
        )
        action = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{action} notification-pruning schedule ({schedule.pk})."))

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Register recurring django-q2 maintenance schedules."

    def handle(self, *args, **options):
        if not settings.ENABLE_BACKGROUND_TASKS:
            self.stdout.write("Background tasks are disabled; no schedules were changed.")
            return

        from django_q.models import Schedule

        # Both run daily. The chase has to be a schedule rather than a signal:
        # "still not received after two days" and "past its deadline" become
        # true by time passing, not by anybody doing something there is a hook
        # to hang off.
        wanted = [
            ("notification-pruning", "apps.core.tasks.prune_notifications"),
            ("notification-chase", "apps.core.tasks.chase_unreceived_and_overdue"),
        ]
        for name, func in wanted:
            schedule, created = Schedule.objects.update_or_create(
                name=name,
                defaults={
                    "func": func,
                    "schedule_type": Schedule.DAILY,
                    "repeats": -1,
                },
            )
            action = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(f"{action} {name} schedule ({schedule.pk})."))

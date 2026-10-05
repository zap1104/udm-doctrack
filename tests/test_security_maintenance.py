"""Security cleanup does not remove active limits and is registered for workers."""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.core.models import SecurityThrottle
from apps.core.tasks import prune_security_throttles

pytestmark = pytest.mark.django_db


def test_cleanup_removes_only_expired_limit_rows():
    now = timezone.now()
    SecurityThrottle.objects.create(key="expired", attempts=4, expires_at=now - timedelta(minutes=1))
    SecurityThrottle.objects.create(key="current", attempts=4, expires_at=now + timedelta(minutes=5))
    assert prune_security_throttles() == 1
    assert not SecurityThrottle.objects.filter(pk="expired").exists()
    assert SecurityThrottle.objects.get(pk="current").attempts == 4
    assert prune_security_throttles() == 0


def test_worker_schedules_register_security_cleanup_without_duplicates(settings):
    if "django_q" not in settings.INSTALLED_APPS:
        pytest.skip("Exercised by the background-enabled CI step.")
    from django_q.models import Schedule

    settings.ENABLE_BACKGROUND_TASKS = True
    call_command("ensure_schedules", verbosity=0)
    call_command("ensure_schedules", verbosity=0)
    schedules = Schedule.objects.filter(name="security-throttle-pruning")
    assert schedules.count() == 1
    schedule = schedules.get()
    assert schedule.func == "apps.core.tasks.prune_security_throttles"
    assert schedule.schedule_type == Schedule.DAILY
    assert schedule.repeats == -1


def test_disabled_workers_do_not_register_schedules(settings):
    settings.ENABLE_BACKGROUND_TASKS = False
    call_command("ensure_schedules", verbosity=0)

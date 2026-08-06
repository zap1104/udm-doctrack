"""Creates the database cache table used by settings.CACHES.

Done as a migration rather than a documented `manage.py createcachetable`
step so a fresh clone, the CI job and the Render release phase all get the
table from the `migrate` they already run. `createcachetable` only touches
cache entries that use the database backend, so this is a no-op when
REDIS_URL is set.
"""

from django.core.management import call_command
from django.db import migrations


def create_cache_table(apps, schema_editor):
    call_command("createcachetable", database=schema_editor.connection.alias, verbosity=0)


def drop_cache_table(apps, schema_editor):
    from django.conf import settings

    for config in settings.CACHES.values():
        if config["BACKEND"] == "django.core.cache.backends.db.DatabaseCache":
            schema_editor.execute(f'DROP TABLE IF EXISTS "{config["LOCATION"]}"')


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_cache_table, drop_cache_table),
    ]

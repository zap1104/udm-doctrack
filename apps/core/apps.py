from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    label = "core"
    verbose_name = "Core & master data"

    def ready(self):
        from django.db.models.signals import post_migrate

        from . import checks  # noqa: F401
        from .public_database import after_migrations

        post_migrate.connect(after_migrations, sender=self, dispatch_uid="core.close_public_api_grants")

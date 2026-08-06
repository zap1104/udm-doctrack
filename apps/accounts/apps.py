from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    label = "accounts"
    verbose_name = "Accounts & offices"

    def ready(self):
        from django.conf import settings

        from . import signals  # noqa: F401

        if getattr(settings, "ENABLE_AXES", False):
            from . import axes_hooks  # noqa: F401

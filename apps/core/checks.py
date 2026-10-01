from urllib.parse import urlsplit

from django.conf import settings
from django.core.checks import Error, Tags, register


@register(Tags.security, deploy=True)
def deployment_configuration(app_configs, **kwargs):
    """Fail closed on settings that would lose data or weaken production security."""
    if getattr(settings, "DEBUG", True):
        return [Error("DEBUG must be disabled for deployment.", hint="Set DJANGO_DEBUG=False.", id="doctrack.E013")]
    errors = []
    if settings.SECRET_KEY == "dev-only-insecure-key-change-me" or len(settings.SECRET_KEY) < 50:
        errors.append(Error("DJANGO_SECRET_KEY is the development default or too short.", hint="Set DJANGO_SECRET_KEY to a random secret before deploying.", id="doctrack.E001"))
    if not settings.ALLOWED_HOSTS or "*" in settings.ALLOWED_HOSTS:
        errors.append(Error("DJANGO_ALLOWED_HOSTS is empty or allows every host.", hint="Set DJANGO_ALLOWED_HOSTS to the deployed hostname(s).", id="doctrack.E002"))
    if getattr(settings, "FILE_STORAGE_BACKEND", "local") == "local":
        errors.append(Error("STORAGE_BACKEND=local is not durable on a production platform.", hint="Set STORAGE_BACKEND=s3 or azure and configure its credentials.", id="doctrack.E003"))
    if not getattr(settings, "ENABLE_CSP", False):
        errors.append(Error("Content Security Policy is disabled.", hint="Set ENABLE_CSP=True in the production environment.", id="doctrack.E004"))
    if settings.EMAIL_BACKEND == "django.core.mail.backends.console.EmailBackend":
        errors.append(Error("EMAIL_BACKEND is the console backend.", hint="Set EMAIL_BACKEND to smtp.EmailBackend and provide EMAIL_HOST, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD, and EMAIL_PORT.", id="doctrack.E005"))
    if not getattr(settings, "SECURE_SSL_REDIRECT", False):
        errors.append(Error("HTTPS redirect is disabled.", hint="Set SECURE_SSL_REDIRECT=True in the production environment.", id="doctrack.E006"))
    storage = settings.STORAGES["default"]
    options = storage.get("OPTIONS", {})
    if storage["BACKEND"].endswith("FileSystemStorage"):
        if settings.FILE_STORAGE_BACKEND != "local":
            errors.append(Error("Cloud storage was selected but its package is missing.", id="doctrack.E014"))
    elif settings.FILE_STORAGE_BACKEND == "s3" and not options.get("bucket_name"):
        errors.append(Error("The private storage bucket name is missing.", id="doctrack.E015"))
    if settings.EMAIL_BACKEND == "django.core.mail.backends.smtp.EmailBackend" and (not settings.EMAIL_HOST or not settings.EMAIL_USE_TLS):
        errors.append(Error("Production SMTP requires a host and encrypted transport.", id="doctrack.E016"))
    if not settings.ENABLE_AXES:
        errors.append(Error("Login lockout is disabled or django-axes is missing.", id="doctrack.E009"))
    if settings.PASSWORD_HASHERS[0] != "django.contrib.auth.hashers.Argon2PasswordHasher":
        errors.append(Error("Argon2 is not the preferred password hasher.", id="doctrack.E010"))
    if settings.ALLOW_DEMO_SEED:
        errors.append(Error("Demo seeding is enabled in production.", hint="Set ALLOW_DEMO_SEED=False.", id="doctrack.E011"))
    base = urlsplit(settings.SITE_BASE_URL)
    if settings.EMAIL_CONFIGURED and (base.scheme != "https" or not base.hostname or base.username or base.query or base.fragment):
        errors.append(Error("SITE_BASE_URL must be an explicit HTTPS address for account emails.", id="doctrack.E012"))
    return errors


@register()
def office_hours_configuration(app_configs, **kwargs):
    """Refuse an office day that cannot be counted.

    `business_time` clips a stray lunch to the window rather than counting a
    negative day, so a typo in these settings would not crash anything — it
    would quietly change every turnaround figure. That is worth failing on.
    """
    opens, closes = settings.OFFICE_DAY_START, settings.OFFICE_DAY_END
    lunch_from, lunch_to = settings.OFFICE_LUNCH_START, settings.OFFICE_LUNCH_END
    errors = []
    if closes <= opens:
        errors.append(Error("OFFICE_DAY_END is not after OFFICE_DAY_START.", hint="Set the office window, e.g. 08:00 to 17:00.", id="doctrack.E007"))
    if lunch_to < lunch_from or lunch_from < opens or lunch_to > closes:
        errors.append(Error("The lunch break is not inside the office day.", hint="Set OFFICE_LUNCH_START and OFFICE_LUNCH_END within the window, or both to the same time for no break.", id="doctrack.E008"))
    if closes > opens and lunch_from == opens and lunch_to == closes:
        errors.append(Error("The lunch break consumes the entire office day.", hint="Leave a positive working interval so turnaround can be expressed in working days.", id="doctrack.E017"))
    if not 1 <= settings.OFFICE_WEEK_DAYS <= 7:
        errors.append(Error("OFFICE_WEEK_DAYS must be between 1 and 7.", hint="Set the number of working weekdays, e.g. 5 for Monday to Friday.", id="doctrack.E018"))
    return errors

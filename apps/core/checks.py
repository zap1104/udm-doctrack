from django.conf import settings
from django.core.checks import Error, Tags, register


@register(Tags.security, deploy=True)
def deployment_configuration(app_configs, **kwargs):
    """Fail closed on settings that would lose data or weaken production security."""
    if getattr(settings, "DEBUG", True):
        return []
    errors = []
    if settings.SECRET_KEY == "dev-only-insecure-key-change-me":
        errors.append(Error("DJANGO_SECRET_KEY is still the development default.", hint="Set DJANGO_SECRET_KEY to a random secret before deploying.", id="doctrack.E001"))
    if not settings.ALLOWED_HOSTS or settings.ALLOWED_HOSTS == ["*"]:
        errors.append(Error("DJANGO_ALLOWED_HOSTS is empty or allows every host.", hint="Set DJANGO_ALLOWED_HOSTS to the deployed hostname(s).", id="doctrack.E002"))
    if getattr(settings, "FILE_STORAGE_BACKEND", "local") == "local":
        errors.append(Error("STORAGE_BACKEND=local is not durable on a production platform.", hint="Set STORAGE_BACKEND=s3 or azure and configure its credentials.", id="doctrack.E003"))
    if not getattr(settings, "ENABLE_CSP", False):
        errors.append(Error("Content Security Policy is disabled.", hint="Set ENABLE_CSP=True in the production environment.", id="doctrack.E004"))
    if settings.EMAIL_BACKEND == "django.core.mail.backends.console.EmailBackend":
        errors.append(Error("EMAIL_BACKEND is the console backend.", hint="Set EMAIL_BACKEND to smtp.EmailBackend and provide EMAIL_HOST, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD, and EMAIL_PORT.", id="doctrack.E005"))
    if not getattr(settings, "SECURE_SSL_REDIRECT", False):
        errors.append(Error("HTTPS redirect is disabled.", hint="Set SECURE_SSL_REDIRECT=True in the production environment.", id="doctrack.E006"))
    return errors

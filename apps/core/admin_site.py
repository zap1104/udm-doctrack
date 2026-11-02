"""The raw administration site is reserved for global administrators."""

from django.contrib.admin import AdminSite
from django.contrib.admin.apps import AdminConfig


class SystemAdminSite(AdminSite):
    def admin_view(self, view, cacheable=False):
        from functools import wraps

        from django.core.exceptions import PermissionDenied
        from django.db import connection, transaction

        from apps.accounts.models import User
        from apps.accounts.otp import verified

        wrapped = super().admin_view(view, cacheable=cacheable)

        @wraps(wrapped)
        def checked(request, *args, **kwargs):
            if request.method in {'GET', 'HEAD', 'OPTIONS'} or not request.user.is_authenticated:
                return wrapped(request, *args, **kwargs)
            with transaction.atomic():
                # Serialize raw-admin writes with account/policy changes. The
                # actor cannot be demoted halfway through an authorized write.
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_advisory_xact_lock(%s)', [0x55444D414343])
                actor = User.objects.select_for_update(no_key=True).filter(pk=request.user.pk, is_active=True).first()
                if actor is None or not actor.is_system_admin or not actor.is_staff:
                    raise PermissionDenied('Your administrator permissions changed. Sign in again.')
                request.user = actor
                if not verified(request):
                    raise PermissionDenied('Sign in with your password and email code before continuing.')
                return wrapped(request, *args, **kwargs)
        return checked

    def get_urls(self):
        from django.urls import path

        from apps.accounts.permission_admin import record_sharing

        def sharing(request):
            return record_sharing(request, self)
        return [path('record-sharing/', self.admin_view(sharing), name='record_sharing')] + super().get_urls()

    def get_app_list(self, request, app_label=None):
        from django.urls import reverse

        apps = super().get_app_list(request, app_label)
        if request.user.is_authenticated and request.user.is_system_admin:
            for app in apps:
                if app['app_label'] == 'accounts':
                    app['models'].append({'name': 'Record sharing', 'object_name': 'RecordSharing',
                        'perms': {'view': True}, 'admin_url': reverse('admin:record_sharing'), 'view_only': True})
        return apps

    def has_permission(self, request):
        from apps.accounts.otp import verified

        return super().has_permission(request) and request.user.is_system_admin and verified(request)

    def login(self, request, extra_context=None):
        from urllib.parse import urlencode

        from django.shortcuts import redirect
        from django.urls import reverse

        from apps.accounts.otp import required, safe_return

        if required():
            target = safe_return(request, request.GET.get('next') or request.POST.get('next') or '/django-admin/')
            return redirect(reverse('accounts:login') + '?' + urlencode({'next': target}))
        return super().login(request, extra_context)


class SystemAdminConfig(AdminConfig):
    default_site = "apps.core.admin_site.SystemAdminSite"

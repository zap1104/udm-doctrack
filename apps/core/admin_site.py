"""The raw administration site is reserved for global administrators."""

from django.contrib.admin import AdminSite
from django.contrib.admin.apps import AdminConfig


class SystemAdminSite(AdminSite):
    def has_permission(self, request):
        return super().has_permission(request) and request.user.is_system_admin


class SystemAdminConfig(AdminConfig):
    default_site = "apps.core.admin_site.SystemAdminSite"

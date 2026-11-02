from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from . import permission_admin  # noqa: F401
from .models import LoginLockout, Office, User


@admin.register(Office)
class OfficeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "swatch", "cluster", "head_name", "sort_order", "is_active")
    list_filter = ("cluster", "is_active")
    search_fields = ("name", "code", "head_name")

    @admin.display(description="Badge colour")
    def swatch(self, obj):
        """The colour itself, not just its hex. A list of hex codes tells you
        nothing about whether two offices are about to look alike."""
        from django.utils.html import format_html

        badge = obj.badge
        return format_html(
            '<span style="display:inline-block;padding:2px 8px;border-radius:5px;'
            'background:{};color:{};font-weight:700;">{}</span>',
            badge["tint"], badge["ink"], badge["base"],
        )


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "display_name", "office", "role", "is_active", "last_login")
    list_filter = ("role", "office", "is_active")
    search_fields = ("username", "first_name", "last_name", "email")
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("DocTrack profile", {"fields": ("office", "role", "position", "phone", "must_change_password")}),
    )
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ("DocTrack profile", {"fields": ("first_name", "last_name", "email", "office", "role", "position")}),
    )

    def get_readonly_fields(self, request, obj=None):
        from .otp import required

        return ('email',) if obj is not None and required() else ()

    def get_form(self, request, obj=None, **kwargs):
        from .otp import required

        form = super().get_form(request, obj, **kwargs)
        if required() and 'email' in form.base_fields:
            form.base_fields['email'].required = True
        return form

    def changeform_view(self, request, object_id=None, form_url='', extra_context=None):
        from urllib.parse import urlencode

        from django.shortcuts import redirect

        from .otp import verified

        if request.method == 'POST' and not verified(request, recent=True):
            return redirect('/accounts/reauthenticate/?' + urlencode({'next': request.path}))
        return super().changeform_view(request, object_id, form_url, extra_context)


@admin.register(LoginLockout)
class LoginLockoutAdmin(admin.ModelAdmin):
    list_display = ("key", "kind", "stage", "locked_until", "last_lockout_at")
    list_filter = ("kind",)
    search_fields = ("key",)
    readonly_fields = ("created_at", "updated_at")
    actions = ["clear_escalation"]

    @admin.action(description="Clear escalation (next lockout starts from the base wait)")
    def clear_escalation(self, request, queryset):
        count, _ = queryset.delete()
        self.message_user(request, f"Cleared {count} lockout escalation record(s).")

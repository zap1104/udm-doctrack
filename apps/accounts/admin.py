from copy import copy

from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import AdminPasswordChangeForm
from django.core.exceptions import PermissionDenied

from . import permission_admin  # noqa: F401
from .admin_safety import is_ready_system_admin, ready_system_admins
from .models import LoginLockout, Office, User


def _require_ready_replacement(user):
    if is_ready_system_admin(user) and not ready_system_admins(exclude_ids=[user.pk]):
        raise forms.ValidationError(
            'Keep at least one active system administrator with staff access, a usable '
            'password and a verified email when email codes are required.'
        )


class AvailableAdminPasswordChangeForm(AdminPasswordChangeForm):
    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('set_usable_password', True):
            _require_ready_replacement(self.user)
        return cleaned


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
    change_password_form = AvailableAdminPasswordChangeForm
    list_display = ("username", "display_name", "office", "role", "is_active", "last_login")
    list_filter = ("role", "office", "is_active")
    search_fields = ("username", "first_name", "last_name", "email")
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("DocTrack profile", {"fields": ("office", "role", "position", "phone", "must_change_password")}),
    )
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ("DocTrack profile", {"fields": ("first_name", "last_name", "email", "office", "role", "position")}),
    )

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            # The site holds the account-wide lock. Also lock the target before
            # Django builds a form that later saves the whole account instance.
            queryset = queryset.select_for_update(no_key=True, of=('self',))
        return queryset

    def get_readonly_fields(self, request, obj=None):
        from .otp import required

        return ('email',) if obj is not None and required() else ()

    def get_form(self, request, obj=None, **kwargs):
        from .otp import required

        form = super().get_form(request, obj, **kwargs)
        if required() and 'email' in form.base_fields:
            form.base_fields['email'].required = True
        if obj is None:
            return form

        class AvailableAccountForm(form):
            def clean(self):
                cleaned = super().clean()
                proposed = copy(self.instance)
                for field in ('role', 'is_active', 'is_staff'):
                    if field in cleaned:
                        setattr(proposed, field, cleaned[field])
                if proposed.pk == request.user.pk:
                    if not proposed.is_active:
                        raise forms.ValidationError('You cannot deactivate your own account.')
                    if not proposed.is_office_admin:
                        raise forms.ValidationError('You cannot remove your own administrator role.')
                if not is_ready_system_admin(proposed):
                    _require_ready_replacement(self.instance)
                return cleaned

        return AvailableAccountForm

    def _check_deletions(self, request, targets):
        ids = [target.pk for target in targets]
        if request.user.pk in ids:
            raise PermissionDenied('You cannot delete your own administrator account.')
        if any(is_ready_system_admin(target) for target in targets) and not ready_system_admins(exclude_ids=ids):
            raise PermissionDenied('Keep a ready system administrator before deleting these accounts.')

    def delete_model(self, request, obj):
        self._check_deletions(request, [obj])
        return super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        self._check_deletions(request, list(queryset))
        return super().delete_queryset(request, queryset)

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

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import LoginLockout, Office, User


@admin.register(Office)
class OfficeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "cluster", "head_name", "sort_order", "is_active")
    list_filter = ("cluster", "is_active")
    search_fields = ("name", "code", "head_name")


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

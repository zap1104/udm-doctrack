from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import Office, User


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

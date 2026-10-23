from django.contrib import admin

from .models import (
    AuditLog,
    DocumentType,
    FileLifecycleEvent,
    Holiday,
    MetadataFieldDefinition,
    StorageCleanupIntent,
    Tag,
    TagRule,
)


@admin.register(Holiday)
class HolidayAdmin(admin.ModelAdmin):
    list_display = ("name", "date", "recurring", "is_active")
    list_filter = ("recurring", "is_active")
    search_fields = ("name",)


@admin.register(DocumentType)
class DocumentTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "retention_years", "sort_order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "code")


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    readonly_fields = ("canonical",)
    list_display = ("name", "category", "usage_count", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("name",)


@admin.register(TagRule)
class TagRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "match_type", "pattern", "suggest_tag", "suggest_document_type", "priority", "is_active")
    list_filter = ("match_type", "is_active")
    search_fields = ("name", "pattern")


@admin.register(MetadataFieldDefinition)
class MetadataFieldDefinitionAdmin(admin.ModelAdmin):
    list_display = ("label", "key", "field_type", "is_required", "is_searchable", "sort_order", "is_active")
    list_filter = ("field_type", "is_required", "is_active")
    search_fields = ("label", "key")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor_label", "action", "target_type", "summary")
    list_filter = ("action", "created_at")
    search_fields = ("summary", "actor_label", "target_id")
    readonly_fields = [field.name for field in AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StorageCleanupIntent)
class StorageCleanupIntentAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "collection", "office_id_snapshot", "state", "attempts", "last_error")
    list_filter = ("state", "collection")
    readonly_fields = ("id", "created_at", "reason", "resource_kind", "parent_id", "collection", "office_id_snapshot",
        "state", "attempts", "lease_until", "retry_at", "last_error", "completed_at", "superseded_by")
    fields = readonly_fields
    actions = ("retry_cleanup",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs if request.user.is_system_admin else qs.filter(office_id_snapshot=request.user.office_id)

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_office_admin

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request) and (obj is None or request.user.is_system_admin or obj.office_id_snapshot == request.user.office_id)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Retry selected failed or blocked cleanup", permissions=["view"])
    def retry_cleanup(self, request, queryset):
        from django.db import transaction

        from .permissions import active_writer
        with transaction.atomic():
            actor = active_writer(request.user)
            if not actor.is_office_admin:
                return
            if not actor.is_system_admin:
                queryset = queryset.filter(office_id_snapshot=actor.office_id)
            queryset.filter(state__in=["retry", "blocked"], superseded_by__isnull=True).update(state="pending", retry_at=None)


@admin.register(FileLifecycleEvent)
class FileLifecycleEventAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "action", "resource_kind", "parent_id", "actor_name_snapshot")
    readonly_fields = [field.name for field in FileLifecycleEvent._meta.fields]
    list_filter = ("action", "collection")

    def get_queryset(self, request):
        from django.db.models import Q

        from apps.documents.models import Document
        from apps.tracking.models import ExternalTrackingRecord, TrackingRecord
        qs = super().get_queryset(request)
        if request.user.is_system_admin:
            return qs
        return qs.filter(
            Q(resource_kind="tracking", parent_id__in=TrackingRecord.objects.visible_to(request.user).values("pk")) |
            Q(resource_kind="external", parent_id__in=ExternalTrackingRecord.objects.visible_to(request.user).values("pk")) |
            Q(resource_kind="repository", parent_id__in=Document.objects.visible_to(request.user).values("pk")))

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_office_admin

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request) and (obj is None or self.get_queryset(request).filter(pk=obj.pk).exists())

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

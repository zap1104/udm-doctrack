from django.contrib import admin

from .models import AuditLog, DocumentType, MetadataFieldDefinition, Tag, TagRule


@admin.register(DocumentType)
class DocumentTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "retention_years", "sort_order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "code")


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
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

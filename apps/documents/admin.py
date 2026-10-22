"""Raw access to the archive. Normal filing happens through the app."""

from django.contrib import admin

from .models import (
    DeletionReview,
    Document,
    DocumentAccessGrant,
    DocumentFile,
    DocumentFileVersion,
    DocumentMetadata,
    MetadataSuggestion,
    SearchQueryLog,
    SearchResultClick,
)


class DocumentFileInline(admin.TabularInline):
    model = DocumentFile
    extra = 0
    readonly_fields = tuple(field.name for field in DocumentFile._meta.fields)
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(DocumentFile, DocumentFileVersion)
class ImmutableRepositoryFileAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class DocumentMetadataInline(admin.TabularInline):
    model = DocumentMetadata
    extra = 0


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    from apps.core.type_forms import DocumentTypeAdminForm
    form = DocumentTypeAdminForm
    list_display = ("title", "office", "document_type", "year", "source", "ocr_status", "created_at")
    list_filter = ("source", "ocr_status", "access_level", "office", "document_type", "year")
    search_fields = ("title", "description", "reference_number", "author_name", "recipient_name")
    date_hierarchy = "created_at"
    filter_horizontal = ("tags", "document_types")
    readonly_fields = ("index_title", "index_meta", "index_extra", "created_at", "updated_at", "is_active", "training_eligible", "training_family")
    inlines = [DocumentFileInline, DocumentMetadataInline]
    actions = ["rebuild_search_index"]

    def has_delete_permission(self, request, obj=None):
        # Approval, recovery and file cleanup belong to the review workflow.
        return False

    def has_change_permission(self, request, obj=None):
        if obj is not None and not obj.is_active:
            return False
        return super().has_change_permission(request, obj)

    def save_related(self, request, form, formsets, change):
        from .tagging import replace_human_tags

        tags = form.cleaned_data.pop("tags", None)
        super().save_related(request, form, formsets, change)
        if tags is not None:
            replace_human_tags(form.instance, list(tags), user=request.user)
        form.instance.rebuild_index()

    @admin.action(description="Rebuild the search index for the selected documents")
    def rebuild_search_index(self, request, queryset):
        queryset = queryset.filter(is_active=True)
        for document in queryset:
            document.rebuild_index()
        self.message_user(request, f"Reindexed {queryset.count()} document(s).")


@admin.register(MetadataSuggestion)
class MetadataSuggestionAdmin(admin.ModelAdmin):
    """The labelled training set. Read-only on purpose — editing it would
    corrupt the record of what a human actually decided."""

    list_display = ("document", "engine", "engine_version", "reviewed_by", "reviewed_at")
    list_filter = ("engine", "engine_version")
    search_fields = ("document__title",)
    readonly_fields = tuple(field.name for field in MetadataSuggestion._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(SearchQueryLog)
class SearchQueryLogAdmin(admin.ModelAdmin):
    list_display = ("query", "user", "result_count", "duration_ms", "created_at")
    search_fields = ("query",)
    readonly_fields = tuple(field.name for field in SearchQueryLog._meta.fields)

    def has_add_permission(self, request):
        return False


@admin.register(SearchResultClick)
class SearchResultClickAdmin(admin.ModelAdmin):
    list_display = ("query_log", "document", "user", "rank", "created_at")
    readonly_fields = tuple(field.name for field in SearchResultClick._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(DocumentAccessGrant)
class DocumentAccessGrantAdmin(admin.ModelAdmin):
    list_display = ("document", "office", "user", "granted_by", "created_at")
    search_fields = ("document__title", "reason")


@admin.register(DeletionReview)
class DeletionReviewAdmin(admin.ModelAdmin):
    list_display = ("document", "decision", "reviewer_label", "reviewed_at")
    readonly_fields = tuple(field.name for field in DeletionReview._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

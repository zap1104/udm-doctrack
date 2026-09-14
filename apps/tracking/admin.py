"""Raw access for administrators. Day-to-day work happens in the app itself —
this exists for data repair and inspection, not routine use."""

from django.contrib import admin

from .models import (
    Attachment,
    RecordAccessGrant,
    RecordActivity,
    RoutingSLA,
    RoutingStep,
    TrackingRecord,
)


class RoutingStepInline(admin.TabularInline):
    model = RoutingStep
    extra = 0
    readonly_fields = ("sequence", "sent_at", "received_at", "received_by")
    can_delete = False


class RecordActivityInline(admin.TabularInline):
    model = RecordActivity
    extra = 0
    readonly_fields = ("event", "message", "detail", "actor", "created_at")
    can_delete = False


@admin.register(TrackingRecord)
class TrackingRecordAdmin(admin.ModelAdmin):
    list_display = ("tracking_number", "subject", "originating_office", "current_office", "status", "last_movement_at")
    list_filter = ("status", "priority", "classification", "originating_office", "document_type")
    search_fields = ("tracking_number", "subject", "instructions", "remarks")
    date_hierarchy = "created_at"
    readonly_fields = ("tracking_number", "created_at", "updated_at", "last_movement_at")
    inlines = [RoutingStepInline, RecordActivityInline]
    autocomplete_fields = ("originating_office", "current_office", "created_by", "current_holder")


@admin.register(RoutingStep)
class RoutingStepAdmin(admin.ModelAdmin):
    list_display = ("record", "sequence", "action", "from_office", "to_office", "sent_at", "received_at")
    list_filter = ("action", "to_office")
    search_fields = ("record__tracking_number", "record__subject")
    readonly_fields = ("received_at", "received_by")


@admin.register(RecordActivity)
class RecordActivityAdmin(admin.ModelAdmin):
    list_display = ("record", "event", "message", "actor", "created_at")
    list_filter = ("event",)
    search_fields = ("record__tracking_number", "message", "detail")
    readonly_fields = tuple(field.name for field in RecordActivity._meta.fields)

    def has_add_permission(self, request):
        return False  # the timeline is written by services only


@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    list_display = ("original_name", "record", "size", "uploaded_by", "created_at")
    search_fields = ("original_name", "record__tracking_number")


@admin.register(RecordAccessGrant)
class RecordAccessGrantAdmin(admin.ModelAdmin):
    list_display = ("record", "office", "user", "granted_by", "created_at")
    search_fields = ("record__tracking_number", "reason")


@admin.register(RoutingSLA)
class RoutingSLAAdmin(admin.ModelAdmin):
    """The one screen that sets how long an office has to act.

    Editable in the list, because the whole table is a handful of numbers and
    opening a form per row to change a 3 to a 5 is the kind of friction that
    stops anybody maintaining it.

    A blank office or document type is the wildcard, so the list shows both
    columns and filters on both — a reader has to be able to see at a glance
    which rows are house rules and which are exceptions.
    """

    list_display = ("office", "document_type", "due_days", "is_active", "updated_at")
    list_editable = ("due_days", "is_active")
    list_filter = ("is_active", "office", "document_type")
    search_fields = ("office__code", "office__name", "document_type__code", "document_type__name")
    autocomplete_fields = ("office", "document_type")

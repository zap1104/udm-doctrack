"""Bounded, parent-authorized metadata and retained-file populations."""
from django.core import signing
from django.core.exceptions import ValidationError
from django.db.models import Count, Max, Prefetch, Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse


def group_kind(group):
    return {"attachmentgroup": "tracking", "externalattachmentgroup": "external", "documentfile": "repository"}[group._meta.model_name]


def authorize_group(group, user):
    kind = group_kind(group)
    parent = group.document if kind == "repository" else group.record
    visible = parent.can_user_view(user) if kind != "external" else type(parent).objects.visible_to(user).filter(pk=parent.pk).exists()
    if not user.is_active or not visible:
        raise Http404
    return parent


def resolve_group(kind, pk, user):
    from apps.documents.models import DocumentFile
    from apps.tracking.models import AttachmentGroup, ExternalAttachmentGroup
    model = {"tracking": AttachmentGroup, "external": ExternalAttachmentGroup, "repository": DocumentFile}.get(kind)
    if model is None:
        raise Http404
    group = get_object_or_404(model, pk=pk)
    authorize_group(group, user)
    return group


def history_url(group):
    return reverse("core:file_history", args=[group_kind(group), group.pk])


def retained_groups(queryset):
    """SQL limits each group to five payload metadata rows, even pre-rollout."""
    model = queryset.model._meta.get_field("versions").related_model
    retained = model.objects.filter(retired_at__isnull=True).exclude(file="").order_by("-version_number")[:5]
    return queryset.annotate(version_total=Count("versions"), latest_number=Max("versions__version_number"),
        audit_total=Count("versions", filter=Q(versions__retired_at__isnull=False))).prefetch_related(
            Prefetch("versions", queryset=retained, to_attr="retained_versions"))


def current_tracking_files(record):
    return [group.retained_versions[0] for group in retained_groups(record.file_groups.filter(discarded_at__isnull=True)) if group.retained_versions]


def file_history_rows(parent, *, user, cursor=None):
    from apps.core.models import FileLifecycleEvent
    from apps.tracking.version_views import version_rows
    group = parent
    record = authorize_group(group, user)
    kind = group_kind(group)
    phase, before = "versions", None
    if cursor:
        try:
            data = signing.loads(cursor, salt="file-audit-page", max_age=86400)
            if data["group"] != group.pk or data["kind"] != kind or data["phase"] not in {"versions", "events"}:
                raise ValueError
            phase, before = data["phase"], int(data["before"])
            if before < 1:
                raise ValueError
        except (signing.BadSignature, ValueError, KeyError, TypeError) as exc:
            raise ValidationError("Open a valid file history page from this record.") from exc
    entries, next_cursor = [], None
    def continuation(phase, before):
        return signing.dumps({"kind": kind, "group": group.pk, "phase": phase, "before": before}, salt="file-audit-page")
    if phase == "versions":
        versions = group.versions.order_by("-version_number")
        if before:
            versions = versions.filter(version_number__lt=before)
        items = list(versions[:26])
        entries = [dict(row, type="version") for row in version_rows(items[:25], kind, user)]
        if len(items) > 25:
            next_cursor = continuation("versions", items[24].version_number)
            return {"entries": entries, "next_cursor": next_cursor, "record": record}
        before = None
    remaining = 25 - len(entries)
    events = FileLifecycleEvent.objects.filter(resource_kind=kind, parent_id=record.pk, group_id=group.pk)
    if before:
        events = events.filter(pk__lt=before)
    items = list(events.order_by("-pk")[:remaining + 1])
    entries.extend({"type": "event", "item": item} for item in items[:remaining])
    if len(items) > remaining:
        next_cursor = continuation("events", items[remaining - 1].pk) if remaining else continuation("events", items[0].pk + 1)
    return {"entries": entries, "next_cursor": next_cursor, "record": record}

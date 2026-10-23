"""Exact-version links. Storage paths are never accepted from a request."""

from dataclasses import dataclass
from urllib.parse import urlencode

from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse

from apps.core.utils import sign_download


@dataclass
class FileResource:
    item: object
    parent: object
    kind: str
    current_number: int

    @property
    def version_number(self):
        return self.item.version_number


def resource_for(kind, pk, user):
    if kind == "upload":
        from apps.core.models import StagedUpload
        from apps.core.upload_context import UploadContext
        from apps.core.upload_drafts import read_draft

        item = get_object_or_404(StagedUpload.objects.select_related("draft"), pk=pk, draft__owner=user)
        parent = item.draft
        try:
            read_draft(parent.pk, user=user, context=UploadContext(**parent.binding))
        except ValidationError:
            # The parent was authorized before its closed/expired state was
            # reported. Return metadata only so the view can issue private 410.
            pass
        return FileResource(item, parent, kind, 1)
    if kind == "tracking":
        from apps.tracking.models import Attachment

        item = get_object_or_404(Attachment.objects.select_related("record", "file_group"), pk=pk)
        parent = item.record
        visible = parent.can_user_view(user)
        versions = item.file_group.versions
    elif kind == "external":
        from apps.tracking.models import ExternalAttachment, ExternalTrackingRecord

        item = get_object_or_404(ExternalAttachment.objects.select_related("record", "file_group"), pk=pk)
        parent = item.record
        visible = ExternalTrackingRecord.objects.visible_to(user).filter(pk=parent.pk).exists()
        versions = item.file_group.versions
    elif kind == "repository":
        from apps.documents.models import DocumentFileVersion

        item = get_object_or_404(
            DocumentFileVersion.objects.select_related("document_file__document"), pk=pk
        )
        parent = item.document_file.document
        visible = parent.can_user_view(user)
        versions = item.document_file.versions
    else:
        raise Http404
    if not user.is_active or not visible:
        raise Http404
    latest = versions.order_by("-version_number").values_list("version_number", flat=True).first()
    return FileResource(item, parent, kind, latest or item.version_number)


def file_url(kind, pk, user, purpose="preview"):
    token = sign_download(f"file-version:{purpose}:{kind}:{pk}:{user.pk}")
    route = reverse(f"core:upload_file_{purpose}", args=[pk]) if kind == "upload" else reverse(f"core:file_{purpose}", args=[kind, pk])
    return route + "?" + urlencode({"token": token})

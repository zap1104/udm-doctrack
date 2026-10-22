import io

from django.core.exceptions import PermissionDenied
from django.http import FileResponse, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views import View
from django.views.decorators.clickjacking import xframe_options_sameorigin

from apps.core.file_access import file_url, resource_for
from apps.core.file_previews import (
    MIME_TYPES,
    PREVIEW_ERRORS,
    TEXT_LIMIT,
    image_pixels,
    preview_kind,
    read_validated,
    sanitized_docx,
    tabular_preview,
)
from apps.core.mixins import AppLoginRequiredMixin
from apps.core.models import AuditLog
from apps.core.utils import file_extension, log_action, unsign_download


def private(response):
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["Referrer-Policy"] = "no-referrer"
    return response


class FileVersionView(AppLoginRequiredMixin, View):
    purpose = "preview"

    def get(self, request, kind, pk):
        resource = resource_for(kind, pk, request.user)
        expected = f"file-version:{self.purpose}:{kind}:{pk}:{request.user.pk}"
        if unsign_download(request.GET.get("token", "")) != expected:
            raise PermissionDenied("This file link has expired. Open its record for a fresh link.")
        return self.respond(request, resource)


class FilePreviewView(FileVersionView):
    def respond(self, request, resource):
        item = resource.item
        context = {
            "file": item, "preview_kind": preview_kind(item.original_name),
            "version_number": item.version_number, "current_number": resource.current_number,
            "content_url": file_url(resource.kind, item.pk, request.user, "content"),
            "download_url": file_url(resource.kind, item.pk, request.user, "download"),
            "frame_url": reverse("core:file_preview_frame"),
            "back_url": resource.parent.get_absolute_url() + f"#file-version-{resource.kind}-{item.pk}",
        }
        try:
            if context["preview_kind"] in {"txt", "table"}:
                data = read_validated(item)
                if context["preview_kind"] == "txt":
                    context.update(text=data[:TEXT_LIMIT].decode("utf-8-sig", errors="replace"), truncated=len(data) > TEXT_LIMIT)
                else:
                    context.update(tabular_preview(data, file_extension(item.original_name), request.GET.get("worksheet")))
        except PREVIEW_ERRORS:
            context.update(preview_kind="unavailable", preview_error="Preview unavailable. The original file is unchanged.")
        return private(render(request, "core/file_preview.html", context))


class FileContentView(FileVersionView):
    purpose = "content"

    def respond(self, request, resource):
        extension = file_extension(resource.item.original_name)
        if extension not in {"pdf", "docx", "jpg", "jpeg", "png"}:
            return private(HttpResponse("Preview unavailable. Download the original file.", status=415, content_type="text/plain"))
        try:
            data = read_validated(resource.item)
            if extension == "docx":
                data = sanitized_docx(data)
            elif extension in {"png", "jpg", "jpeg"}:
                image_pixels(data, extension)
        except PREVIEW_ERRORS:
            return private(HttpResponse("Preview unavailable. The original file is unchanged.", status=422, content_type="text/plain"))
        return private(FileResponse(io.BytesIO(data), content_type=MIME_TYPES[extension], as_attachment=False, filename=resource.item.original_name))


class FileVersionDownloadView(FileVersionView):
    purpose = "download"

    def respond(self, request, resource):
        item = resource.item
        try:
            stream = item.file.open("rb")
        except OSError:
            return private(HttpResponse("The stored file is unavailable. Contact an administrator.", status=404))
        log_action(
            AuditLog.Action.DOWNLOAD, f"Downloaded {item.original_name} (Version {item.version_number})",
            actor=request.user, target=resource.parent, request=request,
            extra={"file_version_id": item.pk, "file_resource_kind": resource.kind, "version_number": item.version_number},
        )
        return private(FileResponse(stream, as_attachment=True, filename=item.original_name, content_type=MIME_TYPES.get(file_extension(item.original_name), "application/octet-stream")))


class PreviewFrameView(AppLoginRequiredMixin, View):
    @staticmethod
    @xframe_options_sameorigin
    def get(request):
        response = HttpResponse('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Document preview</title></head><body></body></html>')
        # This single inert document is embeddable. All normal pages retain DENY.
        response["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'none'; connect-src 'none'; "
            "style-src 'unsafe-inline'; img-src data: blob:; font-src data: blob:; "
            "frame-ancestors 'self'; base-uri 'none'; form-action 'none'"
        )
        return private(response)

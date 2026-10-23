import io
from urllib.parse import urlencode

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
from apps.core.storage import storage_identity
from apps.core.utils import file_extension, log_action, office_context_url, unsign_download


def private(response):
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    # Native forms (including the account menu) must retain their same-origin
    # Origin in Chromium. no-referrer makes these POSTs opaque (Origin: null).
    # No URL is disclosed cross-origin; binary responses stay no-referrer.
    response["Referrer-Policy"] = "same-origin" if response.get("Content-Type", "").startswith("text/html") else "no-referrer"
    return response


class FileVersionView(AppLoginRequiredMixin, View):
    purpose = "preview"

    def get(self, request, kind, pk):
        resource = resource_for(kind, pk, request.user)
        expected = f"file-version:{self.purpose}:{kind}:{pk}:{request.user.pk}"
        if unsign_download(request.GET.get("token", "")) != expected:
            raise PermissionDenied("This file link has expired. Open its record for a fresh link.")
        if not resource.item.available_content:
            return private(HttpResponse("This file content is no longer available. Return to its record or saved form.", status=410))
        if resource.item.storage_identity and resource.item.storage_identity != storage_identity(resource.item.file.storage):
            return private(HttpResponse("This file's storage configuration changed. Contact an administrator.", status=410))
        return self.respond(request, resource)


class FilePreviewView(FileVersionView):
    def respond(self, request, resource):
        item = resource.item
        back_url = resource.parent.get_absolute_url()
        external = resource.kind == "external" or (resource.kind == "repository" and resource.parent.is_external)
        if external:
            back_url = office_context_url(back_url, request.GET)
        if resource.kind == "upload":
            from .upload_context import UploadContext
            from .upload_forms import authorized_form_slots, form_url, url_with_slots
            context_binding = UploadContext(**resource.parent.binding)
            base_url = form_url(context_binding, resource.parent.pk)
            if context_binding.workflow.endswith("-version"):
                # The record contains the same saved version form and any
                # neighboring unfinished forms. Keep their authorized slots.
                base_url = reverse("tracking:external_detail" if context_binding.target_kind == "external" else "tracking:detail",
                    args=[context_binding.target_id])
                if context_binding.target_kind == "external" and context_binding.creation_office_id != context_binding.actor_office_id:
                    base_url += "?" + urlencode({"office": context_binding.creation_office_id})
            back_url = url_with_slots(base_url, authorized_form_slots(request.user, context_binding, request.GET))
        context = {
            "is_saved_upload": resource.kind == "upload",
            "file": item, "preview_kind": preview_kind(item.original_name),
            "version_number": item.version_number, "current_number": resource.current_number,
            "content_url": file_url(resource.kind, item.pk, request.user, "content"),
            "download_url": file_url(resource.kind, item.pk, request.user, "download"),
            "frame_url": reverse("core:file_preview_frame"),
            "back_url": back_url + f"#file-version-{resource.kind}-{item.pk}",
            "preview_fields": [(key, value) for key, values in request.GET.lists() if key != "worksheet" for value in values],
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
        if resource.kind != "upload":
            self.log_download(request, resource)
        return private(FileResponse(stream, as_attachment=True, filename=item.original_name, content_type=MIME_TYPES.get(file_extension(item.original_name), "application/octet-stream")))

    @staticmethod
    def log_download(request, resource):
        item = resource.item
        log_action(
            AuditLog.Action.DOWNLOAD, f"Downloaded {item.original_name} (Version {item.version_number})",
            actor=request.user, target=resource.parent, request=request,
            extra={"file_version_id": item.pk, "file_resource_kind": resource.kind, "version_number": item.version_number},
        )


class FileHistoryView(AppLoginRequiredMixin, View):
    def get(self, request, kind, pk):
        from django.core.exceptions import ValidationError

        from apps.core.file_history import file_history_rows, resolve_group
        group = resolve_group(kind, pk, request.user)
        try:
            if len(request.GET.getlist("cursor")) > 1:
                raise ValidationError("Open one history page at a time.")
            history = file_history_rows(group, user=request.user, cursor=request.GET.get("cursor"))
        except ValidationError as exc:
            return private(HttpResponse(" ".join(exc.messages), status=400))
        external = kind == "external" or (kind == "repository" and history["record"].is_external)
        def scoped(url):
            return office_context_url(url, request.GET) if external else url
        history["back_url"] = scoped(history["record"].get_absolute_url())
        history["next_url"] = scoped(request.path + "?" + urlencode({"cursor": history["next_cursor"]})) if history["next_cursor"] else ""
        if external:
            for row in history["entries"]:
                for key in ("preview_url", "download_url"):
                    if row.get(key):
                        row[key] = scoped(row[key])
        return private(render(request, "core/file_history.html", {"group": group, "history": history}))


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

"""Authenticated CSRF-protected endpoints for private unfinished uploads."""
import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from .file_access import file_url
from .file_views import private
from .mixins import AppLoginRequiredMixin
from .models import UploadDraft
from .upload_context import UploadContext, resolve_upload_context
from .upload_drafts import discard_staged_file, open_draft, read_draft, save_form_state, stage_files
from .upload_forms import form_factory_for, form_url, normal_form_values, one_value


def serialized_draft(draft, user):
    return {"id": str(draft.pk), "revision": draft.revision, "state": draft.state,
        "form_url": form_url(UploadContext(**draft.binding), draft.pk),
        "files": [{"id": str(row.pk), "client_upload_id": str(row.client_upload_id), "name": row.original_name,
                   "size": row.size, "preview_url": file_url("upload", row.pk, user)}
                  for row in draft.files.filter(state="saved").order_by("created_at", "pk")]}


class UploadAPIView(AppLoginRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        try:
            return super().dispatch(request, *args, **kwargs)
        except ValidationError as exc:
            return private(JsonResponse({"errors": exc.messages}, status=409 if any("changed" in message for message in exc.messages) else 400))
        except PermissionDenied as exc:
            return private(JsonResponse({"errors": [str(exc)]}, status=403))

    def draft(self, request, draft_id):
        saved = get_object_or_404(UploadDraft, pk=draft_id, owner=request.user)
        return read_draft(saved.pk, user=request.user, context=UploadContext(**saved.binding))


class UploadDraftCreateView(UploadAPIView):
    def post(self, request):
        context = resolve_upload_context(request.user, one_value(request.POST, "workflow"),
            target_id=one_value(request.POST, "target_id") or None, group_id=one_value(request.POST, "group_id") or None,
            query=request.GET)
        draft = open_draft(user=request.user, context=context, form_nonce=one_value(request.POST, "form_nonce"))
        return private(JsonResponse(serialized_draft(draft, request.user), status=201))


class UploadDraftView(UploadAPIView):
    def get(self, request, draft_id):
        draft = self.draft(request, draft_id)
        if request.GET.get("format") == "json" or "application/json" in request.headers.get("Accept", ""):
            return private(JsonResponse(serialized_draft(draft, request.user)))
        files = [{"item": row, "preview_url": file_url("upload", row.pk, request.user)} for row in draft.files.filter(state="saved")]
        return private(render(request, "core/upload_draft.html", {"draft": draft, "saved_files": files,
            "continue_url": form_url(UploadContext(**draft.binding), draft.pk)}))

    def post(self, request, draft_id):
        draft = self.draft(request, draft_id)
        context = UploadContext(**draft.binding)
        factory, _ = form_factory_for(context, request.user)
        form = factory()
        raw = one_value(request.POST, "values", "{}")
        try:
            values = json.loads(raw)
            if not isinstance(values, dict):
                raise ValueError
        except (ValueError, TypeError) as exc:
            raise ValidationError("Submit valid saved form fields.") from exc
        from .upload_forms import as_querydict
        safe = normal_form_values(form, as_querydict({name: value if isinstance(value, list) else [str(value)] for name, value in values.items()}))
        draft = save_form_state(draft.pk, safe, user=request.user, expected_revision=one_value(request.POST, "expected_revision"))
        return private(JsonResponse(serialized_draft(draft, request.user)))


class StageUploadView(UploadAPIView):
    def post(self, request, draft_id):
        self.draft(request, draft_id)
        result = stage_files(draft_id, request.FILES.getlist("file"), user=request.user,
            expected_revision=one_value(request.POST, "expected_revision"), client_upload_ids=request.POST.getlist("client_upload_id"))
        draft = self.draft(request, draft_id)
        return private(JsonResponse({**serialized_draft(draft, request.user), "accepted_ids": result.accepted_ids, "errors": result.errors}))


class RemoveStagedUploadView(UploadAPIView):
    def post(self, request, draft_id, file_id):
        self.draft(request, draft_id)
        draft = discard_staged_file(draft_id, file_id, user=request.user, expected_revision=one_value(request.POST, "expected_revision"))
        return private(JsonResponse(serialized_draft(draft, request.user)))


class UploadDraftListView(UploadAPIView):
    def get(self, request):
        from django.db import transaction

        from .permissions import active_writer
        with transaction.atomic():
            actor = active_writer(request.user)
            drafts = list(UploadDraft.objects.filter(owner=actor, owner_office_id_snapshot=actor.office_id,
                state="open").order_by("-updated_at")[:50])
        rows = []
        for draft in drafts:
            try:
                self.draft(request, draft.pk)
            except (PermissionDenied, ValidationError):
                continue
            rows.append({"draft": draft, "continue_url": form_url(UploadContext(**draft.binding), draft.pk)})
        return private(render(request, "core/upload_drafts.html", {"saved_uploads": rows}))


class DiscardUploadDraftView(UploadAPIView):
    def post(self, request, draft_id):
        from .file_maintenance import discard_manifest
        self.draft(request, draft_id)
        if one_value(request.POST, "confirm") != "on":
            raise ValidationError("Confirm that you want to discard this saved form.")
        discard_manifest(draft_id, user=request.user, expected_revision=one_value(request.POST, "revision"))
        return redirect("core:upload_drafts")

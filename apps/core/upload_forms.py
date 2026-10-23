"""Ordinary form recovery shared by both collections and progressive uploads."""
from urllib.parse import urlencode
from uuid import UUID, uuid4, uuid5

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import QueryDict
from django.shortcuts import redirect, render
from django.urls import reverse

from .file_access import file_url
from .models import UploadDraft
from .upload_context import UploadContext, form_nonce_for, resolve_upload_context
from .upload_drafts import (
    discard_staged_file,
    finalize_upload,
    open_draft,
    read_draft,
    save_form_state,
    stage_files,
)
from .utils import checksum_of


class RecoverableUploadMixin:
    def dispatch(self, request, *args, **kwargs):
        try:
            return super().dispatch(request, *args, **kwargs)
        except ValidationError as exc:
            return render(request, "core/upload_error.html", {"upload_errors": exc.messages}, status=400)


def one_value(data, name, default=None):
    values = data.getlist(name) if hasattr(data, "getlist") else ([data[name]] if name in data else [])
    if len(values) > 1:
        raise ValidationError(f"Submit one {name.replace('_', ' ')} value.")
    return values[0] if values else default


def form_url(context, draft_id=None):
    workflow = context.workflow
    params = {}
    if workflow in {"tracking-create", "tracking-edit"}:
        url = reverse("tracking:create")
        if context.target_id:
            params["draft"] = context.target_id
    elif workflow in {"tracking-remark", "tracking-route"}:
        url = reverse("tracking:detail", args=[context.target_id])
    elif workflow in {"tracking-version", "external-version"}:
        url = reverse("tracking:external_file_version" if workflow == "external-version" else "tracking:file_version",
                      args=[context.target_id, context.group_id])
    elif workflow == "external-create":
        url = reverse("tracking:external_create")
    elif workflow == "external-attachment":
        url = reverse("tracking:external_action", args=[context.target_id, "attachment"])
    else:
        url = reverse("documents:external_upload" if context.collection == "EXTERNAL" else "documents:upload")
    if context.creation_office_id != context.actor_office_id:
        params["office"] = context.creation_office_id
    if draft_id:
        params["upload_draft"] = draft_id
    return url + ("?" + urlencode(params) if params else "")


def form_factory_for(context, user):
    """Server-owned registry; arbitrary forms/models are never accepted."""
    workflow = context.workflow
    if workflow in {"repository-upload", "external-repository-upload"}:
        from apps.documents.external_forms import ExternalUploadForm
        from apps.documents.forms import UploadForm
        cls = ExternalUploadForm if context.collection == "EXTERNAL" else UploadForm
        return lambda data=None, files=None: cls(data, files, user=user), "file"
    from apps.tracking import external_forms
    from apps.tracking.forms import CreateRecordForm, RemarkForm, RouteForm
    from apps.tracking.models import ExternalTrackingRecord, TrackingRecord
    from apps.tracking.version_views import VersionUploadForm
    if workflow.endswith("-version"):
        model = ExternalTrackingRecord if context.collection == "EXTERNAL" else TrackingRecord
        record = model.objects.get(pk=context.target_id)
        latest = record.file_groups.get(pk=context.group_id).latest_version
        initial = {"expected_version": latest.version_number, "expected_revision": getattr(record, "revision", None)}
        return lambda data=None, files=None: VersionUploadForm(data, files, initial=initial, prefix=f"version-{context.group_id}"), "file"
    if workflow == "external-create":
        return lambda data=None, files=None: external_forms.ExternalCreateForm(data, files, user=user), "attachments"
    if workflow == "external-attachment":
        record = ExternalTrackingRecord.objects.get(pk=context.target_id)
        return lambda data=None, files=None: external_forms.ExternalAttachmentForm(data, files, initial={"revision": record.revision}), "uploaded_file"
    record = TrackingRecord.objects.get(pk=context.target_id) if context.target_id else None
    if workflow in {"tracking-create", "tracking-edit"}:
        return lambda data=None, files=None: CreateRecordForm(data, files, user=user, instance=record), "attachments"
    if workflow == "tracking-remark":
        return lambda data=None, files=None: RemarkForm(data, files), "attachments"
    if workflow == "tracking-route":
        return lambda data=None, files=None: RouteForm(data, files, record=record, user=user, auto_id="route_%s"), "attachments"
    raise ValidationError("This upload form is not supported.")


def normal_form_values(form, data):
    values = {}
    for name, field in form.fields.items():
        if isinstance(field, forms.FileField) or isinstance(field.widget, forms.PasswordInput) or field.disabled:
            continue
        key = form.add_prefix(name)
        values[key] = data.getlist(key) if hasattr(data, "getlist") else [data[key]] if key in data else []
    return values


def as_querydict(values):
    data = QueryDict(mutable=True)
    for name, items in values.items():
        data.setlist(name, items)
    return data


class UploadFormRecovery:
    def __init__(self, request, workflow, *, target_id=None, group_id=None, factory=None):
        self.request, self.draft = request, None
        self.response = None
        query = request.GET.copy()
        # "all" is a perspective on an existing record, never a creation office.
        if target_id and request.user.is_system_admin and query.getlist("office") == ["all"]:
            query.pop("office")
        self.context = resolve_upload_context(request.user, workflow, target_id=target_id, group_id=group_id, query=query)
        supplied = one_value(request.POST, "upload_draft") if request.method == "POST" else None
        self.slot = f"upload_draft_{workflow.replace('-', '_')}_{self.context.group_id or 0}"
        legacy = one_value(request.GET, "upload_draft")
        supplied = supplied or one_value(request.GET, self.slot) or legacy
        if supplied:
            try:
                candidate = UploadDraft.objects.filter(pk=supplied).first()
            except (ValueError, ValidationError) as exc:
                raise ValidationError("Choose a valid saved upload.") from exc
            if candidate is None or candidate.owner_id != request.user.pk:
                raise PermissionDenied("This saved upload is outside your account.")
            bound = UploadContext(**candidate.binding)
            if (bound.target_id, bound.target_kind) != (self.context.target_id, self.context.target_kind):
                raise PermissionDenied("This saved upload belongs to another record.")
            if bound.workflow == workflow and bound.group_id == group_id:
                self.context = bound
                if candidate.state == "consumed" and request.method == "POST":
                    self.draft = candidate  # finalize_upload performs the fresh replay authorization.
                else:
                    self.draft = read_draft(candidate.pk, user=request.user, context=bound)
        if not self.draft and request.method == "POST" and workflow in {"repository-upload", "external-repository-upload", "external-create"}:
            office_name = "managing_office" if workflow == "external-create" else "office"
            selected = one_value(request.POST, office_name)
            if selected and "office" not in query and request.user.is_system_admin:
                try:
                    self.context = resolve_upload_context(request.user, workflow, query={"office": selected})
                except (PermissionDenied, ValidationError):
                    pass  # Preserve valid files while the business form reports its invalid office.
        default_factory, self.file_field = form_factory_for(self.context, request.user)
        self.factory = factory or default_factory
        self.nonce = one_value(request.POST, "upload_form_nonce") if request.method == "POST" else None
        self.nonce = self.nonce or form_nonce_for(request.user, self.context)
        self.batch_id = str(uuid4())
        self.had_saved_manifest = self.draft is not None
        if request.method == "POST":
            self._post()
        else:
            state = as_querydict(self.draft.form_state) if self.draft and self.draft.form_state else None
            self.form = self.factory(state, None)
            self._configure_form()
            if state is not None:
                self.form.is_valid()
                self._saved_errors()
        self._attach()

    def _configure_form(self):
        if self.draft and self.draft.files.exclude(state="removed").exists():
            self.form.fields[self.file_field].required = False
        for name in ("office", "managing_office"):
            if name in self.form.fields:
                self.form.fields[name].initial = self.context.creation_office_id
                self.form.initial[name] = self.context.creation_office_id
                if self.draft and (self.request.method != "POST" or self.had_saved_manifest):
                    supplied = self.form.data.get(self.form.add_prefix(name)) if self.form.is_bound else None
                    if supplied and str(supplied) != str(self.context.creation_office_id):
                        # Keep the original validation error readable. A valid
                        # neighboring file must not silently accept bad input.
                        self.form.fields[name].queryset = self.form.fields[name].queryset.filter(pk=self.context.creation_office_id)
                    else:
                        self.form.fields[name].disabled = True
                    self.form.fields[name].help_text = "Office is fixed for this saved form. Start a new form to change it."

    def _post(self):
        if self.draft is None:
            self.draft = open_draft(user=self.request.user, context=self.context, form_nonce=self.nonce, allow_consumed=True)
        current = self.draft.revision
        posted_revision = one_value(self.request.POST, "upload_revision")
        # First native submission does not yet know the manifest's identity.
        expected = posted_revision if one_value(self.request.POST, "upload_draft") else current
        self.form = self.factory(self.request.POST, None)
        self._configure_form()
        if self.draft.state == "consumed":
            uploaded = self.request.FILES.getlist(self.form.add_prefix(self.file_field))
            if uploaded:
                try:
                    batch = UUID(one_value(self.request.POST, "upload_batch_id"))
                except (ValueError, TypeError, AttributeError) as exc:
                    raise ValidationError("This upload was already submitted. Open a new form for different files.") from exc
                for index, item in enumerate(uploaded):
                    identity = uuid5(self.draft.form_nonce, f"{batch}:{self.file_field}:{index}")
                    original = self.draft.files.filter(client_upload_id=identity, state="consumed").first()
                    if original is None or original.checksum != checksum_of(item) or original.original_name != item.name[:255]:
                        raise ValidationError("This upload was already submitted with different files. Open a new form.")
            self.form.is_valid()
            return
        uploads = self.request.FILES.getlist(self.form.add_prefix(self.file_field))
        batch = one_value(self.request.POST, "upload_batch_id") or str(uuid4())
        try:
            batch = UUID(batch)
        except (ValueError, TypeError) as exc:
            raise ValidationError("This upload batch is invalid. Reload the form.") from exc
        identities = [uuid5(self.draft.form_nonce, f"{batch}:{self.file_field}:{index}") for index in range(len(uploads))]
        result = stage_files(self.draft.pk, uploads, user=self.request.user, expected_revision=expected, client_upload_ids=identities)
        self.draft.refresh_from_db()
        # Stage writes may advance the revision; no-file submissions still check the posted revision when saving state.
        expected_state = self.draft.revision if uploads else expected
        errors = {self.file_field: [item["message"] for item in result.errors]} if result.errors else {}
        self.draft = save_form_state(self.draft.pk, normal_form_values(self.form, self.request.POST), user=self.request.user,
            expected_revision=expected_state, errors=errors)
        removed = one_value(self.request.POST, "remove_upload")
        if removed:
            self.draft = discard_staged_file(self.draft.pk, removed, user=self.request.user, expected_revision=self.draft.revision)
            self.response = redirect(form_url(self.context, self.draft.pk))
        self._configure_form()
        self.form.is_valid()
        self._saved_errors()

    def _saved_errors(self):
        for name, errors in (self.draft.validation_errors if self.draft else {}).items():
            for error in errors:
                self.form.add_error(None if name == "__all__" else name if name in self.form.fields else None, error)

    def _attach(self):
        files = []
        if self.draft and self.draft.state == "open":
            for saved in self.draft.files.filter(state="saved").order_by("created_at", "pk"):
                files.append({"item": saved, "preview_url": file_url("upload", saved.pk, self.request.user),
                              "download_url": file_url("upload", saved.pk, self.request.user, "download")})
        self.form.recovery = {"nonce": self.nonce, "batch_id": self.batch_id, "draft_id": self.draft.pk if self.draft else None,
            "revision": self.draft.revision if self.draft else 0, "files": files,
            "workflow": self.context.workflow, "target_id": self.context.target_id, "group_id": self.context.group_id,
            "file_field": self.form.add_prefix(self.file_field), "slot": self.slot,
            "limit": 1 if self.context.workflow.endswith("-version") or self.context.workflow == "external-attachment" else settings.MAX_UPLOAD_FILES}

    def validation_response(self):
        if self.response:
            return self.response
        if not self.form.is_valid():
            return redirect(form_url(self.context, self.draft.pk))
        return None

    def finish(self):
        data = dict(self.form.cleaned_data)
        if hasattr(self.form, "deadline_datetime"):
            data["due_at"] = self.form.deadline_datetime()
        try:
            return finalize_upload(self.draft.pk, user=self.request.user, context=self.context, cleaned_data=data,
                expected_revision=self.draft.revision, expected_version=data.get("expected_version"))
        except ValidationError as exc:
            if self.draft.state == "open":
                self.draft = save_form_state(self.draft.pk, normal_form_values(self.form, self.request.POST), user=self.request.user,
                    expected_revision=self.draft.revision, errors={"__all__": exc.messages})
            else:
                messages.error(self.request, "; ".join(exc.messages))
            self.response = redirect(form_url(self.context, self.draft.pk))
            return None

"""Explicit file-version uploads, separate from adding a new logical file."""

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from apps.core.file_access import file_url
from apps.core.forms import BootstrapFormMixin
from apps.core.mixins import AppLoginRequiredMixin

from .file_versions import upload_attachment_version, upload_external_attachment_version
from .models import COMPLETED_STATUSES, ExternalTrackingRecord, TrackingRecord


class VersionUploadForm(BootstrapFormMixin, forms.Form):
    file = forms.FileField(label="New version file")
    change_summary = forms.CharField(max_length=1000, strip=True, label="What changed?", widget=forms.Textarea(attrs={"rows": 2}), help_text="For example: Added the director's signature. Signatures are supplied outside DocTrack.")
    expected_version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    expected_revision = forms.IntegerField(min_value=0, required=False, widget=forms.HiddenInput)


def version_rows(versions, kind, user):
    return [dict(item=item, entry_id=f"file-version-{kind}-{item.pk}", preview_url=file_url(kind, item.pk, user), download_url=file_url(kind, item.pk, user, "download")) for item in versions]


def tracking_file_rows(record, user, *, external=False):
    kind = "external" if external else "tracking"
    can_update = record.status not in COMPLETED_STATUSES and (record.can_act(user) if external else record.can_user_act(user))
    rows = []
    for group in record.file_groups.prefetch_related("versions").order_by("pk"):
        versions = sorted(group.versions.all(), key=lambda item: item.version_number, reverse=True)
        if not versions:
            continue
        rows.append(dict(
            current=version_rows(versions[:1], kind, user)[0], history=version_rows(versions[1:], kind, user),
            version_count=len(versions), can_update=can_update,
            upload_url=reverse("tracking:external_file_version" if external else "tracking:file_version", args=[record.pk, group.pk]),
            form=VersionUploadForm(initial={"expected_version": versions[0].version_number, "expected_revision": getattr(record, "revision", None)}, prefix=f"version-{group.pk}"),
        ))
    return rows


class UploadVersionView(AppLoginRequiredMixin, View):
    external = False

    def post(self, request, pk, group_pk):
        model = ExternalTrackingRecord if self.external else TrackingRecord
        record = get_object_or_404(model.objects.visible_to(request.user), pk=pk)
        get_object_or_404(record.file_groups, pk=group_pk)
        form = VersionUploadForm(request.POST, request.FILES, prefix=f"version-{group_pk}")
        if form.is_valid():
            data = form.cleaned_data
            kwargs = dict(user=request.user, expected_version=data["expected_version"], change_summary=data["change_summary"])
            if self.external:
                kwargs["expected_revision"] = data["expected_revision"]
            try:
                (upload_external_attachment_version if self.external else upload_attachment_version)(record, group_pk, data["file"], **kwargs)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, "New file version saved. Earlier versions are preserved.")
                return redirect(record.get_absolute_url())
        return render(request, "tracking/version_upload.html", {"record": record, "form": form}, status=409)

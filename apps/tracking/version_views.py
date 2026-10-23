"""Explicit file-version uploads, separate from adding a new logical file."""

from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from apps.core.file_access import file_url
from apps.core.forms import BootstrapFormMixin
from apps.core.mixins import AppLoginRequiredMixin
from apps.core.upload_forms import RecoverableUploadMixin, UploadFormRecovery

from .models import COMPLETED_STATUSES, ExternalTrackingRecord, TrackingRecord


class VersionUploadForm(BootstrapFormMixin, forms.Form):
    file = forms.FileField(label="New version file")
    change_summary = forms.CharField(max_length=1000, strip=True, label="What changed?", widget=forms.Textarea(attrs={"rows": 2}), help_text="For example: Added the director's signature. Signatures are supplied outside DocTrack.")
    expected_version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    expected_revision = forms.IntegerField(min_value=0, required=False, widget=forms.HiddenInput)


def version_rows(versions, kind, user):
    return [dict(item=item, entry_id=f"file-version-{kind}-{item.pk}", preview_url=file_url(kind, item.pk, user), download_url=file_url(kind, item.pk, user, "download")) for item in versions]


def tracking_file_rows(record, user, *, external=False, request=None):
    kind = "external" if external else "tracking"
    can_update = record.status not in (*COMPLETED_STATUSES, "PENDING_RECEIPT") and (record.can_act(user) if external else record.can_user_act(user))
    rows = []
    for group in record.file_groups.prefetch_related("versions").order_by("pk"):
        versions = sorted(group.versions.all(), key=lambda item: item.version_number, reverse=True)
        if not versions:
            continue
        version_form = VersionUploadForm(initial={"expected_version": versions[0].version_number, "expected_revision": getattr(record, "revision", None)}, prefix=f"version-{group.pk}")
        if request is not None and can_update:
            version_form = UploadFormRecovery(request, "external-version" if external else "tracking-version", target_id=record.pk, group_id=group.pk).form
        rows.append(dict(
            current=version_rows(versions[:1], kind, user)[0], history=version_rows(versions[1:], kind, user),
            version_count=len(versions), can_update=can_update,
            upload_url=reverse("tracking:external_file_version" if external else "tracking:file_version", args=[record.pk, group.pk]),
            form=version_form,
        ))
    return rows


class UploadVersionView(RecoverableUploadMixin, AppLoginRequiredMixin, View):
    external = False

    def get(self, request, pk, group_pk):
        model = ExternalTrackingRecord if self.external else TrackingRecord
        record = get_object_or_404(model.objects.visible_to(request.user), pk=pk)
        recovery = UploadFormRecovery(request, "external-version" if self.external else "tracking-version", target_id=pk, group_id=group_pk)
        return render(request, "tracking/version_upload.html", {"record": record, "form": recovery.form})

    def post(self, request, pk, group_pk):
        model = ExternalTrackingRecord if self.external else TrackingRecord
        record = get_object_or_404(model.objects.visible_to(request.user), pk=pk)
        get_object_or_404(record.file_groups, pk=group_pk)
        recovery = UploadFormRecovery(request, "external-version" if self.external else "tracking-version", target_id=pk, group_id=group_pk)
        response = recovery.validation_response()
        if response:
            return response
        result = recovery.finish()
        if result is None:
            return recovery.response
        messages.success(request, "New file version saved.")
        return redirect(result.redirect)

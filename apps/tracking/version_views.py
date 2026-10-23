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
from apps.core.upload_forms import RecoverableUploadMixin, UploadFormRecovery
from apps.core.utils import office_context_url

from .models import COMPLETED_STATUSES, ExternalTrackingRecord, TrackingRecord


class VersionUploadForm(BootstrapFormMixin, forms.Form):
    file = forms.FileField(label="New version file")
    change_summary = forms.CharField(max_length=1000, strip=True, label="What changed?", widget=forms.Textarea(attrs={"rows": 2}), help_text="For example: Added the director's signature. Signatures are supplied outside DocTrack.")
    expected_version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    expected_revision = forms.IntegerField(min_value=0, required=False, widget=forms.HiddenInput)


def version_rows(versions, kind, user, *, request=None, external=False):
    def link(item, purpose):
        if not item.available_content:
            return ""
        url = file_url(kind, item.pk, user, purpose)
        return office_context_url(url, request.GET) if external and request is not None else url
    return [dict(item=item, entry_id=f"file-version-{kind}-{item.pk}",
        preview_url=link(item, "preview"), download_url=link(item, "download")) for item in versions]


def record_url(record, request, external):
    url = record.get_absolute_url()
    return office_context_url(url, request.GET) if external else url


def tracking_file_rows(record, user, *, external=False, request=None):
    from apps.core.file_history import history_url, retained_groups
    kind = "external" if external else "tracking"
    can_update = record.status not in (*COMPLETED_STATUSES, "PENDING_RECEIPT") and (record.can_act(user) if external else record.can_user_act(user))
    can_discard = can_update and record.status == "DRAFT" and not record.routing_steps.exists()
    rows = []
    for group in retained_groups(record.file_groups.filter(discarded_at__isnull=True)).order_by("pk"):
        versions = group.retained_versions
        if not versions:
            continue
        version_form = VersionUploadForm(initial={"expected_version": versions[0].version_number, "expected_revision": getattr(record, "revision", None)}, prefix=f"version-{group.pk}")
        if request is not None and can_update:
            version_form = UploadFormRecovery(request, "external-version" if external else "tracking-version", target_id=record.pk, group_id=group.pk).form
        def scoped(url):
            return office_context_url(url, request.GET) if external and request is not None else url
        rows.append(dict(
            group_id=group.pk, available_count=len(versions), can_discard=can_discard,
            expected_revision=getattr(record, "revision", None),
            discard_url=scoped(reverse("tracking:external_file_discard" if external else "tracking:file_discard", args=[record.pk, group.pk])),
            current=version_rows(versions[:1], kind, user, request=request, external=external)[0],
            history=version_rows(versions[1:], kind, user, request=request, external=external),
            version_count=group.version_total, audit_count=group.audit_total, audit_url=scoped(history_url(group)), can_update=can_update,
            upload_url=scoped(reverse("tracking:external_file_version" if external else "tracking:file_version", args=[record.pk, group.pk])),
            form=version_form,
        ))
    return rows


class UploadVersionView(RecoverableUploadMixin, AppLoginRequiredMixin, View):
    external = False

    def get(self, request, pk, group_pk):
        model = ExternalTrackingRecord if self.external else TrackingRecord
        record = get_object_or_404(model.objects.visible_to(request.user), pk=pk)
        recovery = UploadFormRecovery(request, "external-version" if self.external else "tracking-version", target_id=pk, group_id=group_pk)
        return render(request, "tracking/version_upload.html", {"record": record, "form": recovery.form,
            "record_title": record.title if self.external else record.subject,
            "back_url": record_url(record, request, self.external)})

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


class DiscardFileForm(BootstrapFormMixin, forms.Form):
    reason = forms.CharField(max_length=2000, widget=forms.Textarea(attrs={"rows": 2}))
    confirm = forms.BooleanField(label="Discard this draft file and its saved versions")
    expected_version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    expected_revision = forms.IntegerField(required=False, min_value=0, widget=forms.HiddenInput)


class DiscardFileView(AppLoginRequiredMixin, View):
    external = False

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        model = ExternalTrackingRecord if self.external else TrackingRecord
        self.record = get_object_or_404(model.objects.visible_to(request.user), pk=kwargs["pk"])
        self.group = get_object_or_404(self.record.file_groups, pk=kwargs["group_pk"], discarded_at__isnull=True)
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, **kwargs):
        form = DiscardFileForm(initial={"expected_version": self.group.latest_version.version_number,
            "expected_revision": getattr(self.record, "revision", None)})
        return self.page(request, form)

    def post(self, request, **kwargs):
        from apps.core.file_lifecycle import discard_draft_group
        form = DiscardFileForm(request.POST)
        if form.is_valid():
            try:
                discard_draft_group(self.record, self.group.pk, user=request.user, **{
                    key: value for key, value in form.cleaned_data.items() if key != "confirm"})
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, "Draft file discarded. Its audit history is preserved.")
                return redirect(record_url(self.record, request, self.external))
        return self.page(request, form)

    def page(self, request, form):
        return render(request, "tracking/file_discard.html", {"record": self.record, "group": self.group, "form": form,
            "back_url": record_url(self.record, request, self.external),
            "available_count": self.group.versions.filter(retired_at__isnull=True).exclude(file="").count()})

"""Private unfinished uploads. These never belong to document populations."""
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

CREATION_WORKFLOWS = ("tracking-create", "external-create", "repository-upload", "external-repository-upload")
INTERNAL_WORKFLOWS = ("tracking-create", "tracking-edit", "tracking-remark", "tracking-route", "tracking-version", "repository-upload")
EXTERNAL_WORKFLOWS = ("external-create", "external-attachment", "external-version", "external-repository-upload")


def staged_upload_path(instance, filename):
    return f"private/upload-drafts/{instance.draft_id}/{uuid4().hex}"


class UploadDraft(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    form_nonce = models.UUIDField(default=uuid4, unique=True, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="upload_drafts")
    owner_office_id_snapshot = models.PositiveBigIntegerField(null=True)
    workflow = models.CharField(max_length=32, choices=[(value, value) for value in (*INTERNAL_WORKFLOWS, *EXTERNAL_WORKFLOWS)])
    collection = models.CharField(max_length=8, choices=[("OVPA", "OVPA"), ("EXTERNAL", "External")])
    tracking_record = models.ForeignKey("tracking.TrackingRecord", null=True, blank=True, on_delete=models.SET_NULL, related_name="upload_drafts")
    external_record = models.ForeignKey("tracking.ExternalTrackingRecord", null=True, blank=True, on_delete=models.SET_NULL, related_name="upload_drafts")
    tracking_group = models.ForeignKey("tracking.AttachmentGroup", null=True, blank=True, on_delete=models.SET_NULL, related_name="upload_drafts")
    external_group = models.ForeignKey("tracking.ExternalAttachmentGroup", null=True, blank=True, on_delete=models.SET_NULL, related_name="upload_drafts")
    binding = models.JSONField(default=dict)
    state = models.CharField(max_length=12, default="open", choices=[(value, value.title()) for value in ("open", "consumed", "discarded", "expired")])
    revision = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField()
    form_state = models.JSONField(default=dict)
    validation_errors = models.JSONField(default=dict)
    result = models.JSONField(default=dict)
    consumed_fingerprint = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [models.Index(fields=["owner", "state", "expires_at"], name="upload_draft_owner_state_idx")]
        constraints = [
            models.CheckConstraint(condition=~(Q(tracking_record__isnull=False) & Q(external_record__isnull=False)), name="upload_draft_one_record_kind"),
            models.CheckConstraint(condition=~(Q(tracking_group__isnull=False) & Q(external_group__isnull=False)), name="upload_draft_one_group_kind"),
            models.CheckConstraint(condition=(Q(workflow__in=INTERNAL_WORKFLOWS, collection="OVPA", external_record__isnull=True, external_group__isnull=True) |
                Q(workflow__in=EXTERNAL_WORKFLOWS, collection="EXTERNAL", tracking_record__isnull=True, tracking_group__isnull=True)), name="upload_draft_collection_matches"),
            models.CheckConstraint(condition=~Q(state="open") | Q(workflow__in=CREATION_WORKFLOWS) |
                Q(workflow__in=("tracking-edit", "tracking-remark", "tracking-route", "tracking-version"), tracking_record__isnull=False) |
                Q(workflow__in=("external-attachment", "external-version"), external_record__isnull=False), name="upload_draft_open_target_required"),
        ]

    def __str__(self):
        return f"{self.workflow}: {self.state}"

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse("core:upload_draft", args=[self.pk])

    def clean(self):
        super().clean()
        for name, target in (("tracking_group", self.tracking_record_id), ("external_group", self.external_record_id)):
            if getattr(self, f"{name}_id") and getattr(self, name).record_id != target:
                raise ValidationError({name: "This file group belongs to another record."})


class StagedUpload(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    draft = models.ForeignKey(UploadDraft, on_delete=models.CASCADE, related_name="files")
    client_upload_id = models.UUIDField(default=uuid4)
    file = models.FileField(upload_to=staged_upload_path, max_length=400)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=120, blank=True)
    size = models.PositiveBigIntegerField(default=0)
    checksum = models.CharField(max_length=64)
    storage_identity = models.CharField(max_length=64)
    state = models.CharField(max_length=12, default="saved", choices=[(value, value.title()) for value in ("saved", "removed", "consumed")])
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["draft", "client_upload_id"], name="staged_upload_client_id_unique")]

    def __str__(self):
        return self.original_name

    @property
    def version_number(self):
        return 1

    @property
    def available_content(self):
        return self.state == "saved" and self.draft.state == "open" and self.draft.expires_at > timezone.now()

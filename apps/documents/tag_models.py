"""Explicit labels and assignment provenance, separate from suggestions."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class AppendOnly(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Historical decisions cannot be rewritten.")
        return super().save(*args, **kwargs)


class TagAlias(models.Model):
    tag = models.ForeignKey("core.Tag", on_delete=models.PROTECT, related_name="approved_aliases")
    phrase = models.CharField(max_length=128, unique=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.phrase


class VocabularyRevision(AppendOnly):
    digest = models.CharField(max_length=64, unique=True)
    payload = models.JSONField(default=list, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Vocabulary {self.digest[:12]}"


class TagReview(AppendOnly):
    document = models.ForeignKey("documents.Document", on_delete=models.CASCADE, related_name="tag_reviews")
    tag = models.ForeignKey("core.Tag", on_delete=models.PROTECT)
    decision = models.CharField(max_length=12, choices=[(v, v) for v in ("confirm", "reject", "add", "unsure")])
    input_revision = models.CharField(max_length=64)
    vocabulary = models.CharField(max_length=64)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    office_snapshot = models.CharField(max_length=255, blank=True)
    tag_name_snapshot = models.CharField(max_length=64, blank=True)
    actor_name_snapshot = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    complete_review = models.BooleanField(default=False)

    def __str__(self):
        return f"Document {self.document_id}: {self.decision} tag {self.tag_id}"


class TagPrediction(AppendOnly):
    document = models.ForeignKey("documents.Document", on_delete=models.CASCADE, related_name="tag_predictions")
    tag = models.ForeignKey("core.Tag", on_delete=models.PROTECT)
    input_revision = models.CharField(max_length=64)
    vocabulary = models.CharField(max_length=64)
    engine = models.CharField(max_length=32)
    model_reference = models.CharField(max_length=64, blank=True)
    score = models.FloatField(null=True)
    decision = models.CharField(max_length=16)
    evidence = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Document {self.document_id}: {self.engine} tag {self.tag_id}"


class TagContribution(models.Model):
    document = models.ForeignKey("documents.Document", on_delete=models.CASCADE, related_name="tag_contributions")
    tag = models.ForeignKey("core.Tag", on_delete=models.PROTECT)
    kind = models.CharField(max_length=12, choices=[("human", "Human"), ("automatic", "Automatic")])
    source = models.CharField(max_length=64, default="legacy")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    recorded_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["document", "tag", "kind", "source"], name="unique_tag_contribution")
        ]

    def __str__(self):
        return f"Document {self.document_id}: {self.kind} tag {self.tag_id}"

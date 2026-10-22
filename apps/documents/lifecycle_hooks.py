"""Invalidate private derived models at existing ORM mutation boundaries."""

from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver

from apps.core.models import Tag

from .ml.lifecycle import invalidate_models, invalidate_training_sources, purge_training_content
from .ml.models import TrainingSnapshot
from .models import Document, DocumentFile, ExtractionRun, ExtractionUnit, TagAlias, TagReview

SOURCE_FIELDS = {
    "title",
    "description",
    "reference_number",
    "office_id",
    "collection",
    "ocr_text",
    "extraction_generation",
    "is_active",
    "access_level",
    "training_eligible",
    "training_family",
}
TAG_FIELDS = {"name", "description", "category", "is_active", "canonical_id", "globally_approved"}


@receiver(pre_save, sender=Document)
@receiver(pre_save, sender=Tag)
def remember_inputs(sender, instance, **kwargs):
    fields = SOURCE_FIELDS if sender is Document else TAG_FIELDS
    instance._intelligence_previous = (
        sender.objects.filter(pk=instance.pk).values(*fields).first() if instance.pk else None
    )


@receiver(post_save, sender=Document)
def changed_document(sender, instance, created, **kwargs):
    previous = getattr(instance, "_intelligence_previous", None)
    if not created and previous and any(previous[key] != getattr(instance, key) for key in SOURCE_FIELDS):
        invalidate_training_sources([instance.pk], reason="Training source content, scope or eligibility changed")


@receiver(post_save, sender=Tag)
@receiver(post_delete, sender=Tag)
@receiver(post_save, sender=TagAlias)
@receiver(post_delete, sender=TagAlias)
def changed_vocabulary(sender, instance, **kwargs):
    if sender is Tag:
        previous = getattr(instance, "_intelligence_previous", None)
        changed = previous is None or any(previous[key] != getattr(instance, key) for key in TAG_FIELDS)
        relevant = instance.globally_approved or (previous or {}).get("globally_approved")
        if not changed or not relevant:
            return
    elif not Tag.objects.filter(pk=instance.tag_id, globally_approved=True).exists():
        return
    invalidate_models(
        list(TrainingSnapshot.objects.filter(invalidated_at__isnull=True).values_list("pk", flat=True)),
        reason="Approved vocabulary changed",
    )


@receiver(post_save, sender=ExtractionRun)
@receiver(post_save, sender=ExtractionUnit)
@receiver(post_save, sender=DocumentFile)
@receiver(post_save, sender=TagReview)
def changed_evidence(sender, instance, **kwargs):
    document_id = instance.run.document_id if sender is ExtractionUnit else instance.document_id
    invalidate_training_sources([document_id], reason="Training evidence or explicit review changed")


@receiver(pre_delete, sender=Document)
def removed_source(sender, instance, **kwargs):
    purge_training_content([instance.pk])

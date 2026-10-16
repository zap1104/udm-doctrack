"""Keep stored document search indexes current after master-data edits."""

from django.db import transaction
from django.db.models import Q

from apps.accounts.models import Office
from apps.core.models import DocumentType, MetadataFieldDefinition, Tag
from apps.documents.models import Document


def _affected_documents(instance, changed):
    if not instance.pk:
        return Document.objects.none()
    if isinstance(instance, Office) and changed & {"name", "code"}:
        return Document.objects.filter(office_id=instance.pk)
    if isinstance(instance, DocumentType) and changed & {"name", "code"}:
        return Document.objects.filter(
            Q(document_type_id=instance.pk) | Q(document_types__pk=instance.pk),
        )
    if isinstance(instance, Tag) and "name" in changed:
        return Document.objects.filter(tags__pk=instance.pk)
    if isinstance(instance, MetadataFieldDefinition) and changed & {"key", "is_searchable"}:
        return Document.objects.filter(metadata_values__field_id=instance.pk)
    return Document.objects.none()


def _lock_document_ids(affected):
    # The outer query has no joins or DISTINCT, both for stable locking and
    # because PostgreSQL cannot combine DISTINCT with SELECT FOR UPDATE.
    return list(
        Document.objects.filter(pk__in=affected.order_by().values("pk"))
        .order_by("pk").select_for_update(of=("self",)).values_list("pk", flat=True),
    )


@transaction.atomic
def _refresh_additional_documents(model_class, instance_id, changed, original_ids):
    # Run after the label save commits, with no master-data update lock held.
    # New filings may have entered the affected set since the original snapshot.
    # Reading the current master row also handles its removal before this callback.
    current = model_class.objects.filter(pk=instance_id).first()
    if current is None:
        return
    affected = _affected_documents(current, changed).exclude(pk__in=original_ids)
    document_ids = _lock_document_ids(affected)
    if document_ids:
        from apps.documents.services import reindex_all

        reindex_all(Document.objects.filter(pk__in=document_ids).order_by("pk"))


@transaction.atomic
def save_masterdata_form(form):
    """Save valid administrative metadata and refresh only related indexes.

    Document writers lock the document before updating tag usage. Take the
    same order here, before saving a tag name, to avoid a tag/document lock
    cycle. Re-read related values only after those locks and the label save.
    """
    instance = form.instance
    changed = frozenset(form.changed_data)
    affected = _affected_documents(instance, changed)
    refresh_additions = not affected.query.is_empty()
    document_ids = _lock_document_ids(affected)
    if isinstance(instance, Tag) and instance.pk:
        # ModelForm.save writes this non-form field too. Its cached value may
        # predate a document writer we just waited for, so preserve the current
        # counter while taking the Tag lock after the document locks.
        instance.usage_count = Tag.objects.select_for_update().values_list(
            "usage_count", flat=True,
        ).get(pk=instance.pk)
    saved = form.save()
    if document_ids:
        from apps.documents.services import reindex_all

        reindex_all(Document.objects.filter(pk__in=document_ids).order_by("pk"))
    if refresh_additions:
        transaction.on_commit(lambda: _refresh_additional_documents(
            type(saved), saved.pk, changed, frozenset(document_ids),
        ))
    return saved

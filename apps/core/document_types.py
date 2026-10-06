"""Multiple classifications with an explicit, backwards-compatible main type."""

from django.core.exceptions import ValidationError
from django.db.models import Count, OuterRef, Q, Subquery, Value

from .models import DocumentType


class DocumentTypesMixin:
    @property
    def selected_document_types(self):
        selected = list(self.document_types.all()) if self.pk else []
        if self.document_type_id:
            selected = [self.document_type] + [kind for kind in selected if kind.pk != self.document_type_id]
        return selected

    @property
    def document_type_names(self):
        return ", ".join(kind.name for kind in self.selected_document_types)


def resolve_document_types(selected, main=None, *, existing=()):
    """Single-type callers keep working; mixed packets choose their main type."""
    selected = list(selected or [])
    if any(not isinstance(kind, DocumentType) or not kind.pk for kind in selected):
        raise ValidationError("Choose existing document types.")
    selected = list({kind.pk: kind for kind in selected}.values())
    ids = {kind.pk for kind in selected}
    allowed = {kind.pk: kind for kind in DocumentType.objects.filter(pk__in=ids).filter(
        Q(is_active=True) | Q(pk__in=[kind.pk for kind in existing]),
    )} if ids else {}
    if set(allowed) != ids:
        raise ValidationError("A selected document type is unavailable. Choose an active type.")
    selected = [allowed[kind.pk] for kind in selected]
    if main and main.pk not in {kind.pk for kind in selected}:
        raise ValidationError("The main document type must be one of the selected types.")
    if len(selected) > 1 and main is None:
        raise ValidationError("Choose a main document type for deadlines and retention.")
    return selected, allowed[main.pk] if main else (selected[0] if selected else None)


def document_type_match(kind):
    return Q(document_type=kind) | Q(document_types=kind)


def document_type_options(records):
    return DocumentType.objects.filter(
        Q(pk__in=records.order_by().values("document_type_id"))
        | Q(pk__in=records.order_by().values("document_types__pk"))
    ).distinct()


def document_type_counts(records):
    """One distinct count per type, including old single-type-only callers."""
    matches = records.filter(document_type_match(OuterRef("pk"))).order_by().annotate(
        _type_group=Value(1),
    ).values("_type_group").annotate(total=Count("pk", distinct=True)).values("total")
    return document_type_options(records).annotate(total=Subquery(matches)).values("pk", "name", "total")

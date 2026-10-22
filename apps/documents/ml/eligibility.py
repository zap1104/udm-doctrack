from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from apps.accounts.models import Office
from apps.core.permissions import active_writer
from apps.core.utils import log_action

from ..models import AccessLevel, Document
from ..tagging import input_revision
from .lifecycle import invalidate_models


@transaction.atomic
def set_training_eligibility(document, *, user, eligible, family, expected_revision):
    actor = active_writer(user)
    doc = Document.objects.select_for_update().get(pk=document.pk)
    if not actor.is_office_admin or not doc.can_user_edit(actor):
        raise PermissionDenied("Only an authorized office administrator can change training eligibility.")
    if input_revision(doc) != expected_revision:
        raise ValidationError("The content changed. Reload before changing training eligibility.")
    family = str(family).strip()
    if len(family) > 64 or (
        eligible
        and (
            doc.access_level == AccessLevel.RESTRICTED
            or not Office.objects.filter(pk=doc.office_id, is_active=True).exists()
        )
    ):
        raise ValidationError("Restricted records and inactive offices cannot enter topic training.")
    old = {"eligible": doc.training_eligible, "family": doc.training_family}
    if old != {"eligible": bool(eligible), "family": family}:
        invalidate_models(
            list(doc.training_snapshots.values_list("snapshot_id", flat=True)),
            reason="Training eligibility or family changed",
        )
        doc.training_eligible, doc.training_family = bool(eligible), family
        doc.save(update_fields=["training_eligible", "training_family"])
        log_action(
            "UPDATE",
            "Changed scoped training eligibility",
            actor=actor,
            target=doc,
            extra={"old": old, "new": {"eligible": bool(eligible), "family": family}},
        )
    return doc

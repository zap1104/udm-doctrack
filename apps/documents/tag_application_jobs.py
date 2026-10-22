"""Recoverable automatic publication, independent of extraction/inference."""

import logging
from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User

from .ml.inference import TagDecision
from .models import TagApplicationIntent

logger = logging.getLogger("doctrack")


def _enqueue(intent_id):
    if not settings.ENABLE_BACKGROUND_TASKS:
        process_application(intent_id)
        return
    try:
        from django_q.tasks import async_task

        async_task("apps.documents.tag_application_jobs.process_application", intent_id)
    except Exception:
        TagApplicationIntent.objects.filter(pk=intent_id, state="pending").update(last_reason="queue_unavailable")
        logger.warning("Automatic topic queue unavailable; intent %s retained", intent_id)


def request_application(suggestion):
    if (
        not settings.ENABLE_AUTOMATIC_TAGS
        or not suggestion.extraction_run_id
        or not suggestion.suggested.get("topic_decisions")
    ):
        return None
    intent, created = TagApplicationIntent.objects.get_or_create(suggestion=suggestion)
    if created:
        transaction.on_commit(lambda: _enqueue(intent.pk), robust=True)
    return intent


def process_application(intent_id):
    from .automatic_tags import apply_tag_decisions

    if not settings.ENABLE_SOURCE_UNIT_EXTRACTION:
        return {"status": "skipped"}
    with transaction.atomic():
        intent = TagApplicationIntent.objects.select_for_update().filter(pk=intent_id).first()
        if intent is None:
            return {"status": "skipped"}
        if intent.state in {"complete", "blocked", "action_required"}:
            return {"status": intent.state}
        now = timezone.now()
        if intent.lease_expires_at and intent.lease_expires_at > now:
            return {"status": "running"}
        if intent.next_attempt_at and intent.next_attempt_at > now:
            return {"status": "retry_wait"}
        if intent.attempts >= 3:
            intent.state, intent.last_reason = "action_required", "attempts_exhausted"
            intent.save()
            return {"status": intent.state}
        run = intent.suggestion.extraction_run
        if run is None or not run.actor_was_recorded or not run.actor_id:
            intent.state, intent.last_reason = "blocked", "original_actor_unavailable"
            intent.save()
            return {"status": intent.state}
        token = uuid4().hex
        intent.state, intent.lease_token = "running", token
        intent.attempts += 1
        intent.lease_expires_at = now + timedelta(seconds=settings.Q_CLUSTER["timeout"] + 60)
        intent.save()
        suggestion_id, actor_id = intent.suggestion_id, run.actor_id
        generation = run.generation
    try:
        from .models import MetadataSuggestion

        suggestion = MetadataSuggestion.objects.select_related("document", "extraction_run").get(pk=suggestion_id)
        actor = User.objects.filter(pk=actor_id, is_active=True).first()
        if actor is None:
            raise PermissionDenied("The original account is unavailable.")
        if suggestion.document.extraction_generation != generation:
            raise ValidationError("The source generation changed.")
        decisions = [TagDecision(**row) for row in suggestion.suggested.get("topic_decisions", [])]
        result = apply_tag_decisions(
            suggestion.document,
            decisions,
            actor=actor,
            expected_input_revision=suggestion.input_revision,
            _application_lease=(intent_id, token),
        )
        state, reason = "complete", ""
    except PermissionDenied:
        result, state, reason = {}, "blocked", "authority_changed"
    except (ValidationError, TypeError, ValueError):
        result, state, reason = {}, "action_required", "invalid_or_stale_decisions"
    except Exception:
        result = {}
        state = "retry_wait" if intent.attempts < 3 else "action_required"
        reason = "application_failed"
    TagApplicationIntent.objects.filter(pk=intent_id, lease_token=token).update(
        state=state,
        result=result,
        last_reason=reason,
        lease_token="",
        lease_expires_at=None,
        next_attempt_at=timezone.now() + timedelta(seconds=300) if state == "retry_wait" else None,
    )
    return {"status": state}


def recover_applications(*, limit=100, now=None, dry_run=True):
    now = now or timezone.now()
    if not settings.ENABLE_SOURCE_UNIT_EXTRACTION:
        return {"intent_ids": []}
    ids = list(
        TagApplicationIntent.objects.filter(
            Q(state="pending")
            | Q(state="retry_wait", next_attempt_at__lte=now)
            | Q(state="running", lease_expires_at__lte=now)
        )
        .order_by("pk")
        .values_list("pk", flat=True)[: max(0, min(limit, 1000))]
    )
    ready = []
    for intent_id in ids:
        with transaction.atomic():
            intent = TagApplicationIntent.objects.select_for_update().get(pk=intent_id)
            if (
                intent.state not in {"pending", "retry_wait", "running"}
                or (intent.lease_expires_at and intent.lease_expires_at > now)
                or (intent.next_attempt_at and intent.next_attempt_at > now)
            ):
                continue
            if intent.attempts >= 3:
                if not dry_run:
                    intent.state, intent.last_reason = "action_required", "attempts_exhausted"
                    intent.save()
                continue
            ready.append(intent_id)
            if not dry_run:
                intent.state, intent.lease_token = "pending", ""
                intent.lease_expires_at, intent.next_attempt_at = None, None
                intent.save()
        if not dry_run:
            _enqueue(intent_id)
    return {"intent_ids": ready}

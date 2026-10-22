"""Durable second-stage intent. A completed extraction never hides a lost job."""

import logging
from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import InferenceIntent, MetadataSuggestion
from .processing_contracts import WorkState
from .tagging import input_revision

logger = logging.getLogger("doctrack")


def _enqueue(intent_id):
    if not settings.ENABLE_BACKGROUND_TASKS:
        return
    try:
        from django_q.tasks import async_task

        async_task("apps.documents.inference_jobs.process_intent", intent_id)
    except Exception:
        InferenceIntent.objects.filter(pk=intent_id, state=WorkState.PENDING).update(last_reason="queue_unavailable")
        logger.warning("Suggestion queue unavailable; durable intent %s retained", intent_id)


def ensure_intent(run):
    intent, created = InferenceIntent.objects.get_or_create(run=run)
    if created:
        transaction.on_commit(lambda: _enqueue(intent.pk), robust=True)
    return intent


def process_intent(intent_id):
    from . import processing

    reference = InferenceIntent.objects.filter(pk=intent_id).values("run_id").first()
    if reference is None or not settings.ENABLE_SOURCE_UNIT_EXTRACTION:
        return {"status": "skipped"}
    run_id = reference["run_id"]
    with processing._locked(run_id) as (run, document, actor):
        intent = InferenceIntent.objects.select_for_update().get(pk=intent_id)
        if intent.state in {WorkState.COMPLETE, WorkState.ACTION_REQUIRED, WorkState.BLOCKED}:
            return {"status": str(intent.state)}
        if not processing._eligible(run, document, actor, run.generation):
            intent.state, intent.last_reason = WorkState.BLOCKED, "authorization_or_source_changed"
            intent.save()
            return {"status": "blocked"}
        if run.state not in processing.TERMINAL:
            return {"status": "pending"}
        if MetadataSuggestion.objects.filter(extraction_run=run).exists():
            intent.state = WorkState.COMPLETE
            intent.save()
            return {"status": "complete"}
        now = timezone.now()
        if intent.lease_expires_at and intent.lease_expires_at > now:
            return {"status": "running"}
        if intent.next_attempt_at and intent.next_attempt_at > now:
            return {"status": "retry_wait"}
        if intent.attempts >= 3:
            intent.state, intent.last_reason = WorkState.ACTION_REQUIRED, "attempts_exhausted"
            intent.save()
            return {"status": "action_required"}
        token = uuid4().hex
        intent.input_revision = input_revision(document)
        revision = intent.input_revision
        intent.attempts += 1
        intent.state, intent.lease_token = WorkState.RUNNING, token
        intent.lease_expires_at = now + timedelta(seconds=settings.Q_CLUSTER["timeout"] + 60)
        intent.save()
    # No storage/model computation under record locks.
    try:
        suggestion = processing.suggest_metadata(
            text=document.ocr_text, filename=document.title, office=document.office, user=actor, document=document
        )
        with processing._locked(run_id) as (fresh_run, fresh_document, fresh_actor):
            intent = InferenceIntent.objects.select_for_update().get(pk=intent_id)
            if intent.lease_token != token or not intent.lease_expires_at or intent.lease_expires_at <= timezone.now():
                return {"status": "skipped"}
            if not processing._eligible(fresh_run, fresh_document, fresh_actor, fresh_run.generation):
                intent.state, intent.last_reason = WorkState.BLOCKED, "authorization_or_source_changed"
            elif input_revision(fresh_document) != revision:
                intent.state, intent.last_reason = WorkState.RETRY_WAIT, "input_changed"
            else:
                from .ml.models import TagModel

                decisions = suggestion.topic_decisions
                if decisions and not all(
                    TagModel.objects.filter(
                        pk=row["model_id"],
                        artifact_digest=row["artifact_digest"],
                        is_active=True,
                        invalidated_at__isnull=True,
                    ).exists()
                    for row in decisions
                ):
                    intent.state, intent.last_reason = WorkState.RETRY_WAIT, "model_changed"
                else:
                    MetadataSuggestion.objects.get_or_create(
                        extraction_run=fresh_run,
                        defaults={
                            "document": fresh_document,
                            "engine": suggestion.engine,
                            "engine_version": suggestion.engine_version,
                            "suggested": suggestion.as_dict(),
                            "input_revision": revision,
                            "text_sample": fresh_document.ocr_text[:4000],
                        },
                    )
                    intent.state, intent.last_reason = WorkState.COMPLETE, ""
            intent.lease_token, intent.lease_expires_at = "", None
            intent.next_attempt_at = (
                timezone.now() + timedelta(seconds=300) if intent.state == WorkState.RETRY_WAIT else None
            )
            intent.save()
            return {"status": str(intent.state)}
    except Exception:
        with processing._locked(run_id):
            intent = InferenceIntent.objects.select_for_update().get(pk=intent_id)
            if intent.lease_token == token:
                intent.state = WorkState.RETRY_WAIT if intent.attempts < 3 else WorkState.ACTION_REQUIRED
                intent.last_reason = "suggestion_failed"
                intent.lease_token, intent.lease_expires_at = "", None
                intent.next_attempt_at = timezone.now() + timedelta(seconds=300)
                intent.save()
        return {"status": str(intent.state)}


def recover_suggestions(*, limit=100, now=None, dry_run=True):
    from . import processing

    if not settings.ENABLE_SOURCE_UNIT_EXTRACTION:
        return {"intent_ids": []}
    now = now or timezone.now()
    candidates = (
        InferenceIntent.objects.filter(
            Q(state=WorkState.PENDING)
            | Q(state=WorkState.RETRY_WAIT, next_attempt_at__lte=now)
            | Q(state=WorkState.RUNNING, lease_expires_at__lte=now)
        )
        .order_by("pk")
        .values_list("pk", "run_id")[: max(0, min(1000, limit))]
    )
    recovered = []
    for intent_id, run_id in list(candidates):
        ready = False
        with processing._locked(run_id) as (run, document, actor):
            intent = InferenceIntent.objects.select_for_update().get(pk=intent_id)
            if intent.state == WorkState.COMPLETE or (intent.lease_expires_at and intent.lease_expires_at > now):
                continue
            if intent.state not in {WorkState.PENDING, WorkState.RETRY_WAIT, WorkState.RUNNING} or (
                intent.next_attempt_at and intent.next_attempt_at > now
            ):
                continue
            eligible = processing._eligible(run, document, actor, run.generation)
            if eligible and intent.attempts < 3:
                recovered.append(intent_id)
                if not dry_run:
                    intent.state = WorkState.PENDING
                    intent.lease_token, intent.lease_expires_at, intent.next_attempt_at = "", None, None
                    intent.save()
                    ready = True
            elif not dry_run:
                intent.state = WorkState.BLOCKED if not eligible else WorkState.ACTION_REQUIRED
                intent.last_reason = "authorization_changed" if not eligible else "attempts_exhausted"
                intent.save()
        if ready:
            if settings.ENABLE_BACKGROUND_TASKS:
                _enqueue(intent_id)
            else:
                process_intent(intent_id)
    return {"intent_ids": recovered}

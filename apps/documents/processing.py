"""Durable, actor-bound unit work with short claims and fenced finalization."""
from __future__ import annotations

import hashlib
import io
import json
import logging
from contextlib import contextmanager
from dataclasses import asdict
from datetime import timedelta
from urllib.parse import urlsplit
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import Length
from django.utils import timezone

from apps.accounts.models import User

from .models import Document, ExtractionRun, ExtractionUnit, MetadataSuggestion
from .ocr_provider import ocr_unit, prepare_unit_payload
from .processing_contracts import (
    EXTRACTOR_VERSION,
    Coverage,
    ExtractionPolicy,
    UnitBatch,
    UnitResult,
    WorkState,
    legacy_ocr_status,
    resolve_file_version,
)
from .source_readers import read_source_units
from .suggestions import suggest_metadata

logger = logging.getLogger("doctrack")
TERMINAL = {WorkState.COMPLETE, WorkState.BLOCKED, WorkState.UNSUPPORTED, WorkState.FAILED, WorkState.ACTION_REQUIRED}


def _processing_context(document):
    endpoint = urlsplit(settings.OCR_SPACE_ENDPOINT)
    # Store neither credentials nor query parameters, even in a private run.
    endpoint_id = hashlib.sha256(f"{endpoint.scheme}://{endpoint.hostname}{endpoint.path}".encode()).hexdigest()
    return {"language": document.ocr_language, "backend": str(settings.OCR_BACKEND).lower(),
            "endpoint_id": endpoint_id, "extractor_version": EXTRACTOR_VERSION}


@contextmanager
def _locked(run_id):
    """The same actor-before-document order as public mutation services."""
    with transaction.atomic():
        identity = ExtractionRun.objects.filter(pk=run_id).values("actor_id", "document_id").first()
        if identity is None:
            yield None, None, None
            return
        actor = User.objects.select_for_update(no_key=True).filter(pk=identity["actor_id"]).first() if identity["actor_id"] else None
        document = Document.objects.select_for_update().filter(pk=identity["document_id"]).first()
        run = ExtractionRun.objects.select_for_update().select_related("file_version__document_file").filter(pk=run_id).first()
        yield run, document, actor


def _eligible(run, document, actor, generation, *, token=None, external=False):
    from .tasks import _extraction_actor

    if (not settings.ENABLE_SOURCE_UNIT_EXTRACTION or run is None or document is None or not document.is_active
        or run.generation != generation or document.extraction_generation != generation):
        return False
    if token is not None and (run.lease_token != token or run.lease_expires_at is None or run.lease_expires_at <= timezone.now()):
        return False
    try:
        if resolve_file_version(run.file_version.document_file).pk != run.file_version_id:
            return False
        if "context" in run.settings_snapshot and (run.settings_snapshot["context"] != _processing_context(document)
            or run.settings_snapshot["policy"] != asdict(ExtractionPolicy.from_settings())):
            return False
    except ValidationError:
        return False
    if run.actor_was_recorded:
        if actor is None or run.purpose != "user":
            return False
        _fresh_actor, allowed = _extraction_actor(document, actor.pk)
        if not allowed:
            return False
    elif run.purpose != "local_maintenance" or actor is not None:
        return False
    if external:
        return (run.actor_was_recorded and document.allow_external_ocr
                and str(settings.OCR_BACKEND).lower() in {"auto", "ocrspace"})
    return True


def _enqueue(run_ids):
    if not settings.ENABLE_BACKGROUND_TASKS:
        return
    from django_q.tasks import async_task

    for run_id, generation in run_ids:
        try:
            async_task("apps.documents.processing.process_run", run_id, expected_generation=generation)
        except Exception:
            # Pending intent was already committed. Recovery keeps the actor.
            ExtractionRun.objects.filter(pk=run_id, generation=generation, state=WorkState.PENDING).update(last_reason="queue_unavailable")
            logger.warning("Could not enqueue extraction run %s; pending intent retained", run_id)


def request_processing(document, *, user, file_ids=None, replace=False):
    """Persist intent and enqueue after commit. Does not itself read any file."""
    from .tasks import _extraction_actor

    with transaction.atomic():
        actor = User.objects.select_for_update(no_key=True).filter(pk=getattr(user, "pk", None)).first() if user is not None else None
        current = Document.objects.select_for_update().get(pk=document.pk)
        if user is not None:
            if actor is None or not _extraction_actor(current, actor.pk)[1]:
                raise PermissionDenied("Your current account cannot request extraction for this document.")
        elif not current.is_active:
            raise PermissionDenied("Removed documents cannot be processed.")
        if replace:
            current.extraction_generation += 1
        policy = ExtractionPolicy.from_settings()
        snapshot = {"policy": asdict(policy), "context": _processing_context(current)}
        settings_digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
        roots = current.files.order_by("created_at", "pk")
        if file_ids is not None:
            roots = roots.filter(pk__in=file_ids)
        runs = []
        for root in roots:
            version = resolve_file_version(root)
            run, _created = ExtractionRun.objects.get_or_create(document=current, file_version=version,
                generation=current.extraction_generation, settings_digest=settings_digest,
                defaults={"content_hash": version.checksum, "settings_snapshot": snapshot,
                    "actor": actor, "actor_was_recorded": actor is not None,
                    "purpose": "user" if actor is not None else "local_maintenance"})
            # A caller cannot acquire someone else's pending job authority.
            if run.actor_id != getattr(actor, "pk", None) or run.actor_was_recorded != (actor is not None):
                raise ValidationError("Extraction is already requested by another actor. Retry the complete extraction explicitly.")
            runs.append(run)
        identities = [(run.pk, run.generation) for run in runs if run.state not in TERMINAL]
        if identities:
            current.ocr_status = "PENDING"
        elif not runs and not current.files.exists():
            current.ocr_status = "DONE" if current.ocr_text else "EMPTY"
        current.save(update_fields=["ocr_status", "extraction_generation", "updated_at"])
        transaction.on_commit(lambda: _enqueue(identities), robust=True)
    document.refresh_from_db()
    return runs


def _blocked(run, document, reason, *, token=None):
    if token is not None and (run is None or run.lease_token != token):
        return {"status": "skipped"}
    if run:
        run.state, run.coverage, run.last_reason = WorkState.BLOCKED, Coverage.PARTIAL, reason
        run.lease_token, run.lease_expires_at = "", None
        run.save(update_fields=["state", "coverage", "last_reason", "lease_token", "lease_expires_at", "updated_at"])
        if document and document.is_active and run.generation == document.extraction_generation:
            document.retain_extraction_failure("Text extraction was blocked by changed account, file or processing permissions. Previously saved text is preserved.")
            document.ocr_status = "SKIPPED"
            document.save(update_fields=["ocr_status", "ocr_notes", "extraction_failed_generation", "updated_at"])
    return {"status": "blocked"}


def _dispatch_allowed(run_id, generation, token):
    with _locked(run_id) as (run, document, actor):
        return _eligible(run, document, actor, generation, token=token, external=True)


def _as_result(unit):
    return UnitResult(locator_key=unit.locator_key, ordinal=unit.ordinal, kind=unit.kind,
        raw_text=unit.raw_text, normalized_text=unit.normalized_text, method=unit.method,
        state=unit.state, coverage=unit.coverage, warnings=tuple(unit.warnings), truncated=unit.truncated,
        provider_confidence=unit.provider_confidence, metadata=unit.source_metadata)


def _save_native_units(run, batch):
    for unit in batch.units:
        defaults = {
            "ordinal": unit.ordinal, "kind": unit.kind, "raw_text": unit.raw_text, "native_raw_text": unit.raw_text,
            "normalized_text": unit.normalized_text, "method": unit.method, "state": unit.state,
            "coverage": unit.coverage, "warnings": list(unit.warnings), "truncated": unit.truncated,
            "provider_confidence": unit.provider_confidence, "source_metadata": unit.metadata}
        previous = ExtractionUnit.objects.filter(run__file_version=run.file_version, run__is_current=True,
            locator_key=unit.locator_key, coverage=Coverage.COMPLETE, state=WorkState.COMPLETE).exclude(run=run).order_by("-run_id").first()
        if previous and previous.normalized_text and unit.coverage != Coverage.COMPLETE:
            defaults.update(raw_text=previous.raw_text, normalized_text=previous.normalized_text, method=previous.method,
                ocr_raw_text=previous.ocr_raw_text, source_metadata={**unit.metadata, "last_good_run_id": previous.run_id})
        ExtractionUnit.objects.get_or_create(run=run, locator_key=unit.locator_key, defaults=defaults)
    run.expected_units = batch.expected_units
    run.warnings = list(batch.omitted_reasons)
    run.save(update_fields=["expected_units", "warnings", "updated_at"])


def _publish(run, document, actor, *, next_state=None):
    requested = ExtractionRun.objects.filter(document=document, generation=run.generation).values_list("file_version__document_file_id", flat=True)
    missing_ids = list(document.files.exclude(pk__in=requested).values_list("pk", flat=True))
    if missing_ids:
        if run.discovery_round >= 2:
            run.warnings = [*run.warnings, "Files kept changing during extraction. Retry when uploads finish; saved text is preserved."]
            next_state = WorkState.ACTION_REQUIRED
        else:
            discovered = request_processing(document, user=actor, file_ids=missing_ids)
            ExtractionRun.objects.filter(pk__in=[item.pk for item in discovered]).update(discovery_round=run.discovery_round+1)
    units = list(run.units.all())
    batch = UnitBatch(tuple(_as_result(unit) for unit in units), run.expected_units, tuple(run.warnings), run.settings_digest)
    if next_state is None:
        if any(unit.state == WorkState.RETRY_WAIT for unit in units):
            next_state = WorkState.RETRY_WAIT
        elif any(unit.source_metadata.get("ocr_needed") and not unit.source_metadata.get("ocr_attempted") for unit in units):
            next_state = WorkState.PENDING
        elif any(unit.state == WorkState.FAILED for unit in units):
            next_state = WorkState.FAILED
        elif batch.coverage == Coverage.COMPLETE:
            next_state = WorkState.COMPLETE
        elif units and all(unit.state == WorkState.BLOCKED for unit in units):
            next_state = WorkState.BLOCKED
        elif units and all(unit.state == WorkState.UNSUPPORTED for unit in units):
            next_state = WorkState.UNSUPPORTED
        else:
            next_state = WorkState.ACTION_REQUIRED
    ExtractionRun.objects.filter(file_version=run.file_version, is_current=True).exclude(pk=run.pk).update(is_current=False)
    run.state, run.coverage, run.is_current = next_state, batch.coverage, True
    if missing_ids and run.discovery_round >= 2:
        run.coverage = Coverage.PARTIAL
    run.lease_token, run.lease_expires_at = "", None
    waits = [unit.next_attempt_at for unit in units if unit.state == WorkState.RETRY_WAIT and unit.next_attempt_at]
    run.next_attempt_at = min(waits) if waits else None
    run.save(update_fields=["state", "coverage", "is_current", "lease_token", "lease_expires_at", "next_attempt_at", "warnings", "updated_at"])
    root = run.file_version.document_file
    if run.expected_units is not None and units and units[0].kind in {"pdf_page", "image_frame"}:
        root.page_count = run.expected_units
    root.extracted_chars = sum(len(unit.normalized_text) for unit in units)
    root.save(update_fields=["page_count", "extracted_chars", "updated_at"])
    # Compatibility aggregate only. Unit/chunk storage is authoritative and
    # retains later content, while this old field stays bounded for old reads.
    current_runs = ExtractionRun.objects.filter(document=document, is_current=True).select_related("file_version__document_file")
    context = document.tracking_origin
    fields = ("title", "description", "original_reference") if document.is_external else ("subject", "instructions", "remarks", "completion_note")
    context_text = "\n\n".join(str(getattr(context, key, "") or "") for key in fields).strip() if context else ""
    text_parts = [context_text[:200_000]] if context_text else []
    characters = len(context_text[:200_000])
    total_characters = len(context_text)
    all_complete = True
    for item in current_runs.order_by("file_version__document_file__created_at", "pk"):
        try:
            if resolve_file_version(item.file_version.document_file).pk != item.file_version_id:
                continue
        except ValidationError:
            continue
        totals = item.units.aggregate(characters=Sum(Length("normalized_text")), count=Count("pk"))
        total_characters += (totals["characters"] or 0) + 2*totals["count"]
        if characters < 200_000:
            for value in item.units.order_by("ordinal", "pk").values_list("normalized_text", flat=True).iterator(chunk_size=20):
                if value:
                    text_parts.append(value[:max(0, 200_000-characters)])
                    characters += len(text_parts[-1])+2
                if characters >= 200_000:
                    break
        all_complete &= item.state == WorkState.COMPLETE and item.coverage == Coverage.COMPLETE
    text = "\n\n".join(text_parts)[:200_000]
    if text and (all_complete or not document.ocr_text):
        document.ocr_text = text[:200_000]
    elif text and text not in document.ocr_text:
        document.ocr_text = (document.ocr_text + "\n\n" + text)[:200_000]
    pending = ExtractionRun.objects.filter(document=document, generation=document.extraction_generation, state__in=[WorkState.PENDING, WorkState.RUNNING, WorkState.RETRY_WAIT]).exists()
    document.ocr_status = "PENDING" if pending else legacy_ocr_status(batch)
    notes = list(run.warnings) + [note for unit in units for note in unit.warnings]
    if total_characters > 200_000:
        notes.append("The compatibility text display is limited; later source-unit text is retained separately.")
    document.ocr_notes = "\n".join(dict.fromkeys(notes))[:4000]
    document.ocr_engine = "source-units:1"
    document.ocr_confidence = None
    document.page_count = document.file_page_count
    if not pending and (not all_complete or current_runs.count() != document.files.count()):
        document.retain_extraction_failure(document.ocr_notes)
    elif not pending and current_runs.count() == document.files.count():
        document.extraction_failed_generation = None
    document.save(update_fields=["ocr_text", "ocr_status", "ocr_notes", "ocr_engine", "ocr_confidence", "extraction_failed_generation", "page_count", "updated_at"])
    document.rebuild_index()
    if next_state == WorkState.PENDING:
        transaction.on_commit(lambda: _enqueue([(run.pk, run.generation)]), robust=True)
    return {"status": str(next_state), "document_id": document.pk, "characters": len(document.ocr_text)}


def process_run(run_id: int, *, expected_generation: int) -> dict:
    """At most one external OCR unit per invocation, with fenced publication."""
    if not settings.ENABLE_SOURCE_UNIT_EXTRACTION:
        return {"status": "blocked", "reason": "source_processing_disabled"}
    with _locked(run_id) as (run, document, actor):
        if run is None:
            return {"status": "skipped"}
        if run.state in TERMINAL:
            return {"status": str(run.state)}
        if not _eligible(run, document, actor, expected_generation):
            return _blocked(run, document, "authorization_or_generation_changed")
        if run.lease_expires_at and run.lease_expires_at > timezone.now():
            return {"status": "running"}
        if run.next_attempt_at and run.next_attempt_at > timezone.now():
            return {"status": "retry_wait"}
        token = uuid4().hex
        run.state, run.lease_token = WorkState.RUNNING, token
        run.lease_expires_at = timezone.now()+timedelta(seconds=settings.Q_CLUSTER["timeout"]+60)
        run.attempts += 1
        run.save(update_fields=["state", "lease_token", "lease_expires_at", "attempts", "updated_at"])
        document.ocr_status = "RUNNING"
        document.save(update_fields=["ocr_status", "updated_at"])
        policy_data = run.settings_snapshot.get("policy", run.settings_snapshot)
        policy = ExtractionPolicy(**policy_data) if policy_data else ExtractionPolicy.from_settings()
        version = run.file_version
        initialized = run.units.exists()
        language = document.ocr_language
    # All storage/parser/provider I/O is beyond the claim transaction.
    try:
        with version.file.open("rb") as stream:
            payload = stream.read(policy.max_input_bytes+1)
        actual_hash = hashlib.sha256(payload).hexdigest()
        prior_hash = ExtractionRun.objects.filter(file_version=version).exclude(content_hash="").exclude(pk=run_id).order_by("-pk").values_list("content_hash", flat=True).first()
        if len(payload) > policy.max_input_bytes or (version.checksum and actual_hash != version.checksum) or (prior_hash and prior_hash != actual_hash):
            raise ValidationError("Stored file bytes do not match the current checksum or safe input budget.")
        batch = read_source_units(io.BytesIO(payload), version.original_name, policy=policy) if not initialized else None
        with _locked(run_id) as (run, document, actor):
            if not _eligible(run, document, actor, expected_generation, token=token):
                return _blocked(run, document, "changed_before_native_result", token=token)
            if batch is not None:
                run.content_hash = actual_hash
                run.save(update_fields=["content_hash", "updated_at"])
                _save_native_units(run, batch)
            candidates = [unit for unit in run.units.all() if unit.source_metadata.get("ocr_needed") and (
                not unit.source_metadata.get("ocr_attempted") or (unit.state == WorkState.RETRY_WAIT and (not unit.next_attempt_at or unit.next_attempt_at <= timezone.now())))]
            if not _eligible(run, document, actor, expected_generation, token=token, external=True):
                for unit in candidates:
                    unit.state, unit.coverage = WorkState.BLOCKED, Coverage.PARTIAL
                    unit.source_metadata = {**unit.source_metadata, "ocr_attempted": True}
                    unit.warnings = [*unit.warnings, "External OCR is disabled or not currently authorized; no bytes were dispatched."]
                    unit.save(update_fields=["state", "coverage", "source_metadata", "warnings", "updated_at"])
                candidates = []
            selected = candidates[0] if candidates else None
            selected_result = _as_result(selected) if selected else None
        result = None
        if selected:
            try:
                prepared = prepare_unit_payload(io.BytesIO(payload), version.original_name, selected_result, policy=policy)
                result = ocr_unit(prepared.data, prepared.filename, source_locator=selected.locator_key, policy=policy,
                    language_hint=language, authorize_dispatch=lambda: _dispatch_allowed(run_id, expected_generation, token))
                preparation_notes = list(prepared.notes)
            except ValidationError as exc:
                result = UnitResult(locator_key=selected.locator_key, ordinal=selected.ordinal, kind=selected.kind,
                    state=WorkState.ACTION_REQUIRED, coverage=Coverage.PARTIAL, warnings=tuple(exc.messages))
                preparation_notes = []
        with _locked(run_id) as (run, document, actor):
            if not _eligible(run, document, actor, expected_generation, token=token):
                return _blocked(run, document, "changed_before_ocr_result", token=token)
            if selected and result is not None:
                unit = run.units.select_for_update().get(pk=selected.pk)
                if result.method.startswith("ocr.") and not _eligible(run, document, actor, expected_generation, token=token, external=True):
                    result = UnitResult(locator_key=unit.locator_key, ordinal=unit.ordinal, kind=unit.kind,
                        state=WorkState.BLOCKED, coverage=Coverage.PARTIAL,
                        warnings=("OCR eligibility changed before storage; the new provider text was discarded. Previously dispatched bytes cannot be recalled.",))
                unit.attempts += 1
                unit.ocr_raw_text = result.raw_text or unit.ocr_raw_text
                if result.coverage == Coverage.COMPLETE or not unit.raw_text:
                    unit.raw_text, unit.normalized_text = result.raw_text, result.normalized_text
                    unit.method = result.method
                unit.state, unit.coverage, unit.truncated = result.state, result.coverage, result.truncated
                unit.provider_confidence = result.provider_confidence
                unit.warnings = list(dict.fromkeys([*result.warnings, *preparation_notes]))
                unit.source_metadata = {**unit.source_metadata, **result.metadata, "ocr_attempted": True}
                if result.state == WorkState.RETRY_WAIT:
                    if unit.attempts >= 3:
                        unit.state = WorkState.ACTION_REQUIRED
                        unit.warnings.append("The finite unit retry budget is exhausted. An authorized user must retry explicitly.")
                    else:
                        unit.next_attempt_at = timezone.now()+timedelta(seconds=min(3600, max(1, result.metadata.get("retry_after_seconds", 300))))
                unit.save()
            outcome = _publish(run, document, actor)
        # Inference/rules run outside publication locks. Store only after a
        # fresh generation/permission check, and only once per run.
        if outcome["status"] in TERMINAL and not MetadataSuggestion.objects.filter(extraction_run_id=run_id).exists():
            suggestion = suggest_metadata(text=document.ocr_text, filename=document.title, office=document.office, user=actor)
            with _locked(run_id) as (fresh_run, fresh_document, fresh_actor):
                if _eligible(fresh_run, fresh_document, fresh_actor, expected_generation) and not MetadataSuggestion.objects.filter(extraction_run_id=run_id).exists():
                    MetadataSuggestion.objects.create(document=fresh_document, engine=suggestion.engine,
                        extraction_run=fresh_run, engine_version=suggestion.engine_version,
                        suggested=suggestion.as_dict(), text_sample=fresh_document.ocr_text[:4000])
        return outcome
    except (OSError, ValidationError, ValueError):
        with _locked(run_id) as (run, document, actor):
            if _eligible(run, document, actor, expected_generation, token=token):
                run.warnings = ["The current file could not be read or verified. Previously saved text is preserved."]
                return _publish(run, document, actor, next_state=WorkState.FAILED)
        return {"status": "skipped"}
    except Exception:
        with _locked(run_id) as (failed_run, failed_document, failed_actor):
            if failed_run and failed_document and failed_run.lease_token == token and failed_run.generation == failed_document.extraction_generation:
                failed_run.interruptions += 1
                failed_run.state = WorkState.RETRY_WAIT if failed_run.interruptions < 3 else WorkState.ACTION_REQUIRED
                failed_run.last_reason = "finalization_failed"
                failed_run.next_attempt_at = timezone.now()+timedelta(seconds=300)
                failed_run.lease_token, failed_run.lease_expires_at = "", None
                failed_run.save()
                failed_document.retain_extraction_failure("Extraction finalization failed. Saved files and prior text were preserved; retry is pending.")
                failed_document.save(update_fields=["ocr_status", "ocr_notes", "extraction_failed_generation", "updated_at"])
        raise


def drive_local_processing(document_id):
    """Bound local work; one OCR unit per web submission, durable remainder."""
    for _index in range(settings.MAX_UPLOAD_FILES+3):
        run = ExtractionRun.objects.filter(document_id=document_id, state=WorkState.PENDING).order_by("created_at", "pk").first()
        if run is None:
            break
        try:
            process_run(run.pk, expected_generation=run.generation)
        except Exception:
            logger.exception("Local extraction run %s interrupted; intent retained", run.pk)
            break
        if run.units.filter(attempts__gt=0).exists():
            break


def recover_processing(*, limit: int, now=None, dry_run: bool=True) -> dict:
    """Bounded recovery of original intents, never replacement system jobs."""
    if not settings.ENABLE_SOURCE_UNIT_EXTRACTION:
        return {"run_ids": [], "count": 0, "dry_run": dry_run, "reason": "source_processing_disabled"}
    now = now or timezone.now()
    limit = min(max(int(limit), 1), 500)
    candidates = ExtractionRun.objects.filter(document__is_active=True).filter(
        Q(state=WorkState.PENDING) | Q(state=WorkState.RETRY_WAIT, next_attempt_at__lte=now)
        | Q(state=WorkState.RUNNING, lease_expires_at__lte=now)
    ).order_by("created_at", "pk").values_list("pk", flat=True)[:limit]
    ready = []
    for run_id in list(candidates):
        with _locked(run_id) as (run, document, actor):
            if not _eligible(run, document, actor, run.generation):
                if not dry_run:
                    _blocked(run, document, "recovery_not_authorized")
                continue
            if not dry_run:
                if run.state == WorkState.RUNNING:
                    run.interruptions += 1
                if run.interruptions >= 3:
                    run.state = WorkState.ACTION_REQUIRED
                    run.last_reason = "interruption_budget_exhausted"
                    run.lease_token, run.lease_expires_at = "", None
                    run.save()
                    continue
                run.state, run.lease_token, run.lease_expires_at = WorkState.PENDING, "", None
                run.next_attempt_at = None
                run.save()
                transaction.on_commit(lambda identity=(run.pk, run.generation): _enqueue([identity]), robust=True)
            ready.append(run.pk)
    return {"run_ids": ready, "count": len(ready), "dry_run": dry_run}

"""Locked, revision checked mutations of the separate external custody ledger."""

from functools import wraps
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from apps.accounts.models import Office
from apps.core.permissions import active_writer
from apps.core.storage import compensate_storage, create_stored_file
from apps.core.utils import checksum_of, log_action, validate_upload

from .models import (
    ExternalActionRevision,
    ExternalActivity,
    ExternalAttachment,
    ExternalNumberSequence,
    ExternalOffice,
    ExternalRoutingStep,
    Status,
)


def _manager(user, office_id):
    if not user.is_office_admin or not (user.is_system_admin or user.office_id == office_id):
        raise PermissionDenied("Only an authorized administrator for the responsible office can make this change.")


def recording_office(user, selected=None):
    """Revalidate creation context inside the caller's write transaction."""
    office_id = getattr(selected, "pk", selected) if selected is not None else user.office_id
    if not user.is_system_admin and (not user.office_id or office_id != user.office_id):
        raise PermissionDenied("Use your own active office to create an external entry.")
    office = Office.objects.select_for_update(no_key=True).filter(pk=office_id, is_active=True).first()
    if office is None:
        raise ValidationError("Select an active internal office perspective before creating an external entry.")
    _manager(user, office.pk)
    return office


def _text(value, limit=2000):
    value = str(value or "").strip()
    if len(value) > limit:
        raise ValidationError(f"Text cannot exceed {limit} characters.")
    return value


def _actual(value, *, earliest=None):
    value = value or timezone.now()
    if timezone.is_naive(value) or value > timezone.now():
        raise ValidationError("Use a known past or current timestamp with a timezone.")
    if earliest and value < earliest:
        raise ValidationError("This action cannot precede the previous confirmed action.")
    return value


def _latest_actual(record):
    # Movement activities retain the original reported values for audit. The
    # custody rows hold the current, potentially corrected movement facts.
    movements = record.routing_steps.aggregate(sent=Max("sent_at"), received=Max("received_at"))
    activity = record.activities.exclude(event__in=["SENT", "RECEIVED"]).aggregate(latest=Max("actual_at"))["latest"]
    known = [value for value in [*movements.values(), activity] if value is not None]
    return max(known) if known else None


def _event(record, user, event, message, *, detail="", actual_at=None, routing_step=None):
    activity = ExternalActivity.objects.create(
        record=record,
        routing_step=routing_step or record.current_step,
        actor=user,
        actor_office=user.office,
        actor_name_snapshot=user.get_full_name() or user.username,
        actor_office_snapshot=user.office.name if user.office_id else "",
        event=event,
        message=_text(message, 255),
        detail=detail,
        actual_at=actual_at,
    )
    log_action(
        "UPDATE",
        message,
        actor=user,
        target=record,
        extra={"managing_office_id": record.managing_office_id, "responsible_office_id": record.responsible_office_id,
               "revision": record.revision, "external_event": event},
    )
    return activity


def _mutation(function):
    @wraps(function)
    def wrapped(record, *args, user, expected_revision, **kwargs):
        with transaction.atomic():
            actor = active_writer(user)
            locked = type(record).objects.select_for_update().get(pk=record.pk)
            if not type(record).objects.visible_to(actor).filter(pk=locked.pk).exists():
                raise PermissionDenied("This external record is outside your office scope.")
            if expected_revision is None or str(expected_revision) != str(locked.revision):
                raise ValidationError("This record changed. Reload it before trying again.")
            result = function(locked, *args, user=actor, **kwargs)
            locked.revision += 1
            locked.save()
            record.refresh_from_db()
            return record if result is None or result is locked else result

    return wrapped


def _open(record):
    if record.status in {Status.COMPLETED, Status.COMPLETED_PENDING_UPLOAD}:
        raise ValidationError("Completed custody history is frozen. Reopen unfinished work before routing.")


def _external(record, external):
    try:
        external = ExternalOffice.objects.select_for_update().get(pk=external.pk, is_active=True)
    except ExternalOffice.DoesNotExist as exc:
        raise ValidationError("Choose an active external office from the shared directory.") from exc
    return external


def _save_directory_entry(entry):
    try:
        entry.save()
    except IntegrityError as exc:
        diagnostic = getattr(exc.__cause__, "diag", None)
        if getattr(diagnostic, "constraint_name", None) != "external_office_unique_owner_name":
            raise
        raise ValidationError("An external office with this name already exists for your office.") from exc


@transaction.atomic
def create_external_office(*, user, managing_office=None, **fields):
    user = active_writer(user)
    managing_office = recording_office(user, managing_office)
    allowed = {"name", "code", "contact_person", "email", "phone", "notes", "is_active"}
    if set(fields) - allowed:
        raise ValidationError("Unsupported directory fields.")
    fields["name"] = _text(fields.get("name"), 150)
    entry = ExternalOffice(managing_office=managing_office, **fields)
    entry.full_clean(validate_constraints=False)
    _save_directory_entry(entry)
    log_action("CREATE", "Created external directory office", actor=user, target=entry)
    return entry


@transaction.atomic
def save_external_office(office, *, user, expected_revision, **fields):
    user = active_writer(user)
    entry = ExternalOffice.objects.select_for_update().get(pk=office.pk)
    _manager(user, entry.managing_office_id)
    if str(expected_revision) != str(entry.revision):
        raise ValidationError("This directory entry changed. Reload it first.")
    if set(fields) - {"name", "code", "contact_person", "email", "phone", "notes", "is_active"}:
        raise ValidationError("Unsupported directory fields.")
    for field, value in fields.items():
        setattr(entry, field, _text(value, 150) if field == "name" else value)
    entry.revision += 1
    entry.full_clean(validate_constraints=False)
    _save_directory_entry(entry)
    office.refresh_from_db()
    log_action("UPDATE", "Updated external directory office", actor=user, target=entry)
    return office


@transaction.atomic
def create_record(
    *,
    user,
    title,
    managing_office=None,
    description="",
    original_reference="",
    document_type=None,
    document_types=None,
    from_office=None,
    from_external_office=None,
    actual_at=None,
):
    from .models import ExternalTrackingRecord

    user = active_writer(user)
    managing_office = recording_office(user, managing_office)
    if from_office and from_external_office:
        raise ValidationError("Choose one initial custodian.")
    from_office = from_office or (None if from_external_office else managing_office)
    if from_office and from_office.pk != managing_office.pk:
        raise ValidationError("An internal initial custodian must match the selected office perspective.")
    record = ExternalTrackingRecord(
        managing_office=managing_office,
        title=_text(title, 255),
        description=_text(description, 5000),
        original_reference=_text(original_reference, 200),
        document_type=document_type,
        tracking_number="DRAFT-EXT-" + uuid4().hex,
        created_by=user,
        current_office=from_office,
    )
    if not record.title:
        raise ValidationError("A title is required.")
    if from_external_office:
        record.current_external_office = _external(record, from_external_office)
    record.current_office_snapshot = str(record.current_external_office or from_office.name)
    record.full_clean()
    record.save()
    from apps.core.document_types import resolve_document_types

    types, main = resolve_document_types(document_types or ([document_type] if document_type else []), document_type)
    record.document_type = main
    record.save(update_fields=["document_type"])
    record.document_types.set(types)
    _event(
        record, user, "CREATED", "Created external tracking draft", actual_at=_actual(actual_at) if actual_at else None
    )
    return record


def _number(record):
    now = timezone.localtime()
    sequence, _ = ExternalNumberSequence.objects.select_for_update().get_or_create(
        office=record.managing_office, year=now.year, month=now.month
    )
    sequence.last_number += 1
    sequence.save(update_fields=["last_number"])
    return f"UDM-EXT-{record.managing_office.code}-{now.year}-{now.month:02d}-{sequence.last_number:0{settings.TRACKING_NUMBER_SEQUENCE_WIDTH}d}"


@_mutation
def route_record(
    record,
    *,
    user,
    to_office=None,
    to_external_office=None,
    action="FORWARD",
    instructions="",
    sent_at=None,
    due_at=None,
):
    _open(record)
    if not record.can_act(user):
        raise PermissionDenied(
            "Only the confirmed internal custodian or responsible administrator for external custody can route this record."
        )
    if bool(to_office) == bool(to_external_office):
        raise ValidationError("Choose exactly one recipient.")
    if action not in ExternalRoutingStep.Action.values:
        raise ValidationError("Choose a valid routing action.")
    if to_office:
        to_office = Office.objects.filter(pk=to_office.pk, is_active=True).first()
        if not to_office:
            raise ValidationError("Choose an active receiving office.")
    else:
        to_external_office = _external(record, to_external_office)
    if (to_office and record.current_office_id == to_office.pk) or (
        to_external_office and record.current_external_office_id == to_external_office.pk
    ):
        raise ValidationError("The recipient already has custody.")
    previous = record.current_step
    if record.current_office_id:
        if sent_at is not None:
            raise ValidationError("Internal sending times are recorded by DocTrack.")
        sent_at = timezone.now()
    else:
        sent_at = _actual(sent_at, earliest=_latest_actual(record))
    if due_at and (timezone.is_naive(due_at) or due_at < sent_at):
        raise ValidationError("The deadline cannot precede sending.")
    step = ExternalRoutingStep.objects.create(
        record=record,
        sequence=previous.sequence + 1 if previous else 1,
        action=action if previous else "SEND",
        from_office=record.current_office,
        from_external_office=record.current_external_office,
        to_office=to_office,
        to_external_office=to_external_office,
        from_name_snapshot=record.current_office_snapshot,
        to_name_snapshot=(to_office or to_external_office).name,
        sent_by=user if record.current_office_id else None,
        sent_recorded_by=user,
        sent_at=sent_at,
        instructions=_text(instructions, 5000),
        due_at=due_at,
    )
    if record.status == Status.DRAFT:
        record.tracking_number = _number(record)
    record.status = Status.PENDING_RECEIPT
    record.due_at = due_at
    _event(record, user, "SENT", f"Routed to {step.to_name_snapshot}", actual_at=sent_at)
    from apps.core.notifications import resolve_for_external_record

    resolve_for_external_record(record)
    if to_office:
        from apps.core.notifications import notify_office

        notify_office(
            to_office,
            kind="ROUTED",
            title="External document awaiting receipt",
            message=record.title,
            url=record.get_absolute_url(),
            external_record=record,
        )


def _receive(record, user, *, received_at=None, note="", external=False):
    _open(record)
    step = record.current_step
    if step is None or step.received_at:
        raise ValidationError("There is no pending receipt to confirm.")
    if external:
        _manager(user, record.responsible_office_id)
        if not step.to_external_office_id:
            raise ValidationError("This recipient is an internal DocTrack office.")
        received_at = _actual(received_at, earliest=step.sent_at)
    else:
        if not record.can_confirm_receipt(user):
            raise PermissionDenied("Only the receiving office can confirm its receipt.")
        received_at = timezone.now()
    step.received_at = received_at
    step.received_recorded_at = timezone.now()
    step.received_by = None if external else user
    step.receipt_recorded_by = user
    step.receipt_note = _text(note)
    step.save(
        update_fields=["received_at", "received_recorded_at", "received_by", "receipt_recorded_by", "receipt_note"]
    )
    record.current_office = step.to_office
    record.current_external_office = step.to_external_office
    record.current_office_snapshot = step.to_name_snapshot
    record.status = Status.RECEIVED
    _event(record, user, "RECEIVED", f"Receipt confirmed at {step.to_name_snapshot}", actual_at=received_at)
    from apps.core.notifications import resolve_for_external_record

    resolve_for_external_record(record)


@_mutation
def confirm_receipt(record, *, user, note=""):
    _receive(record, user, note=note)


@_mutation
def record_external_receipt(record, *, user, received_at=None, note=""):
    _receive(record, user, received_at=received_at, note=note, external=True)


@_mutation
def add_remark(record, *, user, message):
    _open(record)
    if not record.can_act(user):
        raise PermissionDenied("Only the confirmed custodian can record work.")
    message = _text(message)
    if not message:
        raise ValidationError("A remark is required.")
    if record.status != Status.DRAFT and record.current_office_id:
        record.status = Status.IN_PROCESS
    _event(
        record,
        user,
        "REMARK",
        "Remark added",
        detail=message,
        actual_at=timezone.now() if record.current_office_id else None,
    )


@_mutation
def record_external_work(record, *, user, actual_at=None, message=""):
    _open(record)
    _manager(user, record.responsible_office_id)
    if record.status == Status.DRAFT:
        raise ValidationError("Route the draft first before recording external work.")
    if not record.current_external_office_id or not record.can_act(user):
        raise ValidationError("The external office must first have confirmed custody.")
    step = record.current_step
    boundaries = [step.received_at] if step and step.received_at else []
    if step:
        reopened_at = step.activities.filter(event="REOPENED", actual_at__isnull=False).aggregate(
            latest=Max("actual_at")
        )["latest"]
        if reopened_at:
            boundaries.append(reopened_at)
    when = _actual(actual_at, earliest=max(boundaries) if boundaries else None)
    record.status = Status.IN_PROCESS
    _event(record, user, "EXTERNAL_WORK", "External work recorded", detail=_text(message), actual_at=when)


@compensate_storage
@_mutation
def add_attachment(record, *, user, uploaded_file, description=""):
    _open(record)
    if not record.can_act(user):
        raise PermissionDenied("Only the confirmed custodian can add files.")
    validate_upload(uploaded_file)
    attachment = create_stored_file(ExternalAttachment,
        record=record,
        routing_step=record.current_step,
        file=uploaded_file,
        original_name=uploaded_file.name[:255],
        size=uploaded_file.size,
        content_type=getattr(uploaded_file, "content_type", "")[:120],
        checksum=checksum_of(uploaded_file),
        note=_text(description, 255),
        uploaded_by=user,
    )
    if record.status != Status.DRAFT and record.current_office_id:
        record.status = Status.IN_PROCESS
    _event(
        record,
        user,
        "ATTACHMENT",
        "File attached",
        detail=attachment.original_name,
        actual_at=timezone.now() if record.current_office_id and record.status != Status.DRAFT else None,
    )
    return attachment


@_mutation
def complete_record(record, *, user, note="", completed_at=None):
    _open(record)
    if record.status == Status.DRAFT or not record.can_act(user):
        raise PermissionDenied("Only the confirmed custodian can complete a published record.")
    record.status = Status.COMPLETED_PENDING_UPLOAD
    if completed_at is not None and record.current_office_id:
        raise ValidationError("Internal completion times are recorded by DocTrack.")
    record.completed_at = _actual(completed_at, earliest=_latest_actual(record))
    _event(record, user, "COMPLETED", "Completed, pending filing", detail=_text(note), actual_at=record.completed_at)
    from apps.core.notifications import notify_office, resolve_for_external_record

    resolve_for_external_record(record)
    notify_office(
        record.responsible_office,
        kind="COMPLETED",
        title="External slip awaiting filing",
        message=record.title,
        url=record.get_absolute_url(),
        external_record=record,
    )


@_mutation
def reopen_record(record, *, user, note=""):
    _manager(user, record.responsible_office_id)
    if record.status != Status.COMPLETED_PENDING_UPLOAD:
        raise ValidationError("Only a record pending filing can return to active tracking.")
    record.status = Status.RECEIVED
    record.completed_at = None
    _event(record, user, "REOPENED", "Reopened for continued work", detail=_text(note), actual_at=timezone.now())
    from apps.core.notifications import resolve_for_external_record

    resolve_for_external_record(record)


@_mutation
def annotate_record(record, *, user, message):
    _manager(user, record.responsible_office_id)
    message = _text(message)
    if not message:
        raise ValidationError("An annotation is required.")
    _event(record, user, "ANNOTATION", "Annotation added", detail=message)


@_mutation
def correct_step(record, *, step, user, reason, changes):
    _manager(user, record.responsible_office_id)
    if record.status == Status.COMPLETED:
        raise ValidationError("Filed custody history is frozen. Add an annotation instead.")
    reason = _text(reason)
    if not reason:
        raise ValidationError("A correction reason is required.")
    step = record.routing_steps.select_for_update().get(pk=step.pk)
    allowed = {"sent_at", "instructions"} if step.from_external_office_id else set()
    if step.to_external_office_id:
        allowed |= {"received_at", "receipt_note"}
    endpoint_fields = set(changes) & {"from_external_office", "to_external_office"}
    if endpoint_fields:
        if "from_external_office" in endpoint_fields and step.sequence > 1:
            raise ValidationError(
                "The sender must match the previously confirmed custodian. Record a transfer instead."
            )
        if step.received_at or record.current_step.pk != step.pk:
            raise ValidationError("Only an unreceived latest external endpoint can be corrected.")
        if step.from_external_office_id:
            allowed.add("from_external_office")
        if step.to_external_office_id:
            allowed.add("to_external_office")
    if not changes or set(changes) - allowed:
        raise ValidationError("Only externally recorded facts can be corrected.")
    old = {}
    new = {}
    for field, value in changes.items():
        prior = getattr(step, field)
        if field in endpoint_fields:
            value = _external(record, value)
            direction = field.split("_")[0]
            snapshot_field = direction + "_name_snapshot"
            old[field] = {"id": prior.pk, "name": getattr(step, snapshot_field)}
            new[field] = {"id": value.pk, "name": value.name}
            setattr(step, field, value)
            setattr(step, snapshot_field, value.name)
            continue
        if field in {"sent_at", "received_at"}:
            if prior is None or value is None:
                raise ValidationError("Corrections cannot invent or remove a receipt.")
            value = _actual(value)
        else:
            value = _text(value, 5000 if field == "instructions" else 2000)
        old[field] = prior.isoformat() if hasattr(prior, "isoformat") else prior
        new[field] = value.isoformat() if hasattr(value, "isoformat") else value
        setattr(step, field, value)
    previous = record.routing_steps.filter(sequence__lt=step.sequence).order_by("-sequence").first()
    following = record.routing_steps.filter(sequence__gt=step.sequence).order_by("sequence").first()
    if (
        (step.received_at and step.received_at < step.sent_at)
        or (previous and previous.received_at and step.sent_at < previous.received_at)
        or (following and step.received_at and following.sent_at < step.received_at)
    ):
        raise ValidationError("The correction conflicts with dependent custody chronology.")
    if step.from_external_office_id and step.from_external_office_id == step.to_external_office_id:
        raise ValidationError("The sender and recipient must be different.")
    # Anchor work to its custody step, rather than inferring membership from
    # dates that administrators can correct later.
    work_events = ["EXTERNAL_WORK", "COMPLETED", "REOPENED", "REMARK", "ATTACHMENT"]
    if "sent_at" in changes and previous:
        last_work = previous.activities.filter(event__in=work_events, actual_at__isnull=False).aggregate(
            latest=Max("actual_at"),
        )["latest"]
        if last_work and step.sent_at < last_work:
            raise ValidationError("The dispatch cannot precede work recorded under the previous custody.")
    if "received_at" in changes:
        dependents = step.activities.filter(event__in=work_events, actual_at__isnull=False)
        first_work = dependents.order_by("actual_at").first()
        if first_work and step.received_at > first_work.actual_at:
            raise ValidationError("The receipt cannot follow work already recorded under that custody.")
    if "sent_at" in changes and step.sequence == 1:
        origin = record.activities.filter(event="CREATED", actual_at__isnull=False).first()
        if origin and step.sent_at < origin.actual_at:
            raise ValidationError("Sending cannot precede the known external source time.")
    step.full_clean()
    step.save(update_fields=[*changes, *[field.split("_")[0] + "_name_snapshot" for field in endpoint_fields]])
    if "from_external_office" in changes:
        record.current_external_office = step.from_external_office
        record.current_office_snapshot = step.from_name_snapshot
    ExternalActionRevision.objects.create(
        record=record,
        step=step,
        revision=record.revision + 1,
        reason=reason,
        old_values=old,
        new_values=new,
        actor=user,
        actor_name_snapshot=user.get_full_name() or user.username,
        actor_office_snapshot=user.office.name if user.office_id else "",
    )
    _event(record, user, "CORRECTED", "External action corrected", detail=reason, routing_step=step)

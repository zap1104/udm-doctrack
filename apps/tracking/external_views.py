"""External workspace. Start with visibility, then narrow every filter and write."""

import csv
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import OuterRef, Q, Subquery
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from apps.accounts.models import Office
from apps.core.file_history import current_tracking_files
from apps.core.mixins import AdminRequiredMixin, AppLoginRequiredMixin
from apps.core.models import AuditLog
from apps.core.upload_forms import RecoverableUploadMixin, UploadFormRecovery
from apps.core.utils import log_action, sign_download, unsign_download

from . import external_services as services
from .external_analytics import summarize, timing_for_record
from .external_forms import (
    ExternalAttachmentForm,
    ExternalCompleteForm,
    ExternalCorrectionForm,
    ExternalMessageForm,
    ExternalNoteForm,
    ExternalOfficeForm,
    ExternalReceiptForm,
    ExternalRouteForm,
    ExternalWorkForm,
    RevisionForm,
)
from .external_models import ExternalAttachment, ExternalOffice, ExternalRoutingStep, ExternalTrackingRecord
from .models import Status


def _record(request, pk):
    return get_object_or_404(ExternalTrackingRecord.objects.visible_to(request.user).with_related(), pk=pk)


def _counterparts(user):
    visible = ExternalTrackingRecord.objects.visible_to(user)
    own = ExternalOffice.objects.visible_to(user).values("pk")
    return ExternalOffice.objects.filter(
        Q(pk__in=own)
        | Q(pk__in=visible.values("current_external_office_id"))
        | Q(pk__in=visible.values("routing_steps__from_external_office_id"))
        | Q(pk__in=visible.values("routing_steps__to_external_office_id")),
    ).distinct()


def _context(**kwargs):
    return {"nav_active": "external_tracking", **kwargs}


def _form_page(request, form, *, title, record=None, status=200, submit_label="Save", **kwargs):
    return render(
        request,
        "tracking/external/form.html",
        _context(
            form=form,
            title=title,
            record=record,
            submit_label=submit_label,
            **kwargs,
        ),
        status=status,
    )


def _form_error(form, error):
    for message in error.messages:
        form.add_error(None, message)


def _filtered(request, *, include_filed=False):
    """Invalid or unauthorized filters return no rows and a visible 400 error."""
    records = ExternalTrackingRecord.objects.visible_to(request.user).with_related()
    latest = ExternalRoutingStep.objects.filter(record_id=OuterRef("pk")).order_by("-sequence")
    records = records.annotate(
        latest_from_office_id=Subquery(latest.values("from_office_id")[:1]),
        latest_to_office_id=Subquery(latest.values("to_office_id")[:1]),
    )
    filters = {
        key: request.GET.get(key, "").strip()
        for key in ("q", "status", "office", "external_office", "direction", "overdue")
    }
    errors = []
    perspective = None
    if filters["status"]:
        if filters["status"] not in Status.values:
            errors.append("Choose a valid status.")
        else:
            records = records.filter(status=filters["status"])
    elif not include_filed:
        records = records.exclude(status=Status.COMPLETED)
    if filters["overdue"]:
        if filters["overdue"] not in {"yes", "no"}:
            errors.append("Choose a valid overdue filter.")
        else:
            overdue = Q(due_at__lt=timezone.now()) & ~Q(status__in=[Status.COMPLETED, Status.COMPLETED_PENDING_UPLOAD])
            records = records.filter(overdue) if filters["overdue"] == "yes" else records.exclude(overdue)
    if filters["office"]:
        allowed = (
            Office.objects.all() if request.user.is_system_admin else Office.objects.filter(pk=request.user.office_id)
        )
        try:
            perspective = allowed.get(pk=int(filters["office"]))
        except (ValueError, Office.DoesNotExist):
            errors.append("Choose an internal office within your access.")
        if perspective:
            records = records.filter(
                Q(managing_office=perspective)
                | Q(routing_steps__from_office=perspective)
                | Q(routing_steps__to_office=perspective)
            ).distinct()
    elif not request.user.is_system_admin:
        perspective = request.user.office
    if filters["external_office"]:
        try:
            counterpart = _counterparts(request.user).get(pk=int(filters["external_office"]))
        except (ValueError, ExternalOffice.DoesNotExist):
            errors.append("Choose an external office within your access.")
        else:
            records = records.filter(
                Q(current_external_office=counterpart)
                | Q(routing_steps__from_external_office=counterpart)
                | Q(routing_steps__to_external_office=counterpart)
            ).distinct()
    if filters["direction"]:
        if filters["direction"] not in {"incoming", "outgoing", "other"} or perspective is None:
            errors.append("Choose an internal office and a valid direction.")
        else:
            records = records.filter(status__in=[Status.PENDING_RECEIPT, Status.RECEIVED, Status.IN_PROCESS])
            if filters["direction"] == "other":
                records = records.filter(
                    Q(latest_to_office_id__isnull=True) | ~Q(latest_to_office_id=perspective.pk),
                ).filter(Q(latest_from_office_id__isnull=True) | ~Q(latest_from_office_id=perspective.pk))
            else:
                field = "latest_to_office_id" if filters["direction"] == "incoming" else "latest_from_office_id"
                records = records.filter(**{field: perspective.pk}).distinct()
    if filters["q"]:
        term = filters["q"][:200]
        records = records.filter(
            Q(title__icontains=term) | Q(tracking_number__icontains=term) | Q(original_reference__icontains=term)
        )
    if errors:
        records = records.none()
    return records.order_by("-created_at", "-pk"), filters, errors, perspective


def _list_context(request, records, filters, errors, perspective):
    summary = summarize(records, office=perspective)
    direction_counts = {"incoming": 0, "outgoing": 0, "other": 0}
    if perspective:
        for record in records:
            if record.status in {Status.PENDING_RECEIPT, Status.RECEIVED, Status.IN_PROCESS}:
                direction = record.direction_for(perspective)
                direction_counts[direction if direction in {"incoming", "outgoing"} else "other"] += 1
    params = request.GET.copy()
    params.pop("page", None)
    direction_links = {}
    for direction in direction_counts:
        direction_params = params.copy()
        direction_params["direction"] = direction
        direction_links[direction] = reverse("tracking:external_list") + "?" + direction_params.urlencode()
    return _context(
        page_obj=Paginator(records, 25).get_page(request.GET.get("page")),
        filters=filters,
        filter_errors=errors,
        perspective=perspective,
        status_choices=Status.choices,
        status_counts=[(item["label"], item["count"]) for item in summary["stages"]],
        total_count=summary["total"],
        internal_offices=(
            Office.objects.all() if request.user.is_system_admin else Office.objects.filter(pk=request.user.office_id)
        ),
        external_offices=_counterparts(request.user),
        filter_query=params.urlencode(),
        direction_counts=direction_counts,
        direction_links=direction_links,
    )


class ExternalListView(AppLoginRequiredMixin, View):
    def get(self, request):
        records, filters, errors, perspective = _filtered(request)
        return render(
            request,
            "tracking/external/list.html",
            _list_context(request, records, filters, errors, perspective),
            status=400 if errors else 200,
        )


class ExternalReportsView(AppLoginRequiredMixin, View):
    def get(self, request):
        records, filters, errors, perspective = _filtered(request, include_filed=True)
        context = _list_context(request, records, filters, errors, perspective)
        context["is_report"] = True
        context["report_rows"] = [(record, _timing(record, office=perspective)) for record in context["page_obj"]]
        return render(request, "tracking/external/list.html", context, status=400 if errors else 200)


def _csv_cell(value):
    value = "" if value is None else str(value)
    formula = value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n"))
    return "'" + value if formula else value


def _timing(record, *, office=None):
    values = timing_for_record(record, office=office)
    return {
        key: round(value) if key.endswith("_seconds") and value is not None else value for key, value in values.items()
    }


class ExternalExportView(AppLoginRequiredMixin, View):
    def get(self, request):
        records, _filters, errors, perspective = _filtered(
            request, include_filed=request.GET.get("source") == "reports"
        )
        if errors:
            return HttpResponse(" ".join(errors), status=400, content_type="text/plain")
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="external-tracking.csv"'
        response.write("\ufeff")
        writer = csv.writer(response)
        writer.writerow(
            [
                "External tracking number",
                "Title",
                "Original paper reference",
                "Current case status",
                "Managing office",
                "Confirmed internal custodian",
                "Confirmed external custodian",
                "Pending recipient",
                "Internal office perspective",
                "Direction",
                "Internal custody business seconds",
                "External custody business seconds",
                "Receipt waiting business seconds",
                "Overall lifecycle business seconds",
            ]
        )
        for record in records.iterator(chunk_size=200):
            step = record.current_step
            pending = step.to_name_snapshot if step and not step.received_at else ""
            timing = _timing(record, office=perspective)
            direction = record.direction_for(perspective) if perspective else ""
            writer.writerow(
                [
                    _csv_cell(value)
                    for value in [
                        record.tracking_number,
                        record.title,
                        record.original_reference,
                        record.get_status_display(),
                        record.managing_office.name,
                        record.current_office_snapshot if record.current_office_id else "",
                        record.current_office_snapshot if record.current_external_office_id else "",
                        pending,
                        perspective.name if perspective else "All authorized offices",
                        {"incoming": "Incoming", "outgoing": "Outgoing", "managed": "With another office"}.get(
                            direction, ""
                        ),
                        timing["internal_seconds"] if timing["internal_seconds"] is not None else "Not yet recorded",
                        timing["external_seconds"] if timing["external_seconds"] is not None else "Not yet recorded",
                        timing["receipt_wait_seconds"]
                        if timing["receipt_wait_seconds"] is not None
                        else "Not yet recorded",
                        timing["overall_seconds"] if timing["overall_seconds"] is not None else "Not yet recorded",
                    ]
                ]
            )
        return response


class ExternalOfficeListView(AppLoginRequiredMixin, View):
    def get(self, request):
        return render(
            request,
            "tracking/external/offices.html",
            _context(offices=ExternalOffice.objects.visible_to(request.user).select_related("managing_office")),
        )


class ExternalOfficeEditView(AdminRequiredMixin, View):
    def _instance(self, request, pk):
        return get_object_or_404(ExternalOffice.objects.visible_to(request.user), pk=pk) if pk else None

    def get(self, request, pk=None):
        office = self._instance(request, pk)
        return _form_page(
            request,
            ExternalOfficeForm(user=request.user, instance=office),
            title="Edit external office" if pk else "Add external office",
        )

    def post(self, request, pk=None):
        office = self._instance(request, pk)
        form = ExternalOfficeForm(request.POST, user=request.user, instance=office)
        if form.is_valid():
            data = dict(form.cleaned_data)
            revision = data.pop("revision", None)
            try:
                if office:
                    data.pop("managing_office")
                    services.save_external_office(office, user=request.user, expected_revision=revision, **data)
                else:
                    services.create_external_office(user=request.user, **data)
            except ValidationError as exc:
                _form_error(form, exc)
            else:
                messages.success(request, "External office saved.")
                return redirect("tracking:external_offices")
        return _form_page(request, form, title="Edit external office" if pk else "Add external office", status=400)


class ExternalOfficeDeactivateView(AdminRequiredMixin, View):
    def post(self, request, pk):
        office = get_object_or_404(ExternalOffice.objects.visible_to(request.user), pk=pk)
        form = RevisionForm(request.POST)
        if form.is_valid():
            try:
                services.save_external_office(
                    office, user=request.user, expected_revision=form.cleaned_data["revision"], is_active=False
                )
            except ValidationError as exc:
                _form_error(form, exc)
            else:
                messages.success(request, "External office deactivated. Its history remains available.")
                return redirect("tracking:external_offices")
        return _form_page(request, form, title="Deactivate external office", status=400)


class ExternalCreateView(RecoverableUploadMixin, AdminRequiredMixin, View):
    def get(self, request):
        return _form_page(
            request,
            UploadFormRecovery(request, "external-create").form,
            title="New external tracking draft",
            submit_label="Create draft and review",
        )

    def post(self, request):
        recovery = UploadFormRecovery(request, "external-create")
        response = recovery.validation_response()
        if response:
            return response
        result = recovery.finish()
        if result is None:
            return recovery.response
        recipient = recovery.form.cleaned_data.get("to_office")
        external_recipient = recovery.form.cleaned_data.get("to_external_office")
        if recipient or external_recipient:
            request.session[f"external_draft_recipient_{result.parent_id}"] = (
                f"internal:{recipient.pk}" if recipient else f"external:{external_recipient.pk}")
        return redirect(result.redirect)


class ExternalDetailView(RecoverableUploadMixin, AppLoginRequiredMixin, View):
    def get(self, request, pk):
        from .version_views import tracking_file_rows

        record = _record(request, pk)
        attachments = []
        for attachment in current_tracking_files(record):
            token = sign_download(f"external-attachment:{attachment.pk}:{request.user.pk}")
            attachments.append(
                (
                    attachment,
                    reverse("tracking:external_attachment", args=[attachment.pk]) + "?" + urlencode({"token": token}),
                )
            )
        return render(
            request,
            "tracking/external/detail.html",
            _context(
                record=record,
                steps=record.routing_steps.all(),
                activities=record.activities.all(),
                corrections=record.revisions.all(),
                attachments=attachments,
                file_rows=tracking_file_rows(record, request.user, external=True, request=request),
                can_manage=record.can_manage(request.user),
                can_act=record.can_act(request.user),
                can_receipt=record.can_confirm_receipt(request.user),
                is_filed=record.status == Status.COMPLETED,
                timing=_timing(record),
            ),
        )


class ExternalSlipView(AppLoginRequiredMixin, View):
    def get(self, request, pk):
        record = _record(request, pk)
        if record.status == Status.DRAFT:
            messages.info(
                request, "Send the draft first to assign its external tracking number and create a routing slip."
            )
            return redirect(record)
        entry = log_action(
            AuditLog.Action.PRINT,
            f"Generated external routing slip {record.tracking_number}",
            actor=request.user,
            target=record,
            request=request,
        )
        return render(
            request,
            "tracking/external/slip.html",
            _context(
                record=record,
                steps=record.routing_steps.all(),
                attachments=current_tracking_files(record),
                annotations=record.activities.filter(event="ANNOTATION"),
                printed_at=timezone.localtime(),
                print_reference=entry.pk,
            ),
        )


ACTION_FORMS = {
    "route": (ExternalRouteForm, "Forward or return", "Route"),
    "receipt": (ExternalNoteForm, "Confirm internal receipt", "Confirm receipt"),
    "external-receipt": (ExternalReceiptForm, "Record external receipt", "Record receipt"),
    "remark": (ExternalMessageForm, "Add remark", "Add remark"),
    "attachment": (ExternalAttachmentForm, "Add supporting attachment", "Upload attachment"),
    "external-work": (ExternalWorkForm, "Record external work", "Record work"),
    "complete": (ExternalCompleteForm, "Complete work", "Complete work"),
    "reopen": (ExternalNoteForm, "Reopen external record", "Reopen"),
    "annotation": (ExternalMessageForm, "Add historical annotation", "Add annotation"),
    "file": (RevisionForm, "File into the External repository", "Approve filing"),
}


class ExternalActionView(RecoverableUploadMixin, AppLoginRequiredMixin, View):
    def _prepare(self, request, pk, action):
        if action not in ACTION_FORMS:
            raise Http404
        record = _record(request, pk)
        if request.user.is_viewer:
            raise PermissionDenied("Your account has read-only access.")
        if action == "external-work" and record.status == Status.DRAFT:
            raise PermissionDenied("Send the draft before recording external work.")
        if action in {"external-receipt", "external-work", "reopen", "annotation", "file"}:
            allowed = record.can_manage(request.user)
        elif action == "receipt":
            allowed = record.can_confirm_receipt(request.user)
        else:
            allowed = record.can_manage(request.user) or record.can_act(request.user)
        if not allowed:
            raise PermissionDenied("This action belongs to the managing administrator or the office holding the paper.")
        form_class, title, label = ACTION_FORMS[action]
        kwargs = {"initial": {"revision": record.revision}}
        if action == "route":
            kwargs.update(user=request.user, record=record)
            if record.status == Status.DRAFT:
                remembered = request.session.get(f"external_draft_recipient_{record.pk}")
                if isinstance(remembered, str):
                    kwargs["initial"]["to_endpoint"] = remembered
        if action == "complete":
            kwargs["record"] = record
        return record, form_class, kwargs, title, label

    def get(self, request, pk, action):
        record, form_class, kwargs, title, label = self._prepare(request, pk, action)
        form = UploadFormRecovery(request, "external-attachment", target_id=pk).form if action == "attachment" else form_class(**kwargs)
        if action == "route" and record.status == Status.DRAFT:
            remembered = form.initial.get("to_endpoint")
            if remembered and not form.fields["to_endpoint"].valid_value(remembered):
                form.initial.pop("to_endpoint", None)
                request.session.pop(f"external_draft_recipient_{record.pk}", None)
                messages.warning(request, "The selected recipient is no longer available. Choose another before sending.")
        return _form_page(request, form, title=title, record=record, submit_label=label, action=action)

    def post(self, request, pk, action):
        record, form_class, kwargs, title, label = self._prepare(request, pk, action)
        if action == "attachment":
            recovery = UploadFormRecovery(request, "external-attachment", target_id=pk)
            response = recovery.validation_response()
            if response:
                return response
            result = recovery.finish()
            if result is None:
                return recovery.response
            messages.success(request, "Attachment saved.")
            return redirect(result.redirect)
        form = form_class(request.POST, request.FILES, **kwargs)
        if form.is_valid():
            data = dict(form.cleaned_data)
            revision = data.pop("revision")
            functions = {
                "route": services.route_record,
                "receipt": services.confirm_receipt,
                "external-receipt": services.record_external_receipt,
                "remark": services.add_remark,
                "attachment": services.add_attachment,
                "external-work": services.record_external_work,
                "complete": services.complete_record,
                "reopen": services.reopen_record,
                "annotation": services.annotate_record,
            }
            try:
                if action == "file":
                    from apps.documents.external_services import archive_external_record

                    document = archive_external_record(record, user=request.user, expected_revision=revision)
                    messages.success(request, "Filed into the External repository.")
                    return redirect(document.get_absolute_url())
                functions[action](record, user=request.user, expected_revision=revision, **data)
            except ValidationError as exc:
                _form_error(form, exc)
            else:
                if action == "route":
                    request.session.pop(f"external_draft_recipient_{record.pk}", None)
                messages.success(request, "External tracking record updated.")
                return redirect(record)
        return _form_page(request, form, title=title, record=record, submit_label=label, status=400, action=action)


class ExternalReviewView(ExternalActionView):
    def get(self, request, pk):
        return super().get(request, pk, "route")

    def post(self, request, pk):
        return super().post(request, pk, "route")


class ExternalCorrectionView(AppLoginRequiredMixin, View):
    def _prepare(self, request, pk, step_pk):
        record = _record(request, pk)
        if not record.can_manage(request.user):
            raise PermissionDenied("Only the managing office's administrator can correct external history.")
        if record.status == Status.COMPLETED:
            raise PermissionDenied("Filed custody history is frozen. Add an annotation instead.")
        step = get_object_or_404(record.routing_steps.all(), pk=step_pk)
        initial = {
            field: getattr(step, field)
            for field in (
                "sent_at",
                "received_at",
                "instructions",
                "receipt_note",
                "from_external_office",
                "to_external_office",
            )
        }
        initial["revision"] = record.revision
        return record, step, initial

    def get(self, request, pk, step_pk):
        record, step, initial = self._prepare(request, pk, step_pk)
        return _form_page(
            request,
            ExternalCorrectionForm(step=step, initial=initial),
            title="Correct external paper history",
            record=record,
            correction=True,
        )

    def post(self, request, pk, step_pk):
        record, step, initial = self._prepare(request, pk, step_pk)
        form = ExternalCorrectionForm(request.POST, step=step, initial=initial)
        if form.is_valid():
            data = dict(form.cleaned_data)
            revision, reason = data.pop("revision"), data.pop("reason")
            changes = {key: value for key, value in data.items() if value != getattr(step, key)}
            try:
                services.correct_step(
                    record, step=step, user=request.user, expected_revision=revision, reason=reason, changes=changes
                )
            except ValidationError as exc:
                _form_error(form, exc)
            else:
                messages.success(request, "Correction saved with the original values and reason.")
                return redirect(record)
        return _form_page(
            request, form, title="Correct external paper history", record=record, correction=True, status=400
        )


class ExternalAttachmentDownloadView(AppLoginRequiredMixin, View):
    def get(self, request, pk):
        attachment = get_object_or_404(
            ExternalAttachment.objects.filter(
                record__in=ExternalTrackingRecord.objects.visible_to(request.user)
            ).select_related("record"),
            pk=pk,
        )
        if unsign_download(request.GET.get("token", "")) != f"external-attachment:{attachment.pk}:{request.user.pk}":
            raise PermissionDenied("This download link has expired or is invalid. Open the record to get a new link.")
        from apps.core.file_views import private
        from apps.core.storage import storage_identity
        if not attachment.available_content:
            return private(HttpResponse("This file content is no longer available.", status=410))
        if attachment.storage_identity and attachment.storage_identity != storage_identity(attachment.file.storage):
            return private(HttpResponse("This file's storage configuration changed. Contact an administrator.", status=410))
        log_action(
            AuditLog.Action.DOWNLOAD,
            f"Downloaded external attachment {attachment.original_name}",
            actor=request.user,
            target=attachment.record,
            request=request,
        )
        try:
            response = FileResponse(attachment.file.open("rb"), as_attachment=True, filename=attachment.original_name)
        except FileNotFoundError as exc:
            raise Http404("The file is missing from storage.") from exc
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, no-store"
        return response

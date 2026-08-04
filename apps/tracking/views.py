from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import F, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.generic import View

from apps.core.mixins import AppLoginRequiredMixin, OfficeAssignedMixin
from apps.core.models import AuditLog
from apps.core.utils import log_action
from apps.documents.services import archive_tracking_record

from . import services
from .forms import (
    CompleteForm,
    ConfirmReceiptForm,
    CreateRecordForm,
    GrantAccessForm,
    RemarkForm,
    ReviewRouteForm,
    RouteForm,
    TrackingFilterForm,
)
from .models import Attachment, RoutingStep, Status, TrackingRecord

PAGE_SIZE = 20


def _get_record(request, pk) -> TrackingRecord:
    record = get_object_or_404(TrackingRecord.objects.with_related(), pk=pk)
    if not record.can_user_view(request.user):
        raise PermissionDenied(
            "This document has not been routed to your office and nobody has granted you access to it."
        )
    return record


class RecordListView(AppLoginRequiredMixin, View):
    """Active Document Tracking — newest first, completed records live in Documents."""

    template_name = "tracking/list.html"

    def get(self, request):
        form = TrackingFilterForm(request.GET or None)
        records = services.active_for(request.user)

        if form.is_valid():
            query = form.cleaned_data.get("q")
            status = form.cleaned_data.get("status")
            office = form.cleaned_data.get("office")
            scope = form.cleaned_data.get("scope")

            if query:
                records = records.filter(
                    Q(tracking_number__icontains=query)
                    | Q(subject__icontains=query)
                    | Q(originating_office__name__icontains=query)
                    | Q(originating_office__code__icontains=query)
                    | Q(current_office__name__icontains=query)
                )
            if status == "OVERDUE":
                records = records.filter(due_at__lt=timezone.now()).exclude(status=Status.COMPLETED)
            elif status:
                records = records.filter(status=status)
            if office:
                records = records.filter(Q(originating_office=office) | Q(current_office=office))
            if scope == "inbox":
                records = records.filter(
                    routing_steps__to_office_id=request.user.office_id,
                    routing_steps__received_at__isnull=True,
                    routing_steps__batch=F("current_batch"),
                )
            elif scope == "custody":
                records = records.filter(current_office_id=request.user.office_id)
            elif scope == "sent":
                records = records.filter(
                    routing_steps__from_office_id=request.user.office_id,
                    routing_steps__received_at__isnull=True,
                    routing_steps__batch=F("current_batch"),
                )
            elif scope == "mine":
                records = records.filter(created_by=request.user)

        records = records.distinct().order_by("-last_movement_at")
        page = Paginator(records, PAGE_SIZE).get_page(request.GET.get("page"))
        for record in page.object_list:
            record.can_confirm_now = record.can_user_confirm_receipt(request.user)
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "page_obj": page,
                "records": page.object_list,
                "total": records.count(),
                "querystring": request.GET.urlencode(),
            },
        )


class RecordCreateView(OfficeAssignedMixin, View):
    """Step 1 of 2 — capture details and save a draft (nothing is routed yet)."""

    template_name = "tracking/create.html"

    def get(self, request):
        return render(request, self.template_name, {"form": CreateRecordForm(user=request.user)})

    def post(self, request):
        form = CreateRecordForm(request.POST, request.FILES, user=request.user)
        if not form.is_valid():
            messages.error(request, "Check the highlighted fields.")
            return render(request, self.template_name, {"form": form})

        due_days = form.cleaned_data.get("due_days") or "0"
        try:
            record = services.create_draft_record(
                user=request.user,
                subject=form.cleaned_data["subject"],
                instructions=form.cleaned_data["instructions"],
                document_type=form.cleaned_data.get("document_type"),
                remarks=form.cleaned_data.get("remarks", ""),
                classification=form.cleaned_data.get("classification"),
                priority=form.cleaned_data.get("priority"),
            )
            services.attach_files(record, form.cleaned_data.get("attachments") or [], user=request.user)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return render(request, self.template_name, {"form": form})

        request.session[f"draft_offices_{record.pk}"] = [
            office.pk for office in form.cleaned_data["receiving_offices"]
        ]
        request.session[f"draft_due_{record.pk}"] = due_days
        return redirect("tracking:review", pk=record.pk)


class RecordReviewView(OfficeAssignedMixin, View):
    """Step 2 of 2 — confirm, then the tracking number is routed and the slip exists."""

    template_name = "tracking/review.html"

    def _remembered(self, request, record):
        """Offices picked on step 1, in the order they were picked.

        `pk__in` returns rows in database order, not selection order, and
        route_record() treats the first office as the one taking custody — so
        the list is re-sorted to match what the user actually chose.
        """
        from apps.accounts.models import Office

        office_ids = request.session.get(f"draft_offices_{record.pk}", [])
        if not office_ids:
            return []
        found = {office.pk: office for office in Office.objects.filter(pk__in=office_ids)}
        return [found[pk] for pk in office_ids if pk in found]

    def _context(self, request, record, form, offices):
        return {
            "record": record,
            "form": form,
            "offices": offices,
            "due_days": request.session.get(f"draft_due_{record.pk}", "3"),
            "session_lost": not offices,
        }

    def get(self, request, pk):
        record = _get_record(request, pk)
        if record.status != Status.DRAFT:
            return redirect(record.get_absolute_url())
        offices = self._remembered(request, record)
        form = ReviewRouteForm(
            user=request.user,
            initial={
                "receiving_offices": [office.pk for office in offices],
                "due_days": request.session.get(f"draft_due_{record.pk}", "3"),
            },
        )
        return render(request, self.template_name, self._context(request, record, form, offices))

    def post(self, request, pk):
        record = _get_record(request, pk)
        if record.status != Status.DRAFT:
            return redirect(record.get_absolute_url())

        form = ReviewRouteForm(request.POST, user=request.user)
        if not form.is_valid():
            messages.error(request, "Choose at least one receiving office before sending.")
            return render(
                request, self.template_name, self._context(request, record, form, self._remembered(request, record))
            )

        # Keep the order the user picked; the first office takes custody.
        picked = list(form.cleaned_data["receiving_offices"])
        remembered_ids = request.session.get(f"draft_offices_{record.pk}", [])
        if remembered_ids:
            rank = {pk: index for index, pk in enumerate(remembered_ids)}
            picked.sort(key=lambda office: rank.get(office.pk, len(rank)))

        try:
            services.route_record(
                record,
                picked,
                user=request.user,
                instructions=record.instructions,
                action=RoutingStep.Action.SEND,
                due_days=int(form.cleaned_data.get("due_days") or 0),
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return render(request, self.template_name, self._context(request, record, form, picked))

        request.session.pop(f"draft_offices_{record.pk}", None)
        request.session.pop(f"draft_due_{record.pk}", None)
        messages.success(
            request,
            f"{record.tracking_number} was routed. It stays “In transit” until the receiving office confirms receipt.",
        )
        return redirect(record.get_absolute_url())


class RecordDetailView(AppLoginRequiredMixin, View):
    template_name = "tracking/detail.html"

    def get(self, request, pk):
        record = _get_record(request, pk)
        steps = record.routing_steps.select_related(
            "from_office", "to_office", "sent_by", "received_by"
        ).order_by("sequence")
        activities = record.activities.select_related("actor", "actor_office").order_by("created_at", "id")
        return render(
            request,
            self.template_name,
            {
                "record": record,
                "steps": steps,
                "activities": activities,
                "attachments": record.attachments.select_related("uploaded_by"),
                "receipt_form": ConfirmReceiptForm(),
                "remark_form": RemarkForm(),
                "route_form": RouteForm(record=record, user=request.user),
                "complete_form": CompleteForm(),
                "grant_form": GrantAccessForm(),
                "pending_offices": record.pending_receipt_offices(),
                "can_act": record.can_user_act(request.user),
                "can_confirm": record.can_user_confirm_receipt(request.user),
                "archived_document": getattr(record, "archived_document", None),
            },
        )


class ConfirmReceiptView(OfficeAssignedMixin, View):
    def post(self, request, pk):
        record = _get_record(request, pk)
        form = ConfirmReceiptForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Could not read the receipt note.")
            return redirect(record.get_absolute_url())
        try:
            services.confirm_receipt(record, user=request.user, note=form.cleaned_data.get("note", ""))
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, getattr(exc, "messages", [str(exc)])[0])
            return redirect(record.get_absolute_url())
        messages.success(
            request,
            f"Receipt recorded for {record.tracking_number} at {timezone.localtime():%d %b %Y, %I:%M %p}.",
        )
        return redirect(record.get_absolute_url())


class AddRemarkView(OfficeAssignedMixin, View):
    def post(self, request, pk):
        record = _get_record(request, pk)
        if not record.can_user_act(request.user):
            raise PermissionDenied("Confirm receipt before adding remarks to this document.")
        form = RemarkForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, "Write the remark before saving.")
            return redirect(record.get_absolute_url())
        try:
            services.add_remark(record, user=request.user, remark=form.cleaned_data["remark"])
            services.attach_files(record, form.cleaned_data.get("attachments") or [], user=request.user)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect(record.get_absolute_url())
        messages.success(request, "Remark added to the routing history.")
        return redirect(record.get_absolute_url())


class RouteRecordView(OfficeAssignedMixin, View):
    def post(self, request, pk):
        record = _get_record(request, pk)
        if not record.can_user_act(request.user):
            raise PermissionDenied("Only the office that currently holds the document can forward it.")
        form = RouteForm(request.POST, request.FILES, record=record, user=request.user)
        if not form.is_valid():
            messages.error(request, "Choose at least one office to send the document to.")
            return redirect(record.get_absolute_url())
        try:
            services.attach_files(record, form.cleaned_data.get("attachments") or [], user=request.user)
            services.route_record(
                record,
                list(form.cleaned_data["offices"]),
                user=request.user,
                instructions=form.cleaned_data.get("instructions", ""),
                action=form.cleaned_data["action"],
                due_days=int(form.cleaned_data.get("due_days") or 0),
                remark=form.cleaned_data.get("remark", ""),
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect(record.get_absolute_url())
        messages.success(request, "Document routed. The receiving office must confirm receipt.")
        return redirect(record.get_absolute_url())


class CompleteRecordView(OfficeAssignedMixin, View):
    def post(self, request, pk):
        record = _get_record(request, pk)
        if not record.can_user_act(request.user):
            raise PermissionDenied("Only the office holding the document can mark it completed.")
        form = CompleteForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Could not read the completion note.")
            return redirect(record.get_absolute_url())

        services.complete_record(record, user=request.user, note=form.cleaned_data.get("note", ""))
        message = f"{record.tracking_number} is complete."

        if form.cleaned_data.get("archive_now"):
            try:
                document = archive_tracking_record(record, user=request.user)
                message += " It is now searchable in Document Management."
                messages.success(request, message)
                return redirect(document.get_absolute_url())
            except ValidationError as exc:
                messages.warning(request, f"Completed, but archiving failed: {'; '.join(exc.messages)}")
                return redirect(record.get_absolute_url())

        messages.success(request, message)
        return redirect(record.get_absolute_url())


class ArchiveRecordView(OfficeAssignedMixin, View):
    def post(self, request, pk):
        record = _get_record(request, pk)
        try:
            document = archive_tracking_record(record, user=request.user)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect(record.get_absolute_url())
        messages.success(request, "Record archived into Document Management.")
        return redirect(document.get_absolute_url())


class GrantAccessView(OfficeAssignedMixin, View):
    def post(self, request, pk):
        record = _get_record(request, pk)
        if not (request.user.is_records_staff or record.created_by_id == request.user.pk):
            raise PermissionDenied("Only records personnel or the originator can share this record.")
        form = GrantAccessForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Choose an office or a user.")
            return redirect(record.get_absolute_url())
        services.grant_access(
            record,
            user=request.user,
            office=form.cleaned_data.get("office"),
            target_user=form.cleaned_data.get("user"),
            reason=form.cleaned_data.get("reason", ""),
        )
        messages.success(request, "Access granted.")
        return redirect(record.get_absolute_url())


class RoutingSlipView(AppLoginRequiredMixin, View):
    """Printable slip that always contains the complete movement history."""

    template_name = "tracking/routing_slip.html"

    def get(self, request, pk):
        record = _get_record(request, pk)
        return render(
            request,
            self.template_name,
            {
                "record": record,
                "steps": record.routing_steps.select_related(
                    "from_office", "to_office", "sent_by", "received_by"
                ).order_by("sequence"),
                "attachments": record.attachments.all(),
                "printed_at": timezone.localtime(),
                "printed_by": request.user,
            },
        )


class AttachmentDownloadView(AppLoginRequiredMixin, View):
    def get(self, request, pk):
        attachment = get_object_or_404(Attachment.objects.select_related("record"), pk=pk)
        if not attachment.record.can_user_view(request.user):
            raise PermissionDenied("You do not have access to this document.")
        log_action(
            AuditLog.Action.DOWNLOAD,
            f"Downloaded {attachment.original_name} from {attachment.record.tracking_number}",
            actor=request.user,
            target=attachment.record,
            request=request,
        )
        try:
            return FileResponse(attachment.file.open("rb"), as_attachment=True, filename=attachment.original_name)
        except FileNotFoundError as exc:
            raise Http404("The file is missing from storage.") from exc

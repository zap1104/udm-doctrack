from __future__ import annotations

from datetime import date

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import F, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.generic import View

from apps.core.mixins import AppLoginRequiredMixin, OfficeAssignedMixin
from apps.core.models import AuditLog
from apps.core.utils import log_action, qr_svg

from . import services
from .forms import (
    DEADLINE_DATE,
    DEADLINE_NONE,
    BulkConfirmReceiptForm,
    CompleteForm,
    ConfirmReceiptForm,
    CreateRecordForm,
    GrantAccessForm,
    RemarkForm,
    ReopenForm,
    ReviewRouteForm,
    RouteForm,
    TrackingFilterForm,
)
from .models import (
    COMPLETED_STATUSES,
    QUIET_EVENTS,
    Attachment,
    RoutingStep,
    Status,
    TrackingRecord,
)

PAGE_SIZE = 20
#: Rows of the pending-upload queue shown before it collapses to a link.
PENDING_UPLOAD_SHOWN = 5

#: Session key holding the deadline chosen on step 1, as an ISO date or "" for
#: none. Deliberately not the old `draft_due_<pk>` name — that key held a day
#: count, and a session left over from before this change would be read as a date.
DRAFT_DEADLINE_KEY = "draft_deadline_{pk}"


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

        # Apply every filter that validated, not only the all-or-nothing case.
        # `if form.is_valid()` used to drop *all* filters when any one of them
        # was unrecognised, so a stale link like "?scope=inbox&status=BOGUS"
        # quietly returned every active record while looking like an inbox —
        # the wrong answer presented as the right one.
        form.is_valid()
        data = getattr(form, "cleaned_data", {})
        query = data.get("q")
        status = data.get("status")
        office = data.get("office")
        scope = data.get("scope")

        if query:
            records = records.filter(
                Q(tracking_number__icontains=query)
                | Q(subject__icontains=query)
                | Q(originating_office__name__icontains=query)
                | Q(originating_office__code__icontains=query)
                | Q(current_office__name__icontains=query)
            )
        if status == "OVERDUE":
            records = records.filter(due_at__lt=timezone.now()).exclude(status__in=COMPLETED_STATUSES)
        elif status:
            records = records.filter(status=status)
        if office:
            records = records.filter(Q(originating_office=office) | Q(current_office=office))
        records = services.apply_scope(records, scope, request.user)

        if form.errors:
            messages.warning(
                request,
                "Ignored a filter that was not recognised: "
                + ", ".join(sorted(form.errors)) + ". Showing the rest.",
            )

        records = records.distinct().order_by("-last_movement_at")
        page = Paginator(records, PAGE_SIZE).get_page(request.GET.get("page"))
        # Materialised once so the annotation below lands on the very objects
        # the template iterates, not on a throwaway copy of the queryset.
        page_records = list(page.object_list)
        services.annotate_can_confirm(page_records, request.user)
        services.annotate_receiving_offices(page_records)

        # The completed-but-unapproved queue. It sits on this page rather than
        # on the repository page because these records have not reached the
        # repository — approving them is the act that puts them there — and
        # because approval is now a stage of the tracking lifecycle.
        pending_upload = list(
            services.pending_upload_for(request.user)
            # nulls_last because Postgres sorts NULLs first on DESC, which would
            # float a record with no completion time to the top of the queue.
            .order_by(F("completed_at").desc(nulls_last=True))[: PENDING_UPLOAD_SHOWN + 1]
        )
        pending_upload_more = max(0, len(pending_upload) - PENDING_UPLOAD_SHOWN)
        pending_upload = pending_upload[:PENDING_UPLOAD_SHOWN]
        for record in pending_upload:
            # Approval is one click from here; returning a record to tracking
            # needs a written reason, so that action stays on the record page.
            record.can_approve = record.can_user_approve_upload(request.user)

        return render(
            request,
            self.template_name,
            {
                "form": form,
                "page_obj": page,
                "records": page_records,
                "pending_upload": pending_upload,
                "pending_upload_more": pending_upload_more,
                "can_bulk_receive": any(record.can_confirm_now for record in page_records),
                # The paginator has already counted this queryset; calling
                # .count() again would run the same DISTINCT-over-joins query
                # a second time on every page load.
                "total": page.paginator.count,
                # No "querystring" here any more: the pagination partial reads
                # request.GET itself, which is what stopped one page's links
                # carrying another page's filters (or none at all).
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

        deadline = form.deadline_datetime()
        try:
            record = services.create_draft_record(
                user=request.user,
                subject=form.cleaned_data["subject"],
                instructions=form.cleaned_data["instructions"],
                document_type=form.cleaned_data.get("document_type"),
                classification=form.cleaned_data.get("classification"),
                priority=form.cleaned_data.get("priority"),
                requested_action=form.cleaned_data.get("requested_action", ""),
                due_at=deadline,
            )
            services.attach_files(record, form.cleaned_data.get("attachments") or [], user=request.user)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return render(request, self.template_name, {"form": form})

        request.session[f"draft_offices_{record.pk}"] = [
            office.pk for office in form.cleaned_data["receiving_offices"]
        ]
        request.session[DRAFT_DEADLINE_KEY.format(pk=record.pk)] = (
            form.cleaned_data["due_date"].isoformat() if deadline else ""
        )
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

    def _remembered_deadline(self, request, record):
        """The date picked on step 1, or None. Falls back to whatever is already
        on the draft so a lost session still shows the deadline that was saved."""
        stored = request.session.get(DRAFT_DEADLINE_KEY.format(pk=record.pk))
        if stored is not None:
            return date.fromisoformat(stored) if stored else None
        return timezone.localtime(record.due_at).date() if record.due_at else None

    def _context(self, request, record, form, offices):
        return {
            "record": record,
            "form": form,
            "offices": offices,
            "deadline": self._remembered_deadline(request, record),
            "session_lost": not offices,
        }

    def get(self, request, pk):
        record = _get_record(request, pk)
        if record.status != Status.DRAFT:
            return redirect(record.get_absolute_url())
        offices = self._remembered(request, record)
        deadline = self._remembered_deadline(request, record)
        form = ReviewRouteForm(
            user=request.user,
            initial={
                "receiving_offices": [office.pk for office in offices],
                "deadline_choice": DEADLINE_DATE if deadline else DEADLINE_NONE,
                "due_date": deadline,
            },
        )
        return render(request, self.template_name, self._context(request, record, form, offices))

    def post(self, request, pk):
        record = _get_record(request, pk)
        if record.status != Status.DRAFT:
            return redirect(record.get_absolute_url())

        form = ReviewRouteForm(request.POST, user=request.user)
        if not form.is_valid():
            messages.error(request, "Check the highlighted fields before sending.")
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
                due_at=form.deadline_datetime(),
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return render(request, self.template_name, self._context(request, record, form, picked))

        request.session.pop(f"draft_offices_{record.pk}", None)
        request.session.pop(DRAFT_DEADLINE_KEY.format(pk=record.pk), None)
        messages.success(
            request,
            f"{record.tracking_number} was routed. It stays “Pending receipt” until the "
            "receiving office confirms it.",
        )
        return redirect(record.get_absolute_url())


#: How many of the newest timeline entries stay expanded. Anything older folds
#: behind a labelled control that names the number hidden — the history is
#: append-only and grows for the life of the record, so a document that has
#: been round five offices otherwise opens as a wall nobody reads.
TIMELINE_VISIBLE = 5


class RecordDetailView(AppLoginRequiredMixin, View):
    template_name = "tracking/detail.html"

    def get(self, request, pk):
        record = _get_record(request, pk)
        services.log_view(record, user=request.user)
        # Split here rather than with |slice in the template. Django's slice
        # filter fails *silently* on a queryset — negative indexing is
        # unsupported, and the filter swallows the error and returns the whole
        # thing, which would render every entry twice instead of raising.
        steps = list(
            record.routing_steps.select_related(
                "from_office", "to_office", "sent_by", "received_by"
            ).order_by("sequence")
        )
        activities = list(
            record.activities.select_related("actor", "actor_office")
            .exclude(event__in=QUIET_EVENTS)
            .order_by("created_at", "id")
        )
        attachments = list(record.attachments.select_related("uploaded_by"))
        archived_document = getattr(record, "archived_document", None)
        can_archive_now = archived_document is None and record.can_user_approve_upload(request.user)
        can_reopen = record.can_user_reopen(request.user)
        return render(
            request,
            self.template_name,
            {
                "record": record,
                "steps": steps,
                # Oldest first in both lists, so the fold takes the head and the
                # tail stays open. A short list makes the head empty on its own,
                # which is why neither slice needs a length check.
                "older_steps": steps[:-TIMELINE_VISIBLE],
                "recent_steps": steps[-TIMELINE_VISIBLE:],
                "activities": activities,
                "older_activities": activities[:-TIMELINE_VISIBLE],
                "recent_activities": activities[-TIMELINE_VISIBLE:],
                "older_attachments": attachments[:-TIMELINE_VISIBLE],
                "recent_attachments": attachments[-TIMELINE_VISIBLE:],
                "attachments": attachments,
                "receipt_form": ConfirmReceiptForm(),
                "remark_form": RemarkForm(),
                "route_form": RouteForm(record=record, user=request.user),
                "complete_form": CompleteForm(),
                "grant_form": GrantAccessForm(),
                "pending_offices": record.pending_receipt_offices(),
                "can_act": record.can_user_act(request.user),
                "can_confirm": record.can_user_confirm_receipt(request.user),
                "can_archive_now": can_archive_now,
                "can_reopen": can_reopen,
                # One panel carries both filing actions, so it shows when either
                # is on offer rather than repeating the condition in the markup.
                "show_filing_panel": can_archive_now or can_reopen,
                "reopen_form": ReopenForm(),
                "archived_document": archived_document,
            },
        )


class BulkConfirmReceiptView(OfficeAssignedMixin, View):
    def post(self, request):
        available = services.inbox_for(request.user).with_related().distinct()
        form = BulkConfirmReceiptForm(request.POST, queryset=available)
        if not form.is_valid():
            message = next(iter(form.errors.values()))[0] if form.errors else "Choose documents to receive."
            messages.error(request, message)
            return redirect(f"{reverse('tracking:list')}?scope=inbox")
        try:
            steps = services.bulk_confirm_receipts(
                form.cleaned_data["record_ids"], user=request.user, note=form.cleaned_data.get("note", "")
            )
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, getattr(exc, "messages", [str(exc)])[0])
            return redirect("tracking:list")
        messages.success(
            request,
            f"Receipt recorded for {len(steps)} selected document{'s' if len(steps) != 1 else ''}.",
        )
        return redirect(f"{reverse('tracking:list')}?scope=inbox")


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
            # The deadline can fail validation too, so report what actually
            # broke instead of always blaming the office selection.
            messages.error(request, "; ".join(
                f"{field}: {error}" for field, errors in form.errors.items() for error in errors
            ))
            return redirect(record.get_absolute_url())
        try:
            services.attach_files(record, form.cleaned_data.get("attachments") or [], user=request.user)
            services.route_record(
                record,
                list(form.cleaned_data["offices"]),
                user=request.user,
                instructions=form.cleaned_data.get("instructions", ""),
                action=form.cleaned_data["action"],
                due_at=form.deadline_datetime(),
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

        # "File it now" is only on offer to somebody who may also approve it.
        # For everyone else completion ends here and the record waits in the
        # pending-upload queue, which is the point of the stage: the person who
        # declared the work done is not the person who files it.
        if form.cleaned_data.get("archive_now") and record.can_user_approve_upload(request.user):
            try:
                document = services.approve_upload(record, user=request.user)
                message += " It is now searchable in the Document Repository."
                messages.success(request, message)
                return redirect(document.get_absolute_url())
            except (ValidationError, PermissionDenied) as exc:
                messages.warning(
                    request,
                    f"Completed, but approving it into the repository failed: "
                    f"{'; '.join(getattr(exc, 'messages', [str(exc)]))}",
                )
                return redirect(record.get_absolute_url())

        messages.success(
            request, f"{message} It is waiting for an administrator to approve it into the repository."
        )
        return redirect(record.get_absolute_url())


class ApproveUploadView(OfficeAssignedMixin, View):
    """Approve a finished record into the Document Repository.

    Reachable two ways, both landing here: from the record page after reviewing
    it, and straight from the pending-upload queue on the tracking list. The
    queue form posts `next` so a one-click approval returns to the queue rather
    than dropping the administrator into the document they just filed.
    """

    def post(self, request, pk):
        record = _get_record(request, pk)
        if not record.can_user_approve_upload(request.user):
            raise PermissionDenied(
                "Only an administrator for this document's office can approve it "
                "into the Document Repository."
            )
        try:
            document = services.approve_upload(record, user=request.user)
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
            return redirect(record.get_absolute_url())
        messages.success(
            request, f"{record.tracking_number} was approved into the Document Repository."
        )
        if request.POST.get("next") == "queue":
            return redirect(f"{reverse('tracking:list')}?scope={services.SCOPE_PENDING_UPLOAD}")
        return redirect(document.get_absolute_url())


class ReopenRecordView(OfficeAssignedMixin, View):
    """Send a completed-but-unfiled record back into active tracking."""

    def post(self, request, pk):
        record = _get_record(request, pk)
        if not record.can_user_reopen(request.user):
            raise PermissionDenied(
                "Only records personnel or the office that completed this document "
                "can return it to tracking."
            )
        form = ReopenForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Say briefly why the record is going back.")
            return redirect(record.get_absolute_url())
        try:
            services.reopen_record(record, user=request.user, reason=form.cleaned_data["reason"])
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect(record.get_absolute_url())
        messages.success(
            request,
            f"{record.tracking_number} is back in active tracking. The completion "
            "stays in its history.",
        )
        return redirect(record.get_absolute_url())


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
        # A paper slip leaves the system entirely, so both trails record who
        # generated one before the browser ever opens the print dialog: the
        # audit log for the administrator's view, and the record's own timeline
        # so the print shows up beside the movements it documents.
        entry = log_action(
            AuditLog.Action.PRINT,
            f"Generated the routing slip for {record.tracking_number}",
            actor=request.user,
            target=record,
            request=request,
        )
        services.log_print(record, user=request.user)
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
                # Printed on the slip so a paper copy can be matched to the
                # exact audit row that recorded it. log_action() never raises;
                # it returns an unsaved entry if the write failed, hence the pk
                # check rather than assuming there is one.
                "print_reference": entry.pk if getattr(entry, "pk", None) else None,
                "qr_svg": qr_svg(
                    f"{settings.SITE_BASE_URL}{record.get_absolute_url()}" if settings.SITE_BASE_URL
                    else request.build_absolute_uri(record.get_absolute_url()),
                    label=f"QR code for {record.tracking_number}",
                ),
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
            response = FileResponse(attachment.file.open("rb"), as_attachment=True, filename=attachment.original_name)
            response["X-Content-Type-Options"] = "nosniff"
            return response
        except FileNotFoundError as exc:
            raise Http404("The file is missing from storage.") from exc

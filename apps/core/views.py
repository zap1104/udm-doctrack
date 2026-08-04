from __future__ import annotations

import csv
from datetime import timedelta

from django.contrib import messages
from django.db.models import Count, Q
from django.db.models.functions import TruncMonth
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView, View

from apps.accounts.models import Office
from apps.documents.models import Document, SearchQueryLog
from apps.tracking import services as tracking_services
from apps.tracking.models import Status, TrackingRecord

from .forms import BootstrapFormMixin
from .mixins import AdminRequiredMixin, AppLoginRequiredMixin
from .models import AuditLog, DocumentType, MetadataFieldDefinition, Tag, TagRule


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
class DashboardView(AppLoginRequiredMixin, TemplateView):
    template_name = "core/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        inbox = tracking_services.inbox_for(user)
        custody = tracking_services.in_custody_for(user)
        in_transit = tracking_services.in_transit_from(user)
        overdue = tracking_services.overdue_for(user)
        completed = tracking_services.completed_this_year_for(user)

        attention = list(inbox[:5])
        if len(attention) < 5:
            attention += [record for record in overdue[: 5 - len(attention)] if record not in attention]
        if len(attention) < 5:
            attention += [record for record in custody[: 5 - len(attention)] if record not in attention]

        today = timezone.localdate()
        office_today = TrackingRecord.objects.none()
        if user.office_id:
            office_today = TrackingRecord.objects.visible_to(user).filter(
                routing_steps__to_office_id=user.office_id, routing_steps__received_at__date=today
            )

        forwarded_today = 0
        if user.office_id:
            forwarded_today = (
                TrackingRecord.objects.visible_to(user)
                .filter(routing_steps__sent_at__date=today, routing_steps__from_office_id=user.office_id)
                .distinct()
                .count()
            )
        completed_today = (
            TrackingRecord.objects.visible_to(user)
            .filter(status=Status.COMPLETED, completed_at__date=today)
            .distinct()
            .count()
        )

        for record in attention:
            record.can_confirm_now = record.can_user_confirm_receipt(user)

        context.update(
            {
                "inbox_count": inbox.count(),
                "inbox_new_today": inbox.filter(last_movement_at__date=today).count(),
                "custody_count": custody.count(),
                "in_transit_count": in_transit.count(),
                "overdue_count": overdue.count(),
                "completed_count": completed.count(),
                "attention_records": attention[:5],
                "recent_records": tracking_services.active_for(user)[:6],
                "recent_documents": Document.objects.visible_to(user).with_related().order_by("-created_at")[:5],
                "received_today": office_today.distinct().count(),
                "forwarded_today": forwarded_today,
                "completed_today": completed_today,
                "greeting": _greeting(),
            }
        )
        return context


def _greeting() -> str:
    hour = timezone.localtime().hour
    if hour < 12:
        return "Good morning"
    if hour < 18:
        return "Good afternoon"
    return "Good evening"


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
class ReportsView(AppLoginRequiredMixin, TemplateView):
    template_name = "reports/reports.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        records = TrackingRecord.objects.visible_to(user)
        documents = Document.objects.visible_to(user)
        since = timezone.now() - timedelta(days=90)

        by_status = list(
            records.values("status").annotate(total=Count("id", distinct=True)).order_by("-total")
        )
        by_office = list(
            records.values("originating_office__code", "originating_office__name")
            .annotate(total=Count("id", distinct=True))
            .order_by("-total")[:10]
        )
        by_type = list(
            documents.values("document_type__name").annotate(total=Count("id", distinct=True)).order_by("-total")[:10]
        )
        monthly = list(
            records.filter(created_at__gte=since)
            .annotate(month=TruncMonth("created_at"))
            .values("month")
            .annotate(total=Count("id", distinct=True))
            .order_by("month")
        )
        max_monthly = max([row["total"] for row in monthly], default=0) or 1
        for row in monthly:
            row["percent"] = int(round(100 * row["total"] / max_monthly))

        context.update(
            {
                "total_records": records.distinct().count(),
                "total_documents": documents.distinct().count(),
                "overdue": tracking_services.overdue_for(user).count(),
                "awaiting_receipt": records.filter(routing_steps__received_at__isnull=True)
                .exclude(status=Status.COMPLETED)
                .distinct()
                .count(),
                "by_status": by_status,
                "by_office": by_office,
                "by_type": by_type,
                "monthly": monthly,
                "top_searches": (
                    SearchQueryLog.objects.values("query")
                    .annotate(total=Count("id"))
                    .order_by("-total")[:10]
                ),
                "untagged_documents": documents.filter(tags__isnull=True).distinct().count(),
                "documents_without_text": documents.filter(ocr_text="").distinct().count(),
            }
        )
        return context


class ReportExportView(AppLoginRequiredMixin, View):
    """CSV of the active tracking queue — useful evidence for the defence."""

    def get(self, request):
        response = HttpResponse(content_type="text/csv")
        stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
        response["Content-Disposition"] = f'attachment; filename="doctrack-records-{stamp}.csv"'
        writer = csv.writer(response)
        writer.writerow(
            ["Tracking number", "Subject", "Type", "Originating office", "Current office",
             "Status", "Created", "Last movement", "Completed"]
        )
        for record in TrackingRecord.objects.visible_to(request.user).with_related().order_by("-created_at")[:5000]:
            writer.writerow(
                [
                    record.tracking_number,
                    record.subject,
                    record.document_type.name if record.document_type_id else "",
                    record.originating_office.code,
                    record.current_office.code if record.current_office_id else "",
                    record.display_status_label,
                    timezone.localtime(record.created_at).strftime("%Y-%m-%d %H:%M"),
                    timezone.localtime(record.last_movement_at).strftime("%Y-%m-%d %H:%M"),
                    timezone.localtime(record.completed_at).strftime("%Y-%m-%d %H:%M") if record.completed_at else "",
                ]
            )
        return response


# ---------------------------------------------------------------------------
# Administration — master data
# ---------------------------------------------------------------------------
def _model_form(model_class, field_names):
    from django import forms

    return type(
        f"{model_class.__name__}Form",
        (BootstrapFormMixin, forms.ModelForm),
        {"Meta": type("Meta", (), {"model": model_class, "fields": field_names})},
    )


MASTER_DATA = {
    "document-types": {
        "model": DocumentType,
        "label": "Document types",
        "singular": "document type",
        "fields": ["code", "name", "description", "retention_years", "sort_order", "is_active"],
        "columns": [("name", "Name"), ("code", "Code"), ("retention_years", "Retention (years)"), ("is_active", "Active")],
        "help": "Memorandum, letter, work order… Types drive filing, filters and search weighting.",
    },
    "tags": {
        "model": Tag,
        "label": "Tags",
        "singular": "tag",
        "fields": ["name", "category", "description", "is_active"],
        "columns": [("name", "Tag"), ("category", "Category"), ("usage_count", "Used on"), ("is_active", "Active")],
        "help": "Shared vocabulary for filing. Keep tags short and lower-case.",
    },
    "metadata-rules": {
        "model": TagRule,
        "label": "Metadata rules",
        "singular": "metadata rule",
        "fields": [
            "name", "pattern", "match_type", "search_field", "suggest_tag", "suggest_document_type",
            "suggest_office", "suggest_metadata_key", "suggest_metadata_value", "confidence", "priority", "is_active",
        ],
        "columns": [("name", "Rule"), ("match_type", "Match"), ("pattern", "Pattern"), ("priority", "Priority"), ("is_active", "Active")],
        "help": "When extracted text matches a pattern, the system proposes this tag, type or office. "
                "Rules are how the archive gets smarter without any AI training.",
    },
    "metadata-fields": {
        "model": MetadataFieldDefinition,
        "label": "Metadata fields",
        "singular": "metadata field",
        "fields": [
            "key", "label", "field_type", "choices_csv", "help_text", "is_required",
            "is_searchable", "show_in_list", "sort_order", "is_active",
        ],
        "columns": [("label", "Field"), ("key", "Key"), ("field_type", "Type"), ("is_required", "Required"), ("is_searchable", "Searchable")],
        "help": "Add a field here and it appears on every metadata review screen — no code change needed.",
    },
}


class AdministrationHomeView(AdminRequiredMixin, TemplateView):
    template_name = "administration/home.html"

    def get_context_data(self, **kwargs):
        from apps.accounts.models import User

        context = super().get_context_data(**kwargs)
        context.update(
            {
                "user_count": User.objects.filter(is_active=True).count(),
                "office_count": Office.objects.filter(is_active=True).count(),
                "type_count": DocumentType.objects.filter(is_active=True).count(),
                "tag_count": Tag.objects.filter(is_active=True).count(),
                "rule_count": TagRule.objects.filter(is_active=True).count(),
                "field_count": MetadataFieldDefinition.objects.filter(is_active=True).count(),
                "recent_audit": AuditLog.objects.all()[:10],
                "master_data": MASTER_DATA,
            }
        )
        return context


class MasterDataListView(AdminRequiredMixin, View):
    template_name = "administration/masterdata_list.html"

    def get(self, request, slug):
        config = MASTER_DATA.get(slug)
        if not config:
            raise Http404("Unknown master data section")
        objects = config["model"].objects.all()
        query = request.GET.get("q", "").strip()
        if query:
            first_field = config["columns"][0][0]
            objects = objects.filter(**{f"{first_field}__icontains": query})
        return render(
            request,
            self.template_name,
            {"slug": slug, "config": config, "objects": objects, "query": query, "master_data": MASTER_DATA},
        )


class MasterDataEditView(AdminRequiredMixin, View):
    template_name = "administration/masterdata_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.config = MASTER_DATA.get(kwargs.get("slug"))
        if not self.config:
            raise Http404("Unknown master data section")
        return super().dispatch(request, *args, **kwargs)

    def _instance(self, pk):
        return get_object_or_404(self.config["model"], pk=pk) if pk else None

    def get(self, request, slug, pk=None):
        form_class = _model_form(self.config["model"], self.config["fields"])
        form = form_class(instance=self._instance(pk))
        return render(
            request,
            self.template_name,
            {"slug": slug, "config": self.config, "form": form, "object": self._instance(pk), "master_data": MASTER_DATA},
        )

    def post(self, request, slug, pk=None):
        instance = self._instance(pk)
        form_class = _model_form(self.config["model"], self.config["fields"])
        form = form_class(request.POST, instance=instance)
        if form.is_valid():
            obj = form.save()
            from .utils import log_action

            log_action(
                AuditLog.Action.UPDATE if pk else AuditLog.Action.CREATE,
                f"{'Updated' if pk else 'Created'} {self.config['singular']} “{obj}”",
                actor=request.user,
                target=obj,
                request=request,
            )
            messages.success(request, f"Saved {self.config['singular']} “{obj}”.")
            return redirect(reverse("core:masterdata_list", args=[slug]))
        messages.error(request, "Check the highlighted fields.")
        return render(
            request,
            self.template_name,
            {"slug": slug, "config": self.config, "form": form, "object": instance, "master_data": MASTER_DATA},
        )


class AuditLogView(AdminRequiredMixin, TemplateView):
    template_name = "administration/audit_log.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        entries = AuditLog.objects.select_related("actor")
        action = self.request.GET.get("action", "")
        query = self.request.GET.get("q", "").strip()
        if action:
            entries = entries.filter(action=action)
        if query:
            entries = entries.filter(Q(summary__icontains=query) | Q(actor_label__icontains=query))
        context.update(
            {
                "entries": entries[:300],
                "actions": AuditLog.Action.choices,
                "selected_action": action,
                "query": query,
                "master_data": MASTER_DATA,
            }
        )
        return context


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
def error_403(request, exception=None):
    return render(request, "errors/403.html", {"reason": str(exception) if exception else ""}, status=403)


def error_404(request, exception=None):
    return render(request, "errors/404.html", status=404)


def error_500(request):
    return render(request, "errors/500.html", status=500)

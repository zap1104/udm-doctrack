"""External repository browsing, upload and reporting share permission rules."""

import csv

from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views import View

from apps.core.document_types import document_type_match
from apps.core.mixins import AdminRequiredMixin, AppLoginRequiredMixin
from apps.core.pagination import paginate
from apps.core.upload_forms import RecoverableUploadMixin, UploadFormRecovery
from apps.search.services import search_documents

from .external_forms import ExternalRepositoryFilterForm
from .models import Collection, Document
from .views import DeletionQueueView


def external_documents(request):
    """Validate filters against authorized choices before counting or searching."""
    visible = Document.objects.visible_to(request.user).in_collection(Collection.EXTERNAL).filter(is_active=True)
    form = ExternalRepositoryFilterForm(request.GET, user=request.user, visible=visible)
    if not form.is_valid():
        return form, visible.none(), visible
    data = form.cleaned_data
    documents = visible
    for key in ("office", "year", "source", "tag"):
        if data.get(key):
            documents = documents.filter(**{"tags" if key == "tag" else key: data[key]})
    if data.get("external_office"):
        documents = documents.filter(external_office_links__external_office=data["external_office"])
    if data.get("document_type"):
        documents = documents.filter(document_type_match(data["document_type"]))
    if data.get("date_from"):
        documents = documents.filter(document_date__gte=data["date_from"])
    if data.get("date_to"):
        documents = documents.filter(document_date__lte=data["date_to"])
    if data.get("retention") == "due":
        documents = documents.due_for_retention_review()
    elif data.get("retention") == "soon":
        documents = documents.upcoming_deletion_review()
    elif data.get("retention") == "unscheduled":
        documents = documents.filter(retention_until__isnull=True)
    documents = documents.distinct()
    if data.get("q"):
        result = search_documents(user=request.user, collection=Collection.EXTERNAL, query=data["q"], base_documents=documents, exhaustive=True, show_below_threshold=True, log=False)
        documents = documents.filter(pk__in=[row.document.pk for row in result.results])
    return form, documents, visible


class ExternalRepositoryView(AppLoginRequiredMixin, View):
    def get(self, request):
        form, documents, visible = external_documents(request)
        context = paginate(
            request,
            documents.with_related().annotate(file_count=Count("files", distinct=True)).order_by("-document_date", "-pk"),
        )
        context.update(form=form, documents=context["page_obj"].object_list, total=context["page_obj"].paginator.count,
                       all_count=visible.count(), can_upload=request.user.can_start_work and request.user.is_office_admin)
        return render(request, "documents/external/repository.html", context)


class ExternalUploadView(RecoverableUploadMixin, AdminRequiredMixin, View):
    def get(self, request):
        return render(request, "documents/external/upload.html", {"form": UploadFormRecovery(request, "external-repository-upload").form})

    def post(self, request):
        recovery = UploadFormRecovery(request, "external-repository-upload")
        response = recovery.validation_response()
        if response:
            return response
        result = recovery.finish()
        if result is None:
            return recovery.response
        return redirect(result.redirect)


class ExternalRepositoryReportView(AppLoginRequiredMixin, View):
    def get(self, request):
        form, documents, visible = external_documents(request)
        if request.GET.get("export") == "csv":
            response = HttpResponse(content_type="text/csv; charset=utf-8")
            response["Content-Disposition"] = 'attachment; filename="external-repository.csv"'
            writer = csv.writer(response)
            writer.writerow(["Reference", "Title", "Managing office", "Source", "Year", "External counterparts"])
            for document in documents.with_related().prefetch_related("external_office_links").order_by("pk"):
                values = [document.reference_number, document.title, document.office.name, document.get_source_display(), str(document.year), "; ".join(link.name_snapshot for link in document.external_office_links.all())]
                writer.writerow(["'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value for value in values])
            return response
        return render(request, "documents/external/report.html", {
            "form": form, "total": documents.count(), "all_count": visible.count(),
            "sources": documents.order_by().values("source").annotate(total=Count("pk", distinct=True)),
            "offices": documents.order_by("office__name").values("office__name").annotate(total=Count("pk", distinct=True)),
            "due_count": documents.due_for_retention_review().count(),
        })


class ExternalDeletionQueueView(DeletionQueueView):
    collection = Collection.EXTERNAL

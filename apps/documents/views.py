from __future__ import annotations

from dataclasses import replace

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.db.models.functions import ExtractYear
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.generic import View

from apps.accounts.models import Office
from apps.core import filters as core_filters
from apps.core.document_types import document_type_counts, document_type_match, document_type_options
from apps.core.mixins import AdminRequiredMixin, AppLoginRequiredMixin, OfficeAssignedMixin
from apps.core.models import AuditLog, Tag
from apps.core.pagination import DEFAULT_PAGE_SIZE, paginate
from apps.core.upload_forms import RecoverableUploadMixin, UploadFormRecovery
from apps.core.utils import log_action, unsign_download
from apps.search.services import search_documents
from apps.tracking.services import pending_upload_for

from . import services
from .forms import (
    AddFilesForm,
    DeletionQueueForm,
    DeletionReviewForm,
    DocumentMetadataForm,
    RepositoryFilterForm,
)
from .models import COMPLETED_SOURCE, HISTORICAL_FILTER, Collection, Document, DocumentFile, OcrStatus
from .suggestions import Suggestion

#: Documents per page when the reader has not asked for another size. The
#: shared default rather than a grid-friendly 24: the size control offers one
#: set of sizes across the whole app, and a default outside that set would
#: leave every option looking unselected here.
PAGE_SIZE = DEFAULT_PAGE_SIZE

#: Rows of the pending-filing queue shown before it collapses to a count. It is
#: a to-do list that should be worked down, not another table to page through.
#: Rows of the retention-review queue shown before it collapses to a link.
#: Was PENDING_FILING_SHOWN, which also sized the completed-but-unapproved
#: queue until that moved to the Document Tracking page.
RETENTION_DUE_SHOWN = 8


def _get_document(request, pk) -> Document:
    documents = Document.objects.filter(is_active=True).with_related()
    if request.user.is_office_admin and not request.user.is_system_admin:
        documents = documents.visible_to(request.user)
    document = get_object_or_404(documents, pk=pk)
    if not document.can_user_view(request.user):
        raise PermissionDenied(
            "This record belongs to another office and has not been shared with your account."
        )
    return document


class RepositoryView(AppLoginRequiredMixin, View):
    """Completed records, historical uploads and smart folders."""

    template_name = "documents/repository.html"

    @staticmethod
    def _filter_documents(documents, data, *, omit=None, search_ids=None):
        """A counted facet omits only the filter its link replaces."""
        data = {key: value for key, value in data.items() if key != omit}
        if search_ids is not None:
            documents = documents.filter(pk__in=search_ids)
        elif data.get("q"):
            query = data["q"]
            documents = documents.filter(
                Q(title__icontains=query) | Q(reference_number__icontains=query)
                | Q(index_meta__icontains=query) | Q(ocr_text__icontains=query)
            )
        for key, field in (("year", "year"), ("month", "document_date__month"), ("tag", "tags")):
            if data.get(key):
                documents = documents.filter(**{field: data[key]})
        if data.get("date_from"):
            documents = documents.filter(document_date__gte=data["date_from"])
        if data.get("date_to"):
            documents = documents.filter(document_date__lte=data["date_to"])
        if data.get("document_type"):
            documents = documents.filter(document_type=data["document_type"]) if data.get("type_scope") == "main" else documents.filter(document_type_match(data["document_type"]))
        if data.get("source") == HISTORICAL_FILTER:
            documents = documents.exclude(source=COMPLETED_SOURCE)
        elif data.get("source"):
            documents = documents.filter(source=data["source"])
        today = timezone.localdate()
        if data.get("retention") == "due":
            documents = documents.due_for_retention_review(today)
        elif data.get("retention") == "soon":
            documents = documents.upcoming_deletion_review(today)
        elif data.get("retention") == "unscheduled":
            documents = documents.filter(retention_until__isnull=True)
        return documents.distinct()

    @staticmethod
    def _filter_pending(records, data, *, omit=None):
        data = {key: value for key, value in data.items() if key != omit}
        if data.get("q"):
            records = records.filter(Q(subject__icontains=data["q"]) | Q(tracking_number__icontains=data["q"]))
        for key, field in (("year", "completed_at__year"), ("month", "completed_at__month")):
            if data.get(key):
                records = records.filter(**{field: data[key]})
        if data.get("document_type"):
            records = records.filter(document_type=data["document_type"]) if data.get("type_scope") == "main" else records.filter(document_type_match(data["document_type"]))
        return records.distinct()

    @staticmethod
    def _options(visible):
        """The filter choices that can actually return one of `visible`.

        Built from the unfiltered visible set, not from what is on screen: an
        option list that narrowed as you filtered would take away the control
        you needed to widen the search again.
        """
        return {
            "years": sorted(
                {value for value in visible.values_list("year", flat=True) if value}, reverse=True
            ),
            "months": {
                value
                for value in visible.values_list("document_date__month", flat=True)
                if value
            },
            # Retiring a type prevents new filing under it; existing records
            # must remain filterable and their type badges must still open them.
            "document_types": document_type_options(visible),
            # Most-used first: with a shared vocabulary the useful tags are the
            # common ones, and alphabetical order buries them under one-offs.
            "tags": Tag.active.filter(documents__in=visible).annotate(
                visible_usage=Count("documents", filter=Q(documents__in=visible), distinct=True)
            ).order_by("-visible_usage", "name"),
            "sources": set(visible.values_list("source", flat=True).distinct()),
        }

    def get(self, request):
        documents = Document.objects.visible_to(request.user).in_collection().filter(is_active=True).with_related()
        visible = Document.objects.visible_to(request.user).in_collection().filter(is_active=True)
        pending_visible = pending_upload_for(request.user)
        if request.user.is_office_admin and not request.user.is_system_admin:
            pending_visible = pending_visible.filter(originating_office_id=request.user.office_id) if request.user.office_id else pending_visible.none()
        pending_view = request.GET.get("view") == "pending"
        folders_view = request.GET.get("view") == "folders"
        # Through the shared resolver, so `office` is a primary key here as it
        # is on every other page. It read a *code*, from `Office.objects` rather
        # than `Office.active` — so an archived office went on filtering — and an
        # unmatched value fell through to no filter at all, showing the whole
        # university under one office's heading. A code still resolves, because
        # the smart folders have been emitting them and people have bookmarked
        # those links; what has changed is that a value matching nothing is
        # reported instead of ignored.
        # Ungated: on this page `?office=` is a content filter over documents
        # the reader may already see, and the smart folders above the list are
        # office links. Gated, every folder rendered and none of them filtered
        # for anybody who was not an administrator.
        resolved = core_filters.resolve(request, allow_office=True, gate_office=False)
        selected_office = resolved.as_office
        folder_office_id = selected_office.pk if selected_office else None
        allowed_offices = set(visible.values_list("office_id", flat=True)) | set(pending_visible.values_list("originating_office_id", flat=True))
        denied_folder = selected_office is not None and selected_office.pk not in allowed_offices
        if denied_folder:
            selected_office = None
            documents = documents.none()
            messages.warning(request, "This folder has no documents available to your account.")
        if selected_office:
            documents = documents.filter(office=selected_office)
        elif "office" in resolved.invalid:
            messages.warning(
                request,
                "That office was not recognised. The office filter was not applied.",
            )

        folder_pending = pending_visible
        if denied_folder:
            folder_pending = folder_pending.none()
        elif selected_office:
            folder_pending = folder_pending.filter(originating_office=selected_office)
        options = self._options(documents)
        if pending_view:
            options.update(
                years=sorted({year for year in folder_pending.annotate(completion_year=ExtractYear("completed_at")).values_list("completion_year", flat=True) if year}, reverse=True),
                months=set(folder_pending.values_list("completed_at__month", flat=True)),
                document_types=document_type_options(folder_pending),
                tags=Tag.active.none(), sources=set(),
            )
        params = request.GET.copy()
        if folder_office_id:
            params["office"] = str(folder_office_id)
        elif "office" in params:
            params.pop("office")
        options["offices"] = Office.objects.filter(pk__in=allowed_offices).order_by("name")
        form = RepositoryFilterForm(params or None, **options)
        if pending_view:
            form.fields["q"].widget.attrs["placeholder"] = "Search subject or tracking number…"
            filed_only = ("tag", "source", "retention", "date_from", "date_to", "min_relevance", "show_all")
            for field in filed_only:
                form.fields.pop(field)
            if any(request.GET.get(field) for field in filed_only):
                messages.warning(request, "Tags, origin, document dates, relevance, and deletion review apply only to filed documents. They were ignored for records awaiting filing.")

        # Apply every filter that validated, not the all-or-nothing case. The
        # whole block used to hang off `if form.is_valid()`, so one unrecognised
        # value — a stale bookmark, a tag since deleted — silently dropped
        # *every* filter and returned the entire repository while the controls
        # still showed a narrow search. Same fault the tracking list had.
        form.is_valid()
        data = getattr(form, "cleaned_data", {})

        folder_documents = documents
        search_ids = None
        repository_search = None
        if not pending_view and data.get("q"):
            # Score every accessible match once. Metadata filters and counted
            # facets then narrow the same scored IDs, so a type/tag/deletion
            # count never uses a different substring search or a top-N cutoff.
            scored = search_documents(
                user=request.user, query=data["q"],
                base_documents=folder_documents.annotate(file_count=Count("files", distinct=True)),
                min_relevance=0, show_below_threshold=True, exhaustive=True, log=False,
            )
            matching_ids = set(self._filter_documents(
                folder_documents, data, search_ids=[result.document.pk for result in scored.results],
            ).values_list("pk", flat=True))
            qualifying = [result for result in scored.results if data.get("show_all") or result.relevance >= data.get("min_relevance", form.fields["min_relevance"].initial)]
            search_ids = [result.document.pk for result in qualifying]
            filtered_results = [result for result in qualifying if result.document.pk in matching_ids]
            repository_search = replace(
                scored, results=filtered_results, total_matches=len(matching_ids),
                evaluated_count=len(matching_ids), hidden_count=len(matching_ids) - len(filtered_results),
                result_limit=len(matching_ids),
            )
            documents = []
            for result in filtered_results:
                document = result.document
                document.relevance = result.relevance
                document.snippet_html = result.snippet_html
                document.matched_in = result.matched_in
                documents.append(document)
        else:
            documents = self._filter_documents(folder_documents, data)
        today = timezone.localdate()

        if form.errors:
            messages.warning(
                request,
                "Ignored a filter that no longer applies here: "
                + ", ".join(sorted(form.errors))
                + ". Showing the rest.",
            )

        if repository_search is None:
            documents = documents.annotate(file_count=Count("files", distinct=True)).distinct().order_by("-document_date", "-created_at", "-pk")
        # `?per_page=` decides how many cards a page carries. See
        # apps/core/pagination.py.
        page_context = paginate(request, documents, PAGE_SIZE)
        page = page_context["page_obj"]

        # This is a separate queue, not part of the filed-document total.
        # Start with the tracking visibility rule before counts or filters.
        pending = folder_pending
        pending_count = pending.count()
        if pending_view:
            pending = self._filter_pending(pending, data)
        pending = pending.annotate(file_count=Count("file_groups", filter=Q(file_groups__discarded_at__isnull=True), distinct=True)).order_by("completed_at", "pk")
        if pending_view:
            page_context = paginate(request, pending, PAGE_SIZE)
            page = page_context["page_obj"]
            pending_rows = list(page.object_list)
        else:
            pending_rows = list(pending[:2])
        for record in pending_rows:
            record.can_approve = record.can_user_approve_upload(request.user)

        years = sorted({value for value in visible.values_list("year", flat=True) if value}, reverse=True)
        smart_folders = list(
            # office__id so the folder links can carry a primary key, which is
            # what `office` means everywhere else; the code stays for the active
            # check and the title.
            visible.values("office__id", "office__code", "office__name")
            .annotate(total=Count("id", distinct=True))
            .order_by("office__name")
        )
        base_url = reverse("documents:repository")
        folder_map = {folder["office__id"]: folder for folder in smart_folders}
        for entry in pending_visible.order_by().values("originating_office_id", "originating_office__code", "originating_office__name").annotate(total=Count("pk", distinct=True)):
            office_id = entry["originating_office_id"]
            folder = folder_map.setdefault(office_id, {
                "office__id": office_id, "office__code": entry["originating_office__code"],
                "office__name": entry["originating_office__name"], "total": 0,
            })
            folder["pending_count"] = entry["total"]
        smart_folders = sorted(folder_map.values(), key=lambda folder: folder["office__name"])
        for folder in smart_folders:
            # Badges count whole folders, so opening one clears list filters.
            folder["url"] = core_filters.link(base_url, office=folder["office__id"])
        type_base = self._filter_pending(folder_pending, data, omit="document_type") if pending_view else self._filter_documents(folder_documents, data, omit="document_type", search_ids=search_ids)
        type_folders = [{"document_type_id": row["pk"], "document_type__name": row["name"], "total": row["total"]}
                        for row in document_type_counts(type_base).order_by("name")]
        # Unclassified remains visible in the list; only selectable types become folders.
        type_folders = [folder for folder in type_folders if folder["document_type_id"]]
        for folder in type_folders:
            folder["url"] = core_filters.link(base_url, request, document_type=folder["document_type_id"], type_scope=None, page=None)
            folder["selected"] = bool(data.get("type_scope") != "main" and data.get("document_type") and data["document_type"].pk == folder["document_type_id"])

        # Retention reviews cover only filed documents in the selected folder.
        retention_base = self._filter_documents(folder_documents, data, omit="retention", search_ids=search_ids)
        retention_due_query = retention_base.due_for_retention_review(today).with_related().order_by("retention_until")
        retention_due_count = retention_due_query.count()
        retention_due = list(retention_due_query[:RETENTION_DUE_SHOWN])
        tag_base = self._filter_documents(folder_documents, data, omit="tag", search_ids=search_ids)
        popular_tags = Tag.active.filter(documents__in=tag_base).annotate(
            visible_usage=Count("documents", filter=Q(documents__in=tag_base), distinct=True)
        ).order_by("-visible_usage", "name")[:8]
        # Keep a denied folder in tab/form URLs: dropping it would turn a
        # zero-count tab into a link to every accessible record instead.
        folder_params = {"office": folder_office_id}
        selected_filters = []
        filter_labels = {"q": "Search", "document_type": "Type", "year": "Year", "month": "Month", "tag": "Tag", "source": "Origin", "date_from": "From", "date_to": "To", "retention": "Deletion review"}
        if data.get("type_scope") == "main":
            filter_labels["document_type"] = "Main type"
        for name, label in filter_labels.items():
            value = data.get(name)
            if value:
                choices = dict(form.fields[name].choices) if hasattr(form.fields[name], "choices") and name not in {"document_type", "tag"} else {}
                cleared = {name: None, "page": None}
                if name == "document_type":
                    cleared["type_scope"] = None
                selected_filters.append({"label": label, "value": choices.get(value, choices.get(str(value), str(value))), "url": core_filters.link(base_url, request, **cleared)})

        return render(
            request,
            self.template_name,
            {
                "form": form,
                "selected_filters": selected_filters,
                "more_filters_open": bool(any(data.get(name) for name in ("tag", "source", "retention", "date_from", "date_to")) or data.get("q") or form.errors),
                "repository_search": repository_search,
                **page_context,
                "documents": [] if pending_view else page.object_list,
                "pending_view": pending_view,
                "folders_view": folders_view,
                "folders_url": core_filters.link(base_url, request, view="folders", page=None),
                "pending_upload": pending_rows,
                "pending_count": pending_count,
                "pending_all_count": pending_visible.count(),
                "pending_url": core_filters.link(base_url, **folder_params, view="pending"),
                "filed_url": core_filters.link(base_url, **folder_params),
                "all_folders_url": base_url,
                "folder_reset_url": core_filters.link(base_url, request, q=None, document_type=None, type_scope=None, tag=None, source=None, year=None, month=None, retention=None, date_from=None, date_to=None, min_relevance=None, show_all=None, page=None),
                "type_folders": type_folders,
                "filed_count": folder_documents.count(),
                # Hides the create/upload button from the accounts the
                # target view would turn away. The view still refuses
                # them on its own; this only stops offering a dead end.
                "can_start_work": request.user.can_start_work,
                "smart_folders": smart_folders,
                "selected_office": selected_office,
                "folder_office_id": folder_office_id,
                "folder_unavailable": denied_folder,
                # The paginator has already counted this queryset; .count()
                # would run the same DISTINCT-over-joins query a second time on
                # every page load.
                "total": page.paginator.count,
                "all_count": visible.count(),
                # Settings-driven so the team can settle the number later
                # without touching a template. See REPOSITORY_FOLDER_COLUMNS.
                "folder_columns": settings.REPOSITORY_FOLDER_COLUMNS,
                "retention_due": retention_due,
                "retention_due_count": retention_due_count,
                "retention_due_more": max(0, retention_due_count - len(retention_due)),
                "years": years,
                "popular_tags": popular_tags,
            },
        )


class UploadView(RecoverableUploadMixin, OfficeAssignedMixin, View):
    """Upload or scan — step 1. Text is extracted immediately, then reviewed."""

    template_name = "documents/upload.html"

    def get(self, request):
        return render(request, self.template_name, {"form": UploadFormRecovery(request, "repository-upload").form})

    def post(self, request):
        recovery = UploadFormRecovery(request, "repository-upload")
        response = recovery.validation_response()
        if response:
            return response
        result = recovery.finish()
        if result is None:
            return recovery.response
        document = Document.objects.get(pk=result.parent_id)
        checksums = recovery.draft.files.exclude(state="removed").values("checksum")
        duplicate_file = DocumentFile.objects.filter(checksum__in=checksums,
            document__in=Document.objects.visible_to(request.user).in_collection(Collection.OVPA)).exclude(document=document).select_related("document").first()
        duplicate = duplicate_file.document if duplicate_file else None
        if duplicate:
            messages.warning(
                request,
                f"The same file is already archived as “{duplicate.title}”. Continue only if this copy is different.",
            )
        suggestion = document.suggestions.first()
        request.session[f"suggestion_{document.pk}"] = suggestion.suggested if suggestion else Suggestion().as_dict()
        return redirect("documents:review", pk=document.pk)


class MetadataReviewView(OfficeAssignedMixin, View):
    """Upload or scan — step 2. The system suggests; the uploader decides."""

    template_name = "documents/review.html"

    def _suggestion(self, request, document) -> dict:
        latest = document.suggestions.first()
        if latest:
            return latest.suggested
        stored = request.session.get(f"suggestion_{document.pk}")
        if stored:
            return stored
        return Suggestion().as_dict()

    def _initial(self, document, suggestion) -> dict:
        main_type = document.document_type_id if len(document.selected_document_types) > 1 else (
            suggestion.get("document_type_id") or document.document_type_id
        )
        return {
            "title": suggestion.get("title") or document.title,
            "description": suggestion.get("subject", ""),
            "office": document.office_id if document.is_external else (suggestion.get("office_id") or document.office_id),
            "document_type": main_type,
            "document_date": suggestion.get("document_date") or None,
            "year": document.year,
            "reference_number": suggestion.get("reference_number", ""),
            "author_name": suggestion.get("author_name", ""),
            "recipient_name": suggestion.get("recipient_name", ""),
            "access_level": document.access_level,
            "ocr_language": document.ocr_language,
            "allow_external_ocr": document.allow_external_ocr,
            "retention_until": document.retention_until,
            "tags": ", ".join(suggestion.get("tags", [])),
            **{f"meta_{key}": value for key, value in (suggestion.get("metadata") or {}).items()},
        }

    def get(self, request, pk):
        document = _get_document(request, pk)
        if not document.can_user_edit(request.user):
            raise PermissionDenied("Only the owning office can edit this record.")
        suggestion = self._suggestion(request, document)
        form = DocumentMetadataForm(
            instance=document, initial=self._initial(document, suggestion), user=request.user
        )
        return render(
            request,
            self.template_name,
            {
                "document": document,
                "is_extraction_pending": document.ocr_status in {"PENDING", "RUNNING"},
                "form": form,
                "suggestion": suggestion,
                "confidence": suggestion.get("confidence", {}),
                "text_preview": (document.ocr_text or "")[:4000],
                "all_tags": services.suggested_tags_for(request.user, collection=document.collection)[:50],
            },
        )

    def post(self, request, pk):
        document = _get_document(request, pk)
        if not document.can_user_edit(request.user):
            raise PermissionDenied("Only the owning office can edit this record.")
        suggestion = self._suggestion(request, document)
        form = DocumentMetadataForm(request.POST, instance=document, user=request.user)
        if not form.is_valid():
            messages.error(request, "Check the highlighted fields.")
            return render(
                request,
                self.template_name,
                {
                    "document": document,
                    "is_extraction_pending": document.ocr_status in {"PENDING", "RUNNING"},
                    "form": form,
                    "suggestion": suggestion,
                    "confidence": suggestion.get("confidence", {}),
                    "text_preview": (document.ocr_text or "")[:4000],
                    "all_tags": services.suggested_tags_for(request.user, collection=document.collection)[:50],
                },
            )

        data = form.cleaned_data
        if "retention_until" not in request.POST:
            data.pop("retention_until", None)
        accepted = {
            "title": data["title"],
            "document_type_id": data["document_type"].pk if data.get("document_type") else None,
            "document_type_ids": [kind.pk for kind in data.get("document_types", [])],
            "office_id": data["office"].pk if data.get("office") else None,
            "document_date": data["document_date"].isoformat() if data.get("document_date") else "",
            "reference_number": data.get("reference_number", ""),
            "author_name": data.get("author_name", ""),
            "recipient_name": data.get("recipient_name", ""),
            "tags": data.get("tags", []),
            "metadata": form.metadata_cleaned(),
        }
        services.save_document_metadata(
            document,
            user=request.user,
            data=data,
            tag_names=data.get("tags", []),
            metadata_values=form.metadata_cleaned(),
            accepted_from_suggestion=accepted,
        )
        request.session.pop(f"suggestion_{document.pk}", None)
        messages.success(request, "Saved to the repository. It is searchable straight away.")
        return redirect(document.get_absolute_url())


class DocumentDetailView(AppLoginRequiredMixin, View):
    template_name = "documents/detail.html"

    def get(self, request, pk):
        document = _get_document(request, pk)
        from apps.core.file_history import history_url, retained_groups
        from apps.tracking.version_views import version_rows

        file_rows = []
        for file in retained_groups(document.files.all()):
            versions = file.retained_versions
            if versions:
                file_rows.append({"current": version_rows(versions[:1], "repository", request.user)[0], "history": version_rows(versions[1:], "repository", request.user), "page_count": file.page_count, "audit_count": file.audit_total, "audit_url": history_url(file)})
        return render(
            request,
            self.template_name,
            {
                "document": document,
                "files": document.files.all(),
                "file_rows": file_rows,
                "metadata_values": document.metadata_values.select_related("field"),
                "suggestion": document.suggestions.first(),
                "add_files_form": AddFilesForm(),
                "can_edit": document.can_user_edit(request.user),
                "can_review_deletion": document.can_user_review_deletion(request.user),
                "deletion_reviews": document.deletion_reviews.select_related("reviewed_by")[:20],
                "related": Document.objects.visible_to(request.user)
                .in_collection(document.collection).filter(office=document.office, is_active=True)
                .exclude(pk=document.pk)
                .order_by("-created_at")[:5],
            },
        )


class DocumentEditView(OfficeAssignedMixin, View):
    template_name = "documents/edit.html"

    def get(self, request, pk):
        document = _get_document(request, pk)
        if not document.can_user_edit(request.user):
            raise PermissionDenied("Only the owning office can edit this record.")
        initial = {
            "tags": ", ".join(document.tag_names),
            **{
                f"meta_{value.field.key}": value.value
                for value in document.metadata_values.select_related("field")
            },
        }
        form = DocumentMetadataForm(instance=document, initial=initial, user=request.user)
        return render(request, self.template_name, {"document": document, "form": form})

    def post(self, request, pk):
        document = _get_document(request, pk)
        if not document.can_user_edit(request.user):
            raise PermissionDenied("Only the owning office can edit this record.")
        form = DocumentMetadataForm(request.POST, instance=document, user=request.user)
        if not form.is_valid():
            messages.error(request, "Check the highlighted fields.")
            return render(request, self.template_name, {"document": document, "form": form})
        services.save_document_metadata(
            document,
            user=request.user,
            data={key: value for key, value in form.cleaned_data.items()
                  if key != "retention_until" or "retention_until" in request.POST},
            tag_names=form.cleaned_data.get("tags", []),
            metadata_values=form.metadata_cleaned(),
        )
        messages.success(request, "Metadata updated and re-indexed.")
        return redirect(document.get_absolute_url())


class AddFilesView(OfficeAssignedMixin, View):
    def post(self, request, pk):
        _get_document(request, pk)
        raise PermissionDenied("Repository file contents are locked. Metadata corrections remain available.")


class DocumentFileDownloadView(AppLoginRequiredMixin, View):
    def get(self, request, pk):
        files = DocumentFile.objects.select_related("document")
        if request.user.is_office_admin and not request.user.is_system_admin:
            files = files.filter(document__in=Document.objects.visible_to(request.user))
        document_file = get_object_or_404(files, pk=pk)
        if not document_file.document.can_user_view(request.user):
            raise PermissionDenied("You do not have access to this document.")
        if unsign_download(request.GET.get("token", "")) != f"document-file:{document_file.pk}:{request.user.pk}":
            raise PermissionDenied("This download link has expired or is invalid. Open the document to get a new link.")
        log_action(
            AuditLog.Action.DOWNLOAD,
            f"Downloaded {document_file.original_name}",
            actor=request.user,
            target=document_file.document,
            request=request,
        )
        try:
            response = FileResponse(
                document_file.file.open("rb"), as_attachment=True, filename=document_file.original_name
            )
            response["X-Content-Type-Options"] = "nosniff"
            response["Cache-Control"] = "private, no-store"
            return response
        except FileNotFoundError as exc:
            raise Http404("The file is missing from storage.") from exc


class ReExtractView(OfficeAssignedMixin, View):
    """Run text extraction again — useful after adding an OCR key."""

    def post(self, request, pk):
        document = _get_document(request, pk)
        if not document.can_user_edit(request.user):
            raise PermissionDenied("Only the owning office can re-run extraction.")

        try:
            result = services.re_extract_document(document, user=request.user)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect(document.get_absolute_url())
        document.refresh_from_db()
        if result["status"] == OcrStatus.PENDING and document.ocr_status == OcrStatus.FAILED:
            messages.warning(request, "Text extraction did not finish. See the extraction notes before retrying.")
        elif result["status"] == OcrStatus.PENDING:
            messages.success(request, "Reading the document in the background. This page will update when it is ready.")
        elif result["failures"]:
            messages.warning(request, "Some files could not be read: " + "; ".join(result["failures"][:3]))
        elif result["status"] in {OcrStatus.FAILED, OcrStatus.SKIPPED}:
            messages.warning(request, "Text extraction did not finish. See the extraction notes before retrying.")
        else:
            messages.success(
                request,
                f"Text extraction finished ({document.ocr_engine}): {result['characters']} characters. Search index refreshed.",
            )
        return redirect(document.get_absolute_url())


class ExtractionStatusView(AppLoginRequiredMixin, View):
    def get(self, request, pk):
        document = _get_document(request, pk)
        from .processing_contracts import coverage_summary

        return render(request, "documents/_extraction_status.html", {"document": document, "coverage": coverage_summary(document)})


class TagSuggestJsonView(AppLoginRequiredMixin, View):
    def get(self, request):
        prefix = request.GET.get("q", "").strip().lower()
        tags = services.suggested_tags_for(request.user).filter(name__icontains=prefix)[:10]
        return JsonResponse({"results": [tag.name for tag in tags]})


def _reviewable_documents(user, *, collection=Collection.OVPA):
    documents = Document.objects.visible_to(user).in_collection(collection)
    if not user.is_system_admin:
        documents = documents.filter(office_id=user.office_id)
    return documents


class DeletionQueueView(AdminRequiredMixin, View):
    """A human decision queue scoped to offices the administrator may manage."""

    permission_message = "Only administrators can manage deletion reviews."
    collection = Collection.OVPA

    def get(self, request):
        base = _reviewable_documents(request.user, collection=self.collection)
        office_options = Office.objects.filter(pk__in=base.values("office_id")).order_by("name")
        values = request.GET.copy()
        if not values.get("status"):
            values["status"] = "due"
        form = DeletionQueueForm(values, offices=office_options)
        valid = form.is_valid()
        data = form.cleaned_data
        if not valid:
            base = base.none()
        if data.get("office"):
            base = base.filter(office=data["office"])
        if data.get("q"):
            base = base.filter(Q(title__icontains=data["q"]) | Q(reference_number__icontains=data["q"]))
        active = base.filter(is_active=True)
        queues = {
            "due": active.due_for_retention_review(), "soon": active.upcoming_deletion_review(),
            "unscheduled": active.filter(retention_until__isnull=True), "deleted": base.filter(is_active=False),
        }
        status = data.get("status", "due")
        documents = queues.get(status, base.none()).with_related().order_by("retention_until", "pk")
        context = paginate(request, documents)
        context.update({
            "form": form, "status": status, "total": context["page_obj"].paginator.count,
            "is_external_collection": self.collection == Collection.EXTERNAL,
            "queue_counts": {key: query.count() for key, query in queues.items()},
            "queue_links": {key: core_filters.link(request.path, request, status=key) for key in queues},
        })
        return render(request, "documents/deletion_queue.html", context)


class DeletionReviewView(AdminRequiredMixin, View):
    template_name = "documents/deletion_review.html"
    permission_message = "Only administrators can record deletion review decisions."

    def _document(self, request, pk):
        document = get_object_or_404(Document.objects.visible_to(request.user).with_related(), pk=pk)
        if not document.can_user_review_deletion(request.user, include_deleted=True):
            raise PermissionDenied("Only an authorized administrator for the owning office can review deletion.")
        return document

    def _render(self, request, document, form):
        return render(request, self.template_name, {
            "document": document, "form": form, "files": document.files.all(),
            "deletion_reviews": document.deletion_reviews.select_related("reviewed_by"),
        })

    def get(self, request, pk):
        document = self._document(request, pk)
        return self._render(request, document, DeletionReviewForm(document=document))

    def post(self, request, pk):
        document = self._document(request, pk)
        form = DeletionReviewForm(request.POST, document=document)
        if form.is_valid():
            data = form.cleaned_data
            try:
                services.review_document_deletion(
                    document, user=request.user, decision=data["decision"], reason=data["reason"],
                    next_review_date=data.get("next_review_date"), confirmed=data.get("confirmed"),
                    expected_state=data["state_token"],
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            except Document.DoesNotExist as exc:
                raise Http404("This repository document has been permanently deleted.") from exc
            else:
                messages.success(request, "Deletion review recorded. " + (
                    "The document moved to Deleted documents. It can be restored for 30 days before permanent deletion."
                    if not document.is_active else "The document is available in the repository with its next review date."
                ))
                if document.is_active:
                    return redirect(document.get_absolute_url())
                queue = "documents:external_deletion_queue" if document.is_external else "documents:deletion_queue"
                return redirect(core_filters.link(reverse(queue), status="deleted"))
        return self._render(request, document, form)

from __future__ import annotations

from datetime import timedelta

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

from apps.core import filters as core_filters
from apps.core.mixins import AppLoginRequiredMixin, OfficeAssignedMixin
from apps.core.models import AuditLog, DocumentType, Tag
from apps.core.pagination import DEFAULT_PAGE_SIZE, paginate
from apps.core.utils import log_action
from apps.tracking.services import pending_upload_for

from . import services
from .forms import AddFilesForm, DocumentMetadataForm, RepositoryFilterForm, UploadForm
from .models import COMPLETED_SOURCE, HISTORICAL_FILTER, Document, DocumentFile
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
    document = get_object_or_404(Document.objects.with_related(), pk=pk)
    if not document.can_user_view(request.user):
        raise PermissionDenied(
            "This record belongs to another office and has not been shared with your account."
        )
    return document


class RepositoryView(AppLoginRequiredMixin, View):
    """Completed records, historical uploads and smart folders."""

    template_name = "documents/repository.html"

    @staticmethod
    def _filter_documents(documents, data, *, omit=None):
        """A counted facet omits only the filter its link replaces."""
        data = {key: value for key, value in data.items() if key != omit}
        if data.get("q"):
            query = data["q"]
            documents = documents.filter(
                Q(title__icontains=query) | Q(reference_number__icontains=query)
                | Q(index_meta__icontains=query) | Q(ocr_text__icontains=query)
            )
        for key, field in (("year", "year"), ("month", "document_date__month"),
                           ("document_type", "document_type"), ("tag", "tags")):
            if data.get(key):
                documents = documents.filter(**{field: data[key]})
        if data.get("source") == HISTORICAL_FILTER:
            documents = documents.exclude(source=COMPLETED_SOURCE)
        elif data.get("source"):
            documents = documents.filter(source=data["source"])
        today = timezone.localdate()
        if data.get("retention") == "due":
            documents = documents.due_for_retention_review(today)
        elif data.get("retention") == "soon":
            documents = documents.filter(retention_until__gt=today, retention_until__lte=today + timedelta(days=90))
        elif data.get("retention") == "unscheduled":
            documents = documents.filter(retention_until__isnull=True)
        return documents.distinct()

    @staticmethod
    def _filter_pending(records, data, *, omit=None):
        data = {key: value for key, value in data.items() if key != omit}
        if data.get("q"):
            records = records.filter(Q(subject__icontains=data["q"]) | Q(tracking_number__icontains=data["q"]))
        for key, field in (("year", "completed_at__year"), ("month", "completed_at__month"),
                           ("document_type", "document_type")):
            if data.get(key):
                records = records.filter(**{field: data[key]})
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
            "document_types": DocumentType.objects.filter(documents__in=visible).distinct(),
            # Most-used first: with a shared vocabulary the useful tags are the
            # common ones, and alphabetical order buries them under one-offs.
            "tags": Tag.active.filter(documents__in=visible).annotate(
                visible_usage=Count("documents", filter=Q(documents__in=visible), distinct=True)
            ).order_by("-visible_usage", "name"),
            "sources": set(visible.values_list("source", flat=True).distinct()),
        }

    def get(self, request):
        documents = Document.objects.visible_to(request.user).filter(is_active=True).with_related()
        visible = Document.objects.visible_to(request.user).filter(is_active=True)
        pending_visible = pending_upload_for(request.user).filter(is_archived=False)
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
                "That office was not recognised, so every office is shown. "
                "It may have been archived.",
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
                document_types=DocumentType.objects.filter(tracking_records__in=folder_pending).distinct(),
                tags=Tag.active.none(), sources=set(),
            )
        form = RepositoryFilterForm(request.GET or None, **options)
        if pending_view:
            form.fields["q"].widget.attrs["placeholder"] = "Search subject or tracking number…"
            for field in ("tag", "source", "retention"):
                form.fields.pop(field)
            if any(request.GET.get(field) for field in ("tag", "source", "retention")):
                messages.warning(request, "Tags, origin, and retention apply only to filed documents. They were ignored for pending uploads.")

        # Apply every filter that validated, not the all-or-nothing case. The
        # whole block used to hang off `if form.is_valid()`, so one unrecognised
        # value — a stale bookmark, a tag since deleted — silently dropped
        # *every* filter and returned the entire repository while the controls
        # still showed a narrow search. Same fault the tracking list had.
        form.is_valid()
        data = getattr(form, "cleaned_data", {})

        folder_documents = documents
        documents = self._filter_documents(folder_documents, data)
        today = timezone.localdate()

        if form.errors:
            messages.warning(
                request,
                "Ignored a filter that no longer applies here: "
                + ", ".join(sorted(form.errors))
                + ". Showing the rest.",
            )

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
        pending = pending.annotate(file_count=Count("attachments", distinct=True)).order_by("completed_at", "pk")
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
        type_base = self._filter_pending(folder_pending, data, omit="document_type") if pending_view else self._filter_documents(folder_documents, data, omit="document_type")
        type_folders = list(type_base.order_by().values("document_type_id", "document_type__name").annotate(total=Count("pk", distinct=True)).order_by("document_type__name"))
        # Unclassified remains visible in the list; only selectable types become folders.
        type_folders = [folder for folder in type_folders if folder["document_type_id"]]
        for folder in type_folders:
            folder["url"] = core_filters.link(base_url, request, document_type=folder["document_type_id"], page=None)
            folder["selected"] = bool(data.get("document_type") and data["document_type"].pk == folder["document_type_id"])

        # Retention reviews cover only filed documents in the selected folder.
        retention_base = self._filter_documents(folder_documents, data, omit="retention")
        retention_due_query = retention_base.due_for_retention_review(today).with_related().order_by("retention_until")
        retention_due_count = retention_due_query.count()
        retention_due = list(retention_due_query[:RETENTION_DUE_SHOWN])
        tag_base = self._filter_documents(folder_documents, data, omit="tag")
        popular_tags = Tag.active.filter(documents__in=tag_base).annotate(
            visible_usage=Count("documents", filter=Q(documents__in=tag_base), distinct=True)
        ).order_by("-visible_usage", "name")[:8]
        # Keep a denied folder in tab/form URLs: dropping it would turn a
        # zero-count tab into a link to every accessible record instead.
        folder_params = {"office": folder_office_id}
        selected_filters = []
        filter_labels = {"q": "Search", "document_type": "Type", "year": "Year", "month": "Month", "tag": "Tag", "source": "Origin", "retention": "Retention"}
        for name, label in filter_labels.items():
            value = data.get(name)
            if value:
                choices = dict(form.fields[name].choices) if hasattr(form.fields[name], "choices") and name not in {"document_type", "tag"} else {}
                selected_filters.append({"label": label, "value": choices.get(value, choices.get(str(value), str(value))), "url": core_filters.link(base_url, request, **{name: None, "page": None})})

        return render(
            request,
            self.template_name,
            {
                "form": form,
                "selected_filters": selected_filters,
                "more_filters_open": bool(any(data.get(name) for name in ("tag", "source", "retention")) or form.errors),
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
                "folder_reset_url": core_filters.link(base_url, request, q=None, document_type=None, tag=None, source=None, year=None, month=None, retention=None, page=None),
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


class UploadView(OfficeAssignedMixin, View):
    """Upload or scan — step 1. Text is extracted immediately, then reviewed."""

    template_name = "documents/upload.html"

    def get(self, request):
        return render(request, self.template_name, {"form": UploadForm(user=request.user)})

    def post(self, request):
        form = UploadForm(request.POST, request.FILES, user=request.user)
        if not form.is_valid():
            messages.error(request, "Choose a file and an owning office.")
            return render(request, self.template_name, {"form": form})

        uploaded = form.cleaned_data["file"]
        duplicate = services.duplicate_of(uploaded, user=request.user)
        try:
            document, suggestion = services.ingest_upload(
                user=request.user,
                uploaded_file=uploaded,
                office=form.cleaned_data["office"],
                source=form.cleaned_data["source"],
                ocr_language=form.cleaned_data["ocr_language"],
                allow_external_ocr=form.cleaned_data["allow_external_ocr"],
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return render(request, self.template_name, {"form": form})

        if duplicate:
            messages.warning(
                request,
                f"The same file is already archived as “{duplicate.title}”. Continue only if this copy is different.",
            )
        request.session[f"suggestion_{document.pk}"] = suggestion.as_dict()
        return redirect("documents:review", pk=document.pk)


class MetadataReviewView(OfficeAssignedMixin, View):
    """Upload or scan — step 2. The system suggests; the uploader decides."""

    template_name = "documents/review.html"

    def _suggestion(self, request, document) -> dict:
        stored = request.session.get(f"suggestion_{document.pk}")
        if stored:
            return stored
        latest = document.suggestions.first()
        return latest.suggested if latest else Suggestion().as_dict()

    def _initial(self, document, suggestion) -> dict:
        return {
            "title": suggestion.get("title") or document.title,
            "description": suggestion.get("subject", ""),
            "office": suggestion.get("office_id") or document.office_id,
            "document_type": suggestion.get("document_type_id") or document.document_type_id,
            "document_date": suggestion.get("document_date") or None,
            "year": document.year,
            "reference_number": suggestion.get("reference_number", ""),
            "author_name": suggestion.get("author_name", ""),
            "recipient_name": suggestion.get("recipient_name", ""),
            "access_level": document.access_level,
            "ocr_language": document.ocr_language,
            "allow_external_ocr": document.allow_external_ocr,
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
                "all_tags": Tag.active.order_by("-usage_count")[:50],
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
                    "all_tags": Tag.active.order_by("-usage_count")[:50],
                },
            )

        data = form.cleaned_data
        accepted = {
            "title": data["title"],
            "document_type_id": data["document_type"].pk if data.get("document_type") else None,
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
        return render(
            request,
            self.template_name,
            {
                "document": document,
                "files": document.files.all(),
                "metadata_values": document.metadata_values.select_related("field"),
                "suggestion": document.suggestions.first(),
                "add_files_form": AddFilesForm(),
                "can_edit": document.can_user_edit(request.user),
                "related": Document.objects.visible_to(request.user)
                .filter(office=document.office)
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
            data=form.cleaned_data,
            tag_names=form.cleaned_data.get("tags", []),
            metadata_values=form.metadata_cleaned(),
        )
        messages.success(request, "Metadata updated and re-indexed.")
        return redirect(document.get_absolute_url())


class AddFilesView(OfficeAssignedMixin, View):
    def post(self, request, pk):
        document = _get_document(request, pk)
        if not document.can_user_edit(request.user):
            raise PermissionDenied("Only the owning office can add files.")
        form = AddFilesForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, "Choose at least one file.")
            return redirect(document.get_absolute_url())
        added = 0
        for uploaded in form.cleaned_data["files"]:
            try:
                services.add_file_to_document(document, uploaded, user=request.user)
                added += 1
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
        if added:
            messages.success(request, f"Added {added} file(s) and refreshed the search index.")
        return redirect(document.get_absolute_url())


class DocumentFileDownloadView(AppLoginRequiredMixin, View):
    def get(self, request, pk):
        document_file = get_object_or_404(DocumentFile.objects.select_related("document"), pk=pk)
        if not document_file.document.can_user_view(request.user):
            raise PermissionDenied("You do not have access to this document.")
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
            return response
        except FileNotFoundError as exc:
            raise Http404("The file is missing from storage.") from exc


class ReExtractView(OfficeAssignedMixin, View):
    """Run text extraction again — useful after adding an OCR key."""

    def post(self, request, pk):
        document = _get_document(request, pk)
        if not document.can_user_edit(request.user):
            raise PermissionDenied("Only the owning office can re-run extraction.")
        primary = document.primary_file
        if not primary:
            messages.error(request, "This record has no file to read.")
            return redirect(document.get_absolute_url())
        from .extraction import extract_document_text
        from .models import OcrStatus

        if settings.ENABLE_BACKGROUND_TASKS:
            document.ocr_status = OcrStatus.PENDING
            document.save(update_fields=["ocr_status", "updated_at"])
            services._enqueue_extraction(document, user_id=request.user.pk, file_ids=[primary.pk], replace=True)
            messages.success(request, "Reading the document in the background. This page will update when it is ready.")
            return redirect(document.get_absolute_url())

        # The download views already answer a vanished file with a 404; this one
        # opened it bare, so a record whose file had gone missing from storage
        # turned the button into a 500 instead of saying what was wrong.
        try:
            primary.file.open("rb")
        except (FileNotFoundError, OSError):
            messages.error(
                request,
                f"“{primary.original_name}” is missing from storage, so there is nothing to read. "
                "Upload the file again to restore it.",
            )
            return redirect(document.get_absolute_url())
        try:
            result = extract_document_text(
                primary.file,
                primary.original_name,
                language_hint=document.ocr_language,
                allow_external_ocr=document.allow_external_ocr,
            )
        finally:
            primary.file.close()
        document.ocr_text = result.text
        document.ocr_status = getattr(OcrStatus, result.status, OcrStatus.EMPTY)
        document.ocr_engine = result.engine[:32]
        document.ocr_confidence = result.confidence
        document.ocr_notes = "\n".join(result.notes)[:4000]
        document.page_count = result.pages or document.page_count
        document.save(
            update_fields=[
                "ocr_text", "ocr_status", "ocr_engine", "ocr_confidence", "ocr_notes", "page_count", "updated_at"
            ]
        )
        document.rebuild_index()
        messages.success(
            request,
            f"Text extraction finished ({result.engine}): {result.char_count} characters. Search index refreshed.",
        )
        return redirect(document.get_absolute_url())


class ExtractionStatusView(AppLoginRequiredMixin, View):
    def get(self, request, pk):
        document = _get_document(request, pk)
        return render(request, "documents/_extraction_status.html", {"document": document})


class TagSuggestJsonView(AppLoginRequiredMixin, View):
    def get(self, request):
        prefix = request.GET.get("q", "").strip().lower()
        tags = Tag.active.filter(name__icontains=prefix).order_by("-usage_count", "name")[:10]
        return JsonResponse({"results": [tag.name for tag in tags]})

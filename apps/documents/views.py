from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.generic import View

from apps.core import filters as core_filters
from apps.core.mixins import AppLoginRequiredMixin, OfficeAssignedMixin
from apps.core.models import AuditLog, DocumentType, Tag
from apps.core.utils import log_action

from . import services
from .forms import AddFilesForm, DocumentMetadataForm, RepositoryFilterForm, UploadForm
from .models import Document, DocumentFile
from .suggestions import Suggestion

PAGE_SIZE = 24

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
            "document_types": DocumentType.active.filter(documents__in=visible).distinct(),
            # Most-used first: with a shared vocabulary the useful tags are the
            # common ones, and alphabetical order buries them under one-offs.
            "tags": Tag.active.filter(documents__in=visible).distinct().order_by("-usage_count", "name"),
            "sources": set(visible.values_list("source", flat=True).distinct()),
        }

    def get(self, request):
        documents = Document.objects.visible_to(request.user).filter(is_active=True).with_related()
        visible = Document.objects.visible_to(request.user).filter(is_active=True)
        form = RepositoryFilterForm(request.GET or None, **self._options(visible))

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
        if selected_office:
            documents = documents.filter(office=selected_office)
        elif "office" in resolved.invalid:
            messages.warning(
                request,
                "That office was not recognised, so every office is shown. "
                "It may have been archived.",
            )

        # Apply every filter that validated, not the all-or-nothing case. The
        # whole block used to hang off `if form.is_valid()`, so one unrecognised
        # value — a stale bookmark, a tag since deleted — silently dropped
        # *every* filter and returned the entire repository while the controls
        # still showed a narrow search. Same fault the tracking list had.
        form.is_valid()
        data = getattr(form, "cleaned_data", {})

        query = data.get("q")
        if query:
            documents = documents.filter(
                Q(title__icontains=query)
                | Q(reference_number__icontains=query)
                | Q(index_meta__icontains=query)
                | Q(ocr_text__icontains=query)
            )
        if data.get("year"):
            documents = documents.filter(year=data["year"])
        if data.get("month"):
            documents = documents.filter(document_date__month=data["month"])
        if data.get("document_type"):
            documents = documents.filter(document_type=data["document_type"])
        if data.get("tag"):
            documents = documents.filter(tags=data["tag"])
        if data.get("source"):
            documents = documents.filter(source=data["source"])
        retention = data.get("retention")
        today = timezone.localdate()
        if retention == "due":
            documents = documents.due_for_retention_review(today)
        elif retention == "soon":
            documents = documents.filter(retention_until__gt=today, retention_until__lte=today + timedelta(days=90))
        elif retention == "unscheduled":
            documents = documents.filter(retention_until__isnull=True)

        if form.errors:
            messages.warning(
                request,
                "Ignored a filter that no longer applies here: "
                + ", ".join(sorted(form.errors))
                + ". Showing the rest.",
            )

        documents = documents.distinct().order_by("-document_date", "-created_at")
        page = Paginator(documents, PAGE_SIZE).get_page(request.GET.get("page"))

        years = sorted({value for value in visible.values_list("year", flat=True) if value}, reverse=True)
        smart_folders = (
            # office__id so the folder links can carry a primary key, which is
            # what `office` means everywhere else; the code stays for the active
            # check and the title.
            visible.values("office__id", "office__code", "office__name")
            .annotate(total=Count("id", distinct=True))
            .order_by("office__name")
        )

        # The completed-but-unapproved queue used to live here. It has moved to
        # the Document Tracking page, and the reason it was ever on this one no
        # longer holds: completing a record set COMPLETED immediately, which
        # dropped it out of Tracking, so a record finished but never filed was
        # in neither module and this page was the only place left to surface it
        # from. Approval is now a stage of the tracking lifecycle
        # (COMPLETED_PENDING_UPLOAD is in ACTIVE_STATUSES), so those records
        # never leave Tracking until the act that files them has happened, and
        # the queue belongs beside them. See TrackingRecordQuerySet.pending_filing.
        retention_due_query = visible.due_for_retention_review(today).with_related().order_by("retention_until")
        retention_due_count = retention_due_query.count()
        retention_due = list(retention_due_query[:RETENTION_DUE_SHOWN])

        return render(
            request,
            self.template_name,
            {
                "form": form,
                "page_obj": page,
                "documents": page.object_list,
                # Hides the create/upload button from the accounts the
                # target view would turn away. The view still refuses
                # them on its own; this only stops offering a dead end.
                "can_start_work": request.user.can_start_work,
                "smart_folders": smart_folders,
                "selected_office": selected_office,
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
                "popular_tags": Tag.active.filter(usage_count__gt=0).order_by("-usage_count")[:12],
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
        duplicate = services.duplicate_of(uploaded)
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

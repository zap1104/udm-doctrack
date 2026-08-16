from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, F, Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import View

from apps.accounts.models import Office
from apps.core.mixins import AppLoginRequiredMixin, OfficeAssignedMixin
from apps.core.models import AuditLog, DocumentType, Tag
from apps.core.utils import log_action
from apps.tracking.models import TrackingRecord

from . import services
from .forms import AddFilesForm, DocumentMetadataForm, RepositoryFilterForm, UploadForm
from .models import Document, DocumentFile
from .suggestions import Suggestion

PAGE_SIZE = 24

#: Rows of the pending-filing queue shown before it collapses to a count. It is
#: a to-do list that should be worked down, not another table to page through.
PENDING_FILING_SHOWN = 8


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

        office_code = request.GET.get("office", "")
        selected_office = Office.objects.filter(code=office_code).first() if office_code else None
        if selected_office:
            documents = documents.filter(office=selected_office)

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
            visible.values("office__code", "office__name")
            .annotate(total=Count("id", distinct=True))
            .order_by("office__name")
        )

        # Completed records that never made it into the repository. They are in
        # neither module's list until somebody files them, so this is the only
        # place they can be found — and the reason the queue is on this page
        # rather than in Tracking is that filing them is what it is for.
        pending_filing = list(
            TrackingRecord.objects.visible_to(request.user)
            .pending_filing()
            .with_related()
            # nulls_last because Postgres sorts NULLs first on DESC, which would
            # float a record with no completion time to the top of the queue.
            .order_by(F("completed_at").desc(nulls_last=True))[: PENDING_FILING_SHOWN + 1]
        )
        pending_filing_more = max(0, len(pending_filing) - PENDING_FILING_SHOWN)
        pending_filing = pending_filing[:PENDING_FILING_SHOWN]
        for record in pending_filing:
            # Filing is one click from here; returning to tracking needs a
            # written reason, so that action lives on the record page itself.
            record.can_file = record.can_user_archive(request.user)

        return render(
            request,
            self.template_name,
            {
                "form": form,
                "page_obj": page,
                "documents": page.object_list,
                "smart_folders": smart_folders,
                "selected_office": selected_office,
                # The paginator has already counted this queryset; .count()
                # would run the same DISTINCT-over-joins query a second time on
                # every page load.
                "total": page.paginator.count,
                "all_count": visible.count(),
                "pending_filing": pending_filing,
                "pending_filing_more": pending_filing_more,
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
            return FileResponse(
                document_file.file.open("rb"), as_attachment=True, filename=document_file.original_name
            )
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
            result = extract_document_text(primary.file, primary.original_name)
        finally:
            primary.file.close()
        document.ocr_text = result.text
        document.ocr_status = getattr(OcrStatus, result.status, OcrStatus.EMPTY)
        document.ocr_engine = result.engine[:32]
        document.page_count = result.pages or document.page_count
        document.save(update_fields=["ocr_text", "ocr_status", "ocr_engine", "page_count", "updated_at"])
        document.rebuild_index()
        messages.success(
            request,
            f"Text extraction finished ({result.engine}): {result.char_count} characters. Search index refreshed.",
        )
        return redirect(document.get_absolute_url())


class TagSuggestJsonView(AppLoginRequiredMixin, View):
    def get(self, request):
        prefix = request.GET.get("q", "").strip().lower()
        tags = Tag.active.filter(name__icontains=prefix).order_by("-usage_count", "name")[:10]
        return JsonResponse({"results": [tag.name for tag in tags]})

"""Small explicit review screens over existing repository permissions."""

from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.db.models import OuterRef, Subquery
from django.http import HttpResponseRedirect, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache

from apps.core.mixins import AppLoginRequiredMixin, SystemAdminRequiredMixin
from apps.core.pagination import paginate

from .file_versions import DocumentFileVersion
from .intelligence_forms import (
    AliasApprovalForm,
    CompleteTagReviewForm,
    SavedViewForm,
    TagMergeForm,
    TagReviewForm,
    TrainingEligibilityForm,
)
from .ml.eligibility import set_training_eligibility
from .ml.lifecycle import activate_model, rollback_model
from .ml.models import TagModel
from .models import Collection, ExtractionUnit, SavedDocumentView, TagAlias
from .processing_contracts import coverage_summary
from .saved_views import create_saved_view, delete_saved_view, open_saved_view
from .tagging import (
    approve_alias,
    complete_review,
    deactivate_alias,
    input_revision,
    merge_tags,
    review_tag,
    vocabulary_digest,
)
from .views import _get_document


def current_units(document):
    latest = (
        DocumentFileVersion.objects.filter(document_file_id=OuterRef("run__file_version__document_file_id"))
        .order_by("-version_number", "-pk")
        .values("pk")[:1]
    )
    return (
        ExtractionUnit.objects.filter(
            run__document=document,
            run__is_current=True,
            run__file_version__document_file__document=document,
            run__file_version_id=Subquery(latest),
        )
        .select_related("run__file_version")
        .order_by("run__file_version__document_file_id", "ordinal")
    )


def review_context(request, document):
    revision, vocabulary = input_revision(document), vocabulary_digest()
    initial = {"input_revision": revision, "vocabulary": vocabulary}
    context = paginate(
        request, current_units(document).defer("raw_text", "native_raw_text", "ocr_raw_text", "normalized_text")
    )
    may_edit = document.can_user_edit(request.user)
    context.update(
        document=document,
        units=context["page_obj"].object_list,
        coverage=coverage_summary(document),
        can_edit=may_edit,
        input_revision=revision,
        review_form=TagReviewForm(document=document, user=request.user, initial=initial, auto_id="review_%s"),
        complete_form=CompleteTagReviewForm(
            document=document, user=request.user, initial=initial, auto_id="complete_%s"
        ),
        eligibility_form=TrainingEligibilityForm(
            initial={
                "input_revision": revision,
                "eligible": document.training_eligible,
                "family": document.training_family,
            },
            auto_id="eligibility_%s",
        ),
        can_set_eligibility=may_edit and request.user.is_office_admin,
        runs=document.extraction_runs.filter(generation=document.extraction_generation).order_by("pk"),
        suggestion=document.suggestions.first(),
    )
    context["predictions"] = []
    contributions = list(document.tag_contributions.select_related("tag"))
    assignments = []
    for tag in document.tags.all():
        rows = [row for row in contributions if row.tag_id == tag.pk]
        if any(row.kind == "human" and row.source == "review" for row in rows):
            origin = "Human confirmed"
        elif any(row.kind == "human" for row in rows) or not rows:
            origin = "Existing assignment; original reviewer unknown"
        else:
            origin = "Automatic topic contribution"
        assignments.append({"tag": tag, "origin": origin})
    context["assignments"] = assignments
    context["reviews"] = document.tag_reviews.order_by("-pk")[:20]
    if request.user.is_system_admin or request.user.office_id == document.office_id:
        model_ids = TagModel.objects.filter(
            office=document.office,
            collection=document.collection,
            is_active=True,
            invalidated_at__isnull=True,
            mode__in=["suggestion", "automatic"],
        ).values_list("pk", flat=True)
        context["predictions"] = (
            document.tag_predictions.filter(
                input_revision=revision,
                vocabulary=vocabulary,
                engine="ml",
                model_reference__in=[str(pk) for pk in model_ids],
                decision="suggest",
                evidence__mode__in=["suggestion", "automatic"],
            )
            .select_related("tag")
            .order_by("-created_at", "pk")[:10]
        )
    return context


@method_decorator(never_cache, name="dispatch")
class SourceUnitsView(AppLoginRequiredMixin, View):
    def get(self, request, pk):
        document = _get_document(request, pk)
        return render(request, "documents/intelligence/units.html", review_context(request, document))


@method_decorator(never_cache, name="dispatch")
class SourceUnitView(AppLoginRequiredMixin, View):
    def get(self, request, pk, unit_id):
        document = _get_document(request, pk)
        unit = get_object_or_404(current_units(document), pk=unit_id)
        from apps.tracking.version_views import version_rows

        text = unit.normalized_text[:50_000]
        return render(
            request,
            "documents/intelligence/unit.html",
            {
                "document": document,
                "unit": unit,
                "text": text,
                "display_truncated": len(unit.normalized_text) > len(text),
                "file": version_rows([unit.run.file_version], "repository", request.user)[0],
            },
        )


class TagReviewView(AppLoginRequiredMixin, View):
    complete = False

    def post(self, request, pk):
        document = _get_document(request, pk)
        if not document.can_user_edit(request.user):
            raise PermissionDenied("Only an authorized editor can review topic tags.")
        form_class = CompleteTagReviewForm if self.complete else TagReviewForm
        form = form_class(
            request.POST, document=document, user=request.user, auto_id="complete_%s" if self.complete else "review_%s"
        )
        status = 400
        if form.is_valid():
            data = form.cleaned_data
            try:
                common = {
                    "user": request.user,
                    "expected_revision": data["input_revision"],
                    "vocabulary": data["vocabulary"],
                }
                if self.complete:
                    complete_review(
                        document,
                        covered_ids=[tag.pk for tag in data["covered"]],
                        positive_ids=[tag.pk for tag in data["positive"]],
                        **common,
                    )
                else:
                    review_tag(document, tag_id=data["tag"].pk, decision=data["decision"], **common)
                messages.success(request, "Topic review saved. Unreviewed tags remain unknown.")
                return redirect(reverse("documents:source_units", args=[pk]) + "#tag-review")
            except ValidationError as exc:
                form.add_error(None, exc)
                status = 409
        context = review_context(request, document)
        context["complete_form" if self.complete else "review_form"] = form
        return render(request, "documents/intelligence/units.html", context, status=status)


class TrainingEligibilityView(AppLoginRequiredMixin, View):
    def post(self, request, pk):
        document = _get_document(request, pk)
        if not request.user.is_office_admin or not document.can_user_edit(request.user):
            raise PermissionDenied("Only an authorized office administrator can change training eligibility.")
        form = TrainingEligibilityForm(request.POST, auto_id="eligibility_%s")
        status = 400
        if form.is_valid():
            try:
                set_training_eligibility(
                    document,
                    user=request.user,
                    eligible=form.cleaned_data["eligible"],
                    family=form.cleaned_data["family"],
                    expected_revision=form.cleaned_data["input_revision"],
                )
                messages.success(
                    request, "Training eligibility saved. Existing models are not reactivated automatically."
                )
                return redirect(reverse("documents:source_units", args=[pk]) + "#training-eligibility")
            except ValidationError as exc:
                form.add_error(None, exc)
                status = 409
        context = review_context(request, document)
        context["eligibility_form"] = form
        return render(request, "documents/intelligence/units.html", context, status=status)


class TagModelsView(SystemAdminRequiredMixin, View):
    def get(self, request):
        collection = request.GET.get("collection", Collection.OVPA)
        if (
            len(request.GET.getlist("collection")) > 1
            or collection not in Collection.values
            or len(request.GET.getlist("office")) > 1
        ):
            return render(
                request,
                "documents/intelligence/models.html",
                {"models": [], "scope_error": "Select one valid model scope."},
                status=400,
            )
        models = (
            TagModel.objects.filter(collection=collection)
            .defer("artifact", "snapshot__payload")
            .select_related("office", "snapshot")
            .order_by("-created_at", "-pk")
        )
        if request.GET.get("office"):
            from apps.accounts.models import Office

            try:
                office_id = int(request.GET["office"])
            except ValueError:
                return render(
                    request,
                    "documents/intelligence/models.html",
                    {"models": [], "scope_error": "Select a valid internal office."},
                    status=400,
                )
            office = get_object_or_404(Office, pk=office_id, is_active=True)
            models = models.filter(office=office)
        context = paginate(request, models)
        context.update(models=context["page_obj"].object_list, collection=collection)
        return render(request, "documents/intelligence/models.html", context)

    def post(self, request):
        if request.POST.get("action") not in {"activate", "disable"} or any(
            len(request.POST.getlist(key)) != 1 for key in request.POST
        ):
            return render(
                request,
                "documents/intelligence/models.html",
                {"models": [], "scope_error": "Choose one valid model action."},
                status=400,
            )
        target_collection = request.POST.get("collection", Collection.OVPA)
        target_office = None
        try:
            if request.POST.get("action") == "disable":
                target_office = int(request.POST["office"])
                rollback_model(user=request.user, office_id=target_office, collection=request.POST["collection"])
            else:
                model = activate_model(
                    int(request.POST["model"]), user=request.user, mode=request.POST.get("mode", "shadow")
                )
                target_collection, target_office = model.collection, model.office_id
        except (ValidationError, ValueError, KeyError, TagModel.DoesNotExist) as exc:
            messages.error(
                request,
                "; ".join(exc.messages) if isinstance(exc, ValidationError) else "Choose a valid model and scope.",
            )
        else:
            messages.success(request, "Model configuration saved.")
        params = {"collection": target_collection if target_collection in Collection.values else Collection.OVPA}
        if target_office is not None:
            params["office"] = target_office
        return redirect(reverse("documents:tag_models") + "?" + urlencode(params))


class VocabularyView(SystemAdminRequiredMixin, View):
    def get(self, request):
        return self.render(request)

    def post(self, request):
        action = request.POST.get("action")
        alias_form = AliasApprovalForm(request.POST if action == "alias" else None, auto_id="alias_%s")
        merge_form = TagMergeForm(request.POST if action == "merge" else None, auto_id="merge_%s")
        if any(len(request.POST.getlist(key)) != 1 for key in request.POST):
            return self.render(request, alias_form, merge_form, status=400)
        try:
            if action == "alias" and alias_form.is_valid():
                approve_alias(alias_form.cleaned_data["tag"].pk, alias_form.cleaned_data["phrase"], user=request.user)
            elif action == "merge" and merge_form.is_valid():
                data = merge_form.cleaned_data
                merge_tags(data["source"].pk, data["target"].pk, reason=data["reason"], user=request.user)
            elif action == "deactivate":
                deactivate_alias(int(request.POST["alias"]), user=request.user)
            else:
                return self.render(request, alias_form, merge_form, status=400)
        except (ValidationError, IntegrityError, ValueError, KeyError, TagAlias.DoesNotExist) as exc:
            messages.error(
                request,
                "; ".join(exc.messages)
                if isinstance(exc, ValidationError)
                else "The vocabulary changed. Reload and choose a valid topic or alias.",
            )
            return self.render(request, alias_form, merge_form, status=409)
        messages.success(request, "Vocabulary change saved. Historical reviews and predictions are preserved.")
        return redirect("documents:tag_vocabulary")

    @staticmethod
    def render(request, alias_form=None, merge_form=None, status=200):
        context = paginate(request, TagAlias.objects.select_related("tag").order_by("tag__name", "phrase"))
        context.update(
            aliases=context["page_obj"].object_list,
            alias_form=alias_form or AliasApprovalForm(auto_id="alias_%s"),
            merge_form=merge_form or TagMergeForm(auto_id="merge_%s"),
        )
        return render(request, "documents/intelligence/vocabulary.html", context, status=status)


class SavedViewsView(AppLoginRequiredMixin, View):
    def get(self, request):
        form = SavedViewForm(
            initial={
                "collection": request.GET.get("collection", Collection.OVPA),
                "filters": request.GET.get("filters", ""),
            }
        )
        return self.render(request, form)

    def post(self, request):
        form = SavedViewForm(request.POST)
        if form.is_valid():
            try:
                create_saved_view(
                    user=request.user,
                    name=form.cleaned_data["name"],
                    collection=form.cleaned_data["collection"],
                    params=QueryDict(form.cleaned_data["filters"]),
                )
                messages.success(request, "Private repository view saved.")
                return redirect("documents:saved_views")
            except ValidationError as exc:
                form.add_error(None, exc)
        return self.render(request, form, status=400)

    @staticmethod
    def render(request, form, status=200):
        return render(
            request,
            "documents/intelligence/saved_views.html",
            {"form": form, "saved_views": SavedDocumentView.objects.filter(owner=request.user)},
            status=status,
        )


class OpenSavedView(AppLoginRequiredMixin, View):
    def get(self, request, view_id):
        get_object_or_404(SavedDocumentView, pk=view_id, owner=request.user)
        try:
            target = open_saved_view(view_id, user=request.user)
        except ValidationError as exc:
            form = SavedViewForm(initial={"collection": Collection.OVPA})
            messages.error(request, "; ".join(exc.messages))
            return SavedViewsView.render(request, form, status=400)
        return HttpResponseRedirect(target)


class DeleteSavedView(AppLoginRequiredMixin, View):
    def post(self, request, view_id):
        get_object_or_404(SavedDocumentView, pk=view_id, owner=request.user)
        delete_saved_view(view_id, user=request.user)
        messages.success(request, "Private saved view deleted.")
        return redirect("documents:saved_views")

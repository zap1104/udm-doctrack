"""Small explicit review screens over existing repository permissions."""

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.db.models import OuterRef, Subquery
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache

from apps.core.mixins import AppLoginRequiredMixin, SystemAdminRequiredMixin
from apps.core.pagination import paginate
from apps.core.utils import office_context_url

from .file_versions import DocumentFileVersion
from .intelligence_forms import (
    AliasApprovalForm,
    CompleteTagReviewForm,
    TagMergeForm,
    TagReviewForm,
    TrainingEligibilityForm,
)
from .ml.eligibility import set_training_eligibility
from .ml.models import TagModel
from .models import Collection, ExtractionUnit, TagAlias
from .processing_contracts import coverage_summary
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


def _source_redirect(request, document, fragment):
    url = reverse("documents:source_units", args=[document.pk]) + fragment
    return redirect(office_context_url(url, request.GET) if document.is_external else url)


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
    from .reconciliation import index_status

    revision, vocabulary = input_revision(document), vocabulary_digest()
    initial = {"input_revision": revision, "vocabulary": vocabulary}
    sample = request.GET.get("review_sample", "")
    if sample:
        from .review_sampling import validate_sample

        if len(request.GET.getlist("review_sample")) != 1 or len(sample) > 4096:
            raise ValidationError("Open one valid review sample from the queue.")
        validate_sample(sample, document, request.user)
        initial["review_sample"] = sample
    context = paginate(
        request, current_units(document).defer("raw_text", "native_raw_text", "ocr_raw_text", "normalized_text")
    )
    may_edit = document.can_user_edit(request.user)
    context.update(
        document=document,
        units=context["page_obj"].object_list,
        coverage=coverage_summary(document),
        can_edit=may_edit,
        can_generate=may_edit
        and settings.ENABLE_TAG_ML
        and (request.user.is_system_admin or request.user.office_id == document.office_id),
        input_revision=revision,
        index_status=index_status(document),
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
        try:
            context = review_context(request, document)
        except ValidationError as exc:
            from django.http import HttpResponseBadRequest

            return HttpResponseBadRequest(" ".join(exc.messages))
        return render(request, "documents/intelligence/units.html", context)


@method_decorator(never_cache, name="dispatch")
class TagReviewQueueView(AppLoginRequiredMixin, View):
    def get(self, request):
        from .review_sampling import review_samples

        collection = request.GET.get("collection", Collection.OVPA)
        seed = request.GET.get("seed", "review-1")
        context = {"collection": collection, "seed": seed, "office": request.GET.get("office", "")}
        try:
            if any(len(request.GET.getlist(key)) > 1 for key in ("collection", "seed", "office")):
                raise ValidationError("Choose one value for each review filter.")
            try:
                office_id = int(request.GET["office"]) if request.GET.get("office") else None
            except ValueError as exc:
                raise ValidationError("Choose a valid internal office.") from exc
            context["samples"] = review_samples(
                user=request.user, collection=collection, seed=seed, office_id=office_id
            )
        except ValidationError as exc:
            context.update(samples=[], scope_error=" ".join(exc.messages))
            return render(request, "documents/intelligence/review_queue.html", context, status=400)
        return render(request, "documents/intelligence/review_queue.html", context)


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


class TagPredictView(AppLoginRequiredMixin, View):
    def post(self, request, pk):
        from .ml.inference import predict_tags

        document = _get_document(request, pk)
        if not document.can_user_edit(request.user):
            raise PermissionDenied("Only an authorized editor can request topic proposals.")
        if (
            any(len(request.POST.getlist(key)) != 1 for key in ("input_revision", "vocabulary"))
            or request.POST.get("input_revision") != input_revision(document)
            or request.POST.get("vocabulary") != vocabulary_digest()
        ):
            messages.error(request, "The content or vocabulary changed. Reload before requesting proposals.")
            return render(request, "documents/intelligence/units.html", review_context(request, document), status=409)
        result = predict_tags(document, user=request.user)
        if result.mode == "fallback":
            messages.warning(request, "No eligible local model is available. Manual topic review remains available.")
        elif result.mode == "shadow":
            messages.info(request, "Shadow predictions recorded. They do not change visible suggestions or tags.")
        else:
            messages.success(request, "Current topic proposals generated. Review them before assigning tags.")
        return _source_redirect(request, document, "#tag-review")


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
                    "sampling_token": data["review_sample"],
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
                return _source_redirect(request, document, "#tag-review")
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
                return _source_redirect(request, document, "#training-eligibility")
            except ValidationError as exc:
                form.add_error(None, exc)
                status = 409
        context = review_context(request, document)
        context["eligibility_form"] = form
        return render(request, "documents/intelligence/units.html", context, status=status)


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

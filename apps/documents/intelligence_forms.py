from django import forms

from apps.core.forms import BootstrapFormMixin
from apps.core.models import Tag

from .models import Collection
from .tagging import reviewable_tags


class TagReviewForm(BootstrapFormMixin, forms.Form):
    tag = forms.ModelChoiceField(queryset=Tag.objects.none(), label="Topic tag")
    decision = forms.ChoiceField(
        choices=[("confirm", "Confirm"), ("reject", "Reject"), ("add", "Add"), ("unsure", "Not sure")]
    )
    input_revision = forms.CharField(widget=forms.HiddenInput)
    vocabulary = forms.CharField(widget=forms.HiddenInput)
    review_sample = forms.CharField(required=False, max_length=4096, widget=forms.HiddenInput)

    def __init__(self, *args, document, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tag"].queryset = reviewable_tags(document, user)


class CompleteTagReviewForm(BootstrapFormMixin, forms.Form):
    covered = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.none(),
        label="Tags covered by this review",
        help_text="Only these tags will receive a positive or negative label. Everything else remains unknown.",
    )
    positive = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.none(), required=False, label="Confirmed tags from that selection"
    )
    input_revision = forms.CharField(widget=forms.HiddenInput)
    vocabulary = forms.CharField(widget=forms.HiddenInput)
    review_sample = forms.CharField(required=False, max_length=4096, widget=forms.HiddenInput)

    def __init__(self, *args, document, user, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("covered", "positive"):
            self.fields[name].queryset = reviewable_tags(document, user)


class TrainingEligibilityForm(BootstrapFormMixin, forms.Form):
    eligible = forms.BooleanField(required=False, label="Allow scoped local topic training for this document")
    family = forms.CharField(
        required=False,
        max_length=64,
        label="Related document family",
        help_text="Use the same label for close copies or templates so they stay in one training partition.",
    )
    input_revision = forms.CharField(widget=forms.HiddenInput)


class SavedViewForm(BootstrapFormMixin, forms.Form):
    name = forms.CharField(max_length=80, label="View name")
    collection = forms.ChoiceField(choices=Collection.choices)
    filters = forms.CharField(required=False, max_length=4000, widget=forms.HiddenInput)


class AliasApprovalForm(BootstrapFormMixin, forms.Form):
    tag = forms.ModelChoiceField(
        queryset=Tag.objects.filter(is_active=True, canonical__isnull=True, globally_approved=True),
        label="Approved shared topic",
    )
    phrase = forms.CharField(max_length=128, label="Acronym or synonym")


class TagMergeForm(BootstrapFormMixin, forms.Form):
    source = forms.ModelChoiceField(
        queryset=Tag.objects.filter(is_active=True, canonical__isnull=True), label="Retire this duplicate topic"
    )
    target = forms.ModelChoiceField(
        queryset=Tag.objects.filter(is_active=True, canonical__isnull=True), label="Keep this canonical topic"
    )
    reason = forms.CharField(max_length=1000, widget=forms.Textarea(attrs={"rows": 3}), label="Merge reason")

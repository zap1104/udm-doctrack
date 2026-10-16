"""Filters and upload controls for the separate external collection."""

from django import forms

from apps.accounts.models import Office
from apps.core.forms import BootstrapFormMixin, DateInput
from apps.core.models import DocumentType, Tag
from apps.tracking.models import ExternalOffice

from .forms import UploadForm
from .models import Collection, Document, Source


class ExternalUploadForm(UploadForm):
    external_offices = forms.ModelMultipleChoiceField(
        queryset=ExternalOffice.objects.none(),
        required=True,
        label="External UDM offices",
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        offices = ExternalOffice.objects.filter(is_active=True)
        if not user.is_system_admin:
            offices = offices.filter(managing_office_id=user.office_id)
        self.fields["external_offices"].queryset = offices

    def clean(self):
        data = super().clean()
        managing = data.get("office")
        if managing and any(office.managing_office_id != managing.pk for office in data.get("external_offices", [])):
            self.add_error("external_offices", "Choose external offices managed by the owning office.")
        return data


class ExternalRepositoryFilterForm(BootstrapFormMixin, forms.Form):
    q = forms.CharField(required=False, label="Search")
    office = forms.ModelChoiceField(queryset=Office.objects.none(), required=False, label="Managing office")
    external_office = forms.ModelChoiceField(
        queryset=ExternalOffice.objects.none(), required=False, label="External counterpart"
    )
    document_type = forms.ModelChoiceField(queryset=DocumentType.objects.none(), required=False)
    tag = forms.ModelChoiceField(queryset=Tag.objects.none(), required=False)
    year = forms.IntegerField(required=False, min_value=1, max_value=9999)
    source = forms.ChoiceField(required=False, label="Document origin", choices=[("", "All origins"), *Source.choices])
    date_from = forms.DateField(required=False, label="Document date from", widget=DateInput())
    date_to = forms.DateField(required=False, label="Document date to", widget=DateInput())
    retention = forms.ChoiceField(
        required=False,
        label="Deletion review",
        choices=[
            ("", "All review dates"),
            ("due", "Review due"),
            ("soon", "Due within 90 days"),
            ("unscheduled", "Unscheduled"),
        ],
    )

    def __init__(self, *args, user, visible=None, **kwargs):
        super().__init__(*args, **kwargs)
        visible = (
            visible
            if visible is not None
            else Document.objects.visible_to(user).in_collection(Collection.EXTERNAL).filter(is_active=True)
        )
        self.fields["office"].queryset = Office.objects.filter(pk__in=visible.values("office_id"))
        self.fields["external_office"].queryset = ExternalOffice.objects.filter(
            document_links__document__in=visible
        ).distinct()
        self.fields["document_type"].queryset = (
            DocumentType.objects.filter(typed_documents__in=visible).distinct()
            | DocumentType.objects.filter(documents__in=visible).distinct()
        )
        self.fields["tag"].queryset = Tag.objects.filter(documents__in=visible).distinct()
        for name, label in {
            "office": "All authorized managing offices",
            "external_office": "All authorized counterparts",
            "document_type": "All document types",
            "tag": "All tags",
        }.items():
            self.fields[name].empty_label = label

    def clean(self):
        data = super().clean()
        if data.get("date_from") and data.get("date_to") and data["date_from"] > data["date_to"]:
            self.add_error("date_to", "End date must follow the start date.")
        return data

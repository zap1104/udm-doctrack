from django.urls import path

from . import external_views as views
from .version_views import DiscardFileView, UploadVersionView

urlpatterns = [
    path("", views.ExternalListView.as_view(), name="external_list"),
    path("new/", views.ExternalCreateView.as_view(), name="external_create"),
    path("reports/", views.ExternalReportsView.as_view(), name="external_reports"),
    path("export/", views.ExternalExportView.as_view(), name="external_export"),
    path("offices/", views.ExternalOfficeListView.as_view(), name="external_offices"),
    path("offices/new/", views.ExternalOfficeEditView.as_view(), name="external_office_create"),
    path("offices/<int:pk>/edit/", views.ExternalOfficeEditView.as_view(), name="external_office_edit"),
    path(
        "offices/<int:pk>/deactivate/", views.ExternalOfficeDeactivateView.as_view(), name="external_office_deactivate"
    ),
    path("attachments/<int:pk>/", views.ExternalAttachmentDownloadView.as_view(), name="external_attachment"),
    path("<int:pk>/", views.ExternalDetailView.as_view(), name="external_detail"),
    path("<int:pk>/files/<int:group_pk>/versions/", UploadVersionView.as_view(external=True), name="external_file_version"),
    path("<int:pk>/files/<int:group_pk>/discard/", DiscardFileView.as_view(external=True), name="external_file_discard"),
    path("<int:pk>/review/", views.ExternalReviewView.as_view(), name="external_review"),
    path("<int:pk>/slip/", views.ExternalSlipView.as_view(), name="external_slip"),
    path("<int:pk>/correct/<int:step_pk>/", views.ExternalCorrectionView.as_view(), name="external_correct"),
    path("<int:pk>/actions/<slug:action>/", views.ExternalActionView.as_view(), name="external_action"),
]

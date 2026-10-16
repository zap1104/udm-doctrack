from django.urls import path

from . import external_views, views

app_name = "documents"

urlpatterns = [
    path("external/", external_views.ExternalRepositoryView.as_view(), name="external_repository"),
    path("external/upload/", external_views.ExternalUploadView.as_view(), name="external_upload"),
    path("external/reports/", external_views.ExternalRepositoryReportView.as_view(), name="external_reports"),
    path("external/deletion-review/", external_views.ExternalDeletionQueueView.as_view(), name="external_deletion_queue"),
    path("", views.RepositoryView.as_view(), name="repository"),
    path("upload/", views.UploadView.as_view(), name="upload"),
    path("deletion-review/", views.DeletionQueueView.as_view(), name="deletion_queue"),
    path("<int:pk>/deletion-review/", views.DeletionReviewView.as_view(), name="deletion_review"),
    path("<int:pk>/review/", views.MetadataReviewView.as_view(), name="review"),
    path("<int:pk>/", views.DocumentDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.DocumentEditView.as_view(), name="edit"),
    path("<int:pk>/files/", views.AddFilesView.as_view(), name="add_files"),
    path("<int:pk>/re-extract/", views.ReExtractView.as_view(), name="re_extract"),
    path("<int:pk>/extraction-status/", views.ExtractionStatusView.as_view(), name="extraction_status"),
    path("file/<int:pk>/", views.DocumentFileDownloadView.as_view(), name="file_download"),
    path("tags/suggest/", views.TagSuggestJsonView.as_view(), name="tag_suggest"),
]

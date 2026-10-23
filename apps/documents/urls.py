from django.urls import path

from . import external_views, intelligence_views, views

app_name = "documents"

urlpatterns = [
    path("intelligence/review-queue/", intelligence_views.TagReviewQueueView.as_view(), name="tag_review_queue"),
    path("intelligence/vocabulary/", intelligence_views.VocabularyView.as_view(), name="tag_vocabulary"),
    path("<int:pk>/extraction/units/", intelligence_views.SourceUnitsView.as_view(), name="source_units"),
    path("<int:pk>/extraction/units/<int:unit_id>/", intelligence_views.SourceUnitView.as_view(), name="source_unit"),
    path("<int:pk>/tags/review/", intelligence_views.TagReviewView.as_view(), name="tag_review"),
    path("<int:pk>/tags/predict/", intelligence_views.TagPredictView.as_view(), name="tag_predict"),
    path("<int:pk>/tags/review-complete/", intelligence_views.TagReviewView.as_view(complete=True), name="tag_review_complete"),
    path("<int:pk>/training-eligibility/", intelligence_views.TrainingEligibilityView.as_view(), name="training_eligibility"),
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

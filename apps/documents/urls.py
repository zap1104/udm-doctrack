from django.urls import path

from . import views

app_name = "documents"

urlpatterns = [
    path("", views.RepositoryView.as_view(), name="repository"),
    path("upload/", views.UploadView.as_view(), name="upload"),
    path("<int:pk>/review/", views.MetadataReviewView.as_view(), name="review"),
    path("<int:pk>/", views.DocumentDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.DocumentEditView.as_view(), name="edit"),
    path("<int:pk>/files/", views.AddFilesView.as_view(), name="add_files"),
    path("<int:pk>/re-extract/", views.ReExtractView.as_view(), name="re_extract"),
    path("<int:pk>/extraction-status/", views.ExtractionStatusView.as_view(), name="extraction_status"),
    path("file/<int:pk>/", views.DocumentFileDownloadView.as_view(), name="file_download"),
    path("tags/suggest/", views.TagSuggestJsonView.as_view(), name="tag_suggest"),
]

from django.urls import path

from . import views

app_name = "tracking"

urlpatterns = [
    path("", views.RecordListView.as_view(), name="list"),
    path("new/", views.RecordCreateView.as_view(), name="create"),
    path("<int:pk>/review/", views.RecordReviewView.as_view(), name="review"),
    path("<int:pk>/", views.RecordDetailView.as_view(), name="detail"),
    path("<int:pk>/receipt/", views.ConfirmReceiptView.as_view(), name="confirm_receipt"),
    path("<int:pk>/remark/", views.AddRemarkView.as_view(), name="add_remark"),
    path("<int:pk>/route/", views.RouteRecordView.as_view(), name="route"),
    path("<int:pk>/complete/", views.CompleteRecordView.as_view(), name="complete"),
    path("<int:pk>/archive/", views.ArchiveRecordView.as_view(), name="archive"),
    path("<int:pk>/reopen/", views.ReopenRecordView.as_view(), name="reopen"),
    path("<int:pk>/share/", views.GrantAccessView.as_view(), name="grant_access"),
    path("<int:pk>/slip/", views.RoutingSlipView.as_view(), name="routing_slip"),
    path("attachment/<int:pk>/", views.AttachmentDownloadView.as_view(), name="attachment_download"),
]

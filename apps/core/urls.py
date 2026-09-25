from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = "core"

urlpatterns = [
    path("healthz/", views.HealthzView.as_view(), name="healthz"),
    path("notifications/", views.NotificationListView.as_view(), name="notifications"),
    path("notifications/count/", views.NotificationCountView.as_view(), name="notification_count"),
    path("notifications/read-all/", views.NotificationMarkAllReadView.as_view(), name="notification_mark_all_read"),
    path("notifications/<int:pk>/read/", views.NotificationReadView.as_view(), name="notification_read"),
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("memo/print/", views.DashboardMemoPrintView.as_view(), name="dashboard_memo_print"),
    # Reports is a part of Document Tracking, so it lives under its path and the
    # sidebar highlights Document Tracking there. The names are unchanged, and
    # the old addresses redirect permanently, query string and all, so a
    # bookmarked or printed link to a report still opens that report.
    path("tracking/reports/", views.ReportsView.as_view(), name="reports"),
    path("tracking/reports/export/", views.ReportExportView.as_view(), name="report_export"),
    path("reports/", RedirectView.as_view(pattern_name="core:reports", permanent=True, query_string=True)),
    path(
        "reports/export/",
        RedirectView.as_view(pattern_name="core:report_export", permanent=True, query_string=True),
    ),
    path("print-log/", views.PrintLogView.as_view(), name="log_print"),
    path("administration/", views.AdministrationHomeView.as_view(), name="administration"),
    path("administration/audit-log/", views.AuditLogView.as_view(), name="audit_log"),
    path("administration/<slug:slug>/", views.MasterDataListView.as_view(), name="masterdata_list"),
    path("administration/<slug:slug>/new/", views.MasterDataEditView.as_view(), name="masterdata_create"),
    path("administration/<slug:slug>/<int:pk>/", views.MasterDataEditView.as_view(), name="masterdata_edit"),
]

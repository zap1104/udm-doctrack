from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.core import views as core_views

urlpatterns = [
    path("", include("apps.core.urls")),
    path("accounts/", include("apps.accounts.urls")),
    path("tracking/", include("apps.tracking.urls")),
    path("documents/", include("apps.documents.urls")),
    path("search/", include("apps.search.urls")),
    path("django-admin/", admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

handler403 = core_views.error_403
handler404 = core_views.error_404
handler500 = core_views.error_500

admin.site.site_header = "UDM DocTrack — Django administration"
admin.site.site_title = "UDM DocTrack"
admin.site.index_title = "Master data and raw records"

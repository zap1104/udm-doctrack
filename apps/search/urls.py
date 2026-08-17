from django.urls import path

from . import views

app_name = "search"

urlpatterns = [
    path("", views.SearchView.as_view(), name="index"),
    path("autocomplete/", views.AutocompleteView.as_view(), name="autocomplete"),
    path("click/<int:log_id>/<int:document_id>/<int:rank>/", views.SearchClickView.as_view(), name="click"),
]

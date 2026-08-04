from django.urls import path

from . import views

app_name = "search"

urlpatterns = [
    path("", views.SearchView.as_view(), name="index"),
    path("autocomplete/", views.AutocompleteView.as_view(), name="autocomplete"),
]

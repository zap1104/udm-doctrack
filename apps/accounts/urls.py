from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.SignInView.as_view(), name="login"),
    path("logout/", views.SignOutView.as_view(), name="logout"),
    path("password/", views.PasswordChangeViewCustom.as_view(), name="password_change"),
    path("profile/", views.ProfileView.as_view(), name="profile"),
    path("users/", views.UserListView.as_view(), name="user_list"),
    path("users/new/", views.UserCreateView.as_view(), name="user_create"),
    path("users/<int:pk>/", views.UserUpdateView.as_view(), name="user_edit"),
    path("users/<int:pk>/toggle/", views.UserToggleActiveView.as_view(), name="user_toggle"),
    path("offices/", views.OfficeListView.as_view(), name="office_list"),
    path("offices/new/", views.OfficeEditView.as_view(), name="office_create"),
    path("offices/<int:pk>/", views.OfficeEditView.as_view(), name="office_edit"),
]

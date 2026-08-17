from django.conf import settings
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.SignInView.as_view(), name="login"),
    path("password-reset/", views.PasswordResetRequestView.as_view(), name="password_reset"),
    path("password-reset/done/", views.PasswordResetDonePage.as_view(), name="password_reset_done"),
    path("password-reset/<uidb64>/<token>/", views.PasswordResetConfirmPage.as_view(), name="password_reset_confirm"),
    path("password-reset/complete/", views.PasswordResetCompletePage.as_view(), name="password_reset_complete"),
    path("logout/", views.SignOutView.as_view(), name="logout"),
    path("password/", views.PasswordChangeViewCustom.as_view(), name="password_change"),
    path("session/keep-alive/", views.SessionKeepAliveView.as_view(), name="session_keep_alive"),
    path("profile/", views.ProfileView.as_view(), name="profile"),
    path("users/", views.UserListView.as_view(), name="user_list"),
    path("users/new/", views.UserCreateView.as_view(), name="user_create"),
    path("users/<int:pk>/", views.UserUpdateView.as_view(), name="user_edit"),
    path("users/<int:pk>/toggle/", views.UserToggleActiveView.as_view(), name="user_toggle"),
    path("offices/", views.OfficeListView.as_view(), name="office_list"),
    path("offices/new/", views.OfficeEditView.as_view(), name="office_create"),
    path("offices/<int:pk>/", views.OfficeEditView.as_view(), name="office_edit"),
]

if getattr(settings, "ENABLE_AXES", False):
    from . import axes_hooks

    urlpatterns.append(path("locked/", axes_hooks.lockout_status_view, name="lockout"))

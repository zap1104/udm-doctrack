from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.views import (
    LoginView,
    LogoutView,
    PasswordChangeView,
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.core.cache import cache
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import TemplateView, View

from apps.core.middleware import idle_seconds_for
from apps.core.mixins import AdminRequiredMixin, AppLoginRequiredMixin, SystemAdminRequiredMixin
from apps.core.models import AuditLog, NotificationPreference
from apps.core.utils import log_action

from .forms import (
    AdminSetPasswordForm,
    AdminUserCreateForm,
    AdminUserUpdateForm,
    NotificationPreferenceForm,
    OfficeForm,
    ProfileForm,
    SignInForm,
)
from .models import Office, User


class PasswordResetAvailableMixin:
    def dispatch(self, request, *args, **kwargs):
        if not settings.EMAIL_CONFIGURED:
            messages.info(request, "Password recovery email is not configured. Contact the system administrator.")
            return redirect("accounts:login")
        return super().dispatch(request, *args, **kwargs)


class PasswordResetRequestView(PasswordResetAvailableMixin, PasswordResetView):
    template_name = "accounts/password_reset_form.html"
    email_template_name = "accounts/password_reset_email.txt"
    subject_template_name = "accounts/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")

    def form_valid(self, form):
        key = f"password-reset:{self.request.META.get('REMOTE_ADDR', '')}"
        attempts = cache.get(key, 0)
        if attempts >= 5:
            return redirect(self.success_url)
        cache.set(key, attempts + 1, 60 * 60)
        response = super().form_valid(form)
        log_action(AuditLog.Action.UPDATE, "Password recovery requested", request=self.request, extra={"kind": "password_reset"})
        return response


class PasswordResetDonePage(PasswordResetAvailableMixin, PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class PasswordResetConfirmPage(PasswordResetAvailableMixin, PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        log_action(AuditLog.Action.UPDATE, "Password recovery completed", actor=getattr(self, "user", None), extra={"kind": "password_reset"})
        return response


class PasswordResetCompletePage(PasswordResetAvailableMixin, PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"


class SignInView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = SignInForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        if self.request.user.must_change_password:
            messages.info(self.request, "Please set a new password before you continue.")
            return redirect("accounts:password_change")
        return response


class SignOutView(LogoutView):
    next_page = reverse_lazy("accounts:login")


class PasswordChangeViewCustom(AppLoginRequiredMixin, PasswordChangeView):
    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("core:dashboard")

    def form_valid(self, form):
        response = super().form_valid(form)
        User.objects.filter(pk=self.request.user.pk).update(must_change_password=False)
        update_session_auth_hash(self.request, form.user)
        # Only now is the sign-in fully complete, so this is where a forced
        # password change forgives the lockout escalation (when configured to).
        if getattr(settings, "AXES_ESCALATION_RESET_ON_LOGIN", False):
            from .axes_hooks import clear_escalation

            clear_escalation(username=self.request.user.get_username())
        messages.success(self.request, "Password updated.")
        log_action(AuditLog.Action.UPDATE, "Changed own password", actor=self.request.user, request=self.request)
        return response


class SessionKeepAliveView(AppLoginRequiredMixin, View):
    """Push the idle sign-out back, and report how long is now left.

    POST-only on purpose. Extending a session is a state change, and a GET that
    silently kept people signed in could be triggered by a prefetch or a stray
    link — the browser deciding on the user's behalf that somebody is still at
    the desk.

    There is nothing to do in the body: SESSION_SAVE_EVERY_REQUEST means the
    session middleware rewrites the expiry on the way out of any request from a
    signed-in user. The value returned is therefore what the expiry *will* be
    once this response is written, not what it was on the way in.
    """

    def post(self, request):
        # The role's own window, not the project default — an administrator's
        # countdown must not be reset to a figure longer than their session.
        return JsonResponse(
            {"seconds_remaining": idle_seconds_for(request.user), "authenticated": True}
        )


class ProfileView(AppLoginRequiredMixin, View):
    template_name = "accounts/profile.html"

    def get(self, request):
        preferences, _ = NotificationPreference.objects.get_or_create(user=request.user)
        return render(request, self.template_name, {"form": ProfileForm(instance=request.user), "preferences_form": NotificationPreferenceForm(instance=preferences)})

    def post(self, request):
        form = ProfileForm(request.POST, instance=request.user)
        preferences, _ = NotificationPreference.objects.get_or_create(user=request.user)
        preferences_form = NotificationPreferenceForm(request.POST, instance=preferences)
        if form.is_valid() and preferences_form.is_valid():
            form.save()
            preferences_form.save()
            messages.success(request, "Profile and notification preferences updated.")
            return redirect("accounts:profile")
        return render(request, self.template_name, {"form": form, "preferences_form": preferences_form})


# ---------------------------------------------------------------------------
# Administration — users
# ---------------------------------------------------------------------------
class OfficeScopedUserMixin:
    """Every account screen sees only the accounts its user may administer.

    Without this the administration area was scoped by role and by nothing
    else: an office administrator could list, edit, rename, reset the password
    of, and suspend every account in the university, including the system
    administrators. The role split in this change is what makes that a live
    problem rather than a latent one — before it, everyone who could reach these
    screens was global by definition.

    Scoping the *queryset* rather than adding a check to each view is deliberate:
    a missed check is a silent hole, whereas a missed queryset is a 404.
    """

    def administrable_users(self):
        users = User.objects.select_related("office")
        if self.request.user.is_system_admin:
            return users
        # An office administrator with no office of their own administers
        # nobody. `filter(office_id=None)` would instead hand them every
        # unassigned account in the system.
        if not self.request.user.office_id:
            return users.none()
        return users.filter(office_id=self.request.user.office_id)

    def selectable_offices(self):
        if self.request.user.is_system_admin:
            return Office.active.all()
        return Office.active.filter(pk=self.request.user.office_id)


class UserListView(OfficeScopedUserMixin, AdminRequiredMixin, TemplateView):
    template_name = "administration/users.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        users = self.administrable_users()
        query = self.request.GET.get("q", "").strip()
        office = self.request.GET.get("office", "")
        if query:
            users = users.filter(
                Q(username__icontains=query) | Q(first_name__icontains=query) | Q(last_name__icontains=query)
            )
        if office:
            users = users.filter(office_id=office)
        context.update(
            {
                "users": users,
                "offices": self.selectable_offices(),
                "query": query,
                "selected_office": office,
                "is_office_scoped": not self.request.user.is_system_admin,
            }
        )
        return context


class UserCreateView(OfficeScopedUserMixin, AdminRequiredMixin, View):
    template_name = "administration/user_form.html"

    def get(self, request):
        return render(
            request,
            self.template_name,
            {"form": AdminUserCreateForm(actor=request.user), "is_new": True},
        )

    def post(self, request):
        form = AdminUserCreateForm(request.POST, actor=request.user)
        if form.is_valid():
            user = form.save()
            log_action(
                AuditLog.Action.CREATE,
                f"Created account “{user.username}” ({user.get_role_display()})",
                actor=request.user,
                target=user,
                request=request,
            )
            messages.success(
                request,
                f"Account created for {user.display_name}. Give them the username and the password you set.",
            )
            return redirect("accounts:user_list")
        messages.error(request, "Check the highlighted fields.")
        return render(request, self.template_name, {"form": form, "is_new": True})


class UserUpdateView(OfficeScopedUserMixin, AdminRequiredMixin, View):
    template_name = "administration/user_form.html"

    def get(self, request, pk):
        # Scoped queryset, so an account outside the administrator's office is a
        # 404 rather than an editable form.
        user = get_object_or_404(self.administrable_users(), pk=pk)
        return render(
            request,
            self.template_name,
            {"form": AdminUserUpdateForm(instance=user, editing_self=user.pk == request.user.pk,
                                         actor=request.user),
             "edited_user": user, "is_new": False,
             "password_form": AdminSetPasswordForm(user)},
        )

    def post(self, request, pk):
        user = get_object_or_404(self.administrable_users(), pk=pk)
        if "set_password" in request.POST:
            password_form = AdminSetPasswordForm(user, request.POST)
            if password_form.is_valid():
                password_form.save()
                User.objects.filter(pk=user.pk).update(must_change_password=True)
                log_action(
                    AuditLog.Action.UPDATE,
                    f"Reset the password of “{user.username}”",
                    actor=request.user,
                    target=user,
                    request=request,
                )
                messages.success(request, f"New password set for {user.display_name}.")
                return redirect("accounts:user_edit", pk=user.pk)
            return render(
                request,
                self.template_name,
                {"form": AdminUserUpdateForm(instance=user, editing_self=user.pk == request.user.pk,
                                             actor=request.user),
                 "edited_user": user, "is_new": False,
                 "password_form": password_form},
            )

        form = AdminUserUpdateForm(
            request.POST, instance=user, editing_self=user.pk == request.user.pk,
            actor=request.user,
        )
        if form.is_valid():
            form.save()
            log_action(
                AuditLog.Action.UPDATE, f"Updated account “{user.username}”", actor=request.user,
                target=user, request=request,
            )
            messages.success(request, "Account updated.")
            return redirect("accounts:user_list")
        return render(
            request,
            self.template_name,
            {"form": form, "edited_user": user, "is_new": False, "password_form": AdminSetPasswordForm(user)},
        )


class UserToggleActiveView(OfficeScopedUserMixin, AdminRequiredMixin, View):
    def post(self, request, pk):
        user = get_object_or_404(self.administrable_users(), pk=pk)
        if user.pk == request.user.pk:
            messages.error(request, "You cannot deactivate your own account.")
            return redirect("accounts:user_list")
        user.is_active = not user.is_active
        user.save(update_fields=["is_active"])
        log_action(
            AuditLog.Action.UPDATE,
            f"{'Reactivated' if user.is_active else 'Suspended'} account “{user.username}”",
            actor=request.user,
            target=user,
            request=request,
        )
        messages.success(request, f"{user.display_name} is now {'active' if user.is_active else 'suspended'}.")
        return redirect("accounts:user_list")


# ---------------------------------------------------------------------------
# Administration — offices
# ---------------------------------------------------------------------------
class OfficeListView(SystemAdminRequiredMixin, TemplateView):
    """Offices are not any one office's to edit — see SystemAdminRequiredMixin.

    This screen was open to every administrator, which after the role split
    would have let an office head rename or deactivate other offices.
    """

    template_name = "administration/offices.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["offices"] = Office.objects.all().annotate(member_count=Count("members", distinct=True))
        return context


class OfficeEditView(SystemAdminRequiredMixin, View):
    template_name = "administration/office_form.html"

    def get(self, request, pk=None):
        office = get_object_or_404(Office, pk=pk) if pk else None
        return render(request, self.template_name, {"form": OfficeForm(instance=office), "office": office})

    def post(self, request, pk=None):
        office = get_object_or_404(Office, pk=pk) if pk else None
        form = OfficeForm(request.POST, instance=office)
        if form.is_valid():
            saved = form.save()
            log_action(
                AuditLog.Action.UPDATE if pk else AuditLog.Action.CREATE,
                f"{'Updated' if pk else 'Created'} office “{saved.name}”",
                actor=request.user,
                target=saved,
                request=request,
            )
            messages.success(request, f"Saved office “{saved.name}”.")
            return redirect("accounts:office_list")
        messages.error(request, "Check the highlighted fields.")
        return render(request, self.template_name, {"form": form, "office": office})

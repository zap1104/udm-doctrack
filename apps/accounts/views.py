from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.views import LoginView, LogoutView, PasswordChangeView
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views.generic import TemplateView, View

from apps.core.mixins import AdminRequiredMixin, AppLoginRequiredMixin
from apps.core.models import AuditLog
from apps.core.utils import log_action

from .forms import (
    AdminSetPasswordForm,
    AdminUserCreateForm,
    AdminUserUpdateForm,
    OfficeForm,
    ProfileForm,
    SignInForm,
)
from .models import Office, User


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
        messages.success(self.request, "Password updated.")
        log_action(AuditLog.Action.UPDATE, "Changed own password", actor=self.request.user, request=self.request)
        return response


class ProfileView(AppLoginRequiredMixin, View):
    template_name = "accounts/profile.html"

    def get(self, request):
        return render(request, self.template_name, {"form": ProfileForm(instance=request.user)})

    def post(self, request):
        form = ProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Profile updated.")
            return redirect("accounts:profile")
        return render(request, self.template_name, {"form": form})


# ---------------------------------------------------------------------------
# Administration — users
# ---------------------------------------------------------------------------
class UserListView(AdminRequiredMixin, TemplateView):
    template_name = "administration/users.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        users = User.objects.select_related("office")
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
                "offices": Office.active.all(),
                "query": query,
                "selected_office": office,
            }
        )
        return context


class UserCreateView(AdminRequiredMixin, View):
    template_name = "administration/user_form.html"

    def get(self, request):
        return render(request, self.template_name, {"form": AdminUserCreateForm(), "is_new": True})

    def post(self, request):
        form = AdminUserCreateForm(request.POST)
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


class UserUpdateView(AdminRequiredMixin, View):
    template_name = "administration/user_form.html"

    def get(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        return render(
            request,
            self.template_name,
            {"form": AdminUserUpdateForm(instance=user), "edited_user": user, "is_new": False,
             "password_form": AdminSetPasswordForm(user)},
        )

    def post(self, request, pk):
        user = get_object_or_404(User, pk=pk)
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
                {"form": AdminUserUpdateForm(instance=user), "edited_user": user, "is_new": False,
                 "password_form": password_form},
            )

        form = AdminUserUpdateForm(request.POST, instance=user)
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


class UserToggleActiveView(AdminRequiredMixin, View):
    def post(self, request, pk):
        user = get_object_or_404(User, pk=pk)
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
class OfficeListView(AdminRequiredMixin, TemplateView):
    template_name = "administration/offices.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["offices"] = Office.objects.all().annotate(member_count=Count("members", distinct=True))
        return context


class OfficeEditView(AdminRequiredMixin, View):
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

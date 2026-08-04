"""Access-control mixins.

Rule of the system (from the flowchart):
a regular user only sees documents that were routed to, assigned to,
originated by, or explicitly granted to their office or account.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect


class AppLoginRequiredMixin(LoginRequiredMixin):
    """Login required + a clear message instead of a silent redirect."""

    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            messages.info(self.request, "Sign in to continue.")
        return super().handle_no_permission()


class RoleRequiredMixin(AppLoginRequiredMixin):
    """Restrict a view to a set of roles. `allowed_roles` is a tuple of role codes."""

    allowed_roles: tuple[str, ...] = ()
    permission_message = "Your account does not have access to this page."

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and self.allowed_roles:
            if request.user.role not in self.allowed_roles and not request.user.is_superuser:
                raise PermissionDenied(self.permission_message)
        return super().dispatch(request, *args, **kwargs)


class AdminRequiredMixin(RoleRequiredMixin):
    allowed_roles = ("ADMIN",)
    permission_message = "Only system administrators can open the administration area."


class RecordsStaffRequiredMixin(RoleRequiredMixin):
    allowed_roles = ("ADMIN", "SECRETARY")
    permission_message = "Only records personnel and administrators can do this."


class OfficeAssignedMixin(AppLoginRequiredMixin):
    """Blocks users whose account has no office yet — they cannot route anything."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.office_id is None and not request.user.is_superuser:
            messages.warning(
                request,
                "Your account is not assigned to an office yet. Ask the system administrator to set one.",
            )
            return redirect("core:dashboard")
        return super().dispatch(request, *args, **kwargs)

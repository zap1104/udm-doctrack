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
    """Any administrator. What they may then *see* is still office-scoped —
    this only says they may open the administration area at all, and a view
    that lists other people's accounts must narrow the queryset itself.
    """

    allowed_roles = ("ADMIN", "SYSTEM_ADMIN")
    permission_message = "Only administrators can open the administration area."


class SystemAdminRequiredMixin(RoleRequiredMixin):
    """For settings that are not any one office's to change."""

    allowed_roles = ("SYSTEM_ADMIN",)
    permission_message = "Only system administrators can change this."


class RecordsStaffRequiredMixin(RoleRequiredMixin):
    """Records work on the office's documents — everyone but a viewer.

    Was ("ADMIN", "SECRETARY"). SECRETARY's accounts became USER in accounts
    migration 0005, so USER is listed here to keep those accounts working; see
    `User.is_records_staff`, which this mirrors.
    """

    allowed_roles = ("USER", "ADMIN", "SYSTEM_ADMIN")
    permission_message = "View-only accounts cannot do this."


class WriteAccessRequiredMixin(AppLoginRequiredMixin):
    """Refuses VIEWER accounts.

    Hiding a button is not a permission — the endpoint stays reachable to anyone
    who knows the URL, and view-only accounts exist precisely because somebody
    should not be able to write. The service layer refuses them too
    (`apps.tracking.services.refuse_viewers`); this is the outer of the two so
    the user gets a page rather than a stack trace.
    """

    permission_message = (
        "Your account has view-only access. Ask your office administrator if you "
        "need to make changes."
    )

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.is_viewer:
            raise PermissionDenied(self.permission_message)
        return super().dispatch(request, *args, **kwargs)


class OfficeAssignedMixin(WriteAccessRequiredMixin):
    """Blocks users whose account has no office yet — they cannot route anything.

    Extends WriteAccessRequiredMixin because the two questions have the same
    answer everywhere they are asked: this mixin guards the views that change a
    record, and those are exactly the views a viewer must not reach.
    """

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.office_id is None and not request.user.is_superuser:
            messages.warning(
                request,
                "Your account is not assigned to an office yet. Ask the system administrator to set one.",
            )
            return redirect("core:dashboard")
        return super().dispatch(request, *args, **kwargs)

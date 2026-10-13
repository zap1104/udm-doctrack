"""Shared direction-and-office-stage navigation for tracking surfaces."""

from apps.core import filters as core_filters

from .models import Status

DIRECTION_STAGES = (
    (Status.PENDING_RECEIPT, "pending-receipt"),
    (Status.RECEIVED, "received"),
    (Status.IN_PROCESS, "in-process"),
)


def direction_groups(*, disabled, make_link, scope="", statuses=(), counts=None):
    """Build identical groups; callers supply native links and optional counts.

    Incoming stages describe the receiving office's receipt and work. Outgoing
    summarizes the current recipients, so it must not use the inbound
    `received` scope.
    """
    groups = []
    for direction, label in (("incoming", "Incoming"), ("outgoing", "Outgoing")):
        stages = []
        for status, slug in DIRECTION_STAGES:
            stages.append({
                "status": status, "slug": f"{direction}-{slug}", "label": status.label,
                "active": scope == direction and set(statuses) == {status},
                "count": counts[direction].get(status, 0) if counts is not None and not disabled else None,
                **(make_link(direction, status) if not disabled else {}),
            })
        groups.append({
            "key": direction, "label": label, "disabled": disabled,
            "active": scope == direction and not statuses,
            "count": sum(stage["count"] for stage in stages) if counts is not None and not disabled else None,
            "stages": stages,
            **(make_link(direction, None) if not disabled else {}),
        })
    return groups


def tracking_view_link(request, resolved, direction="", status=None):
    """Replace direction and stage together, retaining every other valid filter."""
    params = core_filters.tracking_params(request, resolved).copy()
    params.pop("page", None)
    if direction:
        params["scope"] = direction
    else:
        params.pop("scope", None)
    if status:
        params.setlist("status", [status])
    else:
        params.pop("status", None)
    return f"{request.path}?{params.urlencode()}" if params else request.path


def direction_filter_groups(request, resolved):
    """Canonical stage links shared by the Tracking and Search workspaces."""
    def make_link(direction, status):
        return {"href": tracking_view_link(request, resolved, direction, status)}

    return direction_groups(
        disabled=resolved.all_offices or not (resolved.as_office or request.user.office_id),
        make_link=make_link, scope=resolved.scope, statuses=resolved.statuses,
    )

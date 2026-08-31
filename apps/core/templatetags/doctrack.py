"""Template filters used across the UI."""

from __future__ import annotations

from django import template
from django.http import QueryDict
from django.utils.safestring import mark_safe

from apps.core.utils import human_size as _human_size

register = template.Library()


@register.simple_tag(takes_context=True)
def pagination_url(context, page_number) -> str:
    """A link to `page_number` that keeps the current filters and nothing else.

    The pagination partial used to build this by hand as
    `?page=N&{{ request.GET.urlencode }}`, which broke twice over:

    * On any page but the first, `request.GET` already contained `page`, so the
      link came out `?page=3&page=2`. A QueryDict yields the *last* value for a
      repeated key, so Next served page 2 again — once you reached page two of
      Tracking you could not leave it.
    * The Repository page never passed the querystring in at all, so its links
      were a bare `?page=N` and paging through a filtered result silently threw
      the filters away.

    Copying the real query and *assigning* page fixes both: assignment replaces
    the key rather than appending to it, and every other parameter rides along.
    The return value is escaped by the template engine, which is what turns the
    separators into `&amp;` for the href.
    """
    request = context.get("request")
    params = request.GET.copy() if request is not None else QueryDict(mutable=True)
    params["page"] = page_number
    return f"?{params.urlencode()}"


@register.simple_tag(takes_context=True)
def filter_url(context, param, value, multi=False) -> str:
    """A link that turns one filter value on or off, keeping the rest.

    This is what lets a *multi*-select filter be a row of links rather than a
    form: each pill's href already contains the selection that clicking it
    would produce, so there is nothing to submit and no Apply button — which is
    the whole point of the pill design. Selecting three offices is three links,
    each one carrying the previous two along.

    `multi=True` toggles the value in and out of a repeated parameter.
    Otherwise the parameter is replaced, and clicking the value that is already
    active clears it — so a single-select pill row needs no separate "all"
    entry unless the caller wants one for discoverability.

    `page` is always dropped: page four of the old filter is not page four of
    the new one, and keeping it lands the reader on an empty table.
    """
    request = context.get("request")
    params = request.GET.copy() if request is not None else QueryDict(mutable=True)
    params.pop("page", None)

    value = "" if value is None else str(value)
    current = params.getlist(param)

    if multi:
        chosen = [item for item in current if item != value] if value in current else [*current, value]
    elif value == "" or current == [value]:
        chosen = []
    else:
        chosen = [value]

    if chosen:
        params.setlist(param, chosen)
    else:
        params.pop(param, None)

    query = params.urlencode()
    # A bare "?" is a valid same-page link but reads as broken in the status
    # bar, so an empty query returns the path instead.
    return f"?{query}" if query else (request.path if request is not None else "?")


STATUS_PILL = {
    "DRAFT": "pill-draft",
    "PENDING_RECEIPT": "pill-pending",
    "RECEIVED": "pill-received",
    "IN_PROCESS": "pill-process",
    "COMPLETED_PENDING_UPLOAD": "pill-process",
    "COMPLETED": "pill-completed",
    "OVERDUE": "pill-overdue",
}

STATUS_LABEL = {
    "DRAFT": "Draft",
    "PENDING_RECEIPT": "Pending receipt",
    "RECEIVED": "Received",
    "IN_PROCESS": "In process",
    "COMPLETED_PENDING_UPLOAD": "Completed - pending upload",
    "COMPLETED": "Completed",
    "OVERDUE": "Overdue",
}


@register.filter
def status_pill_class(status: str) -> str:
    return STATUS_PILL.get(str(status).upper(), "pill-muted")


@register.filter
def status_label(status: str) -> str:
    return STATUS_LABEL.get(str(status).upper(), str(status).replace("_", " ").title())


@register.filter
def human_size(value) -> str:
    return _human_size(value)


@register.filter
def relevance_class(score) -> str:
    try:
        value = float(score)
    except (TypeError, ValueError):
        return ""
    if value >= 85:
        return "high"
    if value >= 70:
        return ""
    return "low"


@register.filter
def confidence_label(value) -> str:
    """Turns 0.0–1.0 into words a records officer can act on."""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return ""
    if score >= 0.8:
        return "Strong match"
    if score >= 0.55:
        return "Likely"
    if score > 0:
        return "Check this"
    return ""


@register.filter
def dict_get(mapping, key):
    if isinstance(mapping, dict):
        return mapping.get(key)
    return None


@register.filter
def initials(value: str) -> str:
    parts = [part for part in str(value or "").split() if part]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return (str(value or "?")[:2]).upper()


@register.filter
def attr(obj, name: str):
    """Reads a field by name so one template can render any master-data table."""
    value = getattr(obj, str(name), "")
    if callable(value):
        value = value()
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if value is None or value == "":
        return "—"
    getter = getattr(obj, f"get_{name}_display", None)
    if callable(getter):
        return getter()
    return value


@register.simple_tag
def relevance_bar(score) -> str:
    try:
        value = max(0, min(100, int(round(float(score)))))
    except (TypeError, ValueError):
        value = 0
    css = relevance_class(value)
    return mark_safe(
        f'<div class="relevance-bar {css}"><span style="width:{value}%"></span></div>'
    )


@register.filter
def field_type_is(field, widget_name: str) -> bool:
    return field.field.widget.__class__.__name__.lower() == str(widget_name).lower()

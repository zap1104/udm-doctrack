"""Template filters used across the UI."""

from __future__ import annotations

from django import template
from django.utils.safestring import mark_safe

from apps.core.utils import human_size as _human_size

register = template.Library()

STATUS_PILL = {
    "DRAFT": "pill-draft",
    "IN_TRANSIT": "pill-transit",
    "FORWARDED": "pill-forwarded",
    "RETURNED": "pill-returned",
    "RECEIVED": "pill-received",
    "IN_PROCESS": "pill-process",
    "COMPLETED": "pill-completed",
    "OVERDUE": "pill-overdue",
}

STATUS_LABEL = {
    "DRAFT": "Draft",
    "IN_TRANSIT": "In transit",
    "FORWARDED": "Forwarded",
    "RETURNED": "Returned",
    "RECEIVED": "Received",
    "IN_PROCESS": "In process",
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

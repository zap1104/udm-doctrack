"""Single source of truth for the UDM DocTrack brand palette (Python side).

Mirrors the CSS custom properties declared in the :root block at the top of
static/css/doctrack.css — when a new palette is adopted, update the hex
values here and there together.
"""

from __future__ import annotations

#: Brand palette. Keys match the CSS variable names (minus the `--udm-`
#: prefix) so the two files stay easy to cross-check by eye.
PALETTE = {
    "navy": "#0b315a",
    "gold": "#c49a2e",
    "teal": "#16697a",
    "green": "#2e7d5b",
    "red": "#b4342b",
    "muted": "#63718a",
}

#: Bar colour per record status, used by the reports/analytics charts.
#: Statuses are states, not series identities, so they wear this reserved
#: palette and are always shown with their label — never colour alone.
#: Kept in step with STATUS_PILL in apps/core/templatetags/doctrack.py.
STATUS_COLOUR_KEYS = {
    "DRAFT": "muted",
    "PENDING_RECEIPT": "gold",
    "FORWARDED": "gold",
    "RETURNED": "gold",
    "RECEIVED": "green",
    "IN_PROCESS": "teal",
    "COMPLETED": "green",
    "OVERDUE": "red",
}

STATUS_COLOURS = {status: PALETTE[key] for status, key in STATUS_COLOUR_KEYS.items()}

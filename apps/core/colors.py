"""Single source of truth for the UDM DocTrack brand palette (Python side).

Mirrors the CSS custom properties declared in the :root block at the top of
static/css/doctrack.css — when a new palette is adopted, update the hex
values here and there together.
"""

from __future__ import annotations

#: Brand palette. Keys match the CSS variable names (minus the `--udm-`
#: prefix) so the two files stay easy to cross-check by eye.
#:
#: `navy` is a historical key name kept deliberately: it is read by templates
#: (the theme-color meta tag) and it mirrors `--udm-navy` in the stylesheet,
#: where the name is likewise kept so a repaint stays a one-place edit. Read it
#: as "the brand chrome colour", which is now emerald.
PALETTE = {
    "navy": "#0f6e4c",
    "gold": "#d4af6a",
    # Interactive states were unified with the brand colour in the redesign, so
    # `teal` deliberately resolves to the same emerald. The real teal survives
    # below as `process`, which is a document state rather than an interaction
    # and so still needs a hue of its own.
    "teal": "#0f6e4c",
    "green": "#2f9e6b",
    "process": "#16697a",
    "red": "#c0392b",
    "muted": "#63718a",
}

#: Bar colour per record status, used by the reports/analytics charts.
#: Statuses are states, not series identities, so they wear this reserved
#: palette and are always shown with their label — never colour alone.
#: Kept in step with STATUS_PILL in apps/core/templatetags/doctrack.py.
#:
#: RECEIVED and COMPLETED are deliberately different greens. "Received" wears
#: the brighter status green; "Completed" wears the brand emerald, because it
#: is the terminal state and reads as the stronger, more settled colour.
STATUS_COLOUR_KEYS = {
    "DRAFT": "muted",
    "PENDING_RECEIPT": "gold",
    "FORWARDED": "gold",
    "RETURNED": "gold",
    "RECEIVED": "green",
    "IN_PROCESS": "process",
    "COMPLETED": "navy",
    "OVERDUE": "red",
}

STATUS_COLOURS = {status: PALETTE[key] for status, key in STATUS_COLOUR_KEYS.items()}

"""Single source of truth for the UDM DocTrack palette (Python side).

`PALETTE` mirrors the brand custom properties at the top of
static/css/doctrack.css — when a new palette is adopted, update the hex values
here and there together.

Statuses are different. Their colours live only in the stylesheet, as the
--status-<token> custom properties, because each has a light value, a dark value
and a print value, and a hex written here could carry only one of them: Reports'
stage bars were painted from hex, so in dark mode they stayed their light-theme
colour on a dark card. Python hands out the *variable*, and the stylesheet
decides what it resolves to.
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

#: The one mapping from a status to its colour, by token name. Every place a
#: status is drawn reads it: pills (`pill-<token>`), chart marks
#: (`var(--status-<token>)`), legend swatches (`chart-swatch--<token>`).
#:
#: Six statuses, six colours, in lifecycle order. It used to be four: Received
#: and Completed were both green, In process and Completed - pending upload both
#: teal, so the stage bars for each pair were the same colour, and a pill could
#: not tell a reader whether a document was received or finished.
#:
#: Overdue is a condition on top of a stage, not a stage, and wears red as a tag
#: beside the status pill. Never colour alone: every status is always drawn
#: with its label.
STATUS_TOKENS = {
    "DRAFT": "draft",
    "PENDING_RECEIPT": "pending",
    "RECEIVED": "received",
    "IN_PROCESS": "process",
    "COMPLETED_PENDING_UPLOAD": "upload",
    "COMPLETED": "completed",
    "OVERDUE": "overdue",
}

#: A CSS colour per status, for inline chart marks.
STATUS_COLOURS = {status: f"var(--status-{token})" for status, token in STATUS_TOKENS.items()}

#: The pill class per status.
STATUS_PILLS = {status: f"pill-{token}" for status, token in STATUS_TOKENS.items()}

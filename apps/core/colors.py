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


ACTIVITY_PRESENTATIONS = {
    "CREATED": ("draft", "bi-file-earmark", "Draft created"),
    "SENT": ("endorsement", "bi-send", "Endorsed"),
    "FORWARDED": ("endorsement", "bi-send", "Endorsed"),
    "RECEIVED": ("receipt", "bi-check-circle", "Receipt confirmed"),
    "REMARK": ("work", "bi-chat-left-text", "Work update"),
    "EXTERNAL_WORK": ("work", "bi-chat-left-text", "External work update"),
    "ATTACHMENT": ("attachment", "bi-file-earmark-plus", "File update"),
    "RETURNED": ("return", "bi-arrow-return-left", "Returned"),
    "REOPENED": ("return", "bi-arrow-counterclockwise", "Returned to tracking"),
    "COMPLETED": ("pending-filing", "bi-check2-square", "Pending filing"),
    "ARCHIVED": ("filed", "bi-archive", "Filed in Repository"),
    "FILED": ("filed", "bi-archive", "Filed in Repository"),
    "ACCESS": ("access", "bi-shield-lock", "Access changed"),
    "RETIRED": ("removal", "bi-file-earmark-minus", "Content retired"),
    "DISCARDED": ("removal", "bi-trash", "Draft file discarded"),
    "VIEWED": ("neutral", "bi-eye", "Opened"),
    "PRINTED": ("neutral", "bi-printer", "Printed"),
    "CORRECTED": ("neutral", "bi-pencil-square", "External facts corrected"),
    "ANNOTATION": ("neutral", "bi-journal-text", "Annotation"),
}


def activity_presentation(event):
    value = str(getattr(event, "event", event) or "").upper()
    token, icon, label = ACTIVITY_PRESENTATIONS.get(value, ("neutral", "bi-clock-history", "Activity"))
    paths = {
        "draft": "M3 1h6l4 4v10H3z M9 1v4h4 M5 8h6 M5 11h6",
        "endorsement": "M1 7L14 1 9 14 6 9z M6 9l8-8",
        "receipt": "M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1 M4 8l3 3 5-6",
        "work": "M2 2h12v9H6l-4 3z M4 5h8 M4 8h5",
        "attachment": "M2 1h6l3 3v5 M8 1v4h3 M2 1v14h5 M11 8v7 M8 11.5h6",
        "return": "M6 3L2 7l4 4 M2 7h7a4 4 0 0 1 4 4v2",
        "pending-filing": "M2 2h12v12H2z M4 8l3 3 5-6",
        "filed": "M2 2h12v3H2z M3 5v9h10V5 M6 8h4",
        "removal": "M2 4h12 M6 2h4 M4 4l1 10h6l1-10 M7 6v6 M9 6v6",
        "access": "M8 1l6 2v5c0 4-6 7-6 7S2 12 2 8V3z M6 8l2 2 3-4",
        "neutral": "M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1 M8 4v4l3 2",
    }
    return {"token": token, "icon": icon, "label": label, "path": paths[token]}

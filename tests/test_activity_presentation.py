"""Presentation maps events without changing their stored classification."""
import pytest

from apps.core import colors
from apps.tracking.models import RecordActivity


@pytest.mark.parametrize("event,token", [("CREATED", "draft"), ("SENT", "endorsement"),
    ("FORWARDED", "endorsement"), ("RECEIVED", "receipt"), ("REMARK", "work"),
    ("EXTERNAL_WORK", "work"), ("ATTACHMENT", "attachment"), ("RETURNED", "return"),
    ("REOPENED", "return"), ("COMPLETED", "pending-filing"), ("ARCHIVED", "filed"),
    ("FILED", "filed"), ("ACCESS", "access"), ("retired", "removal"), ("discarded", "removal"),
    ("CORRECTED", "neutral"), ("ANNOTATION", "neutral"), ("VIEWED", "neutral"), ("PRINTED", "neutral"), ("unknown", "neutral")])
def test_every_workflow_activity_has_a_labeled_icon_and_semantic_color(event, token):
    assert hasattr(colors, "activity_presentation"), "Both timelines need one presentation mapping"
    result = colors.activity_presentation(event)
    assert result["token"] == token and result["label"] and result["icon"].startswith("bi-")


def test_official_status_palette_and_all_internal_event_labels_are_preserved():
    assert colors.STATUS_TOKENS["IN_PROCESS"] == "process"
    assert colors.STATUS_TOKENS["COMPLETED_PENDING_UPLOAD"] == "upload"
    assert hasattr(colors, "activity_presentation")
    for value, _ in RecordActivity.Event.choices:
        assert colors.activity_presentation(value)["label"]

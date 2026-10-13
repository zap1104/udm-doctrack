"""Rendered decisions must be available, including after validation failures."""

from types import SimpleNamespace

import pytest
from django.utils import timezone

from apps.documents.forms import DeletionReviewForm


@pytest.mark.parametrize("active,due,expected", [
    (True, True, ["KEEP", "DELETE"]), (True, False, ["KEEP"]), (False, True, ["RESTORE"]),
])
def test_rendered_decision_choices_match_available_actions(active, due, expected):
    document = SimpleNamespace(pk=17, is_active=active, retention_is_due=due,
                               retention_until=timezone.localdate(), updated_at=timezone.now(), deletion_recovery_open=True)
    form = DeletionReviewForm(document=document)
    choices = [value for value, _label in form["decision"].field.widget.choices if value]
    assert choices == expected
    html = str(form["decision"])
    for value in expected:
        assert f'value="{value}"' in html

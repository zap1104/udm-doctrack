"""Mixed records explain which classification controls the existing rules."""

from types import SimpleNamespace

import pytest
from django.template.loader import render_to_string


@pytest.mark.parametrize("count", [0, 1, 2])
def test_type_display_shows_every_selection_and_explains_main_only_for_mixed_records(count):
    kinds = [SimpleNamespace(name="Memorandum"), SimpleNamespace(name="Letter")][:count]
    item = SimpleNamespace(document_type_names=", ".join(kind.name for kind in kinds),
                           selected_document_types=kinds, document_type=kinds[0] if kinds else None,
                           document_type_id=1 if kinds else None)
    body = render_to_string("partials/_document_types_display.html", {"item": item})
    for kind in kinds:
        assert kind.name in body
    assert ("Main type: Memorandum" in body) == (count > 1)
    assert ("Not specified" in body) == (count == 0)

"""What keeps every page inside a 360px phone screen.

Measured in a browser (no page scrolled sideways at 360px); pinned here in the
stylesheet, since pytest has no layout engine.
"""

from __future__ import annotations

import pathlib
import re

CSS = pathlib.Path("static/css/doctrack.css").read_text(encoding="utf-8")


def _rule(selector):
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert match, selector
    return match.group(1)


def test_every_horizontal_scroller_contains_its_absolute_children():
    """A .visually-hidden label is absolutely positioned. Inside a scroller
    with no positioned ancestor it escaped at its static position, far right
    in a wide table or pill row, and the whole page scrolled sideways."""
    for selector in (".table-responsive", ".pill-row", ".tracking-queue-nav"):
        body = _rule(selector)
        assert "overflow-x" in body, selector
        assert re.search(r"position:\s*relative", body), selector


def test_the_account_menu_fits_beside_the_search_box_on_a_phone():
    phone = CSS[CSS.index("@media (max-width:575.98px) {\n  /* The search box kept"):]
    phone = phone[: phone.index("\n}\n")]
    assert ".app-topbar .topbar-search { min-width:0; }" in phone


def test_the_memo_reads_on_a_phone_without_touching_print():
    assert "@media screen and (max-width: 575.98px) {\n  .memo-print { padding: 16px; }" in CSS

"""A selected control must stay readable when the pointer is on it.

The bug this exists for: "Clickable text visibility" was written as

    .app-content a:not(.btn):not(.nav-link):not(.doc-tile):not(.stat-card)
                  :not(.smart-folder):not(.tag-chip):hover { color: navy }

Every `:not()` counts as a class, so that selector weighed *eight* classes and
outranked every component's own rule. A selected pill therefore kept the navy
background from `.is-active` and took its text colour from here — navy on navy,
so the label vanished the instant it was hovered. It hit the queue pills, the
Stage/Deadline/Show rows, the notification filters and the rows-per-page
control at once, because it was one rule sitting above all of them.

Two checks, and the second is the one that matters: the first pins the fix,
the second says the class of bug cannot come back by someone adding a control.
"""

from __future__ import annotations

import pathlib
import re

CSS = pathlib.Path("static/css/doctrack.css")

#: Every "this one is chosen" rule that paints its own ground. Each must state
#: its own hovered appearance, so the background and the text colour can never
#: be taken from two different rules.
SELECTED_STATES = (
    ".pill-toggle.is-active",
    ".pill-toggle--alert.is-active",
    ".notification-filter.is-active",
    ".page-size-option.is-active",
    ".folder-tile.active",
    ".smart-folder.active",
    ".reports-tab.active",
    ".nav-item.active",
)


def _rules(css: str) -> list[tuple[str, str]]:
    """(selector, body) for every rule, comments and at-blocks flattened."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return re.findall(r"([^{}]+)\{([^{}]*)\}", css)


def split_selectors(selector: str) -> list[str]:
    """Split a selector list on top-level commas only.

    `:where(.btn, .nav-link)` contains commas that do not separate selectors,
    and splitting on them yields fragments like `.tag-chip)):hover` — which the
    first draft of this test then measured, reporting a failure that was its own.
    """
    parts, depth, current = [], 0, ""
    for char in selector:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    parts.append(current)
    return [part.strip() for part in parts if part.strip()]


def specificity(selector: str) -> tuple[int, int, int]:
    """(ids, classes, elements), counting `:not()` the way browsers do.

    `:not(...)` contributes the specificity of its argument; `:where(...)`
    contributes nothing. That difference is the entire fix, so the test has to
    model it rather than count characters.
    """
    selector = selector.strip()
    # :where(...) weighs nothing — drop it and whatever it wraps.
    while ":where(" in selector:
        start = selector.index(":where(")
        depth, i = 0, start + len(":where") 
        for i in range(start + len(":where"), len(selector)):
            if selector[i] == "(":
                depth += 1
            elif selector[i] == ")":
                depth -= 1
                if depth == 0:
                    break
        selector = selector[:start] + selector[i + 1:]
    # :not(...) weighs its argument: unwrap it and keep counting.
    selector = selector.replace(":not(", " ").replace(")", " ")
    ids = len(re.findall(r"#[\w-]+", selector))
    classes = len(re.findall(r"[.:\[][\w-]+", selector))
    elements = len(re.findall(r"(?:^|[\s>+~])([a-zA-Z][\w-]*)", selector))
    return (ids, classes, elements)


def test_the_prose_link_rule_cannot_outrank_a_component():
    """It is a default, not an override. At zero specificity a component's own
    class rule wins by being a class, so a control added tomorrow needs no entry
    in that `:not()` list — which is the property worth having, since the old
    list could only ever be one component behind."""
    css = CSS.read_text(encoding="utf-8")
    link_rules = [
        selector
        for selector, _body in _rules(css)
        if "app-content" in selector and ":hover" in selector and "a" in selector
    ]

    assert link_rules, "the prose-link hover rule should still exist"
    for selector in link_rules:
        for one in split_selectors(selector):
            assert specificity(one) <= (0, 1, 1), (one, specificity(one))


def test_every_selected_control_states_its_own_hover():
    """Background and text colour have to come from the same rule.

    Written down as a pair, there is no arrangement of the cascade that paints
    navy text on a navy ground. Left to source order, there is — and it was
    reached by a rule three hundred lines away that named none of these.
    """
    css = CSS.read_text(encoding="utf-8")
    selectors = [selector for selector, _body in _rules(css)]

    for state in SELECTED_STATES:
        hovered = [
            one
            for selector in selectors
            for one in split_selectors(selector)
            if one.startswith(state) and (":hover" in one or ":focus" in one)
        ]
        assert hovered, f"{state} does not say what it looks like when hovered"


def test_a_selected_hover_sets_colour_whenever_it_sets_a_background():
    """Half a rule is what caused this: `.is-active` gave the background and
    something else gave the text."""
    css = CSS.read_text(encoding="utf-8")

    for selector, body in _rules(css):
        parts = split_selectors(selector)
        if not any(
            part.startswith(state) and (":hover" in part or ":focus" in part)
            for state in SELECTED_STATES
            for part in parts
        ):
            continue
        if "background" in body:
            assert "color:" in body.replace(" ", ""), (selector.strip(), body)

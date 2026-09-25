"""One colour per status, and the same colour wherever that status is drawn.

Before this, six statuses were drawn in four colours and two maps disagreed:

- Received and Completed were both green, In process and Completed - pending
  upload both teal, on the pills and on Reports' stage bars.
- The dashboard ring had its own map, in which pending upload was blue.
- Reports' repository chart painted completed blue and historical green; the
  repository ring beside the same figures painted completed green and
  historical grey. The monthly volume chart painted Completed gold.
- The stage bars were hex written in Python, so in dark mode they kept their
  light-theme colours.

The palette is checked here, from the stylesheet itself, for what a reader
needs: statuses that touch in a chart stay apart for full colour vision and for
the three kinds of colour blindness, marks stand out from the card, and pill
text is readable on its pill.
"""

from __future__ import annotations

import itertools
import math
import pathlib
import re

import pytest

from apps.core.colors import STATUS_COLOURS, STATUS_PILLS, STATUS_TOKENS
from apps.core.templatetags.doctrack import status_pill_class
from apps.tracking.models import Status

CSS = pathlib.Path("static/css/doctrack.css").read_text(encoding="utf-8")
TOKEN_LINE = re.compile(
    r"--status-(\w+): (#[0-9a-f]{6}); --status-\1-soft: (#[0-9a-f]{6}); --status-\1-ink: (#[0-9a-f]{6});"
)


def _blocks():
    """The status tokens as declared: light (:root), dark, and the print reset."""
    found = TOKEN_LINE.findall(CSS)
    size = len(STATUS_TOKENS)
    blocks = [found[i:i + size] for i in range(0, len(found), size)]
    return {
        name: {token: (mark, soft, ink) for token, mark, soft, ink in block}
        for name, block in zip(("light", "dark", "print"), blocks, strict=True)
    }


SURFACE = {"light": "#ffffff", "dark": "#17212c"}

#: Pairs of statuses drawn touching: the tracking ring's slices, the three
#: turnaround lines. Everywhere else a status sits in its own labelled row.
TOUCHING = [
    ("pending", "received"), ("received", "process"), ("pending", "process"),
    ("process", "completed"), ("pending", "completed"),
]


# --- colour arithmetic: OKLab distance, Machado (2009) colour-blind simulation --
MACHADO = {
    "protan": [[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]],
    "deutan": [[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.011820, 0.042940, 0.968881]],
    "tritan": [[1.255528, -0.076749, -0.178779], [-0.078411, 0.930809, 0.147602], [0.004733, 0.691367, 0.303900]],
}


def _linear(hex_colour):
    channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    return [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]


def _oklab(rgb):
    r, g, b = rgb
    lms = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b,
           0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b,
           0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    long_, medium, short = (math.copysign(abs(x) ** (1 / 3), x) for x in lms)
    return (0.2104542553 * long_ + 0.7936177850 * medium - 0.0040720468 * short,
            1.9779984951 * long_ - 2.4285922050 * medium + 0.4505937099 * short,
            0.0259040371 * long_ + 0.7827717662 * medium - 0.8086757660 * short)


def _distance(a, b, vision="normal"):
    def seen(hex_colour):
        rgb = _linear(hex_colour)
        if vision != "normal":
            matrix = MACHADO[vision]
            rgb = [max(0.0, min(1.0, sum(matrix[i][j] * rgb[j] for j in range(3)))) for i in range(3)]
        return _oklab(rgb)

    return 100 * math.dist(seen(a), seen(b))


def _contrast(a, b):
    def luminance(hex_colour):
        r, g, b = _linear(hex_colour)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


# --- one colour per status ------------------------------------------------------
def test_every_status_has_a_colour_of_its_own():
    assert set(STATUS_TOKENS) == set(Status.values) | {"OVERDUE"}
    assert len(set(STATUS_TOKENS.values())) == len(STATUS_TOKENS), "no two statuses share a colour"


@pytest.mark.parametrize(("one", "other"), [
    (Status.RECEIVED, Status.COMPLETED),
    (Status.IN_PROCESS, Status.COMPLETED_PENDING_UPLOAD),
])
def test_the_pairs_that_used_to_share_a_colour_no_longer_do(one, other):
    assert status_pill_class(one) != status_pill_class(other)
    assert STATUS_COLOURS[one] != STATUS_COLOURS[other]


def test_a_pill_and_a_chart_mark_come_from_the_same_mapping():
    for status, token in STATUS_TOKENS.items():
        assert status_pill_class(status) == STATUS_PILLS[status] == f"pill-{token}"
        assert STATUS_COLOURS[status] == f"var(--status-{token})"
        assert f".pill-{token} " in CSS or f".pill-{token}{{" in CSS, token


def test_every_status_is_declared_for_light_dark_and_print():
    blocks = _blocks()

    for name in ("light", "dark", "print"):
        assert set(blocks[name]) == set(STATUS_TOKENS.values()), name
    assert blocks["print"] == blocks["light"], "paper is always the light palette"


# --- the palette itself ---------------------------------------------------------
@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize(("one", "other"), TOUCHING)
def test_touching_statuses_are_apart_for_every_reader(theme, one, other):
    """At least 15 apart for full colour vision and 6 for each kind of colour
    blindness, in OKLab distance x100: the pairs a reader has to tell apart
    by colour where they meet in a chart."""
    palette = _blocks()[theme]
    a, b = palette[one][0], palette[other][0]

    assert _distance(a, b) >= 15, (one, other)
    for vision in ("protan", "deutan", "tritan"):
        assert _distance(a, b, vision) >= 6, (one, other, vision)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_every_pair_of_stages_is_visibly_different(theme):
    """Statuses that never touch are always labelled, so the bar is lower,
    but no two stages may look alike at a glance."""
    palette = _blocks()[theme]
    stages = ["draft", "pending", "received", "process", "upload", "completed"]

    for one, other in itertools.combinations(stages, 2):
        assert _distance(palette[one][0], palette[other][0]) >= 9.5, (one, other)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_marks_stand_out_and_pill_text_is_readable(theme):
    for token, (mark, soft, ink) in _blocks()[theme].items():
        assert _contrast(mark, SURFACE[theme]) >= 3, (token, "mark on the card")
        assert _contrast(ink, soft) >= 7, (token, "pill text on its pill")


# --- drawn the same way everywhere ----------------------------------------------
@pytest.mark.django_db
def test_every_chart_draws_a_status_in_its_own_colour(client, users, offices, memo_type):
    from apps.tracking.services import confirm_receipt, create_draft_record, route_record

    for subject, confirm in (("Waiting", False), ("Signed for", True)):
        record = create_draft_record(
            user=users["med"], subject=subject, instructions="x", document_type=memo_type,
        )
        route_record(record, [offices["SUP"]], user=users["med"])
        if confirm:
            confirm_receipt(record, user=users["sup"])
    client.force_login(users["sup"])

    dashboard = client.get("/").context
    for ring in dashboard["tracking_rings"]["rings"]:
        for view in ("status", "overdue"):
            for row in ring[view]["slices"]:
                status = {"pending_receipt": "PENDING_RECEIPT", "received": "RECEIVED",
                          "in_process": "IN_PROCESS"}[row["key"]]
                assert row["colour"] == STATUS_COLOURS[status]
    series = {row["key"]: row["colour"] for row in dashboard["turnaround_trend_points"]}
    assert series.get("receipt", STATUS_COLOURS["PENDING_RECEIPT"]) == STATUS_COLOURS["PENDING_RECEIPT"]

    client.force_login(users["admin"])
    for row in client.get("/tracking/reports/").context["by_status"]:
        assert row["colour"] == STATUS_COLOURS[row["status"]]


def test_completed_is_green_on_every_chart_that_shows_it():
    """Gold on the monthly chart, blue on the repository chart, green on the
    ring: one word, three colours."""
    from apps.core.analytics import VOLUME_SERIES

    assert dict((label, series) for label, _key, series in VOLUME_SERIES)["Completed"] == "completed"
    assert ".column--completed { background:var(--status-completed); }" in CSS
    assert ".chart-swatch--completed { background:var(--status-completed); }" in CSS
    for template in ("templates/core/dashboard.html", "templates/reports/reports.html"):
        markup = pathlib.Path(template).read_text(encoding="utf-8")
        assert "chart-swatch--one" not in markup and "chart-swatch--two" not in markup, template


def test_non_status_badges_do_not_borrow_a_status_colour():
    """"Active" wore the Received pill, which is now blue for received."""
    for template in ("templates/administration/users.html", "templates/administration/offices.html"):
        markup = pathlib.Path(template).read_text(encoding="utf-8")
        assert "pill-received" not in markup, template
        assert "pill-ok" in markup, template

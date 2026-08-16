"""Colour identifiers for offices.

An administrator can set these to anything, which is the whole point and also
the whole risk: the pairing of background and text cannot be chosen up front,
so it is derived, and these tests are what say the derivation actually holds.

Colour is never the only signal. Every badge prints the office code too, which
is what keeps it meaningful in greyscale, on a mono printer, and to a reader
who cannot tell the two colours apart.
"""

from __future__ import annotations

import pytest

from apps.accounts.models import OFFICE_COLOURS, Office
from apps.core.utils import MIN_CONTRAST, badge_palette, contrast_ratio, normalise_hex


# ---------------------------------------------------------------------------
# Readability, whatever the administrator picks
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "chosen",
    [
        "#ffffff",  # white: nothing to darken toward on its own
        "#ffffcc",  # the pale yellow that defeats naive "use the colour as text"
        "#ccff00",  # neon
        "#000000",
        "#ffe0f0",
        "#00ffff",
        "#808080",
        "#0f0",  # shorthand
        *OFFICE_COLOURS,
    ],
)
def test_every_badge_clears_wcag_aa(chosen):
    tint, ink = badge_palette(chosen)

    assert contrast_ratio(ink, tint) >= MIN_CONTRAST, f"{chosen} -> {ink} on {tint}"


@pytest.mark.parametrize("junk", ["", None, "not-a-colour", "red;background:url(x)", "#12345", "javascript:x"])
def test_an_unusable_colour_falls_back_instead_of_reaching_the_style_attribute(junk):
    """The field is validated on the way in, but a value from a fixture, a shell
    session or a hand-edited row must not be able to put arbitrary text inside
    a style attribute."""
    safe = normalise_hex(junk)

    assert safe.startswith("#")
    assert len(safe) == 7
    assert all(character in "0123456789abcdef" for character in safe[1:])


def test_shorthand_hex_is_expanded():
    assert normalise_hex("#0f0") == "#00ff00"


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_new_office_is_given_a_colour_without_anyone_choosing_one(db):
    office = Office.objects.create(code="new", name="Brand New Office")

    assert office.colour in OFFICE_COLOURS


@pytest.mark.django_db
def test_offices_do_not_share_a_colour(db):
    """Two offices the same colour on one screen defeats the entire point."""
    made = [
        Office.objects.create(code=f"O{index}", name=f"Office {index}")
        for index in range(len(OFFICE_COLOURS))
    ]

    assert len({office.colour for office in made}) == len(made)


@pytest.mark.django_db
def test_a_colour_the_administrator_chose_is_kept(db):
    office = Office.objects.create(code="pick", name="Chosen Colour Office", colour="#123456")
    office.refresh_from_db()

    assert office.colour == "#123456"


@pytest.mark.django_db
def test_existing_offices_were_backfilled_by_the_migration(offices):
    """Office.save() colours new rows, but a migration gets the historical model
    with no custom save — so without the data step every office already in the
    database would have stayed blank."""
    for office in offices.values():
        assert office.colour, f"{office.code} has no colour"


# ---------------------------------------------------------------------------
# It reaches the pages
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_the_record_page_colour_codes_its_offices(client, users, offices, memo_type):
    from apps.tracking.services import create_draft_record, route_record

    record = create_draft_record(
        user=users["med"], subject="Colour probe", instructions="For action."
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    client.force_login(users["admin"])

    body = client.get(record.get_absolute_url()).content.decode()

    assert "office-badge" in body
    assert offices["MED"].badge["tint"] in body
    # Colour is never alone: the code is on the badge as text.
    assert "MED" in body and "SUP" in body


@pytest.mark.django_db
def test_an_administrator_can_change_the_colour(client, users, offices):
    """"Editable by admin later on" — the field is on the office form."""
    from apps.accounts.forms import OfficeForm

    assert "colour" in OfficeForm().fields

    client.force_login(users["admin"])
    office = offices["MED"]
    response = client.post(
        f"/accounts/offices/{office.pk}/",
        {
            "code": office.code, "name": office.name, "short_name": office.short_name,
            "cluster": office.cluster, "head_name": "", "email": "", "location": "",
            "colour": "#7a1fa2", "sort_order": office.sort_order, "is_active": "on",
        },
    )
    office.refresh_from_db()

    assert response.status_code == 302, response.status_code
    assert office.colour == "#7a1fa2"


@pytest.mark.django_db
def test_a_malformed_colour_is_rejected_by_the_form(users, offices):
    from apps.accounts.forms import OfficeForm

    office = offices["MED"]
    form = OfficeForm(
        data={
            "code": office.code, "name": office.name, "short_name": office.short_name,
            "cluster": office.cluster, "head_name": "", "email": "", "location": "",
            "colour": "rgb(1,2,3)", "sort_order": office.sort_order, "is_active": True,
        },
        instance=office,
    )

    assert not form.is_valid()
    assert "colour" in form.errors

"""Naming, navigation, the draft tracking number, and the dark theme."""

from __future__ import annotations

import pathlib

import pytest

from apps.tracking.models import Status
from apps.tracking.services import (
    DRAFT_NUMBER_PREFIX,
    confirm_receipt,
    create_draft_record,
    route_record,
)

CSS = pathlib.Path("static/css/doctrack.css")
JS = pathlib.Path("static/js/doctrack.js")


# --- 4.1 / 4.2 naming and navigation ---------------------------------------
@pytest.mark.django_db
def test_the_modules_are_named_once_each(client, users):
    client.force_login(users["admin"])
    body = client.get("/tracking/").content.decode()

    assert "Document Tracking" in body
    assert "Active Document Tracking" not in body, "the page is not only active records now"


@pytest.mark.django_db
def test_the_old_module_name_is_gone_from_every_page(client, users, settings):
    """One module, one name.

    The product's own name — "UDM Records and Document Management System" — is
    not the module label and is left alone, so it is removed from the haystack
    rather than being allowed to fail this.
    """
    client.force_login(users["admin"])
    for path in ("/", "/tracking/", "/documents/", "/reports/", "/search/"):
        body = client.get(path).content.decode().replace(settings.SITE_LONG_NAME, "")
        assert "Document Management" not in body, path


@pytest.mark.django_db
def test_the_sidebar_order_puts_search_second(client, users):
    client.force_login(users["admin"])
    body = client.get("/").content.decode()
    nav = body[body.index('aria-label="Main navigation"') : body.index("</nav>")]

    order = [
        label
        for label in ("Dashboard", "Search", "Document Tracking", "Document Repository", "Reports")
        if label in nav
    ]
    assert order == ["Dashboard", "Search", "Document Tracking", "Document Repository", "Reports"]


@pytest.mark.django_db
def test_neither_module_has_sub_tabs_in_the_sidebar(client, users):
    """The queues appeared both here and on the page, with two active states to
    keep in step."""
    client.force_login(users["admin"])
    body = client.get("/tracking/?scope=incoming").content.decode()

    assert "tracking-subnav" not in body


def test_the_sub_nav_styling_went_with_it():
    assert "tracking-subnav" not in CSS.read_text(encoding="utf-8")


@pytest.mark.django_db
def test_an_office_admin_can_see_the_administration_link(client, users):
    """It was gated on is_system_admin, so an office administrator had no way
    into the screens they are now allowed to use."""
    client.force_login(users["med_admin"])
    body = client.get("/").content.decode()

    assert "/administration/" in body


@pytest.mark.django_db
def test_an_ordinary_user_still_does_not_see_it(client, users):
    client.force_login(users["med"])
    body = client.get("/").content.decode()

    assert 'href="/administration/"' not in body


# --- 4.4 the tracking number ------------------------------------------------
@pytest.mark.django_db
def test_a_draft_has_no_tracking_number_to_show(users, memo_type):
    draft = create_draft_record(
        user=users["med"], subject="Still being written", instructions="For action.",
        document_type=memo_type,
    )

    assert draft.has_tracking_number is False
    assert draft.display_tracking_number == "Not yet assigned"
    assert draft.tracking_number.startswith(DRAFT_NUMBER_PREFIX)


@pytest.mark.django_db
def test_the_placeholder_never_reaches_a_screen(client, users, memo_type):
    draft = create_draft_record(
        user=users["med"], subject="Still being written", instructions="For action.",
        document_type=memo_type,
    )
    client.force_login(users["med"])

    # The record's own placeholder, not the bare prefix: "DRAFT" also appears
    # legitimately as the status filter's option value.
    placeholder = draft.tracking_number
    assert placeholder.startswith(DRAFT_NUMBER_PREFIX)

    for path in ("/tracking/", draft.get_absolute_url(), f"/tracking/{draft.pk}/review/"):
        body = client.get(path).content.decode()
        assert placeholder not in body, path
        assert "Not yet assigned" in body, path


@pytest.mark.django_db
def test_the_number_is_issued_on_send(users, offices, memo_type):
    draft = create_draft_record(
        user=users["med"], subject="Ready to go", instructions="For action.",
        document_type=memo_type,
    )
    placeholder = draft.tracking_number

    route_record(draft, [offices["SUP"]], user=users["med"])
    draft.refresh_from_db()

    assert draft.tracking_number != placeholder
    assert draft.has_tracking_number is True
    assert draft.display_tracking_number == draft.tracking_number


@pytest.mark.django_db
def test_the_issued_number_carries_the_office_code(users, offices, memo_type):
    draft = create_draft_record(
        user=users["med"], subject="Ready to go", instructions="For action.",
        document_type=memo_type,
    )
    route_record(draft, [offices["SUP"]], user=users["med"])
    draft.refresh_from_db()

    # UDM-OVPA-<OFFICE>-<YEAR>-<MONTH>-<SEQ>, from the *originating* office.
    assert f"-{offices['MED'].code}-" in draft.tracking_number
    assert draft.tracking_number.startswith("UDM-OVPA-")


@pytest.mark.django_db
def test_an_abandoned_draft_leaves_no_gap_in_the_series(users, offices, memo_type):
    """The reason the number moved to send: a gap in a records series reads as
    a document that existed and cannot be found."""
    abandoned = create_draft_record(
        user=users["med"], subject="Never sent", instructions="x", document_type=memo_type,
    )
    first = create_draft_record(
        user=users["med"], subject="First real one", instructions="x", document_type=memo_type,
    )
    route_record(first, [offices["SUP"]], user=users["med"])
    second = create_draft_record(
        user=users["med"], subject="Second real one", instructions="x", document_type=memo_type,
    )
    route_record(second, [offices["SUP"]], user=users["med"])

    first.refresh_from_db()
    second.refresh_from_db()
    abandoned.refresh_from_db()

    assert abandoned.tracking_number.startswith(DRAFT_NUMBER_PREFIX)
    assert int(first.tracking_number.rsplit("-", 1)[1]) + 1 == int(
        second.tracking_number.rsplit("-", 1)[1]
    ), "consecutive, with nothing spent on the draft that was never sent"


# --- 4.5 current and receiving office --------------------------------------
@pytest.mark.django_db
def test_the_record_page_names_all_three_offices(client, users, offices, memo_type):
    record = create_draft_record(
        user=users["med"], subject="Three offices", instructions="x", document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    record.refresh_from_db()

    client.force_login(users["med"])
    body = client.get(record.get_absolute_url()).content.decode()

    assert "Originating office" in body
    assert "Current office" in body
    assert "Receiving office" in body


@pytest.mark.django_db
def test_receiving_offices_are_the_current_batch(users, offices, memo_type):
    record = create_draft_record(
        user=users["med"], subject="Moving on", instructions="x", document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    record.refresh_from_db()
    route_record(record, [offices["HR"]], user=users["sup"], action="FORWARD")
    record.refresh_from_db()

    codes = {office.code for office in record.receiving_offices}
    assert codes == {offices["HR"].code}, "the batch it is in now, not where it has been"


@pytest.mark.django_db
def test_the_list_shows_the_receiving_office_column(client, users, offices, memo_type):
    record = create_draft_record(
        user=users["med"], subject="On the list", instructions="x", document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])

    client.force_login(users["med"])
    body = client.get("/tracking/").content.decode()

    assert "Receiving office" in body


@pytest.mark.django_db
def test_the_receiving_column_is_annotated_in_one_pass(client, users, offices, memo_type):
    """Per-row `receiving_offices` would be a query per row."""
    for index in range(3):
        record = create_draft_record(
            user=users["med"], subject=f"Row {index}", instructions="x", document_type=memo_type,
        )
        route_record(record, [offices["SUP"]], user=users["med"])

    client.force_login(users["med"])
    records = client.get("/tracking/").context["records"]

    assert all(hasattr(record, "receiving_offices_shown") for record in records)


# --- 4.3 drafts are visually distinct --------------------------------------
def test_draft_has_its_own_pill_treatment():
    css = CSS.read_text(encoding="utf-8")

    assert ".pill-draft" in css
    # Outlined rather than another solid chip beside Pending receipt.
    draft_rule = css[css.index(".pill-draft") : css.index(".pill-draft") + 200]
    assert "transparent" in draft_rule
    assert "inset" in draft_rule


def test_the_retired_statuses_left_no_styling_behind():
    css = CSS.read_text(encoding="utf-8")

    assert ".pill-forwarded" not in css
    assert ".pill-returned" not in css


@pytest.mark.django_db
def test_a_record_awaiting_receipt_does_not_name_a_person_who_does_not_exist(
    client, users, offices, memo_type
):
    """"Held by: Pending Receipt" read as somebody's name."""
    record = create_draft_record(
        user=users["med"], subject="Nobody holds it", instructions="x", document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    record.refresh_from_db()
    assert record.status == Status.PENDING_RECEIPT

    client.force_login(users["med"])
    body = client.get(record.get_absolute_url()).content.decode()

    assert "Nobody yet — awaiting receipt" in body


# --- 4.7 repository folders -------------------------------------------------
@pytest.mark.django_db
def test_the_repository_renders_folders_in_a_grid(client, users):
    client.force_login(users["admin"])
    response = client.get("/documents/")
    body = response.content.decode()

    assert "folder-grid" in body
    assert "Office Folders" in body
    assert response.context["folder_columns"] >= 1


@pytest.mark.django_db
def test_the_column_count_comes_from_settings(client, users, settings):
    settings.REPOSITORY_FOLDER_COLUMNS = 6
    client.force_login(users["admin"])
    body = client.get("/documents/").content.decode()

    assert "--folder-columns:6" in body


def test_the_grid_columns_are_equal():
    css = CSS.read_text(encoding="utf-8")
    rule = css[css.index(".folder-grid {") : css.index(".folder-grid {") + 260]

    assert "grid-template-columns" in rule
    assert "1fr" in rule, "equal columns, not content-sized ones"


# --- 4.8 routing slip attachments (verify and keep) ------------------------
@pytest.mark.django_db
def test_the_printed_slip_lists_attachments_as_plain_names(client, users, offices, memo_type):
    """No thumbnails, no previews, not clickable. Verified rather than changed."""
    record = create_draft_record(
        user=users["med"], subject="With a file", instructions="x", document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])

    client.force_login(users["med"])
    body = client.get(f"/tracking/{record.pk}/slip/").content.decode()
    start = body.find("routing-slip-attachments")
    if start == -1:
        pytest.skip("this slip has no attachment block to check")
    block = body[start : start + 1200]

    assert "<img" not in block
    assert "<a " not in block, "a printed name is not a link"


# --- 4.9 dark mode ----------------------------------------------------------
def test_the_theme_is_built_on_the_existing_tokens():
    """A parallel dark stylesheet drifts from the light one as components are
    added; re-pointing the tokens cannot."""
    css = CSS.read_text(encoding="utf-8")

    assert ':root[data-theme="dark"]' in css
    assert "--udm-surface" in css
    # The raised surface must not be hardcoded anywhere, or it cannot re-point.
    # Bounded so a genuinely different colour that merely starts "fff"
    # (.help-note's warm #fffdf6, which has its own dark value) is not caught.
    import re

    assert not re.search(r"background(-color)?:\s*#fff(fff)?\b", css, re.I)


def test_the_sign_in_page_stays_light():
    css = CSS.read_text(encoding="utf-8")
    assert ':root[data-theme="dark"] .login-wrap' in css


def test_printing_uses_the_light_palette():
    """A dark print wastes toner and comes out unreadable on tinted panels."""
    css = CSS.read_text(encoding="utf-8")
    print_blocks = css.split("@media print")
    assert any(':root[data-theme="dark"]' in block for block in print_blocks[1:])


def test_the_toggle_stores_the_choice_and_follows_the_system_until_then():
    js = JS.read_text(encoding="utf-8")

    assert "data-theme-toggle" in js
    assert "prefers-color-scheme" in js
    assert "localStorage" in js
    # Storage throws rather than returning null in a private window.
    assert "catch" in js


@pytest.mark.django_db
def test_the_toggle_is_in_the_app_but_not_on_the_sign_in_page(client, users):
    client.force_login(users["med"])
    assert "data-theme-toggle" in client.get("/").content.decode()

    client.logout()
    assert "data-theme-toggle" not in client.get("/accounts/login/").content.decode()


# --- 4.10 sign-in -----------------------------------------------------------
@pytest.mark.django_db
def test_the_sign_in_heading_names_the_system(client):
    body = client.get("/accounts/login/").content.decode()

    assert "Sign in to DocTrack" in body
    assert "Welcome Back" not in body


@pytest.mark.django_db
def test_the_sign_in_page_carries_both_marks(client):
    body = client.get("/accounts/login/").content.decode()

    assert "UniversidadDeManila" in body, "the university seal"
    assert "favicon.svg" in body, "the DocTrack mark"


@pytest.mark.django_db
def test_there_is_still_no_way_to_register(client):
    """Already correct, and worth a test so it stays that way."""
    from django.urls import NoReverseMatch, reverse

    body = client.get("/accounts/login/").content.decode()
    assert "register" not in body.lower()

    for name in ("accounts:register", "accounts:signup"):
        with pytest.raises(NoReverseMatch):
            reverse(name)

"""The tracking page's filters must describe the records it actually holds.

Two things went wrong here and both read to a user as "there are none":

1. The status dropdown offered every status there is, including Completed —
   but the page shows active records only, so picking it always returned an
   empty table for a page that never holds those records in the first place.
2. "Pending Receipt" quietly redirected an ordinary user to their own inbox,
   so it returned exactly the same rows as "Incoming" and could not answer the
   question it is named for: is anything we sent still unconfirmed?
"""

from __future__ import annotations

import re

import pytest

from apps.tracking.forms import TrackingFilterForm
from apps.tracking.models import ACTIVE_STATUSES, Status
from apps.tracking.services import (
    apply_scope,
    complete_record,
    confirm_receipt,
    create_draft_record,
    route_record,
)


# --- the status filter -----------------------------------------------------
def test_the_status_filter_offers_only_statuses_this_page_can_show():
    offered = {value for value, _label in TrackingFilterForm().fields["status"].choices}

    assert "COMPLETED" not in offered, "completed records live in Documents, not here"
    # No empty choice. It became a multi-select, where selecting none already
    # means all of them — so an "All statuses" entry would be a second way to
    # say the same thing, and selectable *beside* a stage, reading as "all of
    # them and this one".
    assert offered == {str(status) for status in ACTIVE_STATUSES}


def test_overdue_is_not_offered_as_a_status():
    """It sat in this list and had the parameter to itself, so a record could be
    filtered as overdue *or* as pending receipt and never as both. It is a
    deadline condition lying across the stages, with `?overdue=` of its own."""
    offered = {value for value, _label in TrackingFilterForm().fields["status"].choices}

    assert "OVERDUE" not in offered


def test_the_status_filter_uses_the_new_name():
    labels = dict(TrackingFilterForm().fields["status"].choices)

    assert labels["PENDING_RECEIPT"] == "Pending receipt"
    assert "IN_TRANSIT" not in labels


def test_routing_leaves_the_record_pending_receipt():
    assert Status.PENDING_RECEIPT.value == "PENDING_RECEIPT"
    assert Status.PENDING_RECEIPT.label == "Pending receipt"
    assert not hasattr(Status, "IN_TRANSIT")


# --- the Pending Receipt queue ---------------------------------------------
@pytest.fixture
def sent_record(users, offices, memo_type):
    """MED sends to SUP. Nobody has confirmed it."""
    record = create_draft_record(
        user=users["med"], subject="Electrical supplies request", instructions="For action.",
        document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    record.refresh_from_db()
    return record


@pytest.mark.django_db
def test_the_sending_office_can_see_what_it_is_still_waiting_on(sent_record, users):
    """The regression: MED sent it, so MED must be able to see it unconfirmed."""
    from apps.tracking.models import TrackingRecord

    visible = TrackingRecord.objects.visible_to(users["med"])
    awaiting = apply_scope(visible, "awaiting", users["med"])

    assert sent_record in awaiting


@pytest.mark.django_db
def test_the_receiving_office_still_sees_it_too(sent_record, users):
    from apps.tracking.models import TrackingRecord

    visible = TrackingRecord.objects.visible_to(users["sup"])
    assert sent_record in apply_scope(visible, "awaiting", users["sup"])


@pytest.mark.django_db
def test_pending_receipt_is_no_longer_a_duplicate_of_incoming(sent_record, users):
    """For the sender the two queues must now differ — that is the whole point."""
    from apps.tracking.models import TrackingRecord

    visible = TrackingRecord.objects.visible_to(users["med"])
    inbox = set(apply_scope(visible, "inbox", users["med"]))
    awaiting = set(apply_scope(visible, "awaiting", users["med"]))

    assert sent_record not in inbox, "MED sent it; it is not in MED's inbox"
    assert sent_record in awaiting
    assert inbox != awaiting


@pytest.mark.django_db
def test_a_confirmed_record_leaves_the_queue(sent_record, users):
    from apps.tracking.models import TrackingRecord

    confirm_receipt(sent_record, user=users["sup"])
    sent_record.refresh_from_db()

    visible = TrackingRecord.objects.visible_to(users["med"])
    assert sent_record not in apply_scope(visible, "awaiting", users["med"])


@pytest.mark.django_db
def test_a_completed_record_never_shows_as_pending(sent_record, users):
    """A record completed while a recipient never confirmed is finished, not waiting."""
    from apps.tracking.models import TrackingRecord

    complete_record(sent_record, user=users["admin"])
    sent_record.refresh_from_db()

    visible = TrackingRecord.objects.visible_to(users["admin"])
    assert sent_record not in apply_scope(visible, "awaiting", users["admin"])


@pytest.mark.django_db
def test_a_partly_received_batch_still_counts_as_pending(users, offices, memo_type):
    """Two recipients, one confirms: the record reads RECEIVED but one still owes."""
    from apps.tracking.models import TrackingRecord

    record = create_draft_record(
        user=users["med"], subject="Circular for two offices", instructions="For action.",
        document_type=memo_type,
    )
    route_record(record, [offices["SUP"], offices["HR"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    record.refresh_from_db()

    assert record.status == Status.RECEIVED
    visible = TrackingRecord.objects.visible_to(users["med"])
    assert record in apply_scope(visible, "awaiting", users["med"]), (
        "HR has not confirmed, so the record is still pending a receipt"
    )


# ---------------------------------------------------------------------------
# The simplified filter panel
#
# The free-text box is gone, the queues are pills rather than a dropdown, and
# what is left is the two questions the pills cannot answer: which office raised
# it, and whose files am I looking at.
# ---------------------------------------------------------------------------
def test_the_free_text_box_is_gone_from_the_form():
    """Tracking searches records; the repository's search is the one that
    indexes file text. Two boxes doing different things under the same name is
    what this removes."""
    assert "q" not in TrackingFilterForm().fields


def test_the_pill_driven_fields_are_still_validated():
    """No longer rendered, but the pills arrive as query parameters and
    something has to reject `?scope=bogus` before apply_scope sees it."""
    fields = TrackingFilterForm().fields
    assert "status" in fields
    assert "scope" in fields

    form = TrackingFilterForm({"scope": "bogus"})
    assert form.is_valid() is False
    assert "scope" in form.errors


def test_the_office_filter_is_a_checkbox_group():
    from django import forms

    field = TrackingFilterForm().fields["offices"]
    assert isinstance(field, forms.ModelMultipleChoiceField)
    assert isinstance(field.widget, forms.CheckboxSelectMultiple)


def test_the_owner_filter_offers_exactly_three_choices():
    from django import forms

    field = TrackingFilterForm().fields["owner"]
    assert isinstance(field.widget, forms.RadioSelect)
    assert {value for value, _label in field.choices} == {"", "custody", "mine"}


def test_the_owner_values_are_the_scope_names_apply_scope_already_knows():
    """Chosen to match so apply_scope needs no change. If they ever drift the
    filter silently stops filtering, because apply_scope no-ops on a value it
    does not recognise."""
    from apps.tracking import services

    values = {value for value, _label in TrackingFilterForm().fields["owner"].choices if value}
    assert values == {services.SCOPE_CUSTODY, services.SCOPE_MINE}


def test_the_checkbox_and_radio_widgets_are_not_styled_as_dropdowns():
    """CheckboxSelectMultiple subclasses SelectMultiple and RadioSelect
    subclasses Select, so the Bootstrap mixin used to stamp both form-select."""
    form = TrackingFilterForm()

    for name in ("offices", "owner"):
        classes = form.fields[name].widget.attrs.get("class", "")
        assert "form-check-input" in classes, name
        assert "form-select" not in classes, name


# --- originating office only ------------------------------------------------
@pytest.fixture
def med_record_at_sup(users, offices, memo_type):
    """MED raised it; SUP is holding it."""
    record = create_draft_record(
        user=users["med"], subject="Raised by MED, held by SUP", instructions="For action.",
        document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    record.refresh_from_db()
    return record


@pytest.mark.django_db
def test_filtering_by_office_matches_who_raised_it_not_who_holds_it(
    client, med_record_at_sup, users, offices
):
    """A real behaviour change: the old dropdown matched originating OR current
    office, so picking SUP returned documents merely passing through it. The
    control is labelled "originating office" and now means it."""
    client.force_login(users["admin"])

    by_originator = client.get(f"/tracking/?offices={offices['MED'].pk}")
    assert med_record_at_sup in by_originator.context["records"]

    by_holder = client.get(f"/tracking/?offices={offices['SUP'].pk}")
    assert med_record_at_sup not in by_holder.context["records"], (
        "SUP is holding it, but SUP did not raise it"
    )


@pytest.mark.django_db
def test_checking_two_offices_shows_records_from_either(
    client, med_record_at_sup, users, offices, memo_type
):
    hr_record = create_draft_record(
        user=users["hr"], subject="Raised by HR", instructions="For action.",
        document_type=memo_type,
    )
    route_record(hr_record, [offices["SUP"]], user=users["hr"])

    client.force_login(users["admin"])
    response = client.get(
        f"/tracking/?offices={offices['MED'].pk}&offices={offices['HR'].pk}"
    )
    found = set(response.context["records"])

    assert med_record_at_sup in found
    assert hr_record in found


# --- owner, and composing with a pill ---------------------------------------
@pytest.mark.django_db
def test_files_created_by_me_returns_only_mine(client, users, offices, memo_type):
    mine = create_draft_record(
        user=users["med"], subject="Mine", instructions="x", document_type=memo_type,
    )
    route_record(mine, [offices["SUP"]], user=users["med"])
    theirs = create_draft_record(
        user=users["hr"], subject="Theirs", instructions="x", document_type=memo_type,
    )
    route_record(theirs, [offices["MED"]], user=users["hr"])

    client.force_login(users["med"])
    found = set(client.get("/tracking/?owner=mine").context["records"])

    assert mine in found
    assert theirs not in found


@pytest.mark.django_db
def test_office_files_returns_what_your_office_is_holding(client, users, offices, memo_type):
    held = create_draft_record(
        user=users["med"], subject="Held by SUP", instructions="x", document_type=memo_type,
    )
    route_record(held, [offices["SUP"]], user=users["med"])
    confirm_receipt(held, user=users["sup"])
    held.refresh_from_db()

    client.force_login(users["sup"])
    found = set(client.get("/tracking/?owner=custody").context["records"])

    assert held in found


@pytest.mark.django_db
def test_the_owner_filter_narrows_within_the_pill_rather_than_replacing_it(
    client, users, offices, memo_type
):
    """The composability the hidden inputs in the template preserve."""
    from datetime import timedelta

    from django.utils import timezone

    mine_overdue = create_draft_record(
        user=users["med"], subject="Mine and late", instructions="x", document_type=memo_type,
    )
    route_record(mine_overdue, [offices["SUP"]], user=users["med"])
    mine_overdue.due_at = timezone.now() - timedelta(days=2)
    mine_overdue.save(update_fields=["due_at"])

    mine_on_time = create_draft_record(
        user=users["med"], subject="Mine and fine", instructions="x", document_type=memo_type,
    )
    route_record(mine_on_time, [offices["SUP"]], user=users["med"])
    mine_on_time.due_at = None
    mine_on_time.save(update_fields=["due_at"])

    theirs_overdue = create_draft_record(
        user=users["hr"], subject="Theirs and late", instructions="x", document_type=memo_type,
    )
    route_record(theirs_overdue, [offices["MED"]], user=users["hr"])
    theirs_overdue.due_at = timezone.now() - timedelta(days=2)
    theirs_overdue.save(update_fields=["due_at"])

    client.force_login(users["med"])
    found = set(client.get("/tracking/?scope=overdue&owner=mine").context["records"])

    assert mine_overdue in found, "overdue and mine"
    assert mine_on_time not in found, "mine, but not overdue"
    assert theirs_overdue not in found, "overdue, but not mine"


# --- what the page renders --------------------------------------------------
@pytest.mark.django_db
def test_the_page_has_no_search_box(client, users):
    client.force_login(users["admin"])
    body = client.get("/tracking/").content.decode()
    # The page's own markup, not the chrome around it. The topbar carries the
    # repository's archive search — also name="q" — on every page, and that one
    # is deliberately untouched: it searches documents, which is the job the
    # tracking box was confusingly duplicating.
    content = body.split('<main id="main-content"', 1)[1]

    assert 'name="q"' not in content

    # The queue nav is direction; the rows below carry stage and deadline.
    queue_nav = body[body.index("tracking-queue-nav"):body.index("tracking-filters")]
    for label in ("All Active", "Incoming", "Outgoing"):
        assert label in queue_nav, label
    for label in ("Pending receipt", "Received", "In process",
                  "Completed - pending upload", "Overdue"):
        assert label in body, label
        assert label not in queue_nav, f"{label} belongs in its own row"


# --- the stage row ----------------------------------------------------------
@pytest.mark.django_db
@pytest.mark.parametrize("url", ["/tracking/", "/search/?mode=tracking&q=a"])
def test_the_stage_row_offers_the_four_live_stages(client, users, url):
    client.force_login(users["med"])
    body = client.get(url).content.decode()
    row = body[body.index("status-label"):]
    row = row[:row.index("</nav>") if "</nav>" in row else len(row)]

    for value in ("PENDING_RECEIPT", "RECEIVED", "IN_PROCESS", "COMPLETED_PENDING_UPLOAD"):
        assert f"status={value}" in row, value
    # Selecting none is all of them, so an "All statuses" pill would be a second
    # way to say the same thing — and beside a stage it reads as "all and this".
    assert "All statuses" not in row
    # Visible only to its author, so a Draft pill would mean something different
    # for every reader of the same page.
    assert "status=DRAFT" not in row


@pytest.mark.django_db
def test_two_stages_can_be_asked_for_at_once(client, users, offices, memo_type):
    """`?status=` was single-valued, so "pending receipt and received" — the two
    halves of what an office has not finished with — could not be asked at all.
    You picked one and re-read the page for the other."""
    waiting = create_draft_record(
        user=users["med"], subject="Still waiting", instructions="x", document_type=memo_type,
    )
    route_record(waiting, [offices["SUP"]], user=users["med"])
    signed = create_draft_record(
        user=users["med"], subject="Signed for", instructions="x", document_type=memo_type,
    )
    route_record(signed, [offices["SUP"]], user=users["med"])
    confirm_receipt(signed, user=users["sup"])

    client.force_login(users["med"])

    def subjects(query):
        return {
            record.subject
            for record in client.get(f"/tracking/{query}").context["page_obj"].object_list
        }

    assert subjects("?status=PENDING_RECEIPT") == {"Still waiting"}
    assert subjects("?status=RECEIVED") == {"Signed for"}
    assert subjects("?status=PENDING_RECEIPT&status=RECEIVED") == {"Still waiting", "Signed for"}


@pytest.mark.django_db
def test_a_repeated_stage_lights_one_pill(client, users):
    """`?status=A&status=A` is one selection, not two."""
    client.force_login(users["med"])

    resolved = client.get("/tracking/?status=RECEIVED&status=RECEIVED").context["resolved"]

    assert resolved.statuses == ["RECEIVED"]


@pytest.mark.django_db
def test_an_unknown_stage_is_refused_and_the_rest_kept(client, users):
    """Not all-or-nothing: a stale link with one bad value must narrow by the
    values it does understand rather than quietly returning everything."""
    client.force_login(users["med"])

    response = client.get("/tracking/?status=RECEIVED&status=BOGUS")

    assert response.context["resolved"].statuses == ["RECEIVED"]
    assert "status" in response.context["resolved"].invalid


@pytest.mark.django_db
def test_the_stage_row_composes_with_the_queue(client, users, offices, memo_type):
    """What the four stage pills lost by leaving the queue nav, and how it comes
    back. `?scope=received` meant "addressed to my office *and* signed for";
    `?status=RECEIVED` alone is that stage anywhere. Selecting the queue and the
    stage together asks the original question — and asks two stages at once,
    which a single `scope` never could."""
    waiting = create_draft_record(
        user=users["med"], subject="Waiting", instructions="x", document_type=memo_type,
    )
    route_record(waiting, [offices["SUP"]], user=users["med"])
    signed = create_draft_record(
        user=users["med"], subject="Signed", instructions="x", document_type=memo_type,
    )
    route_record(signed, [offices["SUP"]], user=users["med"])
    confirm_receipt(signed, user=users["sup"])

    client.force_login(users["sup"])

    def subjects(query):
        return {
            record.subject
            for record in client.get(f"/tracking/{query}").context["page_obj"].object_list
        }

    # The old queue, rebuilt out of its two halves.
    assert subjects("?scope=incoming&status=RECEIVED") == subjects("?scope=received")
    # And the question it could not be asked.
    assert subjects("?scope=incoming&status=PENDING_RECEIPT&status=RECEIVED") == {
        "Waiting", "Signed",
    }


@pytest.mark.django_db
@pytest.mark.parametrize(
    "scope", ["pending-receipt", "received", "in-process", "pending-upload"],
)
def test_a_stage_queue_that_lost_its_pill_still_resolves(client, users, scope):
    """The dashboard's links and saved bookmarks carry these. Only which of them
    get a pill has changed — `?scope=received` still means what it always did."""
    client.force_login(users["sup"])

    response = client.get(f"/tracking/?scope={scope}")

    assert response.status_code == 200
    assert response.context["resolved"].scope == scope


# --- direction queues need an office ----------------------------------------
@pytest.mark.django_db
@pytest.mark.parametrize("url", ["/tracking/?office=all", "/search/?mode=tracking&q=a&office=all"])
def test_incoming_and_outgoing_are_disabled_across_every_office(client, users, url):
    """They are properties of a document *and* an office — the batch that is
    outgoing for Supply is incoming for HR at the same instant. Disabled rather
    than hidden: a row that loses two pills when the picker changes reads as the
    page breaking."""
    client.force_login(users["admin"])
    body = client.get(url).content.decode()
    queue_nav = body[body.index("tracking-queue-nav"):body.index("tracking-filters")]

    assert queue_nav.count("pill-toggle is-disabled") == 2
    assert "scope=incoming" not in queue_nav
    assert "scope=outgoing" not in queue_nav


@pytest.mark.django_db
def test_a_direction_queue_asked_across_every_office_is_dropped_not_honoured(client, users):
    """`apply_scope` with no office turns both predicates into the empty Q, so
    honouring the click would head the full active list "Incoming" — the
    fail-open the filter module exists to prevent."""
    client.force_login(users["admin"])

    response = client.get("/tracking/?office=all&scope=incoming")
    resolved = response.context["resolved"]

    assert resolved.scope == ""
    assert resolved.direction_dropped is True
    everything = client.get("/tracking/?office=all").context["page_obj"].paginator.count
    assert response.context["page_obj"].paginator.count == everything


@pytest.mark.django_db
def test_the_direction_queues_still_work_for_one_office(client, users, offices):
    """Only "every office" removes them."""
    client.force_login(users["admin"])
    body = client.get(f"/tracking/?office={offices['SUP'].pk}").content.decode()
    queue_nav = body[body.index("tracking-queue-nav"):body.index("tracking-filters")]

    assert "is-disabled" not in queue_nav
    assert "scope=incoming" in queue_nav


# --- the deadline row -------------------------------------------------------
@pytest.mark.django_db
@pytest.mark.parametrize("url", ["/tracking/", "/search/?mode=tracking&q=a"])
def test_the_deadline_row_offers_all_three_states(client, users, url):
    """A single Overdue pill could say "late" and could not say "on time".
    `?overdue=no` was resolved and filtered all along and simply had no control,
    so the one question an office asks in two directions had a button for one."""
    client.force_login(users["med"])
    body = client.get(url).content.decode()

    for label in ("All deadlines", "On time", "Overdue"):
        assert f">{label}</a>" in body, label


@pytest.mark.django_db
@pytest.mark.parametrize("url", ["/tracking/", "/search/?mode=tracking&q=a"])
def test_the_overdue_pill_left_the_queue_nav(client, users, url):
    """It is a modifier, not a queue — it composes with whichever queue is
    selected, so it belongs with Status and Show rather than among the pills
    that replace each other."""
    client.force_login(users["med"])
    body = client.get(url).content.decode()
    queue_nav = body[body.index("tracking-queue-nav"):body.index("tracking-filters")]

    assert "Overdue" not in queue_nav
    assert "overdue=yes" not in queue_nav


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("query", "lit"),
    [("", "All deadlines"), ("?overdue=no", "On time"), ("?overdue=yes", "Overdue")],
)
def test_the_deadline_row_lights_the_state_it_is_in(client, users, query, lit):
    client.force_login(users["med"])
    body = client.get(f"/tracking/{query}").content.decode()
    # From this row's label to the next row's, so the Show row below — which
    # always has an active pill of its own — cannot be counted as this one's.
    row = body[body.index('id="filter-deadline-label"'):]
    row = row[:row.index("tracking-filter-label")] if "tracking-filter-label" in row else row

    active = re.findall(r'<a class="pill-toggle[^"]*is-active[^"]*"[^>]*>([^<]+)</a>', row)

    assert [label.strip() for label in active] == [lit], row


@pytest.mark.django_db
def test_on_time_actually_narrows(client, users, offices, memo_type):
    """The state that had no control. It has to filter, not merely light up."""
    from datetime import timedelta

    from django.utils import timezone

    late = create_draft_record(
        user=users["med"], subject="Late one", instructions="x", document_type=memo_type,
    )
    route_record(late, [offices["SUP"]], user=users["med"])
    late.due_at = timezone.now() - timedelta(days=3)
    late.save(update_fields=["due_at"])

    punctual = create_draft_record(
        user=users["med"], subject="Punctual one", instructions="x", document_type=memo_type,
    )
    route_record(punctual, [offices["SUP"]], user=users["med"])
    punctual.due_at = timezone.now() + timedelta(days=3)
    punctual.save(update_fields=["due_at"])

    client.force_login(users["med"])

    def subjects(query):
        return {
            record.subject
            for record in client.get(f"/tracking/{query}").context["page_obj"].object_list
        }

    assert subjects("?overdue=no") == {"Punctual one"}
    assert subjects("?overdue=yes") == {"Late one"}
    assert {"Late one", "Punctual one"} <= subjects("")


# --- what a row says about which office is which ----------------------------
@pytest.mark.django_db
@pytest.mark.parametrize("url", ["/tracking/", "/search/?mode=tracking&q=electrical"])
def test_a_row_names_the_originating_office_rather_than_saying_from(
    client, users, offices, memo_type, url
):
    """Every row here already carries a Current office column, and for a
    document in transit the two are different offices — so a bare "from SUP"
    beside "Current office SUP" read as one fact stated twice.

    It is the ambiguity the office filter was split to remove: the dropdown it
    replaced matched originating OR current office, so picking Supply returned
    both what Supply raised and what was merely passing through it. The row now
    uses the filter's word and the detail page's word."""
    record = create_draft_record(
        user=users["med"], subject="Electrical supplies request", instructions="For action.",
        document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    client.force_login(users["med"])

    body = client.get(url).content.decode()
    cell = body[body.index("Electrical supplies request"):]
    cell = cell[:cell.index("</td>")]

    assert "Originating office" in cell
    assert "· from " not in cell


@pytest.mark.django_db
def test_the_in_process_pill_is_a_scope_and_not_a_status(client, users):
    """It was left off this row when a607c14 made the pills scope-based, and is
    back by request — as a scope, which is the part that matters.

    The mixed status pills that commit replaced meant different things from each
    other: a status-based "Received" put documents *another* office had received
    into your queue, because a status says nothing about who is holding one.
    Measured on the demo data, one office saw 3 in-process records it was
    holding against 9 in that status anywhere.
    """
    # An office user, not the system administrator: the administrator defaults
    # to every office, where Incoming and Outgoing are drawn disabled and carry
    # no href at all.
    client.force_login(users["sup"])
    body = client.get("/tracking/").content.decode()
    queue_nav = body[body.index("tracking-queue-nav"):body.index("tracking-filters")]

    # In process is a Stage pill now, and the queue nav is direction only. What
    # the original point survives as: the two ideas are not in one row wearing
    # one pill shape, so neither can be mistaken for the other.
    assert "?status=IN_PROCESS" in body, "still filterable, in the Stage row"
    assert "?status=IN_PROCESS" not in queue_nav
    assert "?scope=in-process" not in queue_nav

    # And the custody question it used to ask is still askable, by composing:
    # the queue carries the office, the stage carries the stage.
    assert "?scope=incoming" in queue_nav


@pytest.mark.django_db
def test_every_filter_pill_carries_the_active_queue(client, users, offices):
    """Each pill's href already contains the selection clicking it produces, so
    there is nothing to submit — and the queue has to ride along in it, or
    filtering would drop the reader back to All Active."""
    client.force_login(users["admin"])
    body = client.get("/tracking/?scope=overdue").content.decode()

    assert "scope=overdue&amp;offices=" in body, "office pills keep the queue"
    assert "scope=overdue&amp;owner=" in body, "owner pills keep the queue"


@pytest.mark.django_db
def test_the_filters_are_links_not_a_form(client, users):
    """The card, the checkboxes, the radios and the Apply button are all gone:
    one pill language for the page, not two."""
    client.force_login(users["admin"])
    content = client.get("/tracking/").content.decode().split('<main id="main-content"', 1)[1]

    assert "Apply filters" not in content
    assert 'name="offices"' not in content, "a link, not a checkbox"
    assert 'name="owner"' not in content, "a link, not a radio"
    assert "form-check-input" not in content


@pytest.mark.django_db
def test_the_panel_renders_both_groups(client, users, offices):
    client.force_login(users["admin"])
    body = client.get("/tracking/").content.decode()

    assert "Originating office" in body
    assert "Office files" in body
    assert "Files created by me only" in body
    for office in offices.values():
        assert f">{office.code}" in body or f"{office.code}\n" in body, office.code


@pytest.mark.django_db
def test_the_queue_and_filter_pills_share_one_class(client, users):
    """The acceptance criterion: one pill design on the page, not two."""
    client.force_login(users["admin"])
    content = client.get("/tracking/").content.decode().split('<main id="main-content"', 1)[1]

    assert "tracking-queue-link" not in content, "folded into the shared class"
    # Six queue pills plus the office and owner rows all use the same class.
    assert content.count("pill-toggle") > 10


@pytest.mark.django_db
def test_an_office_pill_toggles_itself_off_again(client, users, offices):
    """Multi-select without a form: the pill for an office already selected
    links to the selection *without* it. Only its own pill does — the others
    must keep it, which is what makes selecting a second office additive."""
    import re

    client.force_login(users["admin"])
    med, sup = offices["MED"], offices["SUP"]
    body = client.get(f"/tracking/?offices={med.pk}").content.decode()

    anchors = dict(
        re.findall(r'href="([^"]*)"\s*\n\s*title="([^"]*)"', body)
    )
    by_office = {name: href for href, name in anchors.items()}

    assert f"offices={med.pk}" not in by_office[med.name], "its own pill clears it"
    assert f"offices={med.pk}" in by_office[sup.name], "another pill keeps it"


@pytest.mark.django_db
def test_a_second_office_pill_keeps_the_first(client, users, offices):
    client.force_login(users["admin"])
    med, sup = offices["MED"].pk, offices["SUP"].pk
    body = client.get(f"/tracking/?offices={med}").content.decode()

    assert f"offices={med}&amp;offices={sup}" in body


@pytest.mark.django_db
def test_changing_a_filter_returns_to_the_first_page(client, users, offices):
    """Page four of the old filter is not page four of the new one."""
    client.force_login(users["admin"])
    body = client.get(f"/tracking/?page=3&offices={offices['MED'].pk}").content.decode()
    filters = body.split("Originating office", 1)[1].split("active record", 1)[0]

    assert "page=3" not in filters

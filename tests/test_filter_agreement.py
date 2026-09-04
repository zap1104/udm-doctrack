"""The number on the dashboard equals the rows on the page it links to.

Three definitions of the same queues existed side by side: `inbox_for` and its
siblings, `combined_totals` counting bare statuses with no office at all, and
`apply_scope`. The cards were rendered from the first, the ring from the second,
and both linked to the third. Only the third was right.

Nothing tested the join between them. `tests/test_tracking_queues.py` covers
each queue in isolation and passed throughout, because every queue really was
correct on its own — what was wrong was that the dashboard counted a different
one from the page it opened.

Measured before the fix, one office on the demo data: Incoming card 4 against a
page listing 5, Held by your office 1 against 9, and a Pending receipt slice of
8 over a page of 4. The minimal case was one record: an office that sent a memo
saw "Pending receipt 1" and clicked through to an empty table, because the
count included what it had sent and the page correctly did not.

These tests assert on record *sets*, not counts. The Incoming slice once matched
on count while listing entirely different rows — the slice counted a memo
sitting at another office, the page listed memos addressed to this one — and a
count-only assertion would have called that agreement.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest

from apps.tracking.models import Status, TrackingRecord
from apps.tracking.services import (
    SCOPE_INCOMING,
    SCOPE_OUTGOING,
    active_for,
    annotate_direction,
    apply_scope,
    complete_record,
    confirm_receipt,
    create_draft_record,
    mark_in_process,
    route_record,
)

DASHBOARD = "/"
TRACKING = "/tracking/"


@pytest.fixture
def traffic(users, offices, memo_type):
    """Enough shapes that a wrong queue cannot look right by coincidence.

    SUP is the office under test. It has something arriving unconfirmed, one it
    received, one it is working on, one it sent that nobody has signed for, and
    one it has nothing to do with.
    """
    made = {}

    def send(key, sender, sender_user, targets, subject):
        record = create_draft_record(
            user=sender_user, subject=subject, instructions="For action.",
            document_type=memo_type, originating_office=sender,
        )
        route_record(record, targets, user=sender_user)
        record.refresh_from_db()
        made[key] = record
        return record

    send("arriving", offices["MED"], users["med"], [offices["SUP"]], "Arriving, unconfirmed")

    received = send("received", offices["MED"], users["med"], [offices["SUP"]], "Received here")
    confirm_receipt(received, user=users["sup"])

    working = send("working", offices["HR"], users["hr"], [offices["SUP"]], "Being worked on")
    confirm_receipt(working, user=users["sup"])
    mark_in_process(working, user=users["sup"])

    send("sent", offices["SUP"], users["sup"], [offices["HR"]], "Sent, unconfirmed")
    send("elsewhere", offices["MED"], users["med"], [offices["HR"]], "Nothing to do with SUP")

    for record in made.values():
        record.refresh_from_db()
    return made


def page_records(client, url):
    """Every row the page lists, as a set of primary keys."""
    seen, page = set(), 1
    while True:
        joiner = "&" if "?" in url else "?"
        response = client.get(f"{url}{joiner}page={page}")
        assert response.status_code == 200, url
        assert "Ignored a filter" not in response.content.decode(), url
        listing = response.context["page_obj"]
        seen |= {record.pk for record in listing.object_list}
        if not listing.has_next():
            return seen
        page += 1


# --- the cards and the ring ------------------------------------------------
@pytest.mark.django_db
@pytest.mark.parametrize(
    ("key", "scope"),
    [
        ("incoming_count", "incoming"),
        ("outgoing_count", "outgoing"),
        ("received_count", "received"),
        ("overdue_count", "overdue"),
    ],
)
def test_every_stat_card_matches_the_page_it_opens(client, users, traffic, key, scope):
    """A count nobody can click through to is a claim; a count that disagrees
    with the page behind it is a wrong claim presented as a right one."""
    client.force_login(users["sup"])
    counted = client.get(DASHBOARD).context[key]

    assert counted == len(page_records(client, f"{TRACKING}?scope={scope}"))


@pytest.mark.django_db
def test_every_ring_slice_matches_the_page_it_opens(client, users, traffic):
    """Looped over the slices rather than written one test per slice, so a slice
    added later is covered without anybody remembering to cover it."""
    client.force_login(users["sup"])
    response = client.get(DASHBOARD)

    checked = 0
    for row in response.context["breakdown"]["slices"]:
        if not row["url"].startswith(TRACKING):
            continue
        assert row["total"] == len(page_records(client, row["url"])), row["label"]
        checked += 1
    assert checked >= 4, "the tracking slices should all be checked"


@pytest.mark.django_db
def test_the_counts_are_the_same_records_not_merely_the_same_number(client, users, traffic):
    """The failure this is really for.

    On one fixture the Incoming slice and its page both said 2 while listing
    disjoint sets — the slice counted a memo sitting at another office, the page
    listed the two addressed to this one. Equal counts over different rows is
    the shape most likely to survive review.
    """
    client.force_login(users["sup"])
    listed = page_records(client, f"{TRACKING}?scope=incoming")
    scoped = set(
        apply_scope(active_for(users["sup"]), SCOPE_INCOMING, users["sup"])
        .distinct()
        .values_list("pk", flat=True)
    )

    assert listed == scoped
    assert traffic["elsewhere"].pk not in listed, "not addressed to this office"


@pytest.mark.django_db
def test_the_minimal_reported_case(client, users, offices, memo_type):
    """One record, one office: SUP sends to HR and HR has not confirmed.

    The dashboard said Pending receipt 1 and the page listed 0 rows, because the
    count was a bare status with no office on it. Both halves are now the same
    query, and per the agreed definition the sending office does see it — this
    is the one queue that answers in both directions.
    """
    record = create_draft_record(
        user=users["sup"], subject="Sent and unconfirmed", instructions="For action.",
        document_type=memo_type,
    )
    route_record(record, [offices["HR"]], user=users["sup"])

    client.force_login(users["sup"])
    response = client.get(DASHBOARD)
    slice_row = next(
        row for row in response.context["breakdown"]["slices"] if row["key"] == "pending_receipt"
    )

    assert slice_row["total"] == 1
    assert page_records(client, slice_row["url"]) == {record.pk}


# --- pending receipt, both directions --------------------------------------
@pytest.mark.django_db
def test_pending_receipt_answers_in_both_directions(client, users, traffic):
    """What we owe a receipt on, and what we are waiting on.

    The outgoing half was missing, so the one queue whose whole job is "who has
    not confirmed" could not answer it for the office doing the asking.
    """
    client.force_login(users["sup"])
    listed = page_records(client, f"{TRACKING}?scope=pending-receipt")

    assert traffic["arriving"].pk in listed, "addressed to us, unconfirmed"
    assert traffic["sent"].pk in listed, "sent by us, unconfirmed"
    assert traffic["received"].pk not in listed, "already confirmed"
    assert traffic["elsewhere"].pk not in listed, "neither ours to send nor receive"


@pytest.mark.django_db
def test_only_the_incoming_half_can_be_confirmed(client, users, traffic):
    """Both directions share the queue; only one of them owes an act. The button
    is gated by annotate_can_confirm, which checks to_office_id on its own."""
    client.force_login(users["sup"])
    response = client.get(f"{TRACKING}?scope=pending-receipt")
    confirmable = {r.pk for r in response.context["page_obj"].object_list if r.can_confirm_now}

    assert traffic["arriving"].pk in confirmable
    assert traffic["sent"].pk not in confirmable, "we cannot sign for what we sent"


# --- the direction tag -----------------------------------------------------
@pytest.mark.django_db
def test_the_tag_agrees_with_the_queues(users, traffic):
    """The tag and the pill that would list the row are derived from the same
    predicate, so they cannot disagree."""
    user = users["sup"]
    records = list(active_for(user))
    annotate_direction(records, user)

    tagged_in = {r.pk for r in records if r.direction == "incoming"}
    tagged_out = {r.pk for r in records if r.direction == "outgoing"}
    scope_in = set(apply_scope(active_for(user), SCOPE_INCOMING, user).distinct().values_list("pk", flat=True))
    scope_out = set(apply_scope(active_for(user), SCOPE_OUTGOING, user).distinct().values_list("pk", flat=True))

    assert tagged_in == scope_in
    assert tagged_out == scope_out


@pytest.mark.django_db
def test_a_record_the_office_never_touched_carries_no_tag(users, traffic):
    """A records officer or an administrator sees these. A direction they do not
    have would be a worse answer than none."""
    records = list(TrackingRecord.objects.visible_to(users["admin"]))
    annotate_direction(records, users["admin"])

    elsewhere = next(r for r in records if r.pk == traffic["elsewhere"].pk)
    assert elsewhere.direction == ""


@pytest.mark.django_db
def test_no_record_is_ever_both_directions(users, traffic):
    """route_record refuses to send an office its own document, and every step
    in a batch shares a from_office, so this cannot happen today. Asserted so a
    change to routing does not start mislabelling rows in silence."""
    for user in (users["sup"], users["hr"], users["med"]):
        records = list(active_for(user))
        annotate_direction(records, user)
        both = [
            r.pk for r in records
            if r.direction == "incoming"
            and r.routing_steps.filter(
                from_office_id=user.office_id, batch=r.current_batch
            ).exists()
        ]
        assert both == [], f"{user.username} sees a record in both directions"


@pytest.mark.django_db
def test_the_same_record_reads_both_ways_at_once(users, traffic):
    """Outgoing for the sender and incoming for the recipient at the same
    instant — which is why direction cannot be a column."""
    sent = traffic["sent"]

    for user, expected in ((users["sup"], "outgoing"), (users["hr"], "incoming")):
        records = [TrackingRecord.objects.get(pk=sent.pk)]
        annotate_direction(records, user)
        assert records[0].direction == expected, user.username


@pytest.mark.django_db
def test_the_tag_renders_on_both_listing_pages(client, users, traffic):
    client.force_login(users["sup"])

    for url in (f"{TRACKING}?scope=incoming", "/search/?mode=tracking&scope=incoming"):
        body = client.get(url).content.decode()
        assert "pill-incoming" in body or "pill-outgoing" in body, url


@pytest.mark.django_db
def test_the_tag_costs_one_query_for_the_whole_page(
    django_assert_num_queries, client, users, traffic
):
    """One grouped query, like annotate_can_confirm beside it. The per-row
    version is twenty queries on a twenty-row page."""
    user = users["sup"]
    records = list(active_for(user))
    assert len(records) > 1, "needs several rows to be worth asserting"

    with django_assert_num_queries(1):
        annotate_direction(records, user)


# --- the aliases -----------------------------------------------------------
@pytest.mark.django_db
def test_the_legacy_aliases_are_not_synonyms(users, traffic):
    """A comment in the dashboard template asserted that `inbox` and `incoming`
    resolved to the same queue. They do not, and the template linked to both
    under one label."""
    user = users["sup"]
    base = TrackingRecord.objects.visible_to(user)

    inbox = set(apply_scope(base, "inbox", user).distinct().values_list("pk", flat=True))
    incoming = set(apply_scope(base, "incoming", user).distinct().values_list("pk", flat=True))

    assert inbox != incoming
    assert inbox < incoming, "inbox is the unconfirmed subset of incoming"


def test_nothing_links_to_a_legacy_alias():
    """They stay in apply_scope so old bookmarks resolve, but nothing in the app
    should point at one — they are not the queues their names suggest."""
    import pathlib
    import re

    # Links only. The comments beside them exist to record why the aliases are
    # not used, and matching prose would flag the documentation of the fix as
    # the fault it documents.
    link = re.compile(r"(?:href=|redirect\()[^\n]*[?&]scope=(?:inbox|custody|sent|awaiting)\b")

    offenders = []
    for path in list(pathlib.Path("templates").rglob("*.html")) + list(
        pathlib.Path("apps").rglob("*.py")
    ):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if link.search(line):
                offenders.append(f"{path}:{number}")
    assert offenders == [], f"legacy alias still linked from: {offenders}"


@pytest.mark.django_db
def test_the_status_of_a_slice_and_its_page_agree(client, users, traffic):
    """Received and In process are split by status out of one scope, so each
    slice links with `&status=` and must list only that status."""
    client.force_login(users["sup"])
    response = client.get(DASHBOARD)

    for key, status in (("received", Status.RECEIVED), ("in_process", Status.IN_PROCESS)):
        row = next(r for r in response.context["breakdown"]["slices"] if r["key"] == key)
        listed = client.get(row["url"]).context["page_obj"].object_list
        assert all(record.status == status for record in listed), key


# --- the office picker -----------------------------------------------------
@pytest.mark.django_db
def test_the_picker_moves_the_cards_to_that_office(client, users, offices, traffic):
    """The picker filtered the ring and the memo and left the cards alone.

    They counted through `apply_scope`, which resolved against the viewer's own
    office, so an administrator looking at MED was reading their own desk under
    MED's heading — the same figures whichever office they named.
    """
    client.force_login(users["admin"])

    own = client.get(DASHBOARD).context
    picked = client.get(f"{DASHBOARD}?office={offices['SUP'].pk}").context

    assert picked["incoming_count"] != own["incoming_count"], "the picker did nothing"
    # SUP's desk, as SUP sees it.
    client.force_login(users["sup"])
    theirs = client.get(DASHBOARD).context
    assert picked["incoming_count"] == theirs["incoming_count"]
    assert picked["outgoing_count"] == theirs["outgoing_count"]
    assert picked["received_count"] == theirs["received_count"]


@pytest.mark.django_db
def test_a_picked_office_travels_to_the_page_the_card_opens(client, users, offices, traffic):
    """Half a fix is worse than none here: counting the picked office while
    linking to the viewer's would put the disagreement back."""
    client.force_login(users["admin"])
    response = client.get(f"{DASHBOARD}?office={offices['SUP'].pk}")
    body = response.content.decode()

    assert f"?scope=incoming&office={offices['SUP'].pk}" in body
    counted = response.context["incoming_count"]
    listed = page_records(client, f"{TRACKING}?scope=incoming&office={offices['SUP'].pk}")

    assert counted == len(listed)


@pytest.mark.django_db
def test_an_ordinary_user_cannot_read_another_office_by_url(client, users, offices, traffic):
    """`?office=` is gated by the same `is_office_admin` test as the picker
    itself, so the queue param cannot become a way around it."""
    client.force_login(users["med"])

    own = page_records(client, f"{TRACKING}?scope=incoming")
    forged = page_records(client, f"{TRACKING}?scope=incoming&office={offices['SUP'].pk}")

    assert forged == own, "the office param was honoured for a non-admin"


@pytest.mark.django_db
def test_no_office_picked_does_not_narrow_the_queues_that_have_no_office(
    client, users, traffic
):
    """Overdue is not a per-office queue, so nothing should narrow it until
    somebody asks. Falling back to the viewer's own office here took an
    administrator's Overdue from 32 records to 3."""
    client.force_login(users["admin"])

    unpicked = page_records(client, f"{TRACKING}?scope=overdue")
    everything = page_records(client, TRACKING)

    assert unpicked <= everything
    assert client.get(DASHBOARD).context["overdue_count"] == len(unpicked)


# --- whole-system integration ----------------------------------------------
QUERYSTRINGS = [
    "", "scope=incoming", "scope=outgoing", "scope=pending-receipt",
    "scope=received", "scope=overdue", "scope=awaiting", "scope=pending-upload",
    "status=RECEIVED", "status=IN_PROCESS", "owner=mine", "scope=incoming&owner=",
]


@pytest.mark.django_db
@pytest.mark.parametrize("query", QUERYSTRINGS)
def test_tracking_and_search_answer_identically(client, users, traffic, query):
    """Two pages, one question. They shared `filter_records` and `apply_scope`
    and still disagreed — 3 records against 0 — because the *office resolution*
    was never shared. Looped so a filter added later is covered the same day."""
    client.force_login(users["sup"])

    listed = {r.pk for r in client.get(f"{TRACKING}?{query}").context["page_obj"].object_list}
    searched = {
        r.pk for r in client.get(f"/search/?mode=tracking&{query}").context["page_obj"].object_list
    }

    assert listed == searched


@pytest.mark.django_db
@pytest.mark.parametrize("query", ["", "scope=incoming", "scope=overdue", "scope=awaiting"])
def test_the_two_pages_agree_under_a_picked_office(client, users, offices, traffic, query):
    """Including the office, which search dropped entirely."""
    client.force_login(users["admin"])
    full = f"{query}&office={offices['SUP'].pk}"

    listed = {r.pk for r in client.get(f"{TRACKING}?{full}").context["page_obj"].object_list}
    searched = {
        r.pk for r in client.get(f"/search/?mode=tracking&{full}").context["page_obj"].object_list
    }

    assert listed == searched


@pytest.mark.django_db
def test_every_dashboard_link_agrees_under_a_picked_office(client, users, offices, traffic):
    """The stat cards learned to carry the office and the ring slices did not,
    so a slice counted one office and opened another's page."""
    client.force_login(users["admin"])

    for query in ("", f"?office={offices['SUP'].pk}"):
        response = client.get(DASHBOARD + query)
        body = response.content.decode()

        for key, scope in (("incoming_count", "incoming"), ("outgoing_count", "outgoing"),
                           ("received_count", "received"), ("overdue_count", "overdue")):
            href = re.search(rf'href="(/tracking/\?scope={scope}[^"]*)"', body).group(1)
            assert response.context[key] == len(page_records(client, href)), (scope, query)

        for row in response.context["breakdown"]["slices"]:
            if row["url"].startswith(TRACKING):
                assert row["total"] == len(page_records(client, row["url"])), (row["label"], query)


@pytest.mark.django_db
def test_the_direction_tag_follows_the_picked_office(client, users, offices, traffic):
    """Every row of a page headed another office's Incoming used to read
    Outgoing: the queue was built for them, the tag for the viewer."""
    client.force_login(users["admin"])
    listing = client.get(
        f"{TRACKING}?scope=incoming&office={offices['SUP'].pk}"
    ).context["page_obj"].object_list

    assert listing, "needs rows to be worth asserting"
    assert {record.direction for record in listing} == {"incoming"}


@pytest.mark.django_db
def test_the_ring_total_is_the_tracking_page_minus_drafts(client, users, traffic, memo_type):
    """DRAFT is in ACTIVE_STATUSES and the page lists drafts, so the ring ran
    one short while its docstring claimed they were excluded. They are excluded
    now, deliberately: a draft is visible only to its author, so a Draft slice
    would make the ring mean something different for every viewer."""
    create_draft_record(
        user=users["sup"], subject="Unsent", instructions="x", document_type=memo_type,
    )
    client.force_login(users["sup"])

    ring = client.get(DASHBOARD).context["breakdown"]["tracking_total"]
    page = len(page_records(client, TRACKING))
    drafts = len(page_records(client, f"{TRACKING}?status={Status.DRAFT}"))

    assert drafts == 1, "the fixture should have made one"
    assert ring == page - drafts


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/tracking/", "/documents/", "/reports/"])
def test_no_office_filter_fails_open(client, users, path):
    """A filter that fails open is worse than one that errors: the reader
    believes the page is narrowed to one office and it is the whole
    university. An unresolvable value has to be *said*, not dropped."""
    client.force_login(users["admin"])
    response = client.get(f"{path}?office=99999")
    body = response.content.decode().lower()

    assert response.status_code == 200
    assert "not recognised" in body or "could not apply" in body, path


@pytest.mark.django_db
def test_the_two_office_filters_are_different_questions(client, users, offices, traffic):
    """`office` is "view as that office"; `offices` is "raised by that office".
    Both legitimate, and nothing said so — same office, same page, one letter
    apart, different answers. Asserted so a later tidy-up cannot merge them."""
    client.force_login(users["admin"])
    # SUP, not MED: MED raised everything it touches in this fixture, so the two
    # questions happen to coincide there. `traffic["received"]` was raised by
    # MED and now sits at SUP, which is exactly the case that separates them.
    sup = offices["SUP"].pk

    as_office = page_records(client, f"{TRACKING}?office={sup}")
    raised_by = page_records(client, f"{TRACKING}?offices={sup}")

    assert as_office != raised_by
    assert traffic["received"].pk in as_office, "sitting at SUP"
    assert traffic["received"].pk not in raised_by, "but raised by MED"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("query", "phrase"),
    [
        ("scope=incoming&owner=mine", "created by another office"),
        ("scope=received&owner=mine", "created by another office"),
        (f"scope=outgoing&status={Status.DRAFT}", "has not been sent"),
    ],
)
def test_an_impossible_combination_says_why(client, users, traffic, query, phrase):
    """Empty by construction, not empty today. Without a reason the reader
    concludes the filter is broken and stops trusting the others."""
    client.force_login(users["sup"])
    response = client.get(f"{TRACKING}?{query}")

    assert response.context["page_obj"].paginator.count == 0
    assert phrase in response.content.decode()


@pytest.mark.django_db
def test_a_queue_pill_keeps_the_filters_already_applied(client, users, traffic):
    """They were bare `?scope=` links, so a reader with a filter set watched
    the count drop and concluded the queue had narrowed their own files, when
    it had replaced their filter with everybody's."""
    client.force_login(users["sup"])
    body = client.get(f"{TRACKING}?owner=mine&status={Status.RECEIVED}").content.decode()

    pill = re.search(r'href="([^"]*scope=incoming[^"]*)"', body).group(1)

    assert "owner=mine" in pill
    assert f"status={Status.RECEIVED}" in pill


@pytest.mark.django_db
def test_the_action_centre_is_the_queue_its_button_opens(client, users, traffic):
    """It was pending-receipt padded with overdue and then received — three
    queues behind one link, so the panel showed five rows and the page opened
    five, two of them different."""
    client.force_login(users["sup"])
    response = client.get(DASHBOARD)
    panel = {record.pk for record in response.context["attention_records"]}
    body = response.content.decode()

    href = re.search(r'href="(/tracking/\?scope=pending-receipt[^"]*)"', body).group(1)

    assert panel <= page_records(client, href)


# --- overdue is a condition, not a stage -----------------------------------
@pytest.fixture
def deadlines(users, offices, memo_type):
    """One of each live stage, on time and late, plus the two edge cases the
    partition depends on: a record with no deadline, and one completed after
    its deadline passed."""
    from django.utils import timezone as tz

    made = {}

    def routed(key, late, receive=False, process=False, complete=False, due=True):
        record = create_draft_record(
            user=users["med"], subject=f"{key}", instructions="x", document_type=memo_type,
        )
        route_record(record, [offices["SUP"]], user=users["med"])
        if receive:
            confirm_receipt(record, user=users["sup"])
        if process:
            mark_in_process(record, user=users["sup"])
        if complete:
            record.refresh_from_db()
            complete_record(record, user=users["sup"])
        when = (tz.now() - timedelta(days=9)) if late else (tz.now() + timedelta(days=9))
        TrackingRecord.objects.filter(pk=record.pk).update(due_at=when if due else None)
        record.refresh_from_db()
        made[key] = record
        return record

    routed("pending_ontime", late=False)
    routed("pending_late", late=True)
    routed("received_ontime", late=False, receive=True)
    routed("received_late", late=True, receive=True)
    routed("process_ontime", late=False, receive=True, process=True)
    routed("process_late", late=True, receive=True, process=True)
    routed("no_deadline", late=False, due=False)
    routed("completed_after_deadline", late=True, receive=True, complete=True)
    return made


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("key", "label"),
    [("pending_late", "Pending receipt"), ("received_late", "Received"),
     ("process_late", "In process")],
)
def test_a_late_record_shows_its_stage_and_its_lateness(client, users, deadlines, key, label):
    """The pill replaced the stage: `display_status` returned "OVERDUE", so a
    reader who opened the Pending receipt slice counted two records and one
    Pending receipt pill. Overdue says the deadline went by; it does not say
    whether anybody has signed for the document, which is the part to act on."""
    client.force_login(users["sup"])
    record = deadlines[key]
    body = client.get(f"{TRACKING}?q=&status={record.status}").content.decode()

    assert label in body
    assert "Overdue" in body


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status",
    [Status.PENDING_RECEIPT, Status.RECEIVED, Status.IN_PROCESS],
)
def test_the_stage_and_the_deadline_compose(client, users, deadlines, status):
    """Both at once, which `status=OVERDUE` made impossible: it held the one
    parameter, so asking for overdue pending-receipts cost the queue pill."""
    client.force_login(users["sup"])

    staged = page_records(client, f"{TRACKING}?status={status}")
    late = page_records(client, f"{TRACKING}?status={status}&overdue=yes")
    on_time = page_records(client, f"{TRACKING}?status={status}&overdue=no")

    assert late and on_time, "the fixture has one of each"
    assert late | on_time == staged
    assert not (late & on_time)


@pytest.mark.django_db
def test_the_two_deadline_buckets_partition_the_whole(client, users, deadlines):
    """The regression test for the trap in the negative branch.

    `.exclude(due_at__lt=now)` reads as the opposite of overdue and is not: it
    throws out every record whose deadline passed *before* the work finished, so
    those appear under neither filter. Measured on 280 records it returned 13
    where the explicit form returns 248 — 235 rows falling out of a page that
    offers only these two choices. The fixture carries both edge cases.
    """
    client.force_login(users["sup"])

    everything = page_records(client, TRACKING)
    late = page_records(client, f"{TRACKING}?overdue=yes")
    on_time = page_records(client, f"{TRACKING}?overdue=no")

    assert deadlines["no_deadline"].pk in on_time, "no deadline is not late"
    assert deadlines["completed_after_deadline"].pk in on_time, "finished is not late"
    assert late | on_time == everything
    assert not (late & on_time)


@pytest.mark.django_db
def test_the_legacy_spellings_still_resolve(client, users, deadlines):
    """Every saved bookmark, the dashboard's Overdue card and the queue pill
    emit one of these. They translate in the resolver, so nothing downstream
    keeps a second code path — keeping one is what made overdue a status."""
    client.force_login(users["sup"])

    canonical = page_records(client, f"{TRACKING}?overdue=yes")

    assert page_records(client, f"{TRACKING}?status=OVERDUE") == canonical
    assert page_records(client, f"{TRACKING}?scope=overdue") == canonical


@pytest.mark.django_db
def test_a_legacy_spelling_composes_with_a_queue(client, users, deadlines):
    """`?scope=overdue` used to *be* the queue, so it could not narrow one."""
    client.force_login(users["sup"])

    incoming = page_records(client, f"{TRACKING}?scope=incoming")
    late = page_records(client, f"{TRACKING}?scope=incoming&overdue=yes")

    assert late <= incoming
    assert late == incoming & page_records(client, f"{TRACKING}?overdue=yes")


@pytest.mark.django_db
def test_the_export_carries_the_stage_and_the_condition(client, users, deadlines):
    """The CSV wrote "Overdue" into the Status column, so an exported sheet had
    no record of what stage the late documents were at — and a spreadsheet
    attached to a memo is read by somebody who cannot re-run the query."""
    client.force_login(users["admin"])
    rows = client.get("/reports/export/").content.decode().splitlines()

    header = next(r for r in rows if r.startswith("Tracking number"))
    columns = header.split(",")
    assert "Status" in columns and "Overdue" in columns

    late = deadlines["pending_late"]
    line = next(r for r in rows if late.tracking_number in r)
    assert "Pending receipt" in line, "the stage survives the export"
    assert "Yes" in line.split(",")[columns.index("Overdue")]


@pytest.mark.django_db
def test_finished_work_is_never_late(client, users, deadlines):
    """Empty by construction: `is_overdue` and both query branches exclude the
    completed statuses, so asking for both can only ever return nothing."""
    client.force_login(users["sup"])
    response = client.get(f"{TRACKING}?overdue=yes&status={Status.COMPLETED_PENDING_UPLOAD}")

    assert response.context["page_obj"].paginator.count == 0
    assert "is not late" in response.content.decode()


@pytest.mark.django_db
def test_in_process_is_the_started_half_of_received(client, users, deadlines):
    """Two halves of one queue, not two questions side by side.

    SCOPE_RECEIVED is "here with us" and spans Received and In process together,
    so the new pill asks the same custody question and then pins the stage. It
    is therefore always a subset — a property a `?status=` pill would not have,
    since a status says nothing about who is holding the document.
    """
    client.force_login(users["sup"])

    held = page_records(client, f"{TRACKING}?scope=received")
    started = page_records(client, f"{TRACKING}?scope=in-process")

    assert started <= held
    assert all(
        record.status == Status.IN_PROCESS
        for record in client.get(f"{TRACKING}?scope=in-process").context["page_obj"].object_list
    )


@pytest.mark.django_db
def test_the_in_process_queue_is_office_scoped_like_its_neighbours(
    client, users, offices, memo_type, deadlines
):
    """The distinction the pill exists to preserve: everything in that status
    anywhere is a wider set than what this office is holding, and the row's
    label promises the second."""
    # A record SUP sent onward that HR is now working on. SUP can see it — it
    # raised it — but SUP is not holding it, which is the whole distinction.
    theirs = create_draft_record(
        user=users["sup"], subject="Sent on, worked on elsewhere", instructions="x",
        document_type=memo_type,
    )
    route_record(theirs, [offices["HR"]], user=users["sup"])
    confirm_receipt(theirs, user=users["hr"])
    mark_in_process(theirs, user=users["hr"])

    client.force_login(users["sup"])

    held = page_records(client, f"{TRACKING}?scope=in-process")
    anywhere = page_records(client, f"{TRACKING}?status={Status.IN_PROCESS}")

    assert theirs.pk in anywhere, "SUP can see it"
    assert theirs.pk not in held, "but SUP is not holding it"
    assert held < anywhere, "a status pill here would answer the wrong question"

"""Receipt displays distinguish confirmed offices from outstanding receipts."""

from __future__ import annotations

import re
from html import unescape

import pytest
from django.urls import reverse

from apps.tracking import services
from apps.tracking.models import COMPLETED_STATUSES, Status, TrackingRecord


@pytest.fixture
def receipt_record_factory(users, offices, memo_type):
    def make(*, received=(), sent=True, subject="Receipt display record"):
        record = services.create_draft_record(
            user=users["admin"], subject=subject, instructions="For review",
            document_type=memo_type,
        )
        if sent:
            services.route_record(record, [offices["HR"], offices["MED"]], user=users["admin"])
            for actor in received:
                services.confirm_receipt(record, user=users[actor])
        record.refresh_from_db()
        return record

    return make


def _codes(offices):
    return {office.code for office in offices}


def _receipt_cells(response):
    """Inspect only the receipt columns, excluding origin and historical badges."""
    assert response.status_code == 200
    cells = {"received": [], "pending": []}
    for attrs, body in re.findall(r"<td\b([^>]*)>(.*?)</td>", response.content.decode(), re.S):
        marker = re.search(r'''\bdata-receipt-offices=(["'])(received|pending)\1''', attrs)
        if marker is None:
            continue
        offices = {
            unescape(code).strip()
            for _quote, code in re.findall(
                r'''<span\b[^>]*\bclass=(["'])office-badge-code\1[^>]*>([^<]*)</span>''', body,
            )
        }
        text = unescape(re.sub(r"<[^>]*>", "", body)).strip()
        cells[marker[2]].append((offices, text))
    return cells


def _one_cell(cells, kind):
    assert len(cells[kind]) == 1, (kind, cells)
    return cells[kind][0]


def _page(client, surface, record):
    if surface == "tracking":
        return client.get(reverse("tracking:list"), {"office": "all"})
    if surface == "search":
        return client.get(reverse("search:index"), {
            "mode": "tracking", "office": "all", "q": record.subject,
        })
    return client.get(record.get_absolute_url(), {"office": "all"})


@pytest.mark.django_db
@pytest.mark.parametrize("surface", ["tracking", "search", "detail"])
@pytest.mark.parametrize("received, confirmed, pending", [
    ((), set(), {"HR", "MED"}),
    (("hr",), {"HR"}, {"MED"}),
    (("hr", "med"), {"HR", "MED"}, set()),
])
def test_receipt_columns_show_disjoint_current_batch_offices(
    client, users, receipt_record_factory, surface, received, confirmed, pending,
):
    record = receipt_record_factory(received=received)
    client.force_login(users["admin"])
    response = _page(client, surface, record)
    cells = _receipt_cells(response)
    confirmed_codes, _ = _one_cell(cells, "received")
    pending_codes, pending_text = _one_cell(cells, "pending")

    assert confirmed_codes == confirmed
    assert pending_codes == pending
    assert confirmed_codes.isdisjoint(pending_codes)
    assert "Received by" in response.content.decode()
    assert "Awaiting receipt" in response.content.decode()
    if not pending:
        assert pending_text, "An empty receipt column should explain that no receipt is due."


@pytest.mark.django_db
@pytest.mark.parametrize("surface", ["tracking", "search", "detail"])
def test_a_new_batch_excludes_old_confirmed_and_old_pending_recipients(
    client, users, offices, receipt_record_factory, surface,
):
    record = receipt_record_factory(received=("hr",))
    services.route_record(record, [offices["SUP"]], user=users["hr"], action="FORWARD")
    record.refresh_from_db()
    assert record.current_office_id == offices["HR"].pk, "The cached sender is not a receipt."
    client.force_login(users["admin"])

    cells = _receipt_cells(_page(client, surface, record))
    assert _one_cell(cells, "received")[0] == set()
    assert _one_cell(cells, "pending")[0] == {"SUP"}


@pytest.mark.django_db
@pytest.mark.parametrize("surface", ["tracking", "search", "detail"])
def test_drafts_show_no_confirmed_office_or_receipt_task(
    client, users, receipt_record_factory, surface,
):
    record = receipt_record_factory(sent=False)
    client.force_login(users["admin"])
    cells = _receipt_cells(_page(client, surface, record))

    assert _one_cell(cells, "received")[0] == set()
    pending_codes, pending_text = _one_cell(cells, "pending")
    assert pending_codes == set()
    assert "Not sent yet" in pending_text


@pytest.mark.django_db
@pytest.mark.parametrize("surface", ["tracking", "search", "detail"])
def test_completed_pending_filing_does_not_display_an_outstanding_receipt_task(
    client, users, receipt_record_factory, surface,
):
    record = receipt_record_factory(received=("hr",))
    services.complete_record(record, user=users["hr"])
    record.refresh_from_db()
    assert record.status == Status.COMPLETED_PENDING_UPLOAD
    assert record.current_step_queryset.filter(received_at__isnull=True).exists()
    client.force_login(users["admin"])
    cells = _receipt_cells(_page(client, surface, record))

    assert _one_cell(cells, "received")[0] == {"HR"}
    pending_codes, pending_text = _one_cell(cells, "pending")
    assert pending_codes == set()
    assert pending_text


@pytest.mark.django_db
def test_dashboard_action_queue_only_names_the_unconfirmed_recipient(
    client, users, receipt_record_factory,
):
    record = receipt_record_factory(received=("hr",))
    client.force_login(users["admin"])
    response = client.get("/", {"office": "all", "desk": "awaiting"},
                          HTTP_HX_REQUEST="true", HTTP_HX_TARGET="action-centre-queue")
    assert {item.pk for item in response.context["attention_records"]} == {record.pk}
    cells = _receipt_cells(response)

    assert _one_cell(cells, "pending")[0] == {"MED"}
    assert "Awaiting receipt" in response.content.decode()


@pytest.mark.django_db
def test_dashboard_received_queue_does_not_fall_back_to_all_destinations(
    client, users, offices, receipt_record_factory,
):
    record = receipt_record_factory(received=("hr", "med"))
    client.force_login(users["admin"])
    response = client.get("/", {"office": offices["MED"].pk, "desk": "received"},
                          HTTP_HX_REQUEST="true", HTTP_HX_TARGET="action-centre-queue")
    assert {item.pk for item in response.context["attention_records"]} == {record.pk}
    pending_codes, pending_text = _one_cell(_receipt_cells(response), "pending")

    assert pending_codes == set()
    assert pending_text


@pytest.mark.django_db
@pytest.mark.parametrize("status", sorted({Status.DRAFT, *COMPLETED_STATUSES}))
def test_grouped_display_suppresses_stale_pending_steps_for_nonactive_records(
    receipt_record_factory, status,
):
    record = receipt_record_factory(received=("hr",))
    TrackingRecord.objects.filter(pk=record.pk).update(status=status)
    record.refresh_from_db()
    assert record.current_step_queryset.filter(received_at__isnull=True).exists()

    services.annotate_receiving_offices([record])

    assert record.pending_receipt_offices_shown == []
    assert record.pending_receipt_more == 0
    assert record.receipt_wait_label
    assert _codes(record.receiving_offices_shown) == {"HR", "MED"}, "Endorsement history remains metadata."


@pytest.mark.django_db
def test_grouped_receipt_display_loads_badge_fields_in_one_query(
    receipt_record_factory, django_assert_num_queries,
):
    created = [
        receipt_record_factory(received=received, subject=f"Grouped display {index}")
        for index, received in enumerate([(), ("hr",), ("hr", "med")])
    ]
    records = list(TrackingRecord.objects.filter(pk__in=[record.pk for record in created]).order_by("pk"))

    with django_assert_num_queries(1):
        services.annotate_receiving_offices(records)
        for record in records:
            for offices in (
                record.confirmed_receipt_offices,
                record.confirmed_receipt_offices_shown,
                record.pending_receipt_offices_shown,
                record.receiving_offices_shown,
            ):
                for office in offices:
                    assert office.code and office.name and office.colour
                    assert office.badge["base"]

    assert [_codes(record.confirmed_receipt_offices) for record in records] == [
        set(), {"HR"}, {"HR", "MED"},
    ]
    assert [_codes(record.pending_receipt_offices_shown) for record in records] == [
        {"HR", "MED"}, {"MED"}, set(),
    ]
    assert all(record.confirmed_receipt_more == record.pending_receipt_more == 0 for record in records)


@pytest.mark.django_db
def test_receipt_display_reuses_loaded_steps_without_another_query(
    users, offices, receipt_record_factory, django_assert_num_queries,
):
    record = receipt_record_factory(received=("hr",))
    services.route_record(record, [offices["SUP"]], user=users["hr"], action="FORWARD")
    record.refresh_from_db()
    steps = list(record.routing_steps.select_related("to_office"))

    with django_assert_num_queries(0):
        services.annotate_receiving_offices([record], steps=steps)
        assert record.confirmed_receipt_offices == []
        assert _codes(record.pending_receipt_offices_shown) == {"SUP"}
        for office in record.pending_receipt_offices_shown:
            assert office.name and office.badge["base"]


@pytest.mark.django_db
def test_receipt_display_stays_consistent_if_a_route_changes_after_the_row_is_loaded(
    users, offices, receipt_record_factory, django_assert_num_queries,
):
    record = receipt_record_factory(received=("hr",))
    displayed_row = TrackingRecord.objects.get(pk=record.pk)
    services.route_record(record, [offices["SUP"]], user=users["hr"], action="FORWARD")
    assert record.current_batch != displayed_row.current_batch

    with django_assert_num_queries(1):
        services.annotate_receiving_offices([displayed_row])

    assert _codes(displayed_row.confirmed_receipt_offices) == {"HR"}
    assert _codes(displayed_row.awaiting_receipt_offices) == {"MED"}
    assert displayed_row.receipt_wait_label != "No endorsement recorded"

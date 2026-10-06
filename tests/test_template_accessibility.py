"""Rendered accessible names and descriptions, without database access."""

from copy import deepcopy
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest
from django import forms
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory

from apps.documents.forms import DocumentMetadataForm, UploadForm
from apps.search.forms import SearchForm, TrackingSearchForm
from apps.tracking.forms import ReviewRouteForm


class RenderedHTML(HTMLParser):
    """Keep elements and ancestry so assertions inspect rendered relationships."""

    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, body):
        super().__init__()
        self.nodes = []
        self.stack = []
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag, "attrs": dict(attrs), "text": "", "parent": self.stack[-1] if self.stack else None}
        self.nodes.append(node)
        if tag not in self.VOID_TAGS:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        for node in self.stack:
            node["text"] += data

    def by_id(self, element_id):
        matches = [node for node in self.nodes if node["attrs"].get("id") == element_id]
        assert len(matches) == 1, f"Expected one element for {element_id}, found {len(matches)}"
        return matches[0]

    def field(self, name):
        return next(node for node in self.nodes if node["tag"] in {"input", "select", "textarea"} and node["attrs"].get("name") == name)

    def descriptions(self, control):
        references = control["attrs"].get("aria-describedby", "").split()
        assert references, f"No descriptions for {control['attrs']}"
        descriptions = [self.by_id(reference) for reference in references]
        assert all(node["text"].strip() for node in descriptions)
        return descriptions

    def label(self, control):
        element_id = control["attrs"].get("id")
        return next(node for node in self.nodes if node["tag"] == "label" and node["attrs"].get("for") == element_id)


def template_form(field_definitions, *, data=None, prefix=None):
    """Use real production fields without model-form initialization queries."""
    form = forms.Form(data=data, prefix=prefix)
    form.fields = deepcopy(field_definitions)
    for field in form.fields.values():
        if isinstance(field, forms.ModelChoiceField):
            field.queryset = field.queryset.none()
    return form


def render_page(template_name, context):
    """Provide the base template's account context without authentication queries."""
    return render_to_string(template_name, {
        "user": AnonymousUser(), "request": RequestFactory().get("/"),
        "SEARCH_MIN_RELEVANCE_DEFAULT": settings.SEARCH_MIN_RELEVANCE_DEFAULT, **context,
    })


@pytest.mark.parametrize("enhanced", [False, True])
@pytest.mark.parametrize("value", [0, 317])
def test_column_tooltip_metadata_is_scoped_and_preserves_values(enhanced, value):
    """Only the opted-in dashboard chart gains interactive metadata."""
    from datetime import date

    html = render_to_string("core/_columns.html", {
        "column_hover": enhanced,
        "row": {"has_values": True, "month": date(2026, 8, 1), "columns": [
            {"label": "Handovers", "series": "handover", "value": value, "percent": 91 if value else 0},
        ]},
    })
    parsed = RenderedHTML(html)
    item = next(node for node in parsed.nodes if node["attrs"].get("class") == "column-item")
    assert item["attrs"]["title"] == f"Handovers · August 2026: {value}"
    assert item["text"] == str(value)
    assert ("data-column-hover" in item["attrs"]) is enhanced
    if enhanced:
        assert item["attrs"]["data-column-label"] == "Handovers"
        assert item["attrs"]["data-column-month"] == "August 2026"
        assert item["attrs"]["data-column-value"] == str(value)
        assert item["attrs"]["data-column-series"] == "handover"
    else:
        assert not any(name.startswith("data-column-") for name in item["attrs"])
    bars = [node for node in parsed.nodes if node["attrs"].get("class") == "column column--handover"]
    assert len(bars) == (1 if value else 0)
    if bars:
        assert bars[0]["attrs"]["style"] == "height:91%"


@pytest.mark.parametrize("selection", [{}, {"selected_statuses": ["RECEIVED"]},
    {"selected_office_ids": ["2"]}, {"overdue": "yes"}, {"overdue": "no"},
    {"selected_sort": "deadline"}, {"selected_owner": "mine"}])
def test_tracking_filter_disclosure_exposes_applied_selections_and_keeps_office_view_visible(selection):
    form = template_form({"owner": forms.ChoiceField(choices=[("", "All I can see"), ("mine", "Files created by me only")])})
    body = render_page("tracking/list.html", {
        "form": form, "can_pick_office": True,
        "resolved": SimpleNamespace(all_offices=True, overdue=selection.get("overdue", "")),
        "filter_offices": [SimpleNamespace(pk=2, code="MED", name="Maintenance and Engineering Department")],
        "status_choices": [("RECEIVED", "Received")], "sort_choices": [("", "Recently updated")],
        **{name: value for name, value in selection.items() if name != "overdue"},
    })
    html = RenderedHTML(body)
    disclosure = html.by_id("tracking-filter-options")
    assert disclosure["tag"] == "details"
    assert ("open" in disclosure["attrs"]) == bool(selection)
    summary = next(node for node in html.nodes if node["tag"] == "summary" and node["parent"] is disclosure)
    assert "Filter records" in summary["text"]
    assert "Stage" in summary["text"]
    assert ("Filters applied" in summary["text"]) == bool(selection)
    office = html.by_id("tracking-as-office")
    ancestor = office["parent"]
    while ancestor:
        assert ancestor["tag"] != "details", "Office scope must be visible before expanding filters"
        ancestor = ancestor["parent"]
    stage_group = next(node for node in html.nodes if node["attrs"].get("aria-labelledby") == "filter-status-label")
    ancestor = stage_group["parent"]
    while ancestor and ancestor is not disclosure:
        ancestor = ancestor["parent"]
    assert ancestor is disclosure
    if selection.get("selected_statuses"):
        selected = next(node for node in html.nodes if node["parent"] is stage_group and node["tag"] == "a")
        assert selected["attrs"].get("aria-current") == "true"


@pytest.mark.parametrize("invalid", [False, True])
@pytest.mark.parametrize("prefix", [None, "profile"])
def test_shared_fields_resolve_help_and_errors_for_assistive_technology(invalid, prefix):
    form = template_form(
        {"name": forms.CharField(label="Name", help_text="Use the document name.", max_length=5)},
        data={f"{prefix}-name" if prefix else "name": "Too long"} if invalid else None,
        prefix=prefix,
    )
    html = RenderedHTML(render_to_string("partials/_field.html", {"field": form["name"]}))
    control = html.field(f"{prefix}-name" if prefix else "name")
    assert html.label(control)["text"].strip()
    descriptions = html.descriptions(control)
    assert any("Use the document name." in node["text"] for node in descriptions)
    if invalid:
        assert control["attrs"].get("aria-invalid") == "true"
        assert any(node["attrs"].get("role") == "alert" for node in descriptions)


def test_error_summary_links_prefixed_field_errors_and_explains_non_field_errors():
    form = template_form({"name": forms.CharField(label="Name", max_length=5)}, data={"edit-name": "Too long"}, prefix="edit")
    form.add_error(None, "The selected combination cannot be saved.")
    body = render_to_string("partials/_form_errors.html", {"form": form})
    body += render_to_string("partials/_field.html", {"field": form["name"]})
    html = RenderedHTML(body)
    headings = [node for node in html.nodes if node["tag"] in {"h2", "h3"}]
    assert len(headings) == 1
    summary = next(node for node in html.nodes if "data-error-summary" in node["attrs"])
    assert summary["attrs"]["aria-labelledby"] == headings[0]["attrs"]["id"]
    assert headings[0]["text"].strip(), "The summary must have an accessible heading"
    links = [node for node in html.nodes if node["tag"] == "a" and node["attrs"].get("href", "").startswith("#")]
    assert len(links) == 1
    target = html.by_id(links[0]["attrs"]["href"][1:])
    assert target["attrs"].get("name") == "edit-name"
    assert "Name" in links[0]["text"]
    assert "The selected combination cannot be saved." in body


@pytest.mark.parametrize("data", [None, {"name": "Valid"}])
def test_forms_without_errors_do_not_render_an_error_summary(data):
    form = template_form({"name": forms.CharField(label="Name", max_length=5)}, data=data)
    html = RenderedHTML(render_to_string("partials/_form_errors.html", {"form": form}))
    assert not [node for node in html.nodes if node["attrs"].get("role") == "alert"]
    assert not [node for node in html.nodes if node["tag"] in {"h2", "h3", "a"}]


@pytest.mark.parametrize("invalid", [False, True])
def test_deadline_date_and_time_have_their_own_help_and_error_descriptions(invalid):
    fields = {name: ReviewRouteForm.base_fields[name] for name in ("deadline_choice", "due_date", "due_time")}
    form = template_form(fields, data={"route-deadline_choice": "date", "route-due_date": "bad date", "route-due_time": "bad time"} if invalid else None, prefix="route")
    html = RenderedHTML(render_to_string("tracking/_deadline_field.html", {"form": form}))
    descriptions = []
    for name in ("route-due_date", "route-due_time"):
        control = html.field(name)
        assert html.label(control)["text"].strip()
        described = html.descriptions(control)
        descriptions.append({node["attrs"]["id"] for node in described})
        if invalid:
            assert control["attrs"].get("aria-invalid") == "true"
            assert any(node["attrs"].get("role") == "alert" for node in described)
    assert descriptions[0].isdisjoint(descriptions[1]), "Date and time must describe their own help and errors"


@pytest.mark.parametrize("tracking, form_type, corpus", [(False, SearchForm, "repository"), (True, TrackingSearchForm, "tracking")])
def test_search_input_has_a_persistent_mode_appropriate_label(tracking, form_type, corpus):
    form = template_form({"q": form_type.base_fields["q"]})
    html = RenderedHTML(render_page("search/search.html", {
        "form": form, "is_tracking": tracking, "request": RequestFactory().get("/search/"),
        "repository_mode": "repository", "tracking_mode": "tracking",
    }))
    label = html.label(html.by_id("id_q"))
    assert corpus in label["text"].lower()
    assert "hidden" not in label["attrs"]


def test_metadata_review_exposes_errors_on_each_manually_rendered_field():
    fields = {name: DocumentMetadataForm.base_fields[name] for name in ("title", "office", "document_type", "tags")}
    fields["tags"] = forms.CharField(label="Tags", help_text="Separate tags with commas.", max_length=2)
    form = template_form(fields, data={"title": "", "office": "999", "document_type": "999", "tags": "long"})
    html = RenderedHTML(render_page("documents/review.html", {
        "form": form, "document": SimpleNamespace(ocr_status="EMPTY"), "confidence": {}, "suggestion": {},
    }))
    for name in ("title", "office", "document_type", "tags"):
        control = html.field(name)
        assert html.label(control)["text"].strip()
        assert control["attrs"].get("aria-invalid") == "true"
        assert any(node["attrs"].get("role") == "alert" for node in html.descriptions(control))


def test_upload_file_exposes_its_help_and_required_file_error():
    form = template_form({"file": UploadForm.base_fields["file"]}, data={})
    html = RenderedHTML(render_page("documents/upload.html", {"form": form}))
    control = html.field("file")
    label = html.label(control)
    assert label["text"].strip()
    assert "required" in control["attrs"]
    assert control["attrs"].get("aria-invalid") == "true"
    descriptions = html.descriptions(control)
    assert any(node["attrs"].get("role") == "alert" for node in descriptions)
    markers = [node for node in html.nodes if node["parent"] is label and "required-mark" in node["attrs"].get("class", "").split()]
    assert len(markers) == 1
    assert markers[0]["attrs"].get("aria-hidden") == "true"


@pytest.mark.parametrize("active_filter", ["all", "unread"])
def test_notification_filters_announce_only_the_selected_filter(active_filter):
    html = RenderedHTML(render_page("core/notifications.html", {"active_filter": active_filter, "page_size": 25}))
    filters = [node for node in html.nodes if node["tag"] == "a" and "notification-filter" in node["attrs"].get("class", "").split()]
    current = [node for node in filters if node["attrs"].get("aria-current") in {"true", "page"}]
    assert len(current) == 1
    assert current[0]["text"].strip().lower() == active_filter


@pytest.mark.parametrize("can_receive, column_count", [(False, 2), (True, 3)])
def test_compact_dashboard_queues_have_named_column_headers(can_receive, column_count):
    record = SimpleNamespace(pk=1, display_tracking_number="UDM-OVPA-MED-2026-10-0001", subject="Inspection request", originating_office=SimpleNamespace(code="MED"), status="PENDING_RECEIPT", can_confirm_now=can_receive, get_absolute_url="/tracking/1/")
    html = RenderedHTML(render_to_string("core/_action_centre_queue.html", {
        "desk_target": "action-centre-queue", "show_office_columns": False, "can_bulk_receive": can_receive,
        "attention_records": [record], "desk_queue": {"label": "Pending Receipt", "count": 1, "slug": "pending-receipt", "tracking_url": "/tracking/?scope=pending-receipt"},
    }))
    headers = [node for node in html.nodes if node["tag"] == "th"]
    cells = [node for node in html.nodes if node["tag"] == "td"]
    assert len(headers) == len(cells) == column_count
    assert all(node["attrs"].get("scope") == "col" and node["text"].strip() for node in headers)
    assert all(node["parent"]["parent"]["tag"] == "thead" for node in headers)
    regions = [node for node in html.nodes if node["attrs"].get("role") == "region"]
    assert len(regions) == 1
    assert regions[0]["attrs"].get("tabindex") == "0"
    assert regions[0]["attrs"].get("aria-label") == "Pending Receipt documents"


def test_records_staff_dashboard_groups_document_identity_to_fit_split_panel():
    record = SimpleNamespace(pk=1, display_tracking_number="UDM-OVPA-MED-2026-10-0001", subject="Inspection request", originating_office=SimpleNamespace(code="MED"), pending_offices_shown=[], status="PENDING_RECEIPT", can_confirm_now=False, get_absolute_url="/tracking/1/")
    html = RenderedHTML(render_to_string("core/_action_centre_queue.html", {
        "desk_target": "action-centre-queue", "show_office_columns": True,
        "attention_records": [record], "desk_queue": {"label": "Pending Receipt", "count": 1, "slug": "pending-receipt"},
    }))
    headers = [node for node in html.nodes if node["tag"] == "th"]
    cells = [node for node in html.nodes if node["tag"] == "td"]
    assert len(headers) == len(cells) == 4
    assert headers[0]["text"].strip() == "Document"
    identity = cells[0]
    assert any(node["tag"] == "a" and node["parent"] is identity and node["attrs"].get("href") == "/tracking/1/" for node in html.nodes)
    assert any(node["parent"] is identity and node["text"] == "Inspection request" for node in html.nodes)

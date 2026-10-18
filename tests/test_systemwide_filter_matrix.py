"""Explicit expected populations across roles, collections and filter surfaces.

Expected sets below describe the fixture facts. They never reuse a visibility,
scope, ranking or filter implementation to calculate the answer.
"""

import csv
import io
from datetime import date
from urllib.parse import urlencode

import pytest

from apps.core.models import DocumentType
from apps.documents.models import AccessLevel, Document, Source
from apps.search.services import search_documents
from apps.tracking.services import add_remark, confirm_receipt, create_draft_record, route_record
from scripts.audit_counts import Links
from tests.test_tracking_export_agreement import export_records as export_records  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def mixed_repository(users, offices, memo_type, tag_urgent):
    made = {}
    rows = [
        ("med_private", "MED", AccessLevel.OFFICE, Source.UPLOAD, 2025),
        ("med_shared", "MED", AccessLevel.OVPA, Source.SCAN, 2026),
        ("sup_shared", "SUP", AccessLevel.OVPA, Source.UPLOAD, 2026),
        ("hr_private", "HR", AccessLevel.OFFICE, Source.UPLOAD, 2026),
        ("restricted", "MED", AccessLevel.RESTRICTED, Source.UPLOAD, 2026),
    ]
    for key, office, access, source, year in rows:
        for collection in ("OVPA", "EXTERNAL"):
            document = Document.objects.create(
                title=f"Policy needle {key}", office=offices[office], collection=collection,
                access_level=access, source=source, year=year,
                document_date=date(year, 2, 3), document_type=memo_type,
                uploaded_by=users["admin"], ocr_status="SKIPPED",
            )
            document.rebuild_index()
            made[key if collection == "OVPA" else "external_" + key] = document
    made["med_shared"].tags.add(tag_urgent)
    made["sup_shared"].tags.add(tag_urgent)
    Document.objects.create(title="Inactive", office=offices["MED"], year=2026, is_active=False)
    return made


DOCUMENT_VISIBLE = {
    "admin": {"med_private", "med_shared", "sup_shared", "hr_private", "restricted"},
    "med_admin": {"med_private", "med_shared"},
    "med": {"med_private", "med_shared", "sup_shared"},
    "viewer": {"med_private", "med_shared", "sup_shared"},
}


def document_ids(response, search=False):
    assert response.status_code == 200
    if search:
        return {row.document.pk for row in response.context["results"]}
    return {row.pk for row in response.context["documents"]}


@pytest.mark.parametrize("actor", DOCUMENT_VISIBLE)
@pytest.mark.parametrize("path", ["/documents/", "/search/"])
@pytest.mark.parametrize("params, candidates", [
    ({}, set.union(*DOCUMENT_VISIBLE.values())),
    ({"year": "2025"}, {"med_private"}),
    ({"source": "SCAN"}, {"med_shared"}),
    ({"office": "MED"}, {"med_private", "med_shared", "restricted"}),
    ({"q": "sup_shared", "min_relevance": "0"}, {"sup_shared"}),
    ({"year": "2026", "source": "UPLOAD"}, {"sup_shared", "hr_private", "restricted"}),
])
def test_document_filter_matrix_is_exact(
    client, users, mixed_repository, actor, path, params, candidates,
):
    client.force_login(users[actor])
    response = client.get(path, {**params, "per_page": "all"})
    expected = {mixed_repository[key].pk for key in DOCUMENT_VISIBLE[actor] & candidates}
    if path == "/search/" and not params:
        # The search landing page waits for a query or a real corpus filter.
        assert response.context["response"] is None
        assert not response.context["has_searched"]
        assert document_ids(response, search=True) == set()
        return
    assert document_ids(response, search=path == "/search/") == expected
    if path == "/documents/":
        assert response.context["total"] == len(expected)
    else:
        assert response.context["response"].total_matches == len(expected)


@pytest.mark.parametrize("actor", DOCUMENT_VISIBLE)
@pytest.mark.parametrize("path", ["/documents/", "/search/"])
def test_repeated_scalar_document_filter_uses_last_value(client, users, mixed_repository, actor, path):
    client.force_login(users[actor])
    response = client.get(path + "?year=2025&year=2026&per_page=all")
    keys = DOCUMENT_VISIBLE[actor] - {"med_private"}
    assert document_ids(response, search=path == "/search/") == {mixed_repository[key].pk for key in keys}


@pytest.mark.parametrize("bad", ["unknown", "²", "9" * 65])
@pytest.mark.parametrize("actor", DOCUMENT_VISIBLE)
def test_invalid_document_filters_preserve_endpoint_contract(client, users, mixed_repository, actor, bad):
    client.force_login(users[actor])
    repository = client.get("/documents/", {"year": bad, "per_page": "all"})
    assert document_ids(repository) == {mixed_repository[key].pk for key in DOCUMENT_VISIBLE[actor]}
    search = client.get("/search/", {"year": bad})
    assert search.context["form"].errors
    assert document_ids(search, search=True) == set()


@pytest.mark.parametrize("actor", DOCUMENT_VISIBLE)
def test_external_repository_has_its_own_exact_population(client, users, mixed_repository, actor):
    client.force_login(users[actor])
    response = client.get("/documents/external/", {"per_page": "all"})
    assert document_ids(response) == {
        mixed_repository["external_" + key].pk for key in DOCUMENT_VISIBLE[actor]
    }


@pytest.mark.parametrize("actor", DOCUMENT_VISIBLE)
def test_foreign_office_selection_preserves_repository_and_search_contracts(
    client, users, mixed_repository, actor,
):
    client.force_login(users[actor])
    repository = client.get("/documents/?office=SUP&per_page=all")
    expected = {"med_private", "med_shared"} if actor == "med_admin" else {"sup_shared"}
    assert document_ids(repository) == {mixed_repository[key].pk for key in expected}
    search = client.get("/search/?office=SUP&per_page=all")
    if actor == "med_admin":
        assert search.context["form"].errors
        assert document_ids(search, search=True) == set()
    else:
        assert document_ids(search, search=True) == {mixed_repository["sup_shared"].pk}


def test_type_facets_overlap_but_primary_report_partition_does_not(
    client, users, mixed_repository, memo_type,
):
    other = DocumentType.objects.create(code="LETTER", name="Letter")
    mixed_repository["med_shared"].document_types.add(memo_type, other)
    client.force_login(users["admin"])
    response = client.get("/documents/?per_page=all")
    facets = response.context["type_folders"]
    expected_facets = {
        other.pk: {mixed_repository["med_shared"].pk},
        memo_type.pk: {mixed_repository[key].pk for key in DOCUMENT_VISIBLE["admin"]},
    }
    assert {folder["document_type_id"] for folder in facets} == set(expected_facets)
    for folder in facets:
        expected = expected_facets[folder["document_type_id"]]
        assert folder["total"] == len(expected)
        assert document_ids(client.get(folder["url"] + "&per_page=all")) == expected
    assert sum(folder["total"] for folder in facets) == 6  # one document belongs to both facets
    report = client.get("/documents/reports/").context
    assert report["total_documents"] == 5
    assert sum(row["total"] for row in report["document_types"]) == 5
    assert {row["document_type_id"] for row in report["document_types"]} == {memo_type.pk}
    for row in report["document_types"]:
        assert row["workspace_url"]
        assert document_ids(client.get(row["workspace_url"] + "&per_page=all")) == {
            mixed_repository[key].pk for key in DOCUMENT_VISIBLE["admin"]
        }


ACTIVE = {"draft-med", "draft-sup", "pending", "partial", "received", "processing", "passed", "pending-filing", "foreign"}
MED = ACTIVE - {"draft-sup", "foreign"}
TRACKING_CASES = [
    ("admin", {}, ACTIVE),
    ("med", {}, MED),
    ("med_admin", {}, MED - {"draft-med"}),
    ("viewer", {}, MED - {"draft-med"}),
    ("admin", {"office": "SUP", "scope": "incoming"}, {"pending", "partial", "received", "processing"}),
    ("admin", {"office": "SUP", "status": ["RECEIVED", "IN_PROCESS"]}, {"partial", "received", "processing"}),
    ("admin", {"office": "all", "scope": "incoming"}, ACTIVE),
    # Across all offices the partial record still awaits HR; SUP's perspective
    # above correctly regards that same record as received locally.
    ("admin", {"status": ["RECEIVED", "bad", "IN_PROCESS"]}, {"received", "processing"}),
    ("admin", {"office": "unknown", "status": "unknown", "scope": "unknown"}, ACTIVE),
    ("med", {"office": "HR", "q": "pending"}, {"pending", "pending-filing"}),
    ("admin", {"offices": ["MED", "bad"], "overdue": "yes"}, {"pending", "passed"}),
]


def csv_subjects(response):
    assert response.status_code == 200
    rows = list(csv.reader(io.StringIO(response.content.decode())))
    header = next(i for i, row in enumerate(rows) if row and row[0] == "Tracking number")
    return [row[1] for row in rows[header + 1:]]


@pytest.mark.parametrize("path", ["/tracking/", "/search/?mode=tracking"])
@pytest.mark.parametrize("actor, params, expected", TRACKING_CASES)
def test_tracking_filter_expected_ids_and_export(
    client, users, export_records, path, actor, params, expected,
):
    client.force_login(users[actor])
    join = "&" if "?" in path else "?"
    response = client.get(path + join + urlencode({**params, "per_page": "all"}, doseq=True))
    assert response.status_code == 200
    assert {row.pk for row in response.context["page_obj"]} == {export_records[key].pk for key in expected}
    assert response.context["total"] == len(expected)
    if path == "/tracking/":
        link = next(link["href"] for link in Links(response.content.decode()).links if link["text"].strip() == "Export CSV")
        subjects = csv_subjects(client.get(link))
        assert len(subjects) == len(expected)
        assert set(subjects) == expected


def test_tracking_pages_and_export_cover_same_explicit_draft_ids(client, users, export_records):
    extra = {create_draft_record(user=users["med"], subject=f"Extra draft {n}", instructions="Review").pk for n in range(13)}
    expected = extra | {export_records["draft-med"].pk, export_records["draft-sup"].pk}
    client.force_login(users["admin"])
    first = client.get("/tracking/?status=DRAFT&per_page=10")
    second = client.get("/tracking/?status=DRAFT&per_page=10&page=2")
    first_ids = {row.pk for row in first.context["records"]}
    second_ids = {row.pk for row in second.context["records"]}
    assert not first_ids & second_ids
    assert first_ids | second_ids == expected
    assert first.context["total"] == 15
    link = next(link["href"] for link in Links(second.content.decode()).links if link["text"].strip() == "Export CSV")
    subjects = csv_subjects(client.get(link))
    assert set(subjects) == {"draft-med", "draft-sup", *(f"Extra draft {n}" for n in range(13))}
    assert len(subjects) == 15


@pytest.mark.parametrize("actor", ["admin", "med_admin", "med", "viewer"])
def test_dashboard_direction_counts_and_drilldowns_use_expected_ids(
    client, users, offices, export_records, actor,
):
    client.force_login(users[actor])
    query = {"office": offices["SUP"].pk} if actor == "admin" else {}
    context = client.get("/", query).context
    expected = {
        "incoming": {"pending", "partial", "received", "processing"} if actor == "admin" else set(),
        "outgoing": {"passed", "foreign"} if actor == "admin" else {"pending", "partial", "received", "processing"},
    }
    assert {group["key"] for group in context["desk_direction_groups"]} == set(expected)
    for group in context["desk_direction_groups"]:
        expected_ids = {export_records[key].pk for key in expected[group["key"]]}
        assert group["count"] == len(expected_ids)
        listing = client.get(group["tracking_url"] + "&per_page=all")
        assert {row.pk for row in listing.context["records"]} == expected_ids
        stages = []
        for stage in group["stages"]:
            page = client.get(stage["tracking_url"])
            ids = {row.pk for row in page.context["records"]}
            assert stage["count"] == len(ids)
            stages.append(ids)
        assert set().union(*stages) == expected_ids
        assert sum(map(len, stages)) == len(expected_ids)


def test_ranked_search_cap_and_exhaustive_repository_have_distinct_contracts(
    client, users, mixed_repository, settings,
):
    settings.SEARCH_RESULT_LIMIT = 2
    expected = {mixed_repository[key].pk for key in DOCUMENT_VISIBLE["admin"]}
    result = search_documents(user=users["admin"], query="Policy needle", min_relevance=0, limit=2, log=False)
    assert result.total_matches == 5
    assert len(result.results) == 2
    assert result.evaluated_count == 4
    assert result.truncated and result.limited_count == 2
    assert {row.document.pk for row in result.results} <= expected
    client.force_login(users["admin"])
    repository = client.get("/documents/?q=Policy+needle&min_relevance=0&per_page=all")
    assert document_ids(repository) == expected


@pytest.fixture
def populated_direction_stages(users, offices):
    rows = {}
    for direction, sender, receiver in (("incoming", "sup", "med"), ("outgoing", "med", "sup")):
        for stage in ("PENDING_RECEIPT", "RECEIVED", "IN_PROCESS"):
            record = create_draft_record(
                user=users[sender], subject=f"{direction} {stage}", instructions="Review",
            )
            route_record(record, [users[receiver].office], user=users[sender])
            if stage != "PENDING_RECEIPT":
                confirm_receipt(record, user=users[receiver])
            if stage == "IN_PROCESS":
                add_remark(record, user=users[receiver], remark="Work has started")
            rows[direction, stage] = record.pk
    return rows


@pytest.mark.parametrize("actor", ["admin", "med_admin", "med", "viewer"])
@pytest.mark.parametrize("direction", ["incoming", "outgoing"])
@pytest.mark.parametrize("stage", ["PENDING_RECEIPT", "RECEIVED", "IN_PROCESS"])
def test_populated_direction_stage_pairs_match_for_every_role(
    client, users, offices, populated_direction_stages, actor, direction, stage,
):
    client.force_login(users[actor])
    params = {"office": offices["MED"].pk, "scope": direction, "status": stage}
    expected = {populated_direction_stages[direction, stage]}
    for path in ("/tracking/", "/search/?mode=tracking"):
        separator = "&" if "?" in path else "?"
        response = client.get(path + separator + urlencode(params))
        assert response.status_code == 200
        assert {record.pk for record in response.context["page_obj"]} == expected
        assert response.context["total"] == 1

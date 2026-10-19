"""Exercise the diagnostic scripts against deliberately mixed, disposable data."""

import pytest

from apps.core.models import Tag
from apps.documents.models import AccessLevel, Document
from apps.tracking.models import Status, TrackingRecord
from apps.tracking.services import create_draft_record
from scripts.audit_counts import Audit
from scripts.audit_filters import FilterAudit
from tests.test_tracking_export_agreement import export_records as export_records  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def diagnostic_documents(users, offices, memo_type, tag_urgent):
    made = {}
    for key, collection, code in (("ordinary", "OVPA", "MED"), ("external", "EXTERNAL", "MED"),
                                  ("retired", "OVPA", "HR")):
        made[key] = Document.objects.create(
            title=key, collection=collection, office=offices[code], year=2026,
            document_type=memo_type, access_level=AccessLevel.OVPA,
            uploaded_by=users["admin"], ocr_status="SKIPPED",
        )
        made[key].tags.add(tag_urgent)
    offices["HR"].is_active = False
    offices["HR"].save(update_fields=["is_active"])
    return made


def test_count_diagnostic_population_is_legacy_collection(users, diagnostic_documents):
    class PopulationInspected(Exception):
        pass

    class PopulationAudit(Audit):
        def report(self, url, records, documents, steps):
            assert {row["pk"] for row in documents} == {
                diagnostic_documents["ordinary"].pk, diagnostic_documents["retired"].pk,
            }
            raise PopulationInspected

    # Ensure the first selected role is global; the population assertion must
    # include the retired-office document as well as reject the external one.
    type(users["admin"]).objects.exclude(pk=users["admin"].pk).update(is_active=False)
    with pytest.raises(PopulationInspected):
        PopulationAudit().run(quick=True)


def test_filter_diagnostic_accepts_collection_isolation_and_retired_office_choices(
    users, diagnostic_documents, settings,
):
    settings.SECURE_SSL_REDIRECT = True
    type(users["admin"]).objects.exclude(pk=users["admin"].pk).update(is_active=False)
    audit = FilterAudit()
    audit.run()
    assert audit.roles == ["SYSTEM_ADMIN"]
    assert not audit.failures


def test_repository_report_diagnostic_handles_unclassified_without_drilldown(client, users, offices):
    document = Document.objects.create(title="Unclassified", office=offices["MED"], year=2026)
    client.force_login(users["admin"])
    audit = Audit()
    audit.client, audit.user = client, users["admin"]
    rows = list(Document.objects.filter(pk=document.pk).values("pk", "office_id", "source", "created_at"))
    audit.report("/documents/reports/?office=all", [], rows, [])
    assert not audit.failures


def test_count_diagnostic_compares_displayed_drafts_and_ovpa_tag_usage(
    users, diagnostic_documents,
):
    create_draft_record(user=users["admin"], subject="Still a draft", instructions="Review")
    type(users["admin"]).objects.exclude(pk=users["admin"].pk).update(is_active=False)

    class FocusedAudit(Audit):
        # The independent population and report tests above cover these parts.
        # Keep this regression focused on the real CSV/search/admin endpoints.
        def report(self, *args):
            pass

        def dashboard(self, *args):
            pass

        def repository(self, *args):
            pass

    audit = FocusedAudit()
    audit.run(quick=True)
    assert not audit.failures


def test_full_diagnostics_reconcile_all_roles_with_mixed_collections(
    users, export_records, request, settings,
):
    settings.SECURE_SSL_REDIRECT = True
    # Retire HR only after the real routing fixture has completed. This explicit
    # sequencing avoids relying on the relative order of independent fixtures.
    request.getfixturevalue("diagnostic_documents")
    counts = Audit()
    counts.run(quick=True)
    assert not counts.failures
    assert {case.split(":", 1)[0] for case in counts.cases} == {
        "SYSTEM_ADMIN", "ADMIN", "USER", "VIEWER",
    }
    filters = FilterAudit()
    filters.run()
    assert set(filters.roles) == {"SYSTEM_ADMIN", "ADMIN", "USER", "VIEWER"}
    assert not filters.failures


def test_filter_diagnostic_never_samples_an_inactive_tag(client, users, offices):
    tagged = Document.objects.create(title="Inactive tag", office=offices["MED"], year=2026)
    Document.objects.create(title="No tag", office=offices["MED"], year=2026)
    tagged.tags.add(Tag.objects.create(name="retired-tag", is_active=False))
    client.force_login(users["admin"])
    audit = FilterAudit()
    audit.user, audit.client = users["admin"], client
    audit.repository(Document.objects.filter(collection="OVPA", is_active=True))
    assert not audit.failures


@pytest.mark.parametrize("population", ["filed", "pending"])
def test_filter_diagnostic_walks_every_page_past_show_all_cap(client, users, offices, population):
    if population == "filed":
        Document.objects.bulk_create([
            Document(title=f"Filed {n}", office=offices["MED"], year=2026)
            for n in range(501)
        ])
    else:
        TrackingRecord.objects.bulk_create([
            TrackingRecord(
                subject=f"Pending {n}", tracking_number=f"AUDIT-PENDING-{n}",
                originating_office=offices["MED"], current_office=offices["MED"],
                created_by=users["med"], status=Status.COMPLETED_PENDING_UPLOAD,
            )
            for n in range(501)
        ])
    client.force_login(users["admin"])
    audit = FilterAudit()
    audit.user, audit.client = users["admin"], client
    audit.repository(Document.objects.filter(collection="OVPA", is_active=True))
    assert not audit.failures

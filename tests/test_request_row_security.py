"""Verify PostgreSQL itself restricts unfiltered queries from the web role."""

from contextlib import contextmanager
from uuid import uuid4

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from psycopg import sql

from apps.accounts.permission_models import CapabilityRestriction, UserPermissionPolicy
from apps.core.database_access import request_scope
from apps.core.row_policies import install_policies
from apps.documents.models import Document, DocumentAccessGrant
from apps.tracking import external_services, services
from apps.tracking.models import ExternalTrackingRecord, RecordAccessGrant, TrackingRecord

pytestmark = pytest.mark.django_db


@pytest.fixture
def web(settings):
    # Both the role and its policy changes roll back with the test transaction.
    # SET LOCAL SESSION AUTHORIZATION also changes session_user, as a real login would.
    role = "doctrack_web_" + uuid4().hex[:12]
    with connection.cursor() as cur:
        cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname=current_user")
        if not cur.fetchone()[0]:
            pytest.skip("Role installation test requires a disposable PostgreSQL superuser")
        cur.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role)))
    install_policies(connection, role)
    settings.DOCTRACK_WEB_ROLE = role

    @contextmanager
    def login():
        with connection.cursor() as cur:
            cur.execute(sql.SQL("SET LOCAL SESSION AUTHORIZATION {}").format(sql.Identifier(role)))
            try:
                yield
            finally:
                cur.execute("RESET SESSION AUTHORIZATION")

    return login


def test_scope_rejects_the_migration_login(web, users):
    with pytest.raises(ImproperlyConfigured), request_scope(users["admin"].pk):
        pytest.fail("Owner connection escaped the runtime check")


def test_unfiltered_queries_match_roles_shares_and_revocation(web, users, offices):
    med, sup, viewer = (users[name] for name in ("med", "sup", "viewer"))
    own = services.create_draft_record(user=med, subject="private", instructions="")
    slip = services.create_draft_record(user=sup, subject="shared", instructions="")
    services.route_record(slip, [offices["HR"]], user=sup)
    grant = RecordAccessGrant.objects.create(record=slip, user=viewer, granted_by=sup)
    external = external_services.create_record(user=users["sup_admin"], title="Private external")
    doc = Document.objects.create(
        title="Shared original", uploaded_by=sup, office=sup.office, access_level="RESTRICTED"
    )
    DocumentAccessGrant.objects.create(document=doc, user=viewer, granted_by=sup)
    models = (TrackingRecord, ExternalTrackingRecord, Document)
    expected = {
        u.pk: [set(m.objects.visible_to(u).values_list("pk", flat=True)) for m in models] for u in users.values()
    }
    with web():

        def ids():
            return [set(m.objects.values_list("pk", flat=True)) for m in models]

        with request_scope(0):
            assert ids() == [set(), set(), set()]
        for user in users.values():
            with request_scope(user.pk):
                assert ids() == expected[user.pk]
        with request_scope(viewer.pk):
            assert TrackingRecord.objects.filter(pk=slip.pk).update(subject="denied") == 0
            assert Document.objects.filter(pk=doc.pk).update(title="denied") == 0
            assert not ExternalTrackingRecord.objects.filter(pk=external.pk).exists()
            assert not TrackingRecord.objects.filter(pk=own.pk).exists()
    grant.delete()
    policy = UserPermissionPolicy.objects.create(user=viewer)
    CapabilityRestriction.objects.create(policy=policy, area="repository", action="view_record")
    with web(), request_scope(viewer.pk):
        assert not TrackingRecord.objects.filter(pk=slip.pk).exists()
        assert not Document.objects.filter(pk=doc.pk).exists()


def test_routing_custody_and_filing_under_restricted_login(web, users, offices):
    med, sup = users["med"], users["sup"]
    with web():
        with request_scope(med.pk):
            record = services.create_draft_record(user=med, subject="Internal routing", instructions="")
            services.route_record(record, [offices["SUP"]], user=med)
        with request_scope(sup.pk):
            services.confirm_receipt(record, user=sup)
            services.add_remark(record, user=sup, remark="Checked")
            services.complete_record(record, user=sup, note="Ready")
        with request_scope(users["sup_admin"].pk):
            doc = services.approve_upload(record, user=users["sup_admin"])
            assert doc.office_id == offices["MED"].pk
            assert not Document.objects.filter(pk=doc.pk).exists()
        with request_scope(users["med_admin"].pk):
            assert Document.objects.filter(pk=doc.pk).exists()


def test_external_handoff_under_restricted_login(web, users, offices):
    admin = users["med_admin"]
    with web():
        with request_scope(admin.pk):
            counterpart = external_services.create_external_office(user=admin, name="Library")
            record = external_services.create_record(user=admin, title="External", from_external_office=counterpart)
            external_services.route_record(
                record, user=admin, to_office=offices["SUP"], expected_revision=record.revision
            )
        with request_scope(users["sup"].pk):
            external_services.confirm_receipt(record, user=users["sup"], expected_revision=record.revision)
            external_services.route_record(
                record, user=users["sup"], to_external_office=counterpart, expected_revision=record.revision
            )
        new_admin = users["sup_admin"]
        with request_scope(new_admin.pk):
            external_services.record_external_receipt(record, user=new_admin, expected_revision=record.revision)
            assert record.responsible_office_id == offices["SUP"].pk


def test_file_history_content_restriction_preserves_metadata(web, users):
    from apps.documents.models import DocumentFile, DocumentFileVersion

    actor = users["med"]
    doc = Document.objects.create(title="Approved", uploaded_by=actor, office=actor.office)
    item = DocumentFile.objects.create(document=doc, file="isolated/example.txt", original_name="example.txt")
    with web(), request_scope(actor.pk):
        assert DocumentFileVersion.objects.filter(document_file_id=item.pk).count() == 1
    policy = UserPermissionPolicy.objects.create(user=actor)
    CapabilityRestriction.objects.create(policy=policy, area="repository", action="view_content")
    with web(), request_scope(actor.pk):
        assert Document.objects.filter(pk=doc.pk).exists()
        assert not DocumentFile.objects.filter(pk=item.pk).exists()
        assert not DocumentFileVersion.objects.filter(document_file_id=item.pk).exists()


def test_cleanup_cannot_mistake_invisible_references_for_unused_bytes(web, users):
    from django.core.exceptions import ValidationError

    from apps.core.storage_references import referenced_storage_key

    with web(), request_scope(users["admin"].pk), pytest.raises(ValidationError, match="maintenance"):
        referenced_storage_key("test-storage", "isolated/example.txt")


def test_api_grants_removed_from_table_columns_and_owned_sequence(users):
    from apps.core.public_database import close_api_grants

    with connection.cursor() as cur:
        cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname=current_user")
        if not cur.fetchone()[0]:
            pytest.skip("Public grant fixture requires a disposable PostgreSQL superuser")
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname='anon'")
        if not cur.fetchone():
            cur.execute("CREATE ROLE anon")
        cur.execute("GRANT SELECT, UPDATE(subject) ON public.tracking_trackingrecord TO anon")
        cur.execute("SELECT pg_get_serial_sequence('public.tracking_trackingrecord','id')")
        seq = cur.fetchone()[0]
        cur.execute(sql.SQL("GRANT USAGE ON SEQUENCE {} TO anon").format(sql.Identifier(*seq.split("."))))
        close_api_grants(connection)
        cur.execute(
            "SELECT has_table_privilege('anon','tracking_trackingrecord','SELECT'), has_column_privilege('anon','tracking_trackingrecord','subject','UPDATE'), has_sequence_privilege('anon',%s,'USAGE')",
            [seq],
        )
        assert cur.fetchone() == (False, False, False)


def test_row_access_rechecked_on_each_http_request(web, client, users):
    from django.urls import reverse

    owner = users["sup"]
    record = services.create_draft_record(user=owner, subject="Restricted test route", instructions="")
    client.force_verified_login(owner)
    with web():
        assert client.get(record.get_absolute_url()).status_code == 200
    client.force_verified_login(users["med"])
    with web():
        assert client.get(record.get_absolute_url()).status_code in (403, 404)
        assert client.get(reverse("core:healthz")).status_code in (200, 503)


def test_runtime_fails_closed_if_a_file_table_loses_row_security(web, users):
    with connection.cursor() as cur:
        cur.execute("ALTER TABLE public.documents_documentfileversion DISABLE ROW LEVEL SECURITY")
    with web(), pytest.raises(ImproperlyConfigured), request_scope(users["med"].pk):
        pytest.fail("File history protection is missing")


def test_recipient_can_confirm_but_cannot_change_a_slip_awaiting_receipt(web, users, offices):
    from django.db import DatabaseError, transaction

    source = users["med"]
    recipient = users["sup"]
    slip = services.create_draft_record(user=source, subject="Awaiting receipt", instructions="")
    services.route_record(slip, [offices["SUP"]], user=source)
    with web(), request_scope(recipient.pk):
        with pytest.raises(DatabaseError), transaction.atomic():
            TrackingRecord.objects.filter(pk=slip.pk).update(subject="Premature change")
        assert TrackingRecord.objects.get(pk=slip.pk).subject == "Awaiting receipt"
        services.confirm_receipt(slip, user=recipient)
        assert slip.status == "RECEIVED"

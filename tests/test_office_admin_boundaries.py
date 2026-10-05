"""Office administrator isolation across pages, APIs and service calls."""

from urllib.parse import urlencode

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import AuditLog, Notification, NotificationRead, Tag
from apps.core.notifications import mark_all_read, unread_count
from apps.core.utils import log_action
from apps.core.views import MASTER_DATA, audit_entries_for
from apps.documents.models import AccessLevel, Document, DocumentAccessGrant, DocumentFile, SearchQueryLog
from apps.tracking.forms import GrantAccessForm
from apps.tracking.models import Attachment, RecordAccessGrant, Status, TrackingRecord
from apps.tracking.services import (
    complete_record,
    confirm_receipt,
    create_draft_record,
    grant_access,
    route_record,
    scope_office,
)


@pytest.fixture
def boundary(users, offices, memo_type):
    own = TrackingRecord.objects.create(
        subject="OWN_BOUNDARY_RECORD", tracking_number="BOUNDARY-MED", originating_office=offices["MED"],
        current_office=offices["MED"], created_by=users["med"], status=Status.IN_PROCESS,
    )
    foreign = TrackingRecord.objects.create(
        subject="FOREIGN_BOUNDARY_RECORD", tracking_number="BOUNDARY-SUP", originating_office=offices["SUP"],
        current_office=offices["SUP"], created_by=users["sup"], status=Status.IN_PROCESS,
    )
    docs = {}
    for key, office, uploader, access in (
        ("own", offices["MED"], users["med"], AccessLevel.OFFICE),
        ("foreign", offices["SUP"], users["sup"], AccessLevel.OVPA),
        ("restricted", offices["SUP"], users["med_admin"], AccessLevel.RESTRICTED),
    ):
        docs[key] = Document.objects.create(
            title=f"{key.upper()}_BOUNDARY_DOCUMENT", office=office, uploaded_by=uploader,
            access_level=access, document_type=memo_type, year=timezone.localdate().year,
        )
    # Even explicit sharing and an old uploader identity must not enlarge ADMIN.
    RecordAccessGrant.objects.create(record=foreign, office=offices["MED"], granted_by=users["admin"])
    RecordAccessGrant.objects.create(record=foreign, user=users["med_admin"], granted_by=users["admin"])
    DocumentAccessGrant.objects.create(document=docs["foreign"], user=users["med_admin"], granted_by=users["admin"])
    DocumentAccessGrant.objects.create(document=docs["restricted"], office=offices["MED"], granted_by=users["admin"])
    tag, _ = Tag.get_or_create_by_name("boundary-foreign-secret")
    docs["foreign"].tags.add(tag)
    attachment = Attachment.objects.create(record=foreign, file="boundary/foreign.pdf", original_name="FOREIGN_BOUNDARY_FILE.pdf")
    file = DocumentFile.objects.create(document=docs["foreign"], file="boundary/foreign.pdf", original_name="FOREIGN_BOUNDARY_FILE.pdf")
    return {"own": own, "foreign": foreign, "docs": docs, "attachment": attachment, "file": file}


LIST_PAGES = (
    "/", "/memo/print/", "/tracking/", "/tracking/reports/", "/documents/reports/",
    "/documents/", "/documents/?view=folders", "/documents/?view=pending",
    "/search/?mode=tracking&q=BOUNDARY", "/search/?q=BOUNDARY&show_all=on",
    "/search/autocomplete/?q=boundary", "/documents/tags/suggest/?q=boundary",
    "/tracking/reports/export/", "/administration/", "/accounts/users/", "/administration/audit-log/",
)


@pytest.mark.django_db
@pytest.mark.parametrize("staff,superuser", [(False, False), (True, False), (True, True)])
def test_every_list_and_export_stays_inside_the_office(client, users, offices, boundary, staff, superuser):
    actor = users["med_admin"]
    actor.is_staff, actor.is_superuser = staff, superuser
    actor.save(update_fields=["is_staff", "is_superuser"])
    client.force_login(actor)
    for page in LIST_PAGES:
        for office in ("", str(offices["SUP"].pk), "SUP", "all"):
            url = page + ("&" if "?" in page else "?") + urlencode({"office": office})
            response = client.get(url, HTTP_HX_REQUEST="true")
            assert response.status_code == 200, url
            body = response.content.decode()
            for secret in ("FOREIGN_BOUNDARY_RECORD", "FOREIGN_BOUNDARY_DOCUMENT", "RESTRICTED_BOUNDARY_DOCUMENT", "boundary-foreign-secret"):
                assert secret not in body, (url, secret, staff, superuser)
    assert not actor.is_system_admin
    assert scope_office(actor, offices["SUP"].pk) is None
    assert scope_office(actor, "all") is None
    dashboard = client.get("/").context
    assert dashboard["scope"]["can_pick"] is False
    assert not client.get("/tracking/").context["can_pick_office"]
    assert not client.get("/tracking/reports/").context["filters"]["can_pick"]
    assert client.get("/documents/").context["all_count"] == 1
    assert {folder["office__code"] for folder in client.get("/documents/?view=folders").context["smart_folders"]} <= {"MED"}


@pytest.mark.django_db
def test_direct_document_and_tracking_urls_never_expose_foreign_objects(client, users, boundary):
    client.force_login(users["med_admin"])
    record = boundary["foreign"]
    document = boundary["docs"]["foreign"]
    for url in (
        f"/tracking/{record.pk}/", f"/tracking/{record.pk}/review/", f"/tracking/{record.pk}/slip/",
        f"/tracking/attachment/{boundary['attachment'].pk}/",
        f"/documents/{document.pk}/", f"/documents/{document.pk}/review/", f"/documents/{document.pk}/edit/",
        f"/documents/{document.pk}/extraction-status/", f"/documents/file/{boundary['file'].pk}/",
    ):
        response = client.get(url, HTTP_HX_REQUEST="true")
        assert response.status_code in {403, 404}, url
        assert "FOREIGN_BOUNDARY" not in response.content.decode()
    for url in (
        *(f"/tracking/{record.pk}/{action}/" for action in ("receipt", "remark", "route", "complete", "archive", "reopen", "share")),
        *(f"/documents/{document.pk}/{action}/" for action in ("edit", "review", "files", "re-extract")),
    ):
        response = client.post(url, {"remark": "forbidden", "office": users["med_admin"].office_id}, HTTP_HX_REQUEST="true")
        assert response.status_code in {403, 404}, url
    record.refresh_from_db()
    assert record.status == Status.IN_PROCESS
    assert record.remarks == ""


@pytest.mark.django_db
def test_admin_global_settings_and_raw_admin_are_blocked(client, users, offices, memo_type):
    actor = users["med_admin"]
    actor.is_staff = actor.is_superuser = True
    actor.save(update_fields=["is_staff", "is_superuser"])
    client.force_login(actor)
    for slug, config in MASTER_DATA.items():
        assert config["system_admin_only"]
        for method in (client.get, client.post):
            for suffix in ("", "new/", f"{memo_type.pk}/"):
                expected = {403, 405} if method == client.post and not suffix else {403}
                assert method(f"/administration/{slug}/{suffix}").status_code in expected, (slug, suffix)
    for method in (client.get, client.post):
        for page in ("/administration/office-schedule/", "/accounts/offices/", f"/accounts/offices/{offices['MED'].pk}/"):
            assert method(page).status_code == 403, page
    assert client.get("/django-admin/").status_code == 302
    home = client.get("/administration/")
    for key in ("type_count", "tag_count", "rule_count", "field_count", "top_searches", "search_analytics"):
        assert key not in home.context


@pytest.mark.django_db
def test_global_settings_redirect_anonymous_users_without_querying_private_data(client):
    for slug in MASTER_DATA:
        for suffix in ("", "new/", "1/"):
            for method in (client.get, client.post):
                assert method(f"/administration/{slug}/{suffix}").status_code == 302


@pytest.mark.django_db
def test_permission_forms_and_service_calls_are_office_scoped(client, users, offices, boundary):
    actor = users["med_admin"]
    form = GrantAccessForm(actor=actor)
    assert set(form.fields["office"].queryset) == {offices["MED"]}
    assert {u.office_id for u in form.fields["user"].queryset} == {actor.office_id}
    assert users["admin"] not in form.fields["user"].queryset
    for payload in ({"office": offices["SUP"].pk}, {"user": users["sup"].pk}):
        assert not GrantAccessForm(payload, actor=actor).is_valid()
    for kwargs in ({"office": offices["SUP"]}, {"target_user": users["sup"]}, {"target_user": users["admin"]}):
        with pytest.raises(PermissionDenied):
            grant_access(boundary["own"], user=actor, **kwargs)
    with pytest.raises(PermissionDenied):
        grant_access(boundary["foreign"], user=actor, target_user=users["med"])
    assert grant_access(boundary["own"], user=actor, target_user=users["med"]).user == users["med"]


@pytest.mark.django_db
def test_own_office_work_and_legitimate_incoming_routes_still_work(client, users, offices, boundary, memo_type):
    actor = users["med_admin"]
    incoming = create_draft_record(user=users["sup"], subject="INCOMING_BOUNDARY", instructions="For action", document_type=memo_type)
    route_record(incoming, [offices["MED"]], user=users["sup"])
    assert incoming.can_user_view(actor)
    confirm_receipt(incoming, user=actor)
    incoming.refresh_from_db()
    assert incoming.current_office_id == actor.office_id
    client.force_login(actor)
    assert client.get(f"/tracking/{incoming.pk}/").status_code == 200
    assert client.get(f"/documents/{boundary['docs']['own'].pk}/").status_code == 200
    assert client.get(f"/accounts/users/{users['med'].pk}/").status_code == 200


@pytest.mark.django_db
def test_notifications_cannot_disclose_foreign_documents(client, users, boundary):
    actor = users["med_admin"]
    own = Notification.objects.create(office=actor.office, kind="SHARED", title="OWN_NOTICE", document=boundary["docs"]["own"])
    hidden = Notification.objects.create(office=actor.office, kind="SHARED", title="FOREIGN_NOTICE", document=boundary["docs"]["foreign"])
    client.force_login(actor)
    body = client.get("/notifications/").content.decode()
    assert "OWN_NOTICE" in body and "FOREIGN_NOTICE" not in body
    assert unread_count(actor) == 1
    assert client.post(f"/notifications/{hidden.pk}/read/").status_code == 404
    assert mark_all_read(actor) == 1
    assert NotificationRead.objects.filter(notification=own, user=actor).exists()
    assert not NotificationRead.objects.filter(notification=hidden, user=actor).exists()


@pytest.mark.django_db
def test_own_document_forms_and_search_clicks_do_not_leak_foreign_data(client, users, boundary):
    actor = users["med_admin"]
    boundary["docs"]["own"].uploaded_by = actor
    boundary["docs"]["own"].save(update_fields=["uploaded_by"])
    client.force_login(actor)
    url = f"/documents/{boundary['docs']['own'].pk}/review/"
    for response in (client.get(url), client.post(url, {})):
        assert response.status_code in {200, 400}
        assert "boundary-foreign-secret" not in response.content.decode()
    query = SearchQueryLog.objects.create(user=actor, query="BOUNDARY")
    assert client.get(f"/search/click/{query.pk}/{boundary['docs']['foreign'].pk}/1/").status_code == 404


@pytest.mark.django_db
def test_print_audit_targets_require_visible_objects(client, users, boundary):
    client.force_login(users["med_admin"])
    for kind, key in (("Document", boundary["docs"]["foreign"].pk), ("TrackingRecord", boundary["foreign"].pk)):
        assert client.post("/print-log/", {"target_type": kind, "target_id": key}).status_code == 404
    for target in ("bad", "9" * 64):
        assert client.post("/print-log/", {"target_type": "Document", "target_id": target}).status_code == 400
    assert not AuditLog.objects.filter(action="PRINT").exists()
    assert client.post("/print-log/", {"target_type": "Document", "target_id": boundary["docs"]["own"].pk}).status_code == 200
    assert audit_entries_for(users["med_admin"]).filter(action="PRINT").count() == 1


@pytest.mark.django_db
def test_own_restricted_documents_still_require_access_and_system_admin_keeps_global_access(users, offices, boundary):
    restricted = Document.objects.create(title="OWN_RESTRICTED", office=offices["MED"],
                                         uploaded_by=users["med"], access_level=AccessLevel.RESTRICTED)
    assert not restricted.can_user_view(users["med_admin"])
    assert not restricted.can_user_edit(users["med_admin"])
    DocumentAccessGrant.objects.create(document=restricted, user=users["med_admin"], granted_by=users["admin"])
    assert restricted.can_user_view(users["med_admin"])
    assert restricted.can_user_edit(users["med_admin"])
    assert boundary["docs"]["foreign"].can_user_view(users["admin"])
    assert boundary["foreign"].can_user_view(users["admin"])


@pytest.mark.django_db
@pytest.mark.parametrize("staff,superuser", [(False, False), (True, False), (True, True)])
def test_foreign_account_urls_and_privilege_escalation_stay_blocked(client, users, offices, staff, superuser):
    actor = users["med_admin"]
    actor.is_staff, actor.is_superuser = staff, superuser
    actor.save(update_fields=["is_staff", "is_superuser"])
    client.force_login(actor)
    foreign = users["sup"]
    payload = {"username": foreign.username, "office": actor.office_id, "role": "SYSTEM_ADMIN", "is_superuser": "on"}
    assert client.get(f"/accounts/users/{foreign.pk}/").status_code == 404
    assert client.post(f"/accounts/users/{foreign.pk}/", payload).status_code == 404
    assert client.post(f"/accounts/users/{foreign.pk}/toggle/").status_code == 404
    own = users["med"]
    payload["username"], payload["office"] = own.username, offices["SUP"].pk
    assert client.post(f"/accounts/users/{own.pk}/", payload).status_code == 200
    own.refresh_from_db()
    foreign.refresh_from_db()
    assert own.role == User.Role.USER and own.office_id == actor.office_id and not own.is_superuser
    assert foreign.role == User.Role.USER and foreign.office_id == offices["SUP"].pk and foreign.is_active


@pytest.mark.django_db
def test_old_creator_identity_cannot_reveal_foreign_tracking(users, offices):
    actor = users["med_admin"]
    foreign = TrackingRecord.objects.create(subject="Old office record", created_by=actor,
                                             originating_office=offices["SUP"], current_office=offices["SUP"])
    assert not foreign.can_user_view(actor)
    assert not foreign.can_user_act(actor)
    assert not foreign.can_user_grant_access(actor)


@pytest.mark.django_db
def test_filing_an_incoming_record_does_not_open_a_foreign_repository(client, users, offices, memo_type):
    actor = users["med_admin"]
    incoming = create_draft_record(user=users["sup"], subject="Own office handled this", instructions="For action", document_type=memo_type)
    route_record(incoming, [offices["MED"]], user=users["sup"])
    confirm_receipt(incoming, user=actor)
    incoming.refresh_from_db()
    complete_record(incoming, user=actor)
    client.force_login(actor)
    response = client.post(f"/tracking/{incoming.pk}/archive/")
    assert response.status_code == 302 and response.url == incoming.get_absolute_url()
    document = Document.objects.get(tracking_record=incoming)
    assert document.office_id == offices["SUP"].pk and not document.can_user_view(actor)
    detail = client.get(response.url)
    assert detail.status_code == 200 and detail.context["archived_document"] is None
    assert document.get_absolute_url() not in detail.content.decode()
    assert client.get(document.get_absolute_url()).status_code == 404


@pytest.mark.django_db
def test_audit_scope_cannot_move_with_accounts_or_reveal_foreign_targets(users, boundary):
    own = log_action(AuditLog.Action.UPDATE, "OWN_AUDIT", actor=users["admin"], target=users["med"])
    foreign = log_action(AuditLog.Action.UPDATE, "FOREIGN_AUDIT", actor=users["sup"], target=boundary["docs"]["foreign"])
    misplaced = log_action(AuditLog.Action.UPDATE, "MISPLACED_AUDIT", actor=users["med"], target=boundary["docs"]["foreign"])
    legacy = AuditLog.objects.create(actor=users["med"], action="UPDATE", summary="LEGACY_UNKNOWN_SCOPE")
    users["sup"].office = users["med"].office
    users["sup"].save(update_fields=["office"])
    visible = set(audit_entries_for(users["med_admin"]).values_list("pk", flat=True))
    assert own.pk in visible
    assert {foreign.pk, misplaced.pk, legacy.pk}.isdisjoint(visible)
    assert {own.pk, foreign.pk, misplaced.pk, legacy.pk} <= set(audit_entries_for(users["admin"]).values_list("pk", flat=True))


@pytest.mark.django_db
def test_unassigned_admin_fails_closed_and_superusers_are_created_with_explicit_role(client, users, boundary):
    actor = users["med_admin"]
    actor.office = None
    actor.save(update_fields=["office"])
    assert not Document.objects.visible_to(actor).exists()
    assert not TrackingRecord.objects.visible_to(actor).exists()
    assert not User.objects.administrable_by(actor).exists()
    assert not audit_entries_for(actor).exists()
    client.force_login(actor)
    for page in LIST_PAGES:
        response = client.get(page)
        assert response.status_code == 200, page
        assert "BOUNDARY_RECORD" not in response.content.decode()
        assert "BOUNDARY_DOCUMENT" not in response.content.decode()
    root = User.objects.create_superuser("boundary-root", password="TestPass123!")
    assert root.role == User.Role.SYSTEM_ADMIN and root.is_system_admin
    with pytest.raises(ValueError):
        User.objects.create_superuser("invalid-office-root", password="TestPass123!", role=User.Role.ADMIN)

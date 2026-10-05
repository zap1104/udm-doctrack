"""Real requests and direct service calls for authorization and stale privileges."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connections
from django.test import RequestFactory
from django.urls import reverse

from apps.accounts.models import User
from apps.core.models import AuditLog
from apps.documents import services as repository
from apps.documents.models import AccessLevel, Document, DocumentAccessGrant, DocumentFile
from apps.tracking import services as tracking
from apps.tracking.models import RecordActivity, RoutingStep, Status, TrackingRecord

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def private_test_storage(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def held(users, offices):
    record = tracking.create_draft_record(user=users["med"], subject="Private tracking", instructions="Review")
    tracking.route_record(record, [offices["SUP"]], user=users["med"])
    tracking.confirm_receipt(record, user=users["sup"])
    return record


@pytest.fixture
def private_document(users, offices):
    return Document.objects.create(title="Private repository", office=offices["SUP"], uploaded_by=users["sup"], year=2026)


PAGES = [
    "/", "/tracking/", "/documents/", "/search/", "/tracking/reports/", "/documents/reports/",
    "/notifications/", "/accounts/profile/", "/accounts/password/",
]
ADMIN_PAGES = ["/administration/", "/accounts/users/", "/accounts/users/new/", "/administration/audit-log/"]
SYSTEM_PAGES = ["/accounts/offices/", "/accounts/offices/new/", "/administration/office-schedule/"]


@pytest.mark.parametrize("role", ["admin", "med_admin", "med", "viewer"])
@pytest.mark.parametrize("page", PAGES + ADMIN_PAGES + SYSTEM_PAGES)
def test_page_role_matrix(client, users, role, page):
    client.force_login(users[role])
    allowed = page in PAGES or role == "admin" or (page in ADMIN_PAGES and role == "med_admin")
    response = client.get(page)
    assert response.status_code == (200 if allowed else 403), (role, page)
    assert "no-store" in response.get("Cache-Control", "")


@pytest.mark.parametrize("page", PAGES + ADMIN_PAGES + SYSTEM_PAGES)
def test_anonymous_pages_require_login(client, page):
    response = client.get(page)
    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")


@pytest.mark.parametrize("role", ["admin", "med_admin", "med", "viewer"])
def test_suspension_removes_existing_session_access(client, users, role):
    actor = users[role]
    client.force_login(actor)
    User.objects.filter(pk=actor.pk).update(is_active=False)
    for path in PAGES + ADMIN_PAGES:
        response = client.get(path)
        assert response.status_code == 302, (role, path)
        assert response["Location"].startswith("/accounts/login/")


def test_demotion_applies_to_an_existing_session(client, users):
    client.force_login(users["admin"])
    User.objects.filter(pk=users["admin"].pk).update(role=User.Role.VIEWER)
    assert client.get("/administration/").status_code == 403
    assert client.get("/documents/upload/").status_code == 403
    assert client.get("/tracking/new/").status_code == 403


@pytest.mark.parametrize("change", ["suspend", "demote"])
def test_admin_write_reloads_account_loaded_before_permission_change(users, change):
    from apps.accounts.views import UserCreateView

    actor = users["med_admin"]
    updates = {"is_active": False} if change == "suspend" else {"role": User.Role.USER}
    User.objects.filter(pk=actor.pk).update(**updates)
    request = RequestFactory().post("/accounts/users/new/", {})
    request.user = actor
    with pytest.raises(PermissionDenied):
        UserCreateView.as_view()(request)


def test_admin_write_uses_current_office_after_mid_request_transfer(users):
    from django.http import Http404

    from apps.accounts.views import UserToggleActiveView

    actor = users["med_admin"]
    target = users["med"]
    User.objects.filter(pk=actor.pk).update(office=users["sup"].office)
    request = RequestFactory().post(f"/accounts/users/{target.pk}/toggle/", {})
    request.user = actor
    with pytest.raises(Http404):
        UserToggleActiveView.as_view()(request, pk=target.pk)
    target.refresh_from_db()
    assert target.is_active


@pytest.mark.parametrize("role", ["ADMIN", "USER", "VIEWER", "UNKNOWN"])
@pytest.mark.parametrize("section", ["schedule", "holidays"])
def test_system_settings_recheck_a_system_admin_demoted_mid_request(users, role, section):
    from apps.core.views import MasterDataEditView, OfficeScheduleView

    actor = users["admin"]
    User.objects.filter(pk=actor.pk).update(role=role)
    request = RequestFactory().post("/administration/", {})
    request.user = actor
    with pytest.raises(PermissionDenied):
        if section == "schedule":
            OfficeScheduleView.as_view()(request)
        else:
            MasterDataEditView.as_view()(request, slug="holidays")


def test_unknown_role_fails_closed_despite_superuser_flags(client, users, held, private_document):
    actor = users["admin"]
    actor.role = "UNKNOWN"
    actor.save(update_fields=["role"])
    client.force_login(actor)
    for path in PAGES + ADMIN_PAGES + SYSTEM_PAGES:
        assert client.get(path).status_code == 403, path
    assert not TrackingRecord.objects.visible_to(actor).exists()
    assert not Document.objects.visible_to(actor).exists()
    assert not actor.can_start_work
    assert not held.can_user_act(actor)
    assert not private_document.can_user_edit(actor)


@pytest.mark.parametrize(("actor", "action"), [
    (actor, action) for actor in ["med", "med_admin", "hr", "viewer"]
    for action in ["remark", "files", "route", "process", "complete", "share"]
    if not (action == "share" and actor in {"med", "med_admin"})
])
def test_read_access_does_not_grant_custody_mutations(users, offices, held, actor, action):
    user = users[actor]
    before = (held.activities.count(), held.routing_steps.count(), AuditLog.objects.count())
    calls = {
        "remark": lambda: tracking.add_remark(held, user=user, remark="Unauthorized"),
        "files": lambda: tracking.attach_files(held, [SimpleUploadedFile("note.txt", b"memo")], user=user),
        "route": lambda: tracking.route_record(held, [offices["HR"]], user=user, action="FORWARD"),
        "process": lambda: tracking.mark_in_process(held, user=user),
        "complete": lambda: tracking.complete_record(held, user=user),
        # Originating offices can share their own record; other readers cannot.
        "share": lambda: tracking.grant_access(held, user=user, target_user=users["sup"]),
    }
    with pytest.raises(PermissionDenied):
        calls[action]()
    held.refresh_from_db()
    assert held.status == Status.RECEIVED
    assert before == (held.activities.count(), held.routing_steps.count(), AuditLog.objects.count())
    assert not held.attachments.exists()


@pytest.mark.parametrize("state", ["inactive", "viewer", "unassigned", "unknown"])
@pytest.mark.parametrize("service", ["draft", "remark", "complete", "upload"])
def test_cached_account_cannot_bypass_changed_permissions(users, offices, held, state, service):
    stale = users["sup"]
    fields = {"inactive": {"is_active": False}, "viewer": {"role": "VIEWER"},
              "unassigned": {"office": None}, "unknown": {"role": "UNKNOWN"}}[state]
    User.objects.filter(pk=stale.pk).update(**fields)
    calls = {
        "draft": lambda: tracking.create_draft_record(user=stale, subject="Unauthorized", instructions=""),
        "remark": lambda: tracking.add_remark(held, user=stale, remark="Unauthorized"),
        "complete": lambda: tracking.complete_record(held, user=stale),
        "upload": lambda: repository.ingest_upload(user=stale, uploaded_file=SimpleUploadedFile("note.txt", b"memo")),
    }
    with pytest.raises(PermissionDenied):
        calls[service]()
    held.refresh_from_db()
    assert held.status == Status.RECEIVED
    assert not Document.objects.exists()


def test_stale_custody_cannot_route_or_complete(users, offices, held):
    stale = TrackingRecord.objects.get(pk=held.pk)
    tracking.route_record(held, [offices["HR"]], user=users["sup"], action="FORWARD")
    tracking.confirm_receipt(held, user=users["hr"])
    with pytest.raises(PermissionDenied):
        tracking.complete_record(stale, user=users["sup"])
    with pytest.raises(PermissionDenied):
        tracking.route_record(stale, [offices["MED"]], user=users["sup"], action="FORWARD")
    held.refresh_from_db()
    assert held.current_office == offices["HR"]


def test_cannot_originate_for_another_office(users, offices):
    with pytest.raises(PermissionDenied):
        tracking.create_draft_record(user=users["med"], originating_office=offices["SUP"], subject="Spoofed", instructions="")
    assert not TrackingRecord.objects.exists()


@pytest.mark.parametrize("role", ["med", "med_admin", "viewer"])
def test_unassigned_accounts_cannot_read_old_or_shared_records(users, role, held, private_document):
    actor = users[role]
    private_document.access_level = AccessLevel.OVPA
    private_document.save()
    actor.office = None
    actor.save(update_fields=["office"])
    assert not TrackingRecord.objects.visible_to(actor).exists()
    assert not Document.objects.visible_to(actor).exists()
    assert not repository.suggested_tags_for(actor).exists()


@pytest.mark.parametrize("role", ["med", "med_admin", "viewer"])
def test_private_keywords_are_not_suggested_to_other_offices(users, role, private_document):
    from apps.core.models import Tag
    from apps.search.services import autocomplete_terms

    tag, _ = Tag.get_or_create_by_name("privatekeyword")
    private_document.tags.add(tag)
    assert not repository.suggested_tags_for(users[role]).filter(pk=tag.pk).exists()
    assert "privatekeyword" not in autocomplete_terms(users[role], "private")


def test_restricted_document_requires_read_permission_to_edit(users, private_document):
    private_document.access_level = AccessLevel.RESTRICTED
    private_document.uploaded_by = users["admin"]
    private_document.save()
    assert not private_document.can_user_view(users["sup"])
    assert not private_document.can_user_edit(users["sup"])
    with pytest.raises(PermissionDenied):
        repository.save_document_metadata(private_document, user=users["sup"], data={"title": "Unauthorized"}, tag_names=[], metadata_values={})
    DocumentAccessGrant.objects.create(document=private_document, user=users["sup"], granted_by=users["admin"])
    assert private_document.can_user_edit(users["sup"])


@pytest.mark.parametrize("original_uploader", [True, False])
@pytest.mark.parametrize("action", ["file", "metadata"])
def test_repository_transfer_invalidates_cached_edit_permission(users, offices, private_document, original_uploader, action):
    stale = Document.objects.get(pk=private_document.pk)
    if not original_uploader:
        private_document.uploaded_by = users["admin"]
    private_document.office = offices["HR"]
    private_document.save()
    with pytest.raises(PermissionDenied):
        if action == "file":
            repository.add_file_to_document(stale, SimpleUploadedFile("note.txt", b"memo"), user=users["sup"])
        else:
            repository.save_document_metadata(stale, user=users["sup"], data={"title": "Unauthorized"}, tag_names=[], metadata_values={})
    private_document.refresh_from_db()
    assert private_document.title == "Private repository"
    assert not private_document.files.exists()


def test_former_uploader_has_read_access_but_no_edit_controls_or_endpoints(client, users, private_document):
    private_document.office = users["hr"].office
    private_document.save(update_fields=["office"])
    actor = users["sup"]
    assert private_document.can_user_view(actor)
    assert not private_document.can_user_edit(actor)
    client.force_login(actor)
    response = client.get(private_document.get_absolute_url())
    assert response.status_code == 200
    assert response.context["can_edit"] is False
    assert client.get(reverse("documents:edit", args=[private_document.pk])).status_code == 403
    for endpoint in ["edit", "add_files", "re_extract", "review"]:
        assert client.post(reverse(f"documents:{endpoint}", args=[private_document.pk]), {}).status_code == 403


def test_direct_archive_enforces_approval_role_and_reopening_is_scoped(users, held):
    tracking.complete_record(held, user=users["sup"])
    for actor in (users["sup"], users["hr"], users["viewer"], AnonymousUser()):
        with pytest.raises(PermissionDenied):
            repository.archive_tracking_record(held, user=actor)
    with pytest.raises(PermissionDenied):
        tracking.reopen_record(held, user=users["hr"])
    document = repository.archive_tracking_record(held, user=users["sup_admin"])
    assert document.office_id == held.originating_office_id
    assert held.approved_by_id == users["sup_admin"].pk


def test_receipt_after_completion_and_draft_completion_are_refused(users, offices, held):
    tracking.route_record(held, [offices["HR"], offices["MED"]], user=users["sup"], action="FORWARD")
    tracking.confirm_receipt(held, user=users["hr"])
    tracking.complete_record(held, user=users["hr"])
    with pytest.raises(ValidationError):
        tracking.confirm_receipt(held, user=users["med"])
    draft = tracking.create_draft_record(user=users["med"], subject="Unsent", instructions="")
    with pytest.raises(ValidationError):
        tracking.complete_record(draft, user=users["med"])


def test_receiving_offices_are_deduplicated(users, offices):
    draft = tracking.create_draft_record(user=users["med"], subject="One handover", instructions="")
    tracking.route_record(draft, [offices["SUP"], offices["SUP"]], user=users["med"])
    assert draft.routing_steps.count() == 1


@pytest.mark.parametrize("path", ["/tracking/1/complete/", "/tracking/1/route/", "/tracking/1/share/", "/tracking/1/receipt/",
                                  "/documents/upload/", "/documents/1/edit/", "/documents/1/re-extract/",
                                  "/accounts/users/new/", "/accounts/users/1/toggle/", "/accounts/logout/",
                                  "/accounts/email/verify/", "/accounts/password/", "/notifications/1/read/"])
def test_mutations_require_csrf_even_with_valid_session(client, users, path):
    browser = type(client)(enforce_csrf_checks=True)
    browser.force_login(users["admin"])
    assert browser.post(path, {}).status_code == 403


@pytest.mark.parametrize("role", ["med", "med_admin", "viewer"])
def test_direct_private_file_identifiers_cannot_bypass_scope(client, users, role, private_document):
    file = DocumentFile.objects.create(document=private_document, file=SimpleUploadedFile("private.txt", b"private"), original_name="private.txt")
    client.force_login(users[role])
    response = client.get(reverse("documents:file_download", args=[file.pk]))
    assert response.status_code in {403, 404}
    assert b"private" not in response.content


@pytest.mark.django_db(transaction=True)
def test_concurrent_completion_records_one_completion(users, offices):
    record = tracking.create_draft_record(user=users["med"], subject="Concurrent", instructions="")
    tracking.route_record(record, [offices["SUP"]], user=users["med"])
    tracking.confirm_receipt(record, user=users["sup"])
    def finish():
        close_old_connections()
        try:
            tracking.complete_record(TrackingRecord.objects.get(pk=record.pk), user=User.objects.get(pk=users["sup"].pk))
        finally:
            connections.close_all()
    with ThreadPoolExecutor(max_workers=2) as workers:
        list(workers.map(lambda _: finish(), range(2)))
    assert record.activities.filter(event=RecordActivity.Event.COMPLETED).count() == 1
    assert AuditLog.objects.filter(action=AuditLog.Action.COMPLETE, target_id=str(record.pk)).count() == 1
    assert RoutingStep.objects.filter(record=record).count() == 1

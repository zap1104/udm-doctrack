"""Restriction checks retain all existing office and custody boundaries."""
import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.documents.models import Document
from apps.tracking.models import Status, TrackingRecord
from tests.auth_helpers import verified_request

pytestmark = pytest.mark.django_db


def block(user, area, action, admin):
    from apps.accounts.capabilities import set_restrictions
    return set_restrictions(user, [(area, action)], actor=admin, request=verified_request(admin), expected_revision=0, reason='Regression access restriction')


def document(users, **values):
    return Document.objects.create(title='Visible metadata', office=users['med'].office,
        uploaded_by=users['med'], year=2026, **values)


def record(users, **values):
    return TrackingRecord.objects.create(subject='Office tracking', originating_office=users['med'].office,
        current_office=users['med'].office, created_by=users['med'], status=Status.DRAFT, **values)


def test_restrictions_remove_rows_without_changing_other_collection(users):
    doc = document(users)
    external = document(users, collection='EXTERNAL')
    block(users['med'], 'repository', 'view_record', users['admin'])
    assert set(Document.objects.visible_to(users['med']).values_list('pk', flat=True)) == {external.pk}
    assert doc.pk != external.pk


def test_old_user_instance_loses_action_after_policy_change(users):
    from apps.tracking.services import create_draft_record
    actor = users['med']
    block(actor, 'tracking', 'create', users['admin'])
    with pytest.raises(PermissionDenied):
        create_draft_record(user=actor, subject='Must not be created', instructions='')
    assert not TrackingRecord.objects.exists()


def test_restrictions_cannot_grant_viewer_edits(users):
    from apps.accounts.capabilities import capability_allowed
    assert not capability_allowed(users['viewer'], 'tracking', 'edit_files')
    assert not capability_allowed(users['viewer'], 'repository', 'edit_metadata')


def test_office_admin_cannot_manage_permission_table(users):
    from apps.accounts.capabilities import set_restrictions
    with pytest.raises(PermissionDenied):
        set_restrictions(users['med'], [], actor=users['med_admin'], expected_revision=0, reason='Not allowed')


def test_stale_permission_form_cannot_overwrite_new_restrictions(users):
    from apps.accounts.capabilities import set_restrictions
    block(users['med'], 'tracking', 'create', users['admin'])
    with pytest.raises(ValidationError):
        set_restrictions(users['med'], [], actor=users['admin'], request=verified_request(users['admin']), expected_revision=0, reason='Stale request')


def test_metadata_edit_denied_in_service_and_model(users):
    from apps.documents.services import save_document_metadata
    doc = document(users)
    block(users['med'], 'repository', 'edit_metadata', users['admin'])
    assert not doc.can_user_edit(users['med'])
    with pytest.raises(PermissionDenied):
        save_document_metadata(doc, user=users['med'], data={'title': 'Forbidden'}, tag_names=[], metadata_values={})
    doc.refresh_from_db()
    assert doc.title == 'Visible metadata'


def test_content_restriction_blocks_signed_version_preview(client, users):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.core.file_access import file_url
    from apps.tracking.services import attach_files
    rec = record(users)
    item = attach_files(rec, [SimpleUploadedFile('note.txt', b'private content', content_type='text/plain')], user=users['med'])[0]
    url = file_url('tracking', item.pk, users['med'])
    block(users['med'], 'tracking', 'view_content', users['admin'])
    client.force_verified_login(users['med'])
    assert client.get(url).status_code in (403, 404)
    assert rec.can_user_view(users['med'])


def test_external_sharing_does_not_grant_custody(users):
    from apps.accounts.sharing import grant_read_access, revoke_read_access
    from apps.tracking.external_models import ExternalTrackingRecord
    rec = ExternalTrackingRecord.objects.create(managing_office=users['med'].office, current_office=users['med'].office,
        tracking_number='UDM-EXT-MED-2026-11-0001', title='Shared external case', created_by=users['med_admin'], status=Status.RECEIVED)
    assert not ExternalTrackingRecord.objects.visible_to(users['sup']).filter(pk=rec.pk).exists()
    grant = grant_read_access(rec, user=users['admin'], target_user=users['sup'], reason='Read only')
    assert ExternalTrackingRecord.objects.visible_to(users['sup']).filter(pk=rec.pk).exists()
    assert not rec.can_act(users['sup'])
    revoke_read_access(rec, grant.pk, user=users['admin'], reason='Access no longer needed')
    assert not ExternalTrackingRecord.objects.visible_to(users['sup']).filter(pk=rec.pk).exists()


def test_system_admin_permission_table_contains_default_and_block_choices(client, users):
    client.force_verified_login(users['admin'])
    response = client.get('/django-admin/accounts/permissionuser/')
    assert response.status_code == 200
    response = client.get(f'/django-admin/accounts/permissionuser/{users["med"].pk}/change/')
    assert response.status_code == 200
    assert 'Use role default' in response.content.decode()
    assert 'Block' in response.content.decode()


def test_sharing_table_removes_only_explicit_access(client, users):
    from apps.accounts.sharing import grant_read_access
    doc = document(users)
    grant = grant_read_access(doc, user=users['admin'], target_user=users['med'], reason='Existing office access')
    client.force_verified_login(users['admin'])
    response = client.post('/django-admin/record-sharing/', {'kind': 'repository', 'record': doc.pk,
        'action': 'revoke', 'grant': grant.pk, 'reason': 'Remove redundant grant'})
    assert response.status_code == 302
    assert not doc.grants.exists()
    assert doc.can_user_view(users['med'])


def test_content_blocked_search_cannot_match_ocr_only_term(users):
    from apps.search.services import search_documents
    doc = document(users, ocr_text='ultravioletsecretcodex')
    doc.rebuild_index()
    block(users['med'], 'repository', 'view_content', users['admin'])
    response = search_documents(user=users['med'], query='ultravioletsecretcodex', min_relevance=0)
    assert response.total_matches == 0
    assert not response.results
    response = search_documents(user=users['med'], query='Visible metadata', min_relevance=0)
    assert [item.document.pk for item in response.results] == [doc.pk]


def test_content_blocked_record_detail_preserves_metadata_but_hides_ocr(client, users):
    doc = document(users, ocr_text='Secret extracted paragraph')
    block(users['med'], 'repository', 'view_content', users['admin'])
    client.force_verified_login(users['med'])
    response = client.get(doc.get_absolute_url())
    assert response.status_code == 200
    assert 'Visible metadata' in response.content.decode()
    assert 'Secret extracted paragraph' not in response.content.decode()


def test_legacy_download_obeys_download_restriction(client, users):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.core.utils import sign_download
    from apps.tracking.services import attach_files
    rec = record(users)
    item = attach_files(rec, [SimpleUploadedFile('note.txt', b'private content', content_type='text/plain')], user=users['med'])[0]
    token = sign_download(f'attachment:{item.pk}:{users["med"].pk}')
    block(users['med'], 'tracking', 'download', users['admin'])
    client.force_verified_login(users['med'])
    assert client.get(f'/tracking/attachment/{item.pk}/?token={token}').status_code in (403, 404)


def test_blocked_file_edits_do_not_block_record_reading(client, users):
    from apps.tracking.services import confirm_receipt, create_draft_record, route_record
    rec = create_draft_record(user=users['sup'], subject='Incoming readable case', instructions='')
    route_record(rec, [users['med'].office], user=users['sup'])
    confirm_receipt(rec, user=users['med'])
    block(users['med'], 'tracking', 'edit_files', users['admin'])
    client.force_verified_login(users['med'])
    assert client.get(rec.get_absolute_url()).status_code == 200


def test_external_completion_does_not_offer_blocked_immediate_filing(users):
    from apps.tracking.external_forms import ExternalCompleteForm
    from apps.tracking.external_models import ExternalTrackingRecord
    rec = ExternalTrackingRecord.objects.create(managing_office=users['med'].office, current_office=users['med'].office,
        tracking_number='UDM-EXT-MED-2026-11-0200', title='Complete without filing', created_by=users['med_admin'], status=Status.RECEIVED)
    block(users['med_admin'], 'external_tracking', 'file', users['admin'])
    form = ExternalCompleteForm(record=rec, user=users['med_admin'])
    assert 'archive_now' not in form.fields


def test_raw_admin_write_rechecks_actor_after_permission_revocation(users):
    from django.contrib import admin
    from django.http import HttpResponse
    from django.views.decorators.csrf import csrf_exempt

    from apps.accounts.models import User
    request = verified_request(users['admin'])
    User.objects.filter(pk=users['admin'].pk).update(role='VIEWER')
    called = []
    @csrf_exempt
    def action(request):
        called.append(True)
        return HttpResponse('unsafe action')
    with pytest.raises(PermissionDenied):
        admin.site.admin_view(action)(request)
    assert not called


def test_metadata_block_does_not_block_initial_upload_or_extraction(users, monkeypatch, settings):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.documents.services import ingest_upload, re_extract_document
    block(users['med'], 'repository', 'edit_metadata', users['admin'])
    settings.ENABLE_SOURCE_UNIT_EXTRACTION = False
    settings.ENABLE_BACKGROUND_TASKS = False
    doc, _ = ingest_upload(user=users['med'], uploaded_files=[
        SimpleUploadedFile('note.txt', b'Allowed contents', content_type='text/plain'),
        SimpleUploadedFile('extra.txt', b'Initial supporting contents', content_type='text/plain')], defer_extraction=True)
    assert doc.files.count() == 2
    assert not doc.can_user_edit(users['med'])
    monkeypatch.setattr('apps.documents.tasks.extract_document_task', lambda *args, **kwargs: {'status': 'DONE', 'failures': []})
    assert re_extract_document(doc, user=users['med'])['status'] == 'DONE'


def test_raw_routing_inline_obeys_endorsement_restriction(users):
    from django.contrib import admin

    from apps.tracking.admin import RoutingStepInline
    rec = record(users)
    block(users['admin'], 'tracking', 'endorse', users['admin'])
    request = verified_request(users['admin'])
    inline = RoutingStepInline(TrackingRecord, admin.site)
    assert not inline.has_add_permission(request, rec)
    assert not inline.has_change_permission(request, rec)


def test_raw_document_admin_checks_actual_collection_on_create(users):
    from django.contrib import admin
    block(users['admin'], 'external_repository', 'create', users['admin'])
    model_admin = admin.site._registry[Document]
    doc = Document(title='Must not cross collection policy', collection='EXTERNAL', office=users['med'].office,
        uploaded_by=users['admin'], year=2026)
    with pytest.raises(PermissionDenied):
        model_admin.save_model(verified_request(users['admin']), doc, None, False)
    assert not Document.objects.exists()


def test_raw_document_admin_cannot_move_existing_collection(users):
    from django.contrib import admin
    model_admin = admin.site._registry[Document]
    doc = document(users)
    assert 'collection' in model_admin.get_readonly_fields(verified_request(users['admin']), doc)


def test_invalid_tag_review_cannot_render_content_when_blocked(client, users):
    from django.urls import reverse
    doc = document(users, ocr_text='Private body evidence')
    block(users['med'], 'repository', 'view_content', users['admin'])
    client.force_verified_login(users['med'])
    assert client.post(reverse('documents:tag_review', args=[doc.pk]), {}).status_code == 403


@pytest.mark.django_db(transaction=True)
def test_share_recipient_is_locked_until_authorization_commits(users):
    from concurrent.futures import ThreadPoolExecutor

    from django.db import DatabaseError, close_old_connections, transaction

    from apps.accounts.models import User
    from apps.accounts.sharing import _target

    def try_edit_recipient():
        close_old_connections()
        try:
            with transaction.atomic():
                User.objects.select_for_update(nowait=True).get(pk=users['med'].pk)
            return 'unlocked'
        except DatabaseError:
            return 'locked'
        finally:
            close_old_connections()

    with transaction.atomic():
        _target(users['med_admin'], None, users['med'])
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(try_edit_recipient).result(timeout=10) == 'locked'


def test_legacy_share_rechecks_transferred_recipient(users):
    from apps.accounts.models import User
    from apps.tracking.services import create_draft_record, grant_access
    rec = create_draft_record(user=users['med_admin'], subject='Same office only', instructions='')
    stale_recipient = users['med']
    User.objects.filter(pk=stale_recipient.pk).update(office=users['sup'].office)
    with pytest.raises(PermissionDenied):
        grant_access(rec, user=users['med_admin'], target_user=stale_recipient, reason='Stale office')
    assert not rec.grants.exists()

"""Focused cross-layer regressions reproduced during the security audit."""
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.accounts.forms import AdminUserUpdateForm
from apps.accounts.models import User
from apps.accounts.views import ProfileView
from apps.core.file_access import file_url
from apps.core.file_lifecycle_models import lifecycle_write
from apps.core.utils import sign_download
from apps.documents.models import Document, DocumentFile
from tests.auth_helpers import prepare_verified_user
from tests.test_release_profile import stale_profile_request

pytestmark = pytest.mark.django_db


def test_stale_profile_save_cannot_restore_replaced_email_factor(users, settings):
    settings.EMAIL_OTP_REQUIRED = True
    user = users['med']
    prepare_verified_user(user)
    old = user.email
    request = stale_profile_request(user, {'action':'profile', 'first_name':'Updated', 'last_name':'Staff',
        'email':old, 'phone':'', 'position':'Records officer'})
    User.objects.filter(pk=user.pk).update(email='replacement@example.test', verified_email='')
    response = ProfileView.as_view()(request)
    assert response.status_code == 302
    user.refresh_from_db()
    assert user.email == 'replacement@example.test'
    assert user.verified_email == ''
    assert user.first_name == 'Updated'


@pytest.mark.parametrize('external', [False, True])
@pytest.mark.parametrize('unavailable', ['backend_changed', 'retired'])
def test_legacy_repository_download_checks_version_lifecycle(client, users, settings, tmp_path, external, unavailable):
    settings.MEDIA_ROOT = tmp_path
    actor = users['med_admin'] if external else users['med']
    doc = Document.objects.create(title='Private retained file', office=actor.office, uploaded_by=actor,
        collection='EXTERNAL' if external else 'OVPA')
    root = DocumentFile.objects.create(document=doc, file=SimpleUploadedFile('proof.txt', b'Private retained bytes', 'text/plain'),
        original_name='proof.txt', uploaded_by=actor)
    snapshot = root.versions.get()
    with lifecycle_write():
        type(snapshot).objects.filter(pk=snapshot.pk).update(**(
            {'storage_identity':'other-private-backend'} if unavailable == 'backend_changed' else
            {'retired_at':timezone.now()-timedelta(seconds=1)}))
    client.force_verified_login(actor)
    exact = client.get(file_url('repository', snapshot.pk, actor, 'download'))
    assert exact.status_code == 410
    token = sign_download(f'document-file:{root.pk}:{actor.pk}')
    legacy = client.get(reverse('documents:file_download', args=[root.pk]), {'token':token})
    assert legacy.status_code == 410
    assert legacy['Cache-Control'] == 'private, no-store'
    assert legacy['X-Content-Type-Options'] == 'nosniff'


@pytest.mark.parametrize('replacement_state', ['unverified', 'not_staff', 'unusable_password'])
def test_frontend_step_down_requires_a_ready_replacement(users, replacement_state):
    actor = users['admin']
    prepare_verified_user(actor)
    other = User.objects.create_user(username='incomplete-admin', password='IncompletePass123!', role='SYSTEM_ADMIN',
        office=actor.office, is_staff=True, email='replacement-admin@example.test')
    other.verified_email = other.email
    if replacement_state == 'unverified':
        other.verified_email = ''
    elif replacement_state == 'not_staff':
        other.is_staff = False
    else:
        other.set_unusable_password()
    other.save()
    data = {'first_name':'Administrator', 'last_name':'User', 'email':actor.email, 'office':actor.office_id,
        'role':'ADMIN', 'position':'', 'phone':'', 'is_active':'on'}
    form = AdminUserUpdateForm(data, instance=actor, actor=actor, editing_self=True)
    assert not form.is_valid()
    assert 'role' in form.errors


@pytest.mark.parametrize('action', ['edit', 'toggle'])
def test_frontend_cannot_remove_last_ready_admin_via_another_app_admin(client, users, action):
    actor = users['admin']
    actor.is_staff = False
    actor.save(update_fields=['is_staff'])
    target = User.objects.create_user(username='last-ready', password='ReadyAdminPass123!', role='SYSTEM_ADMIN',
        office=actor.office, is_staff=True, email='last-ready@example.test')
    prepare_verified_user(target)
    client.force_verified_login(actor)
    data = {'first_name':'Ready', 'last_name':'Admin', 'email':target.email, 'office':target.office_id,
        'role':'USER', 'position':'', 'phone':'', 'is_active':'on'}
    response = client.post(reverse('accounts:user_edit' if action == 'edit' else 'accounts:user_toggle', args=[target.pk]), data)
    assert response.status_code in (200, 302)
    target.refresh_from_db()
    assert target.is_active and target.role == 'SYSTEM_ADMIN'

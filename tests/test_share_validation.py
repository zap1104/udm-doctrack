"""Sharing must choose one recipient and report invalid input without a 500."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.urls import reverse

from apps.accounts import sharing
from apps.accounts.models import Office, User
from apps.documents.models import Document
from apps.tracking.forms import GrantAccessForm
from apps.tracking.models import RecordAccessGrant
from apps.tracking.services import create_draft_record, grant_access, route_record


@pytest.mark.parametrize("office,user", [(True, True), (False, False)])
def test_clean_rejects_ambiguous_or_missing_recipient(office, user):
    form = GrantAccessForm()
    form.cleaned_data = {
        "office": Office(pk=1) if office else None,
        "user": User(pk=2) if user else None,
    }
    with pytest.raises(ValidationError):
        form.clean()


@pytest.mark.django_db
@pytest.mark.parametrize("actor", ["med", "med_admin", "admin"])
def test_share_with_both_recipients_shows_error_without_granting_access(client, users, offices, actor):
    record = create_draft_record(user=users["med"], subject="Share validation", instructions="")
    route_record(record, [offices["SUP"]], user=users["med"])
    client.force_verified_login(users[actor])
    response = client.post(reverse("tracking:grant_access", args=[record.pk]), {
        "office": offices["MED"].pk, "user": users["med"].pk, "reason": "Review",
    })
    assert response.status_code == 302
    assert response.url == record.get_absolute_url()
    assert not RecordAccessGrant.objects.filter(record=record).exists()
    assert [str(message) for message in get_messages(response.wsgi_request)] == [
        "Choose either an office or a user, not both."
    ]


@pytest.mark.django_db
@pytest.mark.parametrize("recipient", ["office", "user"])
def test_share_with_one_recipient_still_grants_access(client, users, offices, recipient):
    record = create_draft_record(user=users["med"], subject="Valid share", instructions="")
    route_record(record, [offices["SUP"]], user=users["med"])
    client.force_verified_login(users["med"])
    selected = offices["HR"].pk if recipient == "office" else users["hr"].pk
    response = client.post(reverse("tracking:grant_access", args=[record.pk]), {recipient: selected})
    assert response.status_code == 302
    grant = RecordAccessGrant.objects.get(record=record)
    assert getattr(grant, f"{recipient}_id") == selected
    other = "user" if recipient == "office" else "office"
    assert getattr(grant, f"{other}_id") is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('area', ['repository', 'tracking'])
def test_granting_to_an_office_admin_can_overlap_their_revocation(users, offices, memo_type, monkeypatch, area):
    system_admin = users["admin"]
    office_admin = users["med_admin"]
    if area == 'repository':
        record = Document.objects.create(title="Shared office file", office=offices["MED"],
                                         document_type=memo_type, uploaded_by=office_admin)
        grant_access_for_record = sharing.grant_read_access
    else:
        record = create_draft_record(user=office_admin, subject="Shared tracking record", instructions="")
        grant_access_for_record = grant_access
    existing = sharing.grant_read_access(record, user=system_admin, target_user=users["hr"], reason="Old review")
    before_target, revoke_has_actor = Event(), Event()
    original_writer = sharing.active_writer

    def wait_for_revoker(execute, sql, params, many, context):
        if 'FOR NO KEY UPDATE' in sql and '"accounts_user"' in sql and office_admin.pk in params:
            before_target.set()
            assert revoke_has_actor.wait(timeout=10)
        return execute(sql, params, many, context)

    def track_actor_lock(user):
        actor = original_writer(user)
        if actor.pk == office_admin.pk:
            revoke_has_actor.set()
        return actor

    def grant():
        close_old_connections()
        try:
            with connection.execute_wrapper(wait_for_revoker):
                return grant_access_for_record(record, user=system_admin,
                                               target_user=office_admin, reason="New review")
        finally:
            close_old_connections()

    def revoke():
        close_old_connections()
        try:
            sharing.revoke_read_access(record, existing.pk, user=office_admin, reason="Review complete")
        finally:
            close_old_connections()

    monkeypatch.setattr(sharing, "active_writer", track_actor_lock)
    with ThreadPoolExecutor(max_workers=2) as pool:
        granting = pool.submit(grant)
        assert before_target.wait(timeout=10)
        revoking = pool.submit(revoke)
        new_grant = granting.result(timeout=15)
        revoking.result(timeout=15)

    assert list(record.grants.values_list("pk", "user_id")) == [
        (new_grant.pk, office_admin.pk),
    ]

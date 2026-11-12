"""Actual login/invitation requests must not survive changed security state."""

import re
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.core import mail
from django.db import close_old_connections

from apps.accounts import otp
from apps.accounts.models import User
from apps.accounts.security_models import EmailChallenge
from tests.auth_helpers import prepare_verified_user, verified_request

CHANGES = ["role", "office", "password", "email", "verified_email", "is_staff", "is_superuser", "is_active"]


@pytest.fixture(autouse=True)
def enabled(settings):
    settings.EMAIL_OTP_REQUIRED = True
    settings.EMAIL_CONFIGURED = True
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.SITE_BASE_URL = "https://testserver"


def change_security_state(user, offices, field):
    values = {
        "role": "VIEWER", "office": offices["SUP"], "password": "!disabled-for-security-test",
        "email": "replacement@example.test", "verified_email": "", "is_staff": not user.is_staff,
        "is_superuser": not user.is_superuser, "is_active": False,
    }
    setattr(user, field, values[field])
    user.save(update_fields=[field])


@pytest.mark.django_db
@pytest.mark.parametrize("field", CHANGES)
def test_code_consumption_rechecks_every_account_security_change(client, users, offices, field):
    user = users["med"]
    prepare_verified_user(user)
    assert client.post("/accounts/login/", {"username": user.username, "password": "TestPass123!"}).status_code == 302
    code = re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)
    challenge = EmailChallenge.objects.get(user=user, purpose="login")
    change_security_state(user, offices, field)

    response = client.post("/accounts/otp/", {"code": code})

    assert response.status_code == 200
    assert "_auth_user_id" not in client.session
    user.refresh_from_db()
    challenge.refresh_from_db()
    assert user.last_login is None
    assert challenge.consumed_at is None
    assert not client.get("/tracking/").status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("field", CHANGES)
def test_email_change_invitation_cannot_restore_superseded_security_state(users, offices, field):
    user = users["med"]
    request = verified_request(user)
    challenge = otp.invitation(user, request=request, new_email="invited-replacement@example.test")
    token = re.search(r"/accounts/email/invitation/[^/]+/([^/]+)/", mail.outbox[-1].body).group(1)
    change_security_state(user, offices, field)
    expected_email, expected_verified = user.email, user.verified_email

    assert not otp.invitation_valid(challenge.pk, token, consume=True)

    user.refresh_from_db()
    challenge.refresh_from_db()
    assert (user.email, user.verified_email) == (expected_email, expected_verified)
    assert challenge.consumed_at is None


@pytest.mark.django_db
@pytest.mark.parametrize("field", CHANGES)
@pytest.mark.parametrize("path,headers,expected", [
    ("/tracking/", {}, 302),
    ("/documents/external/", {"HTTP_HX_REQUEST": "true"}, 401),
    ("/files/content/tracking/1/?token=old-link", {"HTTP_ACCEPT": "application/json"}, 401),
])
def test_existing_otp_session_loses_all_entry_paths_after_security_change(client, users, offices, field, path, headers, expected):
    user = users["med"]
    client.force_verified_login(user)
    change_security_state(user, offices, field)

    response = client.get(path, **headers)

    # Password changes and suspension are rejected by Django authentication
    # itself, before the OTP middleware sees an authenticated account.
    if field in {"password", "is_active"}:
        assert response.status_code in {302, 403}
    else:
        assert response.status_code == expected
    if field != "is_active":
        assert "_auth_user_id" not in client.session
    assert "no-store" in response.get("Cache-Control", "") or response.status_code in {302, 403}
    if headers.get("HTTP_HX_REQUEST") and field not in {"password", "is_active"}:
        assert response["HX-Redirect"].startswith("/accounts/login/")


@pytest.mark.django_db
@pytest.mark.parametrize("field", CHANGES)
@pytest.mark.parametrize("proof_kind", ["session", "code", "invitation"])
@pytest.mark.parametrize("write_path", ["save", "update"])
def test_security_changes_cannot_resurrect_old_proof_when_values_are_restored(client, users, offices, field, proof_kind, write_path):
    user = users["med"]
    prepare_verified_user(user)
    if proof_kind == "session":
        client.force_verified_login(user)
    elif proof_kind == "code":
        client.post("/accounts/login/", {"username": user.username, "password": "TestPass123!"})
        code = re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)
    else:
        challenge = otp.invitation(user, reason="Enrollment before account change")
        secret = re.search(r"/accounts/email/invitation/[^/]+/([^/]+)/", mail.outbox[-1].body).group(1)
    original = getattr(user, field)
    if write_path == "save":
        change_security_state(user, offices, field)
        setattr(user, field, original)
        user.save(update_fields=[field])
    else:
        replacements = {"role": "VIEWER", "office": offices["SUP"], "is_active": False,
                        "is_staff": True, "is_superuser": True, "password": "!temporarily-disabled",
                        "email": "another@example.test", "verified_email": ""}
        User.objects.filter(pk=user.pk).update(**{field: replacements[field]})
        User.objects.filter(pk=user.pk).update(**{field: original})

    if proof_kind == "session":
        response = client.get("/tracking/")
        assert response.status_code == 302
        assert "_auth_user_id" not in client.session
    elif proof_kind == "code":
        assert client.post("/accounts/otp/", {"code": code}).status_code == 200
        assert "_auth_user_id" not in client.session
    else:
        assert not otp.invitation_valid(challenge.pk, secret, consume=True)


@pytest.mark.django_db(transaction=True)
def test_simultaneous_invitation_confirmations_verify_once_without_signing_in(users):
    user = users["med"]
    prepare_verified_user(user)
    challenge = otp.invitation(user, reason="Disposable enrollment invitation")
    token = re.search(r"/accounts/email/invitation/[^/]+/([^/]+)/", mail.outbox[-1].body).group(1)
    barrier = Barrier(2)

    def confirm():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return otp.invitation_valid(challenge.pk, token, consume=True)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: confirm(), range(2)))

    assert sorted(outcomes) == [False, True]
    challenge.refresh_from_db()
    user.refresh_from_db()
    assert challenge.consumed_at is not None
    assert challenge.digest == ""
    assert user.last_login is None


@pytest.mark.django_db
def test_security_revision_changes_only_for_changed_security_fields(users):
    user = users["med"]
    prepare_verified_user(user)
    revision = user.security_revision
    user.save(update_fields=["role"])
    User.objects.filter(pk=user.pk).update(role=user.role, office_id=user.office_id)
    user.first_name = "Updated contact"
    user.save(update_fields=["first_name"])
    User.objects.filter(pk=user.pk).update(phone="1234")
    user.refresh_from_db()
    assert user.security_revision == revision
    User.objects.filter(pk=user.pk).update(is_staff=True)
    user.refresh_from_db()
    assert user.security_revision == revision + 1


@pytest.mark.django_db
def test_stale_full_metadata_save_cannot_reset_security_revision(users):
    user = users["med"]
    revision = user.security_revision
    User.objects.filter(pk=user.pk).update(role="VIEWER")
    User.objects.filter(pk=user.pk).update(role="USER")
    user.first_name = "Older personal form"
    user.save()
    user.refresh_from_db()
    assert user.security_revision == revision + 2
    assert user.first_name == "Older personal form"


@pytest.mark.django_db
def test_bulk_account_update_tracks_changed_rows_and_retains_update_count(users):
    people = [users['med'], users['sup']]
    original_revisions = {person.pk: person.security_revision for person in people}
    people[0].is_staff = True
    # An unchanged object in the same batch must not lose its OTP proof merely
    # because another account's administrator setting changed.
    assert User.objects.bulk_update(people, ['is_staff'], batch_size=1) == 2
    people[0].refresh_from_db()
    people[1].refresh_from_db()
    assert people[0].security_revision == original_revisions[people[0].pk] + 1
    assert people[1].security_revision == original_revisions[people[1].pk]


@pytest.mark.django_db
def test_reconstructed_existing_account_cannot_reset_security_revision(users):
    user = users['med']
    User.objects.filter(pk=user.pk).update(is_staff=True)
    User.objects.filter(pk=user.pk).update(is_staff=False)
    user.refresh_from_db()
    revision = user.security_revision
    reconstructed = User(**{field.attname: getattr(user, field.attname) for field in User._meta.concrete_fields})
    reconstructed.security_revision = 0
    reconstructed.first_name = 'Imported contact correction'
    reconstructed.save()
    user.refresh_from_db()
    assert user.security_revision == revision
    assert user.first_name == 'Imported contact correction'


@pytest.mark.django_db(transaction=True)
def test_simultaneous_different_security_changes_do_not_lose_revision_increments(users):
    user = users["med"]
    revision = user.security_revision
    barrier = Barrier(2)

    def update(field):
        close_old_connections()
        try:
            current = User.objects.get(pk=user.pk)
            setattr(current, field, True)
            barrier.wait(timeout=10)
            current.save(update_fields=[field])
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(update, ["is_staff", "is_superuser"]))
    user.refresh_from_db()
    assert user.security_revision == revision + 2
    assert user.is_staff and user.is_superuser
def test_save_accepts_single_pass_update_fields(users):
    user = users['med']
    revision = user.security_revision
    user.position = 'Office records coordinator'
    user.save(update_fields=iter(('position',)))
    user.refresh_from_db()
    assert user.position == 'Office records coordinator'
    assert user.security_revision == revision

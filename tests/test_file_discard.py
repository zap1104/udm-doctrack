"""Persisted draft-file removal never grants active-custody deletion rights."""
import importlib

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.core.models import FileLifecycleEvent, StorageCleanupIntent
from apps.tracking import external_services, services
from apps.tracking.models import Status

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def storage(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def discard_service():
    assert importlib.util.find_spec("apps.core.file_lifecycle"), "Draft files need an audited discard service"
    return importlib.import_module("apps.core.file_lifecycle").discard_draft_group


def draft_file(users, external=False):
    actor = users["med_admin" if external else "med"]
    uploaded = SimpleUploadedFile("proof.txt", b"Draft evidence", "text/plain")
    if external:
        record = external_services.create_record(user=actor, title="External draft", from_office=actor.office)
        attached = external_services.add_attachment(record, user=actor, uploaded_file=uploaded, expected_revision=record.revision)
    else:
        record = services.create_draft_record(user=actor, subject="Draft evidence", instructions="Review")
        attached = services.attach_files(record, [uploaded], user=actor)[0]
    return actor, record, attached


@pytest.mark.parametrize("external", [False, True])
def test_unsent_draft_group_discard_keeps_audit_and_does_not_start_work(users, external):
    discard = discard_service()
    actor, record, attached = draft_file(users, external)
    before = list(record.activities.values())
    kwargs = {"expected_revision": record.revision} if external else {}
    event = discard(record, attached.file_group_id, user=actor, reason="Accidental upload", expected_version=1, **kwargs)
    record.refresh_from_db()
    attached.refresh_from_db()
    attached.file_group.refresh_from_db()
    assert attached.file_group.discarded_at and not attached.available_content
    assert record.status == Status.DRAFT and list(record.activities.values()) == before
    assert event.action == "discarded" and FileLifecycleEvent.objects.count() >= 1 and StorageCleanupIntent.objects.count() == 1
    assert attached.file.storage.exists(attached.file.name)


@pytest.mark.parametrize("role", ["viewer", "sup", "hr", "med_admin"])
def test_draft_discard_uses_existing_draft_edit_visibility(users, role):
    discard = discard_service()
    actor, record, attached = draft_file(users)
    with pytest.raises((PermissionDenied, ValidationError)):
        discard(record, attached.file_group_id, user=users[role], reason="Unauthorized", expected_version=1)
    assert not FileLifecycleEvent.objects.exists()


def test_after_dispatch_and_even_corrupt_draft_status_cannot_discard(users, offices):
    discard = discard_service()
    actor, record, attached = draft_file(users)
    services.route_record(record, [offices["SUP"]], user=actor)
    type(record).objects.filter(pk=record.pk).update(status=Status.DRAFT)
    with pytest.raises(ValidationError, match="dispatch|sent|endors"):
        discard(record, attached.file_group_id, user=users["admin"], reason="Too late", expected_version=1)
    assert not FileLifecycleEvent.objects.exists()


def test_discard_requires_reason_current_version_and_current_actor(users):
    discard = discard_service()
    actor, record, attached = draft_file(users)
    for reason, expected in [("", 1), ("x" * 2001, 1), ("Changed file", 2)]:
        with pytest.raises(ValidationError):
            discard(record, attached.file_group_id, user=actor, reason=reason, expected_version=expected)
    type(actor).objects.filter(pk=actor.pk).update(is_active=False)
    with pytest.raises(PermissionDenied):
        discard(record, attached.file_group_id, user=actor, reason="Old session", expected_version=1)
    assert not FileLifecycleEvent.objects.exists()


@pytest.mark.parametrize("external", [False, True])
def test_discard_requires_confirmed_post_with_reason(client, users, external):
    from django.urls import reverse
    actor, record, attached = draft_file(users, external)
    client.force_login(actor)
    url = reverse("tracking:external_file_discard" if external else "tracking:file_discard", args=[record.pk, attached.file_group_id])
    assert client.get(url).status_code == 200
    payload = {"reason": "Wrong draft file", "expected_version": 1, "expected_revision": getattr(record, "revision", "")}
    assert client.post(url, payload).status_code == 200
    attached.refresh_from_db()
    assert attached.available_content
    assert client.post(url, {**payload, "confirm": "on"}).status_code == 302
    attached.refresh_from_db()
    assert not attached.available_content


def test_group_lifecycle_cannot_be_changed_outside_service(users):
    from django.utils import timezone
    _, _, attached = draft_file(users)
    group = attached.file_group
    with pytest.raises(ValidationError):
        type(group).objects.filter(pk=group.pk).update(discarded_at=timezone.now())
    group.discard_reason = "Untracked change"
    with pytest.raises(ValidationError):
        group.save()

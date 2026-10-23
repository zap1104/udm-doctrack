"""Five available versions, permanent audit metadata and no premature deletion."""
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.core.models import FileLifecycleEvent, StorageCleanupIntent
from apps.tracking import external_services, services
from apps.tracking.file_versions import upload_attachment_version, upload_external_attachment_version

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def storage(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def upload(number):
    return SimpleUploadedFile(f"signed-{number}.txt", f"Version {number}".encode(), "text/plain")


def history(users, external, count):
    actor = users["med_admin" if external else "med"]
    if external:
        record = external_services.create_record(user=actor, title="External retention", from_office=actor.office)
        first = external_services.add_attachment(record, user=actor, uploaded_file=upload(1), expected_revision=record.revision)
    else:
        record = services.create_draft_record(user=actor, subject="Internal retention", instructions="Review")
        first = services.attach_files(record, [upload(1)], user=actor)[0]
    versions = [first]
    for number in range(2, count + 1):
        versions.append(next_version(record, first, actor, external, number))
    return record, actor, versions


def next_version(record, first, actor, external, number, expected=None):
    kwargs = dict(user=actor, expected_version=number - 1 if expected is None else expected, change_summary=f"Signed copy {number}")
    if external:
        kwargs["expected_revision"] = record.revision
    return (upload_external_attachment_version if external else upload_attachment_version)(record, first.file_group_id, upload(number), **kwargs)


def test_external_retirement_audits_follow_confirmed_internal_custody(users, offices):
    record, actor, versions = history(users, True, 5)
    external_services.route_record(record, user=actor, expected_revision=record.revision, to_office=offices["SUP"])
    external_services.confirm_receipt(record, user=users["sup"], expected_revision=record.revision)
    next_version(record, versions[0], users["sup"], True, 6)
    assert list(FileLifecycleEvent.objects.values_list("office_id_snapshot", flat=True)) == [offices["SUP"].pk]
    assert list(StorageCleanupIntent.objects.values_list("office_id_snapshot", flat=True)) == [offices["SUP"].pk]


@pytest.mark.parametrize("external", [False, True])
def test_sixth_and_seventh_retire_exact_oldest_versions_without_renumbering(users, external):
    record, actor, versions = history(users, external, 5)
    before = [(row.pk, row.file.name, row.checksum, row.created_at) for row in versions]
    sixth = next_version(record, versions[0], actor, external, 6)
    retained = list(record.attachments.filter(retired_at__isnull=True).order_by("version_number").values_list("pk", flat=True))
    assert retained == [row.pk for row in versions[1:]] + [sixth.pk], "V6 must retire only V1"
    seventh = next_version(record, versions[0], actor, external, 7)
    assert list(record.attachments.filter(retired_at__isnull=True).order_by("version_number").values_list("version_number", flat=True)) == [3, 4, 5, 6, 7]
    assert record.file_groups.count() == 1 and record.attachments.count() == 7
    for version, original in zip(versions, before, strict=True):
        version.refresh_from_db()
        assert (version.pk, version.file.name, version.checksum, version.created_at) == original
        assert version.file.storage.exists(version.file.name), "Physical cleanup must wait for committed intent processing"
    assert FileLifecycleEvent.objects.filter(action="retired").count() == StorageCleanupIntent.objects.count() == 2
    assert seventh.version_number == 7


@pytest.mark.parametrize("external", [False, True])
def test_identical_stale_and_failed_new_versions_retire_nothing(users, external):
    record, actor, versions = history(users, external, 5)
    first = versions[0]
    with pytest.raises(ValidationError):
        next_version(record, first, actor, external, 5, expected=5)
    with pytest.raises(ValidationError):
        next_version(record, first, actor, external, 6, expected=4)
    model = type(first)
    original = model.save
    def fail(instance, *args, **kwargs):
        original(instance, *args, **kwargs)
        raise OSError("simulated write failure after insert")
    with patch.object(model, "save", fail), pytest.raises(OSError):
        next_version(record, first, actor, external, 6)
    assert record.attachments.count() == 5 and not record.attachments.filter(retired_at__isnull=False).exists()
    assert not FileLifecycleEvent.objects.exists() and not StorageCleanupIntent.objects.exists()


@pytest.mark.parametrize("external", [False, True])
def test_old_signed_and_legacy_links_cannot_bypass_retirement(client, users, external):
    from apps.core.file_access import file_url
    from apps.core.utils import sign_download
    record, actor, versions = history(users, external, 6)
    old = versions[0]
    kind = "external" if external else "tracking"
    client.force_login(actor)
    for purpose in ("preview", "content", "download"):
        response = client.get(file_url(kind, old.pk, actor, purpose))
        assert response.status_code == 410 and response["Cache-Control"] == "private, no-store"
    prefix = "external-attachment" if external else "attachment"
    from django.urls import reverse
    legacy = reverse("tracking:external_attachment" if external else "tracking:attachment_download", args=[old.pk])
    response = client.get(legacy, {"token": sign_download(f"{prefix}:{old.pk}:{actor.pk}")})
    assert response.status_code == 410
    client.force_login(users["sup"])
    assert client.get(file_url(kind, old.pk, users["sup"], "download")).status_code in {403, 404}

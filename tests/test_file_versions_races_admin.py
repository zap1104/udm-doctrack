"""Final integration coverage for filing races and raw-admin content guards."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, transaction
from django.urls import reverse

from apps.core.permissions import active_writer
from apps.documents.models import Document, DocumentFile, DocumentFileVersion
from apps.documents.services import archive_tracking_record, ingest_upload
from apps.tracking.file_versions import upload_attachment_version
from apps.tracking.models import Attachment, Status, TrackingRecord
from apps.tracking.services import attach_files, complete_record, confirm_receipt, create_draft_record, route_record

pytestmark = pytest.mark.django_db


def upload(payload=b"First stored version", name="proof.txt"):
    return SimpleUploadedFile(name, payload, "text/plain")


@pytest.fixture(autouse=True)
def private_storage(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_BACKGROUND_TASKS = False


@pytest.mark.django_db(transaction=True)
def test_simultaneous_upload_and_atomic_complete_filing_keep_exact_final_history(users, offices, tmp_path):
    record = create_draft_record(user=users["sup"], subject="Filing race", instructions="Review")
    route_record(record, [offices["MED"]], user=users["sup"])
    confirm_receipt(record, user=users["med"])
    first = attach_files(record, [upload()], user=users["med"])[0]
    gate = Barrier(3)
    actor_locked, record_locked = Event(), Event()

    def upload_worker():
        close_old_connections()
        try:
            gate.wait(timeout=20)
            with transaction.atomic():
                # Hold the actor exactly as a mutation service does. Filing can
                # lock the record before this request reaches its record lock.
                active_writer(users["med"])
                actor_locked.set()
                assert record_locked.wait(20)
                try:
                    version = upload_attachment_version(TrackingRecord.objects.get(pk=record.pk), first.file_group_id,
                        upload(b"Current race version"), user=users["med"], expected_version=1, change_summary="Race update")
                    return version.pk
                except (PermissionDenied, ValidationError):
                    assert TrackingRecord.objects.get(pk=record.pk).status == Status.COMPLETED
                    return None
        finally:
            close_old_connections()

    def filing_worker():
        close_old_connections()
        try:
            gate.wait(timeout=20)
            assert actor_locked.wait(20)
            with transaction.atomic():
                current = TrackingRecord.objects.get(pk=record.pk)
                complete_record(current, user=users["med_admin"])
                record_locked.set()
                document = archive_tracking_record(current, user=users["med_admin"])
                return document.pk
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        uploaded, filed = pool.submit(upload_worker), pool.submit(filing_worker)
        gate.wait(timeout=20)
        upload_id, document_id = uploaded.result(timeout=40), filed.result(timeout=40)

    record.refresh_from_db()
    assert record.status == Status.COMPLETED
    assert record.completed_by_id == users["med_admin"].pk
    assert Document.objects.filter(tracking_record=record).count() == 1
    document = Document.objects.get(pk=document_id)
    assert document.office_id == offices["SUP"].pk
    assert document.files.count() == 1
    root = document.files.get()
    sources = list(record.attachments.order_by("version_number"))
    expected_count = 2 if upload_id else 1
    assert [source.version_number for source in sources] == list(range(1, expected_count + 1))
    assert root.version_number == expected_count
    assert root.versions.count() == expected_count
    assert root.versions.last().file.name == root.file.name
    assert root.checksum == sources[-1].checksum
    assert ("Current race version" in document.ocr_text) == bool(upload_id)
    for source, snapshot in zip(sources, root.versions.all(), strict=True):
        assert snapshot.version_number == source.version_number
        assert snapshot.uploaded_by_id == source.uploaded_by_id
        assert snapshot.uploaded_at == source.created_at
        with snapshot.file.open("rb") as stream:
            assert hashlib.sha256(stream.read()).hexdigest() == source.checksum
    if upload_id:
        assert sources[-1].pk == upload_id
        assert sources[-1].uploaded_by_id == users["med"].pk
    expected_keys = set(Attachment.objects.values_list("file", flat=True))
    expected_keys.update(DocumentFile.objects.values_list("file", flat=True))
    expected_keys.update(DocumentFileVersion.objects.values_list("file", flat=True))
    stored_keys = {path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file()}
    assert stored_keys == expected_keys


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("change", ["role", "office", "suspended"])
def test_actor_lock_still_serializes_permission_office_and_suspension_updates(users, offices, change):
    actor_locked, update_started, update_finished, release_actor = Event(), Event(), Event(), Event()
    changes = {"role": {"role": "VIEWER"}, "office": {"office_id": offices["SUP"].pk}, "suspended": {"is_active": False}}[change]

    def hold_actor():
        close_old_connections()
        try:
            with transaction.atomic():
                active_writer(users["med"])
                actor_locked.set()
                assert release_actor.wait(20)
        finally:
            close_old_connections()

    def change_actor():
        close_old_connections()
        try:
            assert actor_locked.wait(20)
            update_started.set()
            type(users["med"]).objects.filter(pk=users["med"].pk).update(**changes)
            update_finished.set()
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        holder, updater = pool.submit(hold_actor), pool.submit(change_actor)
        try:
            assert update_started.wait(20)
            assert not update_finished.wait(0.2), "Permission changes must wait for the active mutation"
        finally:
            release_actor.set()
        holder.result(timeout=30)
        updater.result(timeout=30)
    with transaction.atomic():
        if change == "office":
            assert active_writer(users["med"]).office_id == offices["SUP"].pk
        else:
            with pytest.raises(PermissionDenied):
                active_writer(users["med"])


@pytest.fixture
def content_rows(users):
    record = create_draft_record(user=users["med"], subject="Admin content guards", instructions="Review")
    attachment = attach_files(record, [upload()], user=users["med"])[0]
    document, _ = ingest_upload(user=users["med"], uploaded_file=upload())
    root = document.files.get()
    return document, {"attachment": attachment, "documentfile": root, "documentfileversion": root.versions.get()}


@pytest.mark.parametrize("kind", ["attachment", "documentfile", "documentfileversion"])
@pytest.mark.parametrize("operation", ["add", "change", "delete"])
def test_crafted_django_admin_content_posts_are_denied_to_system_admin(client, users, content_rows, tmp_path, kind, operation):
    document, rows = content_rows
    row = rows[kind]
    app = "tracking" if kind == "attachment" else "documents"
    route = f"admin:{app}_{kind}_{operation}"
    args = [] if operation == "add" else [row.pk]
    before = {model: list(model.objects.order_by("pk").values()) for model in [Attachment, DocumentFile, DocumentFileVersion]}
    before_files = {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()}
    client.force_login(users["admin"])
    payload = {"original_name": "overwrite.txt", "file": upload(b"Injected replacement", "overwrite.txt"),
        "record": rows["attachment"].record_id, "document": document.pk, "document_file": rows["documentfile"].pk,
        "version_number": 99, "change_summary": "Bypass attempt", "post": "yes", "_save": "Save"}
    response = client.post(reverse(route, args=args), payload)
    assert response.status_code == 403
    for model, values in before.items():
        assert list(model.objects.order_by("pk").values()) == values
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()} == before_files
    metadata = client.get(reverse("admin:documents_document_change", args=[document.pk]))
    assert metadata.status_code == 200
    assert "title" in metadata.context["adminform"].form.fields


def test_document_admin_metadata_post_keeps_file_history_unchanged(client, users, content_rows):
    document, rows = content_rows
    client.force_login(users["admin"])
    url = reverse("admin:documents_document_change", args=[document.pk])
    response = client.get(url)
    assert response.status_code == 200
    form = response.context["adminform"].form
    data = {name: form[name].value() for name in form.fields if form[name].value() is not None and form[name].value() is not False}
    data["title"] = "Corrected administration metadata"
    data["_save"] = "Save"
    for inline in response.context["inline_admin_formsets"]:
        formset = inline.formset
        for name in formset.management_form.fields:
            data[f"{formset.prefix}-{name}"] = formset.management_form[name].value()
        for inline_form in formset.forms:
            for name in inline_form.fields:
                value = inline_form[name].value()
                if value is not None:
                    data[f"{inline_form.prefix}-{name}"] = value
    before = list(DocumentFileVersion.objects.order_by("pk").values())
    assert client.post(url, data).status_code == 302
    document.refresh_from_db()
    assert document.title == "Corrected administration metadata"
    assert list(DocumentFileVersion.objects.order_by("pk").values()) == before
    rows["documentfile"].refresh_from_db()
    assert rows["documentfile"].version_number == 1

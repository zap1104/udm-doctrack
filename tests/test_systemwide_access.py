"""Object visibility and private download regressions from the systemwide audit."""

import html
import re
from urllib.parse import urlencode

import pytest
from django.core.files.base import ContentFile
from django.urls import reverse

from apps.core.models import Notification, NotificationRead
from apps.core.notifications import unread_count
from apps.core.utils import sign_download
from apps.documents.models import AccessLevel, Document, DocumentAccessGrant, DocumentFile
from apps.tracking.models import Attachment, RecordAccessGrant, Status, TrackingRecord

pytestmark = pytest.mark.django_db


def _record(users, offices, *, owner="sup", status=Status.IN_PROCESS, number="AUDIT-PRIVATE"):
    return TrackingRecord.objects.create(
        tracking_number=number, subject="Private audit subject", instructions="",
        originating_office=users[owner].office, current_office=users[owner].office,
        created_by=users[owner], status=status,
    )


@pytest.fixture
def notification_cases(users, offices):
    private_record = _record(users, offices, owner="med_admin", status=Status.DRAFT)
    private_document = Document.objects.create(
        title="Restricted audit title", office=offices["MED"], year=2026,
        access_level=AccessLevel.RESTRICTED, uploaded_by=users["med_admin"],
    )
    common = {"office": offices["MED"], "kind": Notification.Kind.SHARED, "message": "Audit event"}
    visible = Notification.objects.create(**common, title="General office update")
    hidden = [
        Notification.objects.create(**common, title="Secret draft notice", tracking_record=private_record),
        Notification.objects.create(**common, title="Secret restricted notice", document=private_document),
    ]
    return visible, hidden


@pytest.mark.parametrize("role", ["med", "viewer"])
@pytest.mark.parametrize("surface", ["query", "list", "badge", "read", "read_all"])
def test_notification_surfaces_hide_objects_user_cannot_read(client, users, notification_cases, role, surface):
    visible, hidden = notification_cases
    actor = users[role]
    client.force_login(actor)
    if surface == "query":
        assert set(Notification.objects.visible_to(actor).values_list("pk", flat=True)) == {visible.pk}
    elif surface == "list":
        response = client.get(reverse("core:notifications"))
        assert response.status_code == 200
        assert {row.pk for row in response.context["notifications"]} == {visible.pk}
        assert b"Secret" not in response.content
    elif surface == "badge":
        assert unread_count(actor) == 1
        response = client.get(reverse("core:notification_count"))
        assert response.status_code == 200
        assert response.context["unread_notifications"] == 1
    elif surface == "read":
        for notification in hidden:
            assert client.post(reverse("core:notification_read", args=[notification.pk])).status_code == 404
        assert not NotificationRead.objects.filter(user=actor).exists()
    else:
        assert client.post(reverse("core:notification_mark_all_read")).status_code == 302
        assert set(NotificationRead.objects.filter(user=actor).values_list("notification_id", flat=True)) == {visible.pk}


@pytest.mark.parametrize("kind", ["record", "document"])
def test_revoked_object_grant_removes_own_office_notification(users, offices, kind):
    actor = users["med"]
    if kind == "record":
        obj = _record(users, offices)
        grant = RecordAccessGrant.objects.create(record=obj, user=actor, granted_by=users["sup"])
        relation = {"tracking_record": obj}
    else:
        obj = Document.objects.create(title="Granted then revoked", office=offices["SUP"], year=2026)
        grant = DocumentAccessGrant.objects.create(document=obj, user=actor, granted_by=users["sup"])
        relation = {"document": obj}
    notification = Notification.objects.create(
        office=offices["MED"], kind=Notification.Kind.SHARED,
        title="Previously granted subject", message="Private event", **relation,
    )
    assert Notification.objects.visible_to(actor).filter(pk=notification.pk).exists()
    grant.delete()
    assert not Notification.objects.visible_to(actor).filter(pk=notification.pk).exists()
    assert unread_count(actor) == 0


@pytest.fixture(params=["attachment", "document-file"])
def private_download(request, users, offices, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    kind = request.param
    if kind == "attachment":
        obj = _record(users, offices, owner="med", number="AUDIT-READABLE")
        file_model, relation, route = Attachment, {"record": obj}, "tracking:attachment_download"
    else:
        obj = Document.objects.create(title="Readable audit file", office=offices["MED"], year=2026)
        file_model, relation, route = DocumentFile, {"document": obj}, "documents:file_download"
    file = file_model.objects.create(
        **relation, file=ContentFile(b"Private audit bytes", name="audit.txt"), original_name="audit.txt",
    )
    other_file = file_model.objects.create(
        **relation, file=ContentFile(b"Other private bytes", name="other.txt"), original_name="other.txt",
    )
    endpoint = reverse(route, args=[file.pk])
    token = sign_download(f"{kind}:{file.pk}:{users['med'].pk}")
    return kind, obj, file, other_file, endpoint, token


@pytest.mark.parametrize("invalid", ["missing", "tampered", "other_user", "other_file", "other_kind", "expired"])
def test_internal_downloads_require_current_user_object_signature(client, users, private_download, monkeypatch, settings, invalid):
    kind, _obj, file, other_file, endpoint, token = private_download
    client.force_login(users["med"])
    if invalid == "missing":
        token = ""
    elif invalid == "tampered":
        token += "invalid"
    elif invalid == "other_user":
        token = sign_download(f"{kind}:{file.pk}:{users['viewer'].pk}")
    elif invalid == "other_file":
        token = sign_download(f"{kind}:{other_file.pk}:{users['med'].pk}")
    elif invalid == "other_kind":
        token = sign_download(f"external-attachment:{file.pk}:{users['med'].pk}")
    else:
        from django.core import signing
        expired_at = int(signing.time.time()) - settings.SIGNED_URL_TTL_SECONDS - 2
        with monkeypatch.context() as patch:
            patch.setattr(signing.TimestampSigner, "timestamp", lambda self: signing.b62_encode(expired_at))
            token = sign_download(f"{kind}:{file.pk}:{users['med'].pk}")
    response = client.get(endpoint, {"token": token})
    assert response.status_code == 403


def test_internal_download_valid_signature_serves_private_file(client, users, private_download):
    _kind, _obj, _file, _other_file, endpoint, token = private_download
    client.force_login(users["med"])
    response = client.get(endpoint, {"token": token})
    assert response.status_code == 200
    assert response["Cache-Control"] == "private, no-store"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert b"".join(response.streaming_content) == b"Private audit bytes"


@pytest.mark.parametrize("role", ["med", "viewer", "med_admin", "admin"])
def test_rendered_private_download_links_work(client, users, private_download, role):
    _kind, obj, _file, _other_file, _endpoint, _token = private_download
    client.force_login(users[role])
    detail = client.get(obj.get_absolute_url())
    assert detail.status_code == 200
    urls = [html.unescape(url) for url in re.findall(r'href="([^"]+)"', detail.content.decode())
            if "/attachment/" in url or "/documents/file/" in url]
    assert urls
    for url in urls:
        assert "token=" in url
        response = client.get(url)
        assert response.status_code == 200
        assert b"".join(response.streaming_content)


def test_signed_download_rechecks_revoked_access(client, users, offices, private_download):
    kind, obj, file, _other_file, endpoint, _token = private_download
    actor = users["sup"]
    if kind == "attachment":
        grant = RecordAccessGrant.objects.create(record=obj, user=actor, granted_by=users["med"])
    else:
        grant = DocumentAccessGrant.objects.create(document=obj, user=actor, granted_by=users["med"])
    token = sign_download(f"{kind}:{file.pk}:{actor.pk}")
    url = endpoint + "?" + urlencode({"token": token})
    client.force_login(actor)
    response = client.get(url)
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b"Private audit bytes"
    grant.delete()
    assert client.get(url).status_code in {403, 404}


def test_signed_download_missing_file_returns_404(client, users, private_download):
    _kind, _obj, file, _other_file, endpoint, token = private_download
    file.file.delete(save=False)
    client.force_login(users["med"])
    assert client.get(endpoint, {"token": token}).status_code == 404


def test_deletion_review_emits_working_signed_file_links(client, users, offices, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    document = Document.objects.create(title="Deletion review evidence", office=offices["MED"], year=2026)
    file = DocumentFile.objects.create(
        document=document, file=ContentFile(b"Review evidence", name="review.txt"), original_name="review.txt",
    )
    client.force_login(users["med_admin"])
    review = client.get(reverse("documents:deletion_review", args=[document.pk]))
    assert review.status_code == 200
    endpoint = reverse("documents:file_download", args=[file.pk])
    match = re.search(r'href="(' + re.escape(endpoint) + r'\?[^"]+)"', review.content.decode())
    assert match is not None
    response = client.get(html.unescape(match.group(1)))
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b"Review evidence"

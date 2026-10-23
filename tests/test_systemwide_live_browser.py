"""Real Django/CSRF/HTMX/browser audit, opt-in because it needs Chromium/Node."""

import json
import os
import subprocess
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.documents.models import AccessLevel, Collection, Document, DocumentFile
from apps.tracking import external_services
from apps.tracking.models import ExternalTrackingRecord, Status, TrackingRecord
from apps.tracking.services import add_remark, confirm_receipt, create_draft_record, route_record

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.skipif(
    os.environ.get("RUN_LIVE_BROWSER_AUDIT") != "1", reason="Opt-in live Chromium audit; run documented command.",
)]


def test_real_browser_filters_workflows_and_responsive_views(live_server, users, offices, settings, tmp_path, memo_type, tag_urgent):
    settings.MEDIA_ROOT = tmp_path / "files"
    settings.ENABLE_BACKGROUND_TASKS = False
    settings.SECURE_SSL_REDIRECT = False
    settings.SESSION_COOKIE_SECURE = False
    settings.CSRF_COOKIE_SECURE = False
    settings.CSRF_TRUSTED_ORIGINS = [live_server.url]
    settings.ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]
    seeded = {}
    for name in ("Pending", "Received", "Processing"):
        record = create_draft_record(user=users["sup"], subject=f"Browser {name}", instructions="Review this paper", document_type=memo_type)
        route_record(record, [offices["MED"]], user=users["sup"])
        if name != "Pending":
            confirm_receipt(record, user=users["med"])
        if name == "Processing":
            add_remark(record, user=users["med"], remark="Work started")
        seeded[name] = record.pk
    outgoing = create_draft_record(user=users["med"], subject="Browser Outgoing", instructions="Review this paper")
    route_record(outgoing, [offices["SUP"]], user=users["med"])
    for title, office, collection in (
        ("Browser Repository Alpha", "MED", Collection.OVPA),
        ("Browser Repository Beta", "SUP", Collection.OVPA),
        ("Browser External Alpha", "MED", Collection.EXTERNAL),
    ):
        document = Document.objects.create(
            title=title, year=timezone.localdate().year, office=offices[office], uploaded_by=users["med"],
            collection=collection, access_level=AccessLevel.OVPA, ocr_status="SKIPPED",
            document_type=memo_type, document_date=timezone.localdate(),
            allow_external_ocr=False,
        )
        document.document_types.add(memo_type)
        document.tags.add(tag_urgent)
        DocumentFile.objects.create(
            document=document, file=SimpleUploadedFile("evidence.txt", b"Private browser audit file"),
            original_name="evidence.txt",
        )
        document.rebuild_index()
    # Enough visible rows to exercise real pagination, with usable type/tag/date controls.
    for index in range(27):
        document = Document.objects.create(
            title=f"Browser Page {index:02d}", year=timezone.localdate().year,
            office=offices["MED"], uploaded_by=users["med"], collection=Collection.OVPA,
            access_level=AccessLevel.OVPA, ocr_status="SKIPPED", allow_external_ocr=False,
            document_type=memo_type, document_date=timezone.localdate(),
        )
        document.document_types.add(memo_type)
        document.tags.add(tag_urgent)
        document.rebuild_index()
    counterpart = external_services.create_external_office(user=users["med_admin"], name="Browser Registrar", code="BR")
    external = external_services.create_record(
        user=users["med_admin"], title="Browser external journey", from_external_office=counterpart,
    )
    external_services.add_attachment(
        external, user=users["med_admin"], expected_revision=external.revision,
        uploaded_file=SimpleUploadedFile("evidence.txt", b"Private browser audit file"),
    )
    manifest = {
        "origin": live_server.url, "root": str(settings.BASE_DIR), "output": str(tmp_path / "browser-report.json"),
        "offices": {key: value.pk for key, value in offices.items()}, "seeded": seeded,
        "today": timezone.localdate().isoformat(), "sessionCookie": settings.SESSION_COOKIE_NAME,
        "external": external.pk, "externalDraftUrl": external.get_absolute_url(),
        "past": timezone.localtime(timezone.now() - timezone.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"),
    }
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
    node = os.environ.get("AUDIT_NODE", "node")
    completed = subprocess.run(
        [node, str(Path(settings.BASE_DIR) / "scripts/check_systemwide_live.cjs"), str(manifest_file)],
        cwd=settings.BASE_DIR, env=os.environ.copy(), capture_output=True, text=True, timeout=1200,
    )
    evidence = Path(os.environ.get("LIVE_BROWSER_EVIDENCE_DIR", str(tmp_path / "evidence")))
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "live-browser-output.txt").write_text(completed.stdout + completed.stderr, encoding="utf-8")
    report_path = Path(manifest["output"])
    if report_path.exists():
        (evidence / "live-browser.json").write_bytes(report_path.read_bytes())
    assert completed.returncode == 0, completed.stdout + completed.stderr
    created = TrackingRecord.objects.get(subject="Browser created and filed")
    assert created.status == Status.COMPLETED
    assert Document.objects.filter(tracking_record=created, collection=Collection.OVPA).count() == 1
    external.refresh_from_db()
    assert external.status == Status.COMPLETED
    assert Document.objects.filter(external_tracking_record=external, collection=Collection.EXTERNAL).count() == 1
    assert ExternalTrackingRecord.objects.filter(pk=external.pk).count() == 1

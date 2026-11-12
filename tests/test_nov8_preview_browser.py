"""Opt-in, real OTP/CSRF previews for current and historical uploaded bytes."""

import hashlib
import io
import json
import os
import re
import subprocess
import zipfile
from pathlib import Path
from threading import Timer

import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from docx import Document as WordDocument
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from openpyxl import Workbook
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from apps.core.file_access import file_url
from apps.documents.external_services import archive_external_record
from apps.documents.file_versions import DocumentFileVersion
from apps.tracking import external_services as external
from apps.tracking import services as internal
from apps.tracking.file_versions import upload_attachment_version, upload_external_attachment_version
from apps.tracking.models import Status
from tests.auth_helpers import prepare_verified_user


def preview_payload(extension, version):
    marker = f"Preview evidence version {version}"
    output = io.BytesIO()
    if extension == "pdf":
        writer = PdfWriter()
        for number in (1, 2):
            page = writer.add_blank_page(width=300, height=400)
            font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
            page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({
                NameObject("/F1"): writer._add_object(font),
            })})
            stream = DecodedStreamObject()
            stream.set_data(f"BT /F1 12 Tf 20 350 Td ({marker} page {number}) Tj ET".encode())
            page[NameObject("/Contents")] = writer._add_object(stream)
        writer.add_js("window.__previewExploit = true; app.alert('Hostile preview action');")
        writer.write(output)
    elif extension == "docx":
        document = WordDocument()
        document.add_paragraph(marker)
        document.add_table(rows=1, cols=1).cell(0, 0).text = "Formatted table evidence"
        picture = io.BytesIO()
        Image.new("RGB", (32, 24), color=(20, 100 + version, 40)).save(picture, format="PNG")
        picture.seek(0)
        document.add_picture(picture)
        relation = document.part.relate_to("https://preview-hostile.invalid/external", RT.HYPERLINK, is_external=True)
        link = OxmlElement("w:hyperlink")
        link.set(qn("r:id"), relation)
        run, text = OxmlElement("w:r"), OxmlElement("w:t")
        text.text = "Untrusted external link"
        run.append(text)
        link.append(run)
        document.paragraphs[0]._p.append(link)
        document.save(output)
    elif extension in {"jpg", "png"}:
        Image.new("RGB", (64, 48), color=(30, 100 + version, 60)).save(
            output, format="JPEG" if extension == "jpg" else "PNG")
    elif extension == "xlsx":
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Stored values"
        sheet.append([marker, '<img src="https://preview-hostile.invalid/image" onerror="window.__previewExploit=true">'])
        sheet.append(["=1+1", '=HYPERLINK("https://preview-hostile.invalid/formula","Click")'])
        for number in range(205):
            sheet.append([f"Bounded row {number}", number])
        workbook.create_sheet("Second sheet").append(["Second worksheet evidence"])
        workbook.save(output)
    elif extension == "csv":
        return (marker + ',"<img src=x onerror=window.__previewExploit=true>"\n'
                + "row,value\n" * 205).encode()
    elif extension == "txt":
        return (marker + '\n<img src=x onerror="window.__previewExploit=true">\n' + "x" * 200_001).encode()
    return output.getvalue()


def broken_docx():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<broken")
    return output.getvalue()


@pytest.mark.skipif(os.environ.get("RUN_PREVIEW_BROWSER_AUDIT") != "1", reason="Opt-in full-format browser preview audit")
@pytest.mark.django_db(transaction=True)
def test_real_browser_previews_current_and_history_in_all_four_areas(live_server, users, offices, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.SECURE_SSL_REDIRECT = False
    settings.SESSION_COOKIE_SECURE = False
    settings.CSRF_COOKIE_SECURE = False
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.EMAIL_CONFIGURED = settings.EMAIL_OTP_REQUIRED = True
    settings.SIGNED_URL_TTL_SECONDS = 3600  # Large format matrix; expiry is tested separately.
    for user in users.values():
        prepare_verified_user(user)
    record = internal.create_draft_record(user=users["med"], subject="Preview browser source", instructions="Review")
    outside = external.create_external_office(user=users["med_admin"], name="Preview Registrar")
    external_record = external.create_record(user=users["med_admin"], title="External preview source", from_external_office=outside)
    originals, cases = {}, []

    def remember(item, kind, area, extension, marker, *, bad=False):
        with item.file.open("rb") as stream:
            digest = hashlib.sha256(stream.read()).hexdigest()
        originals[kind, item.pk] = (item, digest)
        cases.append({"area": area, "kind": kind, "format": extension, "version": item.version_number,
            "marker": marker, "bad": bad, "preview": file_url(kind, item.pk, users["admin"]), "sha256": digest})

    for extension in ("pdf", "docx", "jpg", "png", "txt", "csv", "xlsx"):
        first, second = preview_payload(extension, 1), preview_payload(extension, 2)
        initial = internal.attach_files(record, [SimpleUploadedFile(f"evidence.{extension}", first)], user=users["med"])[0]
        latest = upload_attachment_version(record, initial.file_group_id, SimpleUploadedFile(f"evidence.{extension}", second),
            user=users["med"], expected_version=1, change_summary="Added signed copy evidence")
        remember(initial, "tracking", "tracking", extension, "Preview evidence version 1")
        remember(latest, "tracking", "tracking", extension, "Preview evidence version 2")
        initial = external.add_attachment(external_record, user=users["med_admin"], expected_revision=external_record.revision,
            uploaded_file=SimpleUploadedFile(f"evidence.{extension}", first))
        latest = upload_external_attachment_version(external_record, initial.file_group_id, SimpleUploadedFile(f"evidence.{extension}", second),
            user=users["med_admin"], expected_revision=external_record.revision, expected_version=1,
            change_summary="Added signed copy evidence")
        remember(initial, "external", "external_tracking", extension, "Preview evidence version 1")
        remember(latest, "external", "external_tracking", extension, "Preview evidence version 2")
    bad = internal.attach_files(record, [SimpleUploadedFile("malformed.docx", broken_docx())], user=users["med"])[0]
    remember(bad, "tracking", "tracking", "docx", "", bad=True)
    bad_pdf = internal.attach_files(record, [SimpleUploadedFile("malformed.pdf", b"%PDF-1.4\nInvalid preview")], user=users["med"])[0]
    remember(bad_pdf, "tracking", "tracking", "pdf", "", bad=True)
    internal.route_record(record, [offices["SUP"]], user=users["med"])
    internal.confirm_receipt(record, user=users["sup"])
    internal.complete_record(record, user=users["sup"])
    document = internal.approve_upload(record, user=users["admin"])
    external.route_record(external_record, user=users["med_admin"], expected_revision=external_record.revision, to_office=offices["MED"])
    external.confirm_receipt(external_record, user=users["med"], expected_revision=external_record.revision)
    external.complete_record(external_record, user=users["med"], expected_revision=external_record.revision)
    external_document = archive_external_record(external_record, user=users["admin"], expected_revision=external_record.revision)
    for parent, area in ((document, "repository"), (external_document, "external_repository")):
        for item in DocumentFileVersion.objects.filter(document_file__document=parent).select_related("document_file"):
            extension = item.original_name.rsplit(".", 1)[-1]
            remember(item, "repository", area, extension, f"Preview evidence version {item.version_number}",
                     bad=item.original_name.startswith("malformed."))
    document_states = {}
    for parent in (document, external_document):
        parent.refresh_from_db()
        document_states[parent.pk] = (parent.ocr_status, parent.extraction_generation, parent.ocr_text, parent.page_count)
    external_revision = external_record.revision
    root = Path(__file__).resolve().parents[1]
    output = Path(os.environ["PREVIEW_BROWSER_OUTPUT"])
    output.mkdir(parents=True, exist_ok=True)
    manifest = {"origin": live_server.url, "root": str(root), "output": str(output), "cases": cases}
    manifest_file = output / "manifest.json"
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
    errors = output / "stderr.txt"
    with errors.open("w", encoding="utf-8") as error_stream:
        process = subprocess.Popen(["node", str(root / "scripts/check_nov8_previews.cjs"), str(manifest_file)],
            cwd=root, env=os.environ.copy(), text=True, encoding="utf-8", stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=error_stream)
        watchdog = Timer(1200, process.kill)
        watchdog.start()
        complete = None
        try:
            for line in process.stdout:
                event = json.loads(line)
                if event.get("event") == "code":
                    message = next(message for message in reversed(mail.outbox) if message.to == [users["admin"].email])
                    code = re.search(r"\b(\d{6})\b", message.body).group(1)
                    process.stdin.write(code + "\n")
                    process.stdin.flush()
                elif event.get("event") == "complete":
                    complete = event
            process.wait(timeout=30)
        finally:
            watchdog.cancel()
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
    safe_errors = re.sub(r"\b\d{6}\b", "[test-code-redacted]", errors.read_text(encoding="utf-8"))
    errors.write_text(safe_errors, encoding="utf-8")
    assert process.returncode == 0, safe_errors
    assert complete and complete["previews"] == len(cases)
    for item, digest in originals.values():
        item.refresh_from_db()
        with item.file.open("rb") as stream:
            assert hashlib.sha256(stream.read()).hexdigest() == digest
    record.refresh_from_db()
    external_record.refresh_from_db()
    assert record.status == external_record.status == Status.COMPLETED
    assert external_record.revision == external_revision
    assert document.files.count() == 9 and external_document.files.count() == 7
    for parent in (document, external_document):
        parent.refresh_from_db()
        assert (parent.ocr_status, parent.extraction_generation, parent.ocr_text, parent.page_count) == document_states[parent.pk]

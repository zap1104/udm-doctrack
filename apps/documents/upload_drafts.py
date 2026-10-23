"""Repository ingestion keeps private uploads out of document populations until valid."""
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from apps.accounts.models import Office
from apps.core.filters import link
from apps.core.upload_drafts import UploadResult

from .services import ingest_upload


def finalize_repository_upload(context, data, files, *, user):
    office = Office.objects.get(pk=context.creation_office_id, is_active=True)
    if data.get("office") and data["office"].pk != office.pk:
        raise PermissionDenied("The filing office changed. Open the upload under the intended office view.")
    document, _ = ingest_upload(user=user, uploaded_files=files, office=office, source=data.get("source", "UPLOAD"),
        collection=context.collection, external_offices=data.get("external_offices"),
        ocr_language=data.get("ocr_language", "auto"), allow_external_ocr=bool(data.get("allow_external_ocr", False)),
        defer_extraction=True)
    url = reverse("documents:review", args=[document.pk])
    if context.collection == "EXTERNAL":
        url = link(url, office=context.creation_office_id)
    return UploadResult(context.workflow, "repository", document.pk, url)

"""Django admin metadata edits must serialize with repository workers."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from django.db import OperationalError, connections, transaction
from django.urls import reverse

from apps.documents.admin import DocumentAdmin
from apps.documents.models import Document, OcrStatus


@pytest.mark.django_db(transaction=True)
def test_native_admin_metadata_edit_does_not_erase_concurrent_extraction(client, users, monkeypatch):
    actor = users["admin"]
    document = Document.objects.create(title="Before edit", office=users["med"].office, uploaded_by=actor, year=2026)
    client.force_verified_login(actor)
    url = reverse("admin:documents_document_change", args=[document.pk])
    response = client.get(url)
    assert response.status_code == 200
    form = response.context["adminform"].form
    data = {name: form[name].value() for name in form.fields if form[name].value() is not None and form[name].value() is not False}
    data.update(title="After edit", _save="Save")
    for inline in response.context["inline_admin_formsets"]:
        formset = inline.formset
        for name in formset.management_form.fields:
            data[f"{formset.prefix}-{name}"] = formset.management_form[name].value()

    form_ready, worker_attempted = Event(), Event()
    original_save = DocumentAdmin.save_model

    def paused_save(admin, request, obj, form, change):
        form_ready.set()
        assert worker_attempted.wait(10)
        return original_save(admin, request, obj, form, change)

    def finish_extraction(*, nowait):
        with transaction.atomic():
            current = Document.objects.select_for_update(nowait=nowait).get(pk=document.pk)
            current.extraction_generation = 12
            current.ocr_text = "Text from the current extraction"
            current.ocr_status = OcrStatus.DONE
            current.save(update_fields=["extraction_generation", "ocr_text", "ocr_status"])

    def worker():
        try:
            assert form_ready.wait(10)
            should_retry = False
            try:
                finish_extraction(nowait=True)
            except OperationalError as exc:
                assert getattr(exc.__cause__, "sqlstate", None) == "55P03"
                should_retry = True
            finally:
                worker_attempted.set()
            if should_retry:
                finish_extraction(nowait=False)
        finally:
            connections.close_all()

    monkeypatch.setattr(DocumentAdmin, "save_model", paused_save)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(worker)
        posted = client.post(url, data)
        assert posted.status_code == 302, (
            posted.context["adminform"].form.errors if posted.status_code == 200 else posted.status_code
        )
        pending.result(timeout=15)

    document.refresh_from_db()
    assert document.title == "After edit"
    assert document.extraction_generation == 12
    assert document.ocr_text == "Text from the current extraction"
    assert document.ocr_status == OcrStatus.DONE

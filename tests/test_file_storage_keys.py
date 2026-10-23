"""New immutable binaries must never reuse a previous version's storage key."""
from types import SimpleNamespace

import pytest
from django.utils import timezone

from apps.documents.file_versions import DocumentFileVersion
from apps.documents.models import DocumentFile
from apps.tracking.models import Attachment, ExternalAttachment


@pytest.mark.parametrize("model", [Attachment, ExternalAttachment, DocumentFile, DocumentFileVersion])
def test_new_binary_keys_are_unique_even_after_the_original_key_is_removed(model):
    instance = SimpleNamespace(
        record=SimpleNamespace(created_at=timezone.now(), tracking_number="UDM-MED-2026-0001"),
        record_id=17,
        document=SimpleNamespace(office_id=1, office=SimpleNamespace(code="MED"), year=2026, pk=29),
    )
    field = model._meta.get_field("file")
    first = field.generate_filename(instance, "signed copy.pdf")
    second = field.generate_filename(instance, "signed copy.pdf")
    assert first != second, "Storage cleanup receipts must never apply to a later binary at the same key"
    assert all(key.replace("\\", "/").endswith("/signed_copy.pdf") for key in (first, second))

"""Maintenance admin cannot bypass approval or mutate removed documents."""

from unittest.mock import MagicMock, Mock

import pytest
from django.contrib import admin
from django.test import RequestFactory

from apps.accounts.models import User
from apps.documents.models import Document


@pytest.fixture
def maintenance_request():
    request = RequestFactory().get("/admin/documents/document/")
    request.user = User(is_active=True, is_staff=True, is_superuser=True)
    return request


@pytest.mark.parametrize("active", [True, False])
def test_maintenance_admin_cannot_delete_documents(maintenance_request, active):
    model_admin = admin.site._registry[Document]
    document = Document(is_active=active)
    assert not model_admin.has_delete_permission(maintenance_request, document)
    assert "delete_selected" not in model_admin.get_actions(maintenance_request)


def test_maintenance_admin_can_inspect_but_not_edit_removed_document(maintenance_request):
    model_admin = admin.site._registry[Document]
    assert model_admin.has_view_permission(maintenance_request, Document(is_active=False))
    assert not model_admin.has_change_permission(maintenance_request, Document(is_active=False))
    assert model_admin.has_change_permission(maintenance_request, Document(is_active=True))


def test_readonly_maintenance_form_can_render_without_type_editors(maintenance_request):
    model_admin = admin.site._registry[Document]
    removed = Document(is_active=False)
    form_class = model_admin.get_form(maintenance_request, removed, change=True)
    form = form_class(instance=removed)
    assert "document_type" not in form.fields and "document_types" not in form.fields
    assert form["document_types_submitted"].is_hidden


def test_bulk_reindex_leaves_recoverable_document_unchanged(maintenance_request):
    model_admin = admin.site._registry[Document]
    active, removed = Mock(is_active=True), Mock(is_active=False)
    queryset = MagicMock()
    queryset.__iter__.return_value = iter([active, removed])
    queryset.count.return_value = 2
    queryset.filter.return_value.__iter__.return_value = iter([active])
    queryset.filter.return_value.count.return_value = 1
    with pytest.MonkeyPatch.context() as patcher:
        message = Mock()
        patcher.setattr(model_admin, "message_user", message)
        model_admin.rebuild_search_index(maintenance_request, queryset)
    active.rebuild_index.assert_called_once()
    removed.rebuild_index.assert_not_called()
    message.assert_called_once_with(maintenance_request, "Reindexed 1 document(s).")

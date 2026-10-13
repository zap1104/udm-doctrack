"""Render real deletion-review responses for the offline browser QA helper.

Run after migrating the local development database:
    .venv\\Scripts\\python.exe scripts\\render_deletion_review_qa.py

DJANGO_SETTINGS_MODULE is respected (defaults to config.settings). Every model
row, session, audit entry, and decision created here belongs to a new temporary
office and is rolled back. File fields contain names only: no files are saved,
no storage provider is contacted, and no background jobs or mail are sent.

Output: docs/verification/deletion-review/pages.json. Then run
scripts/check_deletion_review.cjs with Playwright and the pinned vendor assets.
These fixtures verify rendered pages and browser behavior; they do not replace
the application's permission, transaction, or service tests.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import timedelta
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from django.contrib.admin.utils import flatten_fieldsets  # noqa: E402
from django.db import transaction  # noqa: E402
from django.test import Client, override_settings  # noqa: E402
from django.test.utils import setup_test_environment, teardown_test_environment  # noqa: E402
from django.urls import reverse  # noqa: E402
from django.utils import timezone  # noqa: E402
from django.utils.formats import date_format  # noqa: E402

from apps.accounts.models import Office, User  # noqa: E402
from apps.documents.models import DeletionReview, Document, DocumentFile, OcrStatus  # noqa: E402

OUTPUT = ROOT / "docs" / "verification" / "deletion-review" / "pages.json"


class HttpsClient(Client):
    """Reach the views even when SECURE_SSL_REDIRECT is enabled locally."""

    def generic(self, method, path, *args, **kwargs):
        kwargs["secure"] = True
        kwargs["HTTP_HOST"] = "review.test"
        return super().generic(method, path, *args, **kwargs)


def capture(response, *, url, method="GET", expected=200):
    if response.status_code != expected:
        raise RuntimeError(f"{method} {url}: expected {expected}, got {response.status_code}")
    return {
        "url": url,
        "method": method,
        "status": response.status_code,
        "headers": dict(response.items()),
        "html": response.content.decode(response.charset or "utf-8"),
    }


def error_response(client, url, payload, expected_field):
    response = client.post(url, payload)
    if not response.context or expected_field not in response.context["form"].errors:
        raise RuntimeError(f"POST {url}: expected a {expected_field} validation error")
    return capture(response, url=url, method="POST")


class VisibleNamedControls(HTMLParser):
    """Find rendered editing controls without confusing hidden formset IDs."""

    def __init__(self):
        super().__init__()
        self.names = set()

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag not in {"input", "select", "textarea", "button"}:
            return
        if tag == "input" and attributes.get("type", "text").lower() == "hidden":
            return
        if attributes.get("name"):
            self.names.add(attributes["name"])


def document_snapshot(document):
    """Compare database values only; never read or hash stored file bytes."""
    return {
        "fields": Document.objects.filter(pk=document.pk).values().get(),
        "files": list(document.files.order_by("pk").values()),
        "metadata": list(document.metadata_values.order_by("pk").values()),
        "reviews": list(document.deletion_reviews.order_by("pk").values()),
        "tags": list(document.tags.order_by("pk").values_list("pk", flat=True)),
        "types": list(document.document_types.order_by("pk").values_list("pk", flat=True)),
    }


def raw_admin_checks(*, office, suffix, active_document, expired_document):
    """Exercise the real admin permission boundary using temporary rows only."""
    system_admin = User.objects.create_superuser(
        username=f"deletion-review-system-qa-{suffix}",
        password=None, first_name="Temporary", last_name="System Administrator",
        office=office, role=User.Role.SYSTEM_ADMIN,
    )
    if not (system_admin.is_staff and system_admin.is_superuser
            and system_admin.role == User.Role.SYSTEM_ADMIN):
        raise RuntimeError("Raw-admin fixture must be a true staff/system superuser")
    admin_client = HttpsClient()
    admin_client.force_login(system_admin)
    originals = {
        "active": document_snapshot(active_document),
        "expired": document_snapshot(expired_document),
    }
    delete_url = reverse("admin:documents_document_delete", args=[active_document.pk])
    change_url = reverse("admin:documents_document_change", args=[expired_document.pk])

    # Django's read-only FileField links call storage.url(). Render those links
    # locally so this permission check cannot contact Azure/R2. No file bytes
    # are supplied, opened or saved, and all model requests still use the real
    # admin, middleware, forms, permissions and temporary database records.
    with override_settings(STORAGES={
        **settings.STORAGES,
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"base_url": "/qa-only-file-links/"},
        },
    }):
        readonly_response = admin_client.get(change_url)
        readonly_page = capture(readonly_response, url=change_url)
        context = readonly_response.context
        if not context or not context["has_view_permission"]:
            raise RuntimeError("Expired raw-admin GET is not a viewable document page")
        if context["has_change_permission"] or context["has_delete_permission"]:
            raise RuntimeError("Expired raw-admin GET unexpectedly allows document modification")
        admin_form = context["adminform"]
        displayed_fields = set(flatten_fieldsets(admin_form.fieldsets))
        if not displayed_fields.issubset(set(admin_form.readonly_fields)):
            raise RuntimeError("Expired raw-admin page contains editable document fields")
        file_inlines = [inline for inline in context["inline_admin_formsets"]
                        if inline.formset.model is DocumentFile]
        if len(file_inlines) != 1:
            raise RuntimeError("Expired raw-admin page must display its file inline")
        file_inline = file_inlines[0]
        inline_permissions = {
            "add": bool(file_inline.has_add_permission),
            "change": bool(file_inline.has_change_permission),
            "delete": bool(file_inline.has_delete_permission),
        }
        if any(inline_permissions.values()) or file_inline.formset.initial_form_count() != 1:
            raise RuntimeError("Expired raw-admin file inline is missing or allows modification")
        controls = VisibleNamedControls()
        controls.feed(readonly_page["html"])
        prefix = file_inline.formset.prefix
        modifying_controls = sorted(name for name in controls.names
                                    if name in displayed_fields
                                    or name.startswith(f"{prefix}-")
                                    or name in {"_save", "_continue", "_addanother", "_saveasnew"})
        if modifying_controls:
            raise RuntimeError(f"Expired raw-admin page renders modifying controls: {modifying_controls}")
        existing_file = originals["expired"]["files"][0]
        if existing_file["original_name"] not in readonly_page["html"]:
            raise RuntimeError("Expired raw-admin page does not show the existing retained file")

        deletion_response = capture(
            admin_client.post(delete_url, {"post": "yes"}),
            url=delete_url, method="POST", expected=403,
        )
        # Try changing document metadata and removing its retained file through
        # a direct POST, even though the view supplies no editing controls.
        editing_response = capture(admin_client.post(change_url, {
            "title": "Unauthorized raw-admin edit must never be saved",
            "description": "Unauthorized metadata replacement",
            "retention_until": timezone.localdate().isoformat(),
            "is_active": "on", "_save": "Save",
            f"{prefix}-TOTAL_FORMS": "1", f"{prefix}-INITIAL_FORMS": "1",
            f"{prefix}-MIN_NUM_FORMS": "0", f"{prefix}-MAX_NUM_FORMS": "1000",
            f"{prefix}-0-id": str(existing_file["id"]),
            f"{prefix}-0-document": str(expired_document.pk),
            f"{prefix}-0-original_name": "unauthorized-file-name.pdf",
            f"{prefix}-0-file-clear": "on", f"{prefix}-0-DELETE": "on",
        }), url=change_url, method="POST", expected=403)

    for key, document in (("active", active_document), ("expired", expired_document)):
        if document_snapshot(document) != originals[key]:
            raise RuntimeError(f"Raw-admin requests changed the {key} temporary document or its related records")
    return {
        "actor": {"role": system_admin.role, "isStaff": True, "isSuperuser": True, "temporary": True},
        "responses": {
            "activeDeletionRejected": deletion_response,
            "expiredEditingRejected": editing_response,
            "expiredReadOnly": readonly_page,
        },
        "expiredReadOnly": {
            "hasViewPermission": True, "hasChangePermission": False, "hasDeletePermission": False,
            "allDocumentFieldsReadOnly": True, "fileInlinePermissions": inline_permissions,
            "retainedFilesVisible": 1, "renderedModificationControls": modifying_controls,
        },
        "unchanged": {"activeDocumentAndRelations": True, "expiredDocumentAndRelations": True},
        "fileStorage": "Local URL rendering only; no file bytes created or read and no provider contacted.",
    }


def render_fixtures():
    today = timezone.localdate()
    future = today + timedelta(days=365)
    pages = {}
    # No existing user, office, document, or master-data object is edited.
    with transaction.atomic():
        try:
            suffix = uuid4().hex[:8]
            office = Office.objects.create(
                code=f"QA{suffix}",
                name=f"Deletion review QA office {suffix}",
                short_name="Review QA",
                colour="#16697A",
            )
            admin = User.objects.create_user(
                username=f"deletion-review-qa-{suffix}",
                password=None,
                first_name="Review",
                last_name="Administrator",
                office=office,
                role=User.Role.ADMIN,
            )
            client = HttpsClient()
            client.force_login(admin)
            keepalive_url = reverse("accounts:session_keep_alive")
            pages["sessionKeepalive"] = capture(client.post(keepalive_url), url=keepalive_url, method="POST")

            specs = {
                "due": ("Facilities inspection reports and supporting approval records", today - timedelta(days=14), True),
                "dueLong": ("Annual inventory reconciliation with supporting procurement and maintenance records for administrative review", today, True),
                "soon": ("Equipment maintenance schedule and office correspondence", today + timedelta(days=30), True),
                "future": ("Current procurement documentation with a later review date", today + timedelta(days=365), True),
                "unscheduled": ("Historical office correspondence without a review date", None, True),
                "deleted": ("Previous annual inspection report retained for possible restoration", today - timedelta(days=30), False),
            }
            documents = {}
            for key, (title, review_date, active) in specs.items():
                document = Document.objects.create(
                    title=title, office=office, year=today.year,
                    document_date=today - timedelta(days=730),
                    reference_number=f"QA-{today.year}-{key.upper()}",
                    description="Temporary browser verification fixture.",
                    retention_until=review_date, is_active=active, deleted_at=None if active else timezone.now(),
                    uploaded_by=admin, ocr_status=OcrStatus.SKIPPED,
                )
                documents[key] = document
                # A string path does not invoke FileField storage.save().
                DocumentFile.objects.create(
                    document=document, file=f"qa-only/{suffix}/{key}.pdf",
                    original_name=f"{key}-approval-and-inspection-records.pdf",
                    content_type="application/pdf", size=245760,
                    is_primary=True, uploaded_by=admin,
                )

            DeletionReview.objects.create(
                document=documents["due"], decision=DeletionReview.Decision.KEEP,
                reason="Keep the inspection records while the annual reconciliation is completed. Approval reference: QA-KEEP-01.",
                previous_review_date=today - timedelta(days=400),
                next_review_date=documents["due"].retention_until,
                reviewed_by=admin, reviewer_label=admin.display_name,
            )
            DeletionReview.objects.create(
                document=documents["deleted"], decision=DeletionReview.Decision.DELETE,
                reason="Removal approved after records review. Authority reference: QA-REMOVE-01. Retained files remain available for restoration.",
                previous_review_date=documents["deleted"].retention_until,
                reviewed_by=admin, reviewer_label=admin.display_name,
            )

            queue_url = reverse("documents:deletion_queue")
            repository_url = reverse("documents:repository")
            pages["repository"] = capture(client.get(repository_url), url=repository_url)
            notification_url = reverse("core:notification_count")
            pages["notificationCount"] = capture(client.get(notification_url), url=notification_url)
            pages["queueDefault"] = capture(client.get(queue_url), url=queue_url)
            for status in ("due", "soon", "unscheduled", "deleted"):
                url = f"{queue_url}?status={status}"
                pages[f"queue{status.capitalize()}"] = capture(client.get(url), url=url)

            review_url = reverse("documents:deletion_review", args=[documents["due"].pk])
            restore_url = reverse("documents:deletion_review", args=[documents["deleted"].pk])
            review_response = client.get(review_url)
            restore_response = client.get(restore_url)
            pages["review"] = capture(review_response, url=review_url)
            pages["restore"] = capture(restore_response, url=restore_url)
            state = review_response.context["form"].initial["state_token"]
            restore_state = restore_response.context["form"].initial["state_token"]
            base_payload = {"state_token": state, "reason": "QA review with approval reference QA-01."}
            pages["keepError"] = error_response(client, review_url, {
                **base_payload, "decision": "KEEP", "next_review_date": today.isoformat(),
            }, "next_review_date")
            pages["removalError"] = error_response(client, review_url, {
                **base_payload, "decision": "DELETE",
            }, "confirmed")
            pages["expiredError"] = error_response(client, review_url, {
                **base_payload, "decision": "KEEP", "next_review_date": future.isoformat(),
                "state_token": "invalid-qa-state-token",
            }, "state_token")
            pages["restoreError"] = error_response(client, restore_url, {
                "state_token": restore_state, "decision": "RESTORE",
                "reason": "QA restoration review.", "next_review_date": today.isoformat(),
            }, "next_review_date")

            # Preserve genuine successful POST/redirect responses for the browser
            # harness. These affect only the isolated rows and are rolled back.
            pages["removePost"] = capture(client.post(review_url, {
                **base_payload, "decision": "DELETE", "confirmed": "on",
            }), url=review_url, method="POST", expected=302)
            removal_destination = pages["removePost"]["headers"]["Location"]
            pages["removalDestination"] = capture(client.get(removal_destination), url=removal_destination)
            pages["queueAfterRemoval"] = capture(client.get(queue_url), url=queue_url)
            pages["restorePost"] = capture(client.post(restore_url, {
                "state_token": restore_state, "decision": "RESTORE",
                "reason": "Restore for an approved records check. Reference QA-RESTORE-01.",
                "next_review_date": future.isoformat(),
            }), url=restore_url, method="POST", expected=302)
            restoration_destination = pages["restorePost"]["headers"]["Location"]
            pages["restorationDestination"] = capture(client.get(restoration_destination), url=restoration_destination)
            pages["queueAfterRestoration"] = capture(client.get(queue_url), url=queue_url)

            # Add the expiry fixture after every existing queue snapshot so its
            # extra deleted row cannot change those queues' cardinality. Aging
            # only this temporary row simulates a previously opened review
            # reaching its deadline; no cleanup command or storage call runs.
            expired = Document.objects.create(
                title="Expired inspection records awaiting permanent deletion",
                office=office, year=today.year, document_date=today - timedelta(days=730),
                reference_number=f"QA-{today.year}-EXPIRED",
                description="Temporary expired-recovery browser fixture.",
                retention_until=today - timedelta(days=60), is_active=False,
                deleted_at=timezone.now(), uploaded_by=admin, ocr_status=OcrStatus.SKIPPED,
            )
            documents["expired"] = expired
            DocumentFile.objects.create(
                document=expired, file=f"qa-only/{suffix}/expired.pdf",
                original_name="expired-inspection-and-approval-records.pdf",
                content_type="application/pdf", size=245760, is_primary=True, uploaded_by=admin,
            )
            DeletionReview.objects.create(
                document=expired, decision=DeletionReview.Decision.DELETE,
                reason="Temporary removal approved under QA-EXPIRED-01.",
                previous_review_date=expired.retention_until,
                reviewed_by=admin, reviewer_label=admin.display_name,
            )
            expired_url = reverse("documents:deletion_review", args=[expired.pk])
            opened_response = client.get(expired_url)
            pages["expiredOpenedReview"] = capture(opened_response, url=expired_url)
            if "data-deletion-review-form" not in pages["expiredOpenedReview"]["html"]:
                raise RuntimeError("QA review opened before expiry unexpectedly has no decision form")
            opened_token = opened_response.context["form"].initial["state_token"]
            expired_at = timezone.now() - timedelta(days=31)
            Document.objects.filter(pk=expired.pk).update(deleted_at=expired_at)
            expired.refresh_from_db()
            if expired.deletion_recovery_open:
                raise RuntimeError("Expired QA fixture unexpectedly remains recoverable")
            pages["expiredDeleted"] = capture(client.get(expired_url), url=expired_url)
            if "data-deletion-review-form" in pages["expiredDeleted"]["html"]:
                raise RuntimeError("Expired review unexpectedly exposes a decision form")
            expired_payload = {
                "state_token": opened_token, "decision": "RESTORE",
                "reason": "Attempt restoration from a review opened before the deadline.",
                "next_review_date": future.isoformat(),
            }
            rejected = client.post(expired_url, expired_payload)
            if not rejected.context or "decision" not in rejected.context["form"].errors:
                raise RuntimeError("Direct expired restoration POST was not rejected by the view")
            pages["expiredRestorePost"] = {
                **capture(rejected, url=expired_url, method="POST"),
                "requestData": expired_payload,
                "validationErrors": rejected.context["form"].errors.get_json_data(),
            }
            expired.refresh_from_db()
            if (expired.is_active or expired.deleted_at != expired_at
                    or expired.files.count() != 1 or expired.deletion_reviews.count() != 1):
                raise RuntimeError("Rejected expired restoration changed the temporary document")
            administration_checks = raw_admin_checks(
                office=office, suffix=suffix,
                active_document=documents["future"], expired_document=expired,
            )
            return {
                "schemaVersion": 1,
                "generatedAt": timezone.now().isoformat(),
                "settingsModule": os.environ["DJANGO_SETTINGS_MODULE"],
                "origin": "http://review.test",
                "today": today.isoformat(),
                "tomorrow": (today + timedelta(days=1)).isoformat(),
                "futureDate": future.isoformat(),
                "queuePath": queue_url,
                "documents": {key: {
                    "title": document.title, "id": document.pk,
                    "deletedAt": document.deleted_at.isoformat() if document.deleted_at else None,
                    "purgeAt": document.deletion_purge_at.isoformat() if document.deletion_purge_at else None,
                    "purgeAtLabel": date_format(timezone.localtime(document.deletion_purge_at), "d M Y, g:i A T")
                    if document.deletion_purge_at else None,
                } for key, document in documents.items()},
                "pages": pages,
                "administrationChecks": administration_checks,
                "scope": "Actual Django responses from isolated temporary fixtures. All database writes rolled back; no file bytes created.",
            }
        finally:
            transaction.set_rollback(True)


def main():
    setup_test_environment()
    try:
        with override_settings(
            ALLOWED_HOSTS=[*settings.ALLOWED_HOSTS, "review.test", "testserver"],
            EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
            ENABLE_BACKGROUND_TASKS=False,
            USE_LOCAL_VENDOR=True,
            SECURE_SSL_REDIRECT=False,
            SESSION_ENGINE="django.contrib.sessions.backends.db",
            CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "deletion-review-qa"}},
            STORAGES={**settings.STORAGES, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}},
        ), patch("apps.core.context_processors._LOCAL_VENDOR", True):
            # site_context currently derives its vendor switch from files on
            # disk. Force its local-asset branch for the offline fixture host.
            fixtures = render_fixtures()
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps(fixtures, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(
            f"Rendered {len(fixtures['pages'])} page responses and "
            f"{len(fixtures['administrationChecks']['responses'])} raw-admin checks to {OUTPUT}; "
            "temporary database writes rolled back."
        )
    finally:
        teardown_test_environment()


if __name__ == "__main__":
    main()

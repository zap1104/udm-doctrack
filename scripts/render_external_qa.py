"""Capture real external UI responses in a transaction that always rolls back."""

import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from django.db import transaction  # noqa: E402
from django.test import Client, override_settings  # noqa: E402
from django.test.utils import setup_test_environment, teardown_test_environment  # noqa: E402
from django.urls import reverse  # noqa: E402
from django.utils import timezone  # noqa: E402

from apps.accounts.models import Office, User  # noqa: E402
from apps.core.models import DocumentType  # noqa: E402
from apps.documents.external_services import archive_external_record  # noqa: E402
from apps.tracking import external_services as services  # noqa: E402

OUTPUT = ROOT / "docs/verification/external-offices-2026-10-15"


def main():
    database_name = os.environ.get("EXTERNAL_QA_DB_NAME", "test_udm_doctrack")
    if not database_name.startswith("test_"):
        raise RuntimeError("External browser QA must use a disposable test database.")
    settings.DATABASES["default"]["NAME"] = database_name
    setup_test_environment()
    stores = {**settings.STORAGES, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
    try:
        with override_settings(STORAGES=stores), transaction.atomic():
            suffix = uuid4().hex[:6]
            own = Office.objects.create(code="EQ" + suffix, name="External QA Maintenance and Engineering Division")
            other = Office.objects.create(code="EX" + suffix, name="External QA Supply and Property Management Office")
            actors = {
                role.lower(): User.objects.create_user(
                    username=f"external-qa-{role.lower()}-{suffix}",
                    password=None,
                    office=own,
                    role=role,
                    first_name="Temporary",
                    last_name=f"External {role.title()} Reviewer",
                )
                for role in ("SYSTEM_ADMIN", "ADMIN", "USER", "VIEWER")
            }
            admin = actors["admin"]
            worker = actors["user"]
            counterpart = services.create_external_office(
                user=admin,
                name="College of Arts and Sciences External QA",
                code="CAS-QA",
                contact_person="Temporary office contact",
                notes="Transaction rollback fixture only.",
            )
            services.create_external_office(user=admin, name="College of Nursing External QA", code="NUR-QA")
            first_type = DocumentType.objects.create(code="EQA" + suffix, name="External QA memorandum " + suffix)
            second_type = DocumentType.objects.create(code="EQB" + suffix, name="External QA supporting letter " + suffix)
            now = timezone.now()

            def make(title):
                return services.create_record(
                    user=admin,
                    title=title,
                    original_reference="PAPER-REFERENCE-2026-EXT-000000000000000000000001",
                    description="Temporary external QA paper record with supporting office instructions. No file upload or live integration.",
                    document_type=first_type,
                    document_types=[first_type, second_type],
                    from_external_office=counterpart,
                    actual_at=now - timedelta(days=2),
                )

            active = make("External QA: review and endorsement of office memorandum and supporting paper documents")
            services.route_record(
                active, user=admin, expected_revision=active.revision, to_office=own, sent_at=now - timedelta(days=1)
            )
            services.confirm_receipt(active, user=worker, expected_revision=active.revision)
            services.add_remark(
                active,
                user=worker,
                expected_revision=active.revision,
                message="The receiving office is reviewing this paper packet.",
            )
            pending = make("External QA: paper awaiting confirmation at the recipient office")
            services.route_record(
                pending,
                user=admin,
                expected_revision=pending.revision,
                to_office=other,
                sent_at=now - timedelta(hours=4),
            )
            draft = make("External QA: saved draft awaiting review and initial dispatch")
            filed = make("External QA: completed document filed into the separate External repository")
            services.route_record(
                filed, user=admin, expected_revision=filed.revision, to_office=own, sent_at=now - timedelta(hours=2)
            )
            services.confirm_receipt(filed, user=worker, expected_revision=filed.revision)
            services.complete_record(filed, user=worker, expected_revision=filed.revision)
            document = archive_external_record(filed, user=admin, expected_revision=filed.revision)
            pages = []
            for role, actor in actors.items():
                client = Client()
                client.force_login(actor)
                scope = f"?office={own.pk}" if actor.is_system_admin else ""
                targets = [
                    ("external-tracking", reverse("tracking:external_list") + scope),
                    ("external-reports", reverse("tracking:external_reports") + scope),
                    ("external-detail", active.get_absolute_url()),
                    ("external-slip", reverse("tracking:external_slip", args=[active.pk])),
                    ("external-directory", reverse("tracking:external_offices")),
                    ("external-repository", reverse("documents:external_repository") + scope),
                    ("external-repository-reports", reverse("documents:external_reports") + scope),
                    ("external-document-detail", document.get_absolute_url()),
                ]
                if actor.is_office_admin:
                    targets.extend(
                        [
                            ("external-create", reverse("tracking:external_create")),
                            ("external-draft-review", reverse("tracking:external_review", args=[draft.pk])),
                            (
                                "external-correction",
                                reverse("tracking:external_correct", args=[pending.pk, pending.current_step.pk]),
                            ),
                            ("external-office-create", reverse("tracking:external_office_create")),
                            ("external-upload", reverse("documents:external_upload")),
                        ]
                    )
                for surface, url in targets:
                    response = client.get(url, secure=True)
                    if response.status_code != 200:
                        raise RuntimeError(f"{role} {url}: expected 200, got {response.status_code}")
                    pages.append({"role": role, "surface": surface, "url": url, "html": response.content.decode()})
            OUTPUT.mkdir(parents=True, exist_ok=True)
            (OUTPUT / "pages.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "origin": "http://external-qa.test",
                        "generatedAt": timezone.now().isoformat(),
                        "scope": "Real Django responses. Temporary accounts, offices, cases, documents, notifications, logs and sessions all rolled back; no file bytes uploaded.",
                        "pages": pages,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            transaction.set_rollback(True)
        print(f"Captured {len(pages)} external UI pages across four roles. All database writes rolled back.")
    finally:
        teardown_test_environment()


if __name__ == "__main__":
    main()

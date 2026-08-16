"""Requests every GET page as several roles, then rolls the whole thing back.

    python scripts/smoke_pages.py

`manage.py selfcheck` proves the service layer works; this proves the *pages*
render. They are different failures: a template that reads a context key the
view never sets raises only when someone opens it, which is exactly the kind of
bug that surfaces during a demo.

Everything runs inside one transaction that is rolled back at the end, so the
audit-log entries, search logs and session rows the requests create do not
survive. Nothing is written to the database you point it at.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.db import transaction  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment, teardown_test_environment  # noqa: E402
from django.urls import reverse  # noqa: E402

from apps.accounts.models import User  # noqa: E402
from apps.documents.models import Document, DocumentFile  # noqa: E402
from apps.tracking.models import Attachment, TrackingRecord  # noqa: E402
from apps.tracking.services import create_draft_record  # noqa: E402


class HttpsClient(Client):
    """Always speaks HTTPS.

    With DJANGO_DEBUG=False, SECURE_SSL_REDIRECT answers every plain-http
    request with a 301 that never reaches the view, so this script would report
    a wall of failures that say nothing about the pages. The deployed app is
    served over HTTPS anyway.
    """

    def generic(self, method, path, *args, **kwargs):
        kwargs["secure"] = True
        return super().generic(method, path, *args, **kwargs)

# Pages that render for any signed-in user, as (url name, args) pairs.
STATIC_PAGES = [
    ("core:dashboard", ()),
    ("core:reports", ()),
    ("core:report_export", ()),
    ("tracking:list", ()),
    ("tracking:create", ()),
    ("documents:repository", ()),
    ("documents:upload", ()),
    ("documents:tag_suggest", ()),
    ("search:index", ()),
    ("search:autocomplete", ()),
    ("accounts:profile", ()),
    ("accounts:password_change", ()),
]

ADMIN_PAGES = [
    ("core:administration", ()),
    ("core:audit_log", ()),
    ("accounts:user_list", ()),
    ("accounts:user_create", ()),
    ("accounts:office_list", ()),
    ("accounts:office_create", ()),
]

MASTER_DATA_SLUGS = ["document-types", "tags", "metadata-rules", "metadata-fields"]

# Query strings worth exercising: every filter branch a stale bookmark can hit.
QUERY_PAGES = [
    ("tracking:list", "?scope=inbox"),
    ("tracking:list", "?scope=awaiting"),
    ("tracking:list", "?scope=custody"),
    ("tracking:list", "?scope=sent"),
    ("tracking:list", "?scope=mine"),
    ("tracking:list", "?status=OVERDUE"),
    ("tracking:list", "?status=BOGUS&scope=inbox"),
    ("tracking:list", "?q=UDM&page=1"),
    ("core:reports", "?status=OVERDUE"),
    ("core:reports", "?year=2026"),
    ("documents:repository", "?q=report"),
    ("search:index", "?q=maintenance"),
    ("search:index", "?q=maintenance&show_all=on&min_relevance=0"),
    ("search:index", "?year=2025"),
    ("search:autocomplete", "?q=ma"),
    ("documents:tag_suggest", "?q=ma"),
]


class Smoke:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.checked = 0

    def get(self, client: Client, url: str, who: str, expected=(200, 302)) -> None:
        self.checked += 1
        try:
            response = client.get(url)
        except Exception as exc:  # noqa: BLE001 - reporting is the whole point
            self.failures.append(f"[{who}] GET {url} raised {type(exc).__name__}: {exc}")
            return
        if response.status_code not in expected:
            self.failures.append(
                f"[{who}] GET {url} returned {response.status_code} (expected one of {expected})"
            )

    def named(self, client: Client, name: str, args=(), suffix: str = "", who: str = "", **kwargs) -> None:
        self.get(client, reverse(name, args=args) + suffix, who or name, **kwargs)


def pick_user(**filters) -> User | None:
    return User.objects.filter(is_active=True, **filters).exclude(office__isnull=True).first()


def main() -> int:
    # Adds 'testserver' to ALLOWED_HOSTS and swaps the email backend for an
    # in-memory one, so a deployment-shaped .env does not turn every page into
    # a 400 and nothing here can post real mail.
    setup_test_environment()
    try:
        return _run()
    finally:
        teardown_test_environment()


def _run() -> int:
    smoke = Smoke()

    admin = pick_user(role="ADMIN") or User.objects.filter(is_superuser=True).first()
    staff = pick_user(role="SECRETARY")
    plain = pick_user(role="USER")
    roles = [("admin", admin), ("secretary", staff), ("user", plain)]
    present = [(label, person) for label, person in roles if person]
    if not present:
        print("No active users with an office — run: python manage.py seed_demo")
        return 1

    with transaction.atomic():
        # -- signed out -----------------------------------------------------
        anon = HttpsClient()
        smoke.get(anon, "/", "anonymous", expected=(302,))
        smoke.get(anon, reverse("accounts:login"), "anonymous", expected=(200,))
        smoke.get(anon, reverse("tracking:list"), "anonymous", expected=(302,))

        for label, person in present:
            client = HttpsClient()
            client.force_login(person)

            for name, args in STATIC_PAGES:
                smoke.named(client, name, args, who=label)
            for name, query in QUERY_PAGES:
                smoke.named(client, name, suffix=query, who=label)

            if person.is_system_admin:
                for name, args in ADMIN_PAGES:
                    smoke.named(client, name, args, who=label)
                for slug in MASTER_DATA_SLUGS:
                    smoke.named(client, "core:masterdata_list", (slug,), who=label)
                    smoke.named(client, "core:masterdata_create", (slug,), who=label)
                other = User.objects.exclude(pk=person.pk).first()
                if other:
                    smoke.named(client, "accounts:user_edit", (other.pk,), who=label)

            # -- object pages, restricted to what this person may actually see
            records = list(TrackingRecord.objects.visible_to(person)[:4])
            for record in records:
                smoke.named(client, "tracking:detail", (record.pk,), who=label)
                smoke.named(client, "tracking:routing_slip", (record.pk,), who=label)

            # A draft is made rather than looked for. Step 2 of the tracking
            # slip only exists while a record is unrouted, so a database whose
            # drafts have all been sent leaves that page — and the deadline
            # widget on it — untested exactly when it looks well covered.
            if person.office_id:
                draft = create_draft_record(
                    user=person,
                    subject=f"Smoke test draft for {person.username}",
                    instructions="Created by scripts/smoke_pages.py and rolled back.",
                )
                smoke.named(client, "tracking:review", (draft.pk,), who=label)
                smoke.named(client, "tracking:detail", (draft.pk,), who=label)

            attachment = Attachment.objects.filter(record__in=records).first()
            if attachment:
                smoke.named(client, "tracking:attachment_download", (attachment.pk,), who=label,
                            expected=(200, 404))

            documents = list(Document.objects.visible_to(person)[:4])
            for document in documents:
                smoke.named(client, "documents:detail", (document.pk,), who=label)
                if document.can_user_edit(person):
                    smoke.named(client, "documents:edit", (document.pk,), who=label)
                    smoke.named(client, "documents:review", (document.pk,), who=label)
            document_file = DocumentFile.objects.filter(document__in=documents).first()
            if document_file:
                smoke.named(client, "documents:file_download", (document_file.pk,), who=label,
                            expected=(200, 404))

        transaction.set_rollback(True)

    print(f"Requested {smoke.checked} page(s) as {len(present)} role(s).")
    if smoke.failures:
        print(f"\n{len(smoke.failures)} problem(s):\n")
        for failure in smoke.failures:
            print(f"  FAIL  {failure}")
        return 1
    print("No page raised an error.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

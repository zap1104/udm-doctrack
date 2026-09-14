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
    ("documents:repository", ()),
    ("documents:tag_suggest", ()),
    ("search:index", ()),
    ("search:autocomplete", ()),
    ("accounts:profile", ()),
    ("accounts:password_change", ()),
]

#: Pages that begin a change, so a view-only account is refused them by design.
#: Kept apart from STATIC_PAGES rather than given a wider `expected`: a 403 here
#: is correct for a viewer and a regression for everybody else, and one shared
#: expectation could not tell those apart.
WRITE_PAGES = [
    ("tracking:create", ()),
    ("documents:upload", ()),
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
#: Sections only a system administrator may open. Kept apart so the office-admin
#: pass can exercise the rest without tripping over a deliberate 403.
SYSTEM_ADMIN_SLUGS = ["offices"]

#: Administration pages an office administrator reaches too, over their own
#: office. The office screens are not here: offices are system-admin territory.
OFFICE_ADMIN_PAGES = [
    ("core:administration", ()),
    ("accounts:user_list", ()),
    ("accounts:user_create", ()),
]

# Query strings worth exercising: every filter branch a stale bookmark can hit.
QUERY_PAGES = [
    ("tracking:list", "?scope=incoming"),
    ("tracking:list", "?scope=outgoing"),
    ("tracking:list", "?scope=pending-receipt"),
    ("tracking:list", "?scope=received"),
    ("tracking:list", "?scope=overdue"),
    ("tracking:list", "?scope=pending-upload"),
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

    admin = pick_user(role="SYSTEM_ADMIN") or User.objects.filter(is_superuser=True).first()
    office_admin = pick_user(role="ADMIN")
    plain = pick_user(role="USER")
    viewer = pick_user(role="VIEWER")
    roles = [
        ("system admin", admin),
        ("office admin", office_admin),
        ("user", plain),
        ("viewer", viewer),
    ]
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
            for name, args in WRITE_PAGES:
                # A viewer must be refused these, and being refused is the thing
                # worth checking — so the expectation flips rather than relaxes.
                smoke.named(
                    client, name, args, who=label,
                    expected=(403,) if person.is_viewer else (200, 302),
                )

            if person.is_system_admin:
                for name, args in ADMIN_PAGES:
                    smoke.named(client, name, args, who=label)
                for slug in MASTER_DATA_SLUGS + SYSTEM_ADMIN_SLUGS:
                    smoke.named(client, "core:masterdata_list", (slug,), who=label)
                    smoke.named(client, "core:masterdata_create", (slug,), who=label)
                other = User.objects.exclude(pk=person.pk).first()
                if other:
                    smoke.named(client, "accounts:user_edit", (other.pk,), who=label)
            elif person.is_office_admin:
                for name, args in OFFICE_ADMIN_PAGES:
                    smoke.named(client, name, args, who=label)
                for slug in MASTER_DATA_SLUGS:
                    smoke.named(client, "core:masterdata_list", (slug,), who=label)
                # Only within their own office — anyone else is a deliberate 404,
                # which is the behaviour worth exercising here.
                same_office = User.objects.filter(office_id=person.office_id).exclude(
                    pk=person.pk
                ).first()
                if same_office:
                    smoke.named(client, "accounts:user_edit", (same_office.pk,), who=label)

            # -- object pages, restricted to what this person may actually see
            records = list(TrackingRecord.objects.visible_to(person)[:4])
            for record in records:
                smoke.named(client, "tracking:detail", (record.pk,), who=label)
                smoke.named(client, "tracking:routing_slip", (record.pk,), who=label)

            # A draft is made rather than looked for. Step 2 of the tracking
            # slip only exists while a record is unrouted, so a database whose
            # drafts have all been sent leaves that page — and the deadline
            # widget on it — untested exactly when it looks well covered.
            # A viewer is refused by create_draft_record, which is the point of
            # the role — so there is no draft to exercise those two pages with.
            if person.office_id and not person.is_viewer:
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

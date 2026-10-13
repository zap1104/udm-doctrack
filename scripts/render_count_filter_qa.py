"""Render real filtered search responses; all temporary data is rolled back."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.db import transaction  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402

from apps.accounts.models import Office, User  # noqa: E402
from apps.tracking import services  # noqa: E402


def main():
    setup_test_environment()
    with transaction.atomic():
        suffix = uuid4().hex[:6]
        offices = [Office.objects.create(code=f"QA{i}{suffix}", name=f"Filter test office {i}") for i in range(3)]
        users = [User.objects.create_user(username=f"filter-qa-{i}-{suffix}", password=None, office=office,
                                         role="SYSTEM_ADMIN" if i == 2 else "USER")
                 for i, office in enumerate(offices)]
        for subject in ("QA pending letter", "QA received letter"):
            record = services.create_draft_record(user=users[0], subject=subject, instructions="Test only")
            services.route_record(record, [offices[1]], user=users[0])
            if "received" in subject:
                services.confirm_receipt(record, user=users[1])
        client = Client()
        client.force_login(users[2])
        common = [("mode", "tracking"), ("scope", "incoming"), ("status", "PENDING_RECEIPT"),
                  ("status", "RECEIVED"), ("offices", str(offices[0].pk)),
                  ("overdue", "no"), ("per_page", "100")]
        pages = []
        for label, office, query, total in (
            ("initial", offices[1], "", 2),
            ("searched", offices[1], "QA received", 1),
            ("other_office", offices[2], "QA received", 0),
        ):
            url = "/search/?" + urlencode(common + [("office", str(office.pk)), ("q", query)])
            response = client.get(url, secure=True)
            assert response.status_code == 200 and response.context["total"] == total
            pages.append({"label": label, "url": url, "total": total, "html": response.content.decode()})
        output = ROOT / "docs/verification/count-filter-pages.json"
        output.write_text(json.dumps({"pages": pages, "other_office": str(offices[2].pk)}), encoding="utf-8")
        transaction.set_rollback(True)
        print(f"Rendered {len(pages)} actual search pages; temporary records and sessions rolled back.")


if __name__ == "__main__":
    main()

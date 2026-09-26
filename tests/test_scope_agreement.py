"""Every page agrees about every office, for every role.

For each office in the seeded shapes (MED, Supply, HR, Records, Procurement)
and each role, the numbers a page shows are the rows the page they open lists,
no list repeats a row, and naming another office in the URL never widens what
an account may see.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from apps.accounts.models import Office
from apps.documents.models import Document
from tests.test_dashboard_reports_agreement import agreement  # noqa: F401 — fixture, used by name
from tests.test_filter_agreement import traffic  # noqa: F401 — fixture, used by name

CODES = ["MED", "SUP", "HR", "REC", "PRC"]


def _office(code):
    return Office.objects.get(code=code)


@pytest.mark.django_db
def test_every_office_reconciles_for_a_system_administrator(client, users, agreement):  # noqa: F811
    """Every Action Centre chip and every ring segment, for every office."""
    client.force_login(users["admin"])
    for code in CODES:
        context = client.get(f"/?office={_office(code).pk}").context
        for queue in context["desk_queues"]:
            listed = client.get(queue["tracking_url"]).context["total"]
            assert queue["count"] == listed, (code, queue["slug"])
        for segment in context["breakdown"]["slices"]:
            listed = client.get(segment["url"]).context["total"]
            assert segment["total"] == listed, (code, segment["key"])


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["admin", "med_admin", "sup_admin", "med", "sup", "hr", "viewer"])
def test_no_list_repeats_a_row(client, users, agreement, role):  # noqa: F811
    """Joins through routing steps multiply rows unless the query says
    otherwise; a list that shows a document twice counts it twice."""
    client.force_login(users[role])
    pages = {
        "tracking": client.get("/tracking/?per_page=100").context["records"],
        "repository": client.get("/documents/?per_page=100").context["documents"],
        "search": client.get("/search/?mode=tracking&q=&per_page=100").context["results"],
        "action centre": client.get("/").context["attention_records"],
    }
    for page, rows in pages.items():
        keys = [row.pk for row in rows]
        assert len(keys) == len(set(keys)), (role, page)


@pytest.fixture
def office_accounts(offices, agreement):  # noqa: F811
    """A user and a viewer for Procurement too, so every office has both."""
    user_model = get_user_model()
    prc = _office("PRC")
    return {
        "prc_viewer": user_model.objects.create_user(
            username="prc_viewer", password="TestPass123!", office=prc, role="VIEWER"
        ),
    }


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["med", "sup", "hr", "viewer", "prc", "prc_viewer"])
def test_naming_another_office_never_widens_what_an_account_sees(
    client, users, offices, agreement, office_accounts, role  # noqa: F811
):
    account = users.get(role) or office_accounts.get(role) or get_user_model().objects.get(username=role)
    client.force_login(account)

    baseline = {
        "tracking": client.get("/tracking/").context["total"],
        "reports": client.get("/tracking/reports/").context["total_records"],
        "dashboard": client.get("/").context["breakdown"]["total"],
        "search": client.get("/search/?mode=tracking&q=").context["total"],
        "repository": client.get("/documents/").context["total"],
    }
    for code in CODES:
        other = _office(code)
        if other.pk == account.office_id:
            continue
        param = f"office={other.pk}"
        assert client.get(f"/tracking/?{param}").context["total"] == baseline["tracking"], code
        assert client.get(f"/tracking/reports/?{param}").context["total_records"] == baseline["reports"], code
        assert client.get(f"/?{param}").context["breakdown"]["total"] == baseline["dashboard"], code
        assert client.get(f"/search/?mode=tracking&q=&{param}").context["total"] == baseline["search"], code
        # On the repository `office` is a content filter over what the account
        # may already see: it narrows, and everything it lists is visible.
        narrowed = client.get(f"/documents/?{param}&per_page=100").context
        assert narrowed["total"] <= baseline["repository"], code
        visible = set(Document.objects.visible_to(account).values_list("pk", flat=True))
        assert {document.pk for document in narrowed["documents"]} <= visible, code

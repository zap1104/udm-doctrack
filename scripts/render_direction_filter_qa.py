"""Render genuine direction/stage pages; temporary data rolls back on exit.

Run with the project's Django environment. The browser checker serves these
responses locally together with the application's actual JavaScript and CSS.
"""

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
from django.urls import reverse  # noqa: E402

from apps.accounts.models import Office, User  # noqa: E402
from apps.tracking import services  # noqa: E402
from apps.tracking.models import Status  # noqa: E402

STAGES = (
    (Status.PENDING_RECEIPT, "pending-receipt"),
    (Status.RECEIVED, "received"),
    (Status.IN_PROCESS, "in-process"),
)


def group_snapshot(groups):
    return [
        {
            "key": group["key"], "label": group["label"],
            "disabled": group["disabled"], "active": group["active"],
            "href": group.get("href"), "count": group.get("count"),
            "stages": [
                {key: stage.get(key) for key in ("status", "label", "active", "href", "count")}
                for stage in group["stages"]
            ],
        }
        for group in groups
    ]


def main():
    setup_test_environment()
    with transaction.atomic():
        suffix = uuid4().hex[:6]
        query = f"Direction QA {suffix}"
        offices = [
            Office.objects.create(code=f"DQ{i}{suffix}", name=f"Direction QA office {i}")
            for i in range(5)
        ]
        users = [
            User.objects.create_user(
                username=f"direction-qa-{i}-{suffix}", password=None, office=office,
                role="SYSTEM_ADMIN" if i == 4 else "USER",
            )
            for i, office in enumerate(offices)
        ]
        records = {}
        for direction, sender, recipient in (("incoming", 0, 1), ("outgoing", 1, 2)):
            for stage, slug in STAGES:
                record = services.create_draft_record(
                    user=users[sender], subject=f"{query} {direction} {slug}", instructions="QA fixture only",
                )
                services.route_record(record, [offices[recipient]], user=users[sender], due_at=None)
                if stage != Status.PENDING_RECEIPT:
                    services.confirm_receipt(record, user=users[recipient])
                if stage == Status.IN_PROCESS:
                    services.mark_in_process(record, user=users[recipient])
                record.refresh_from_db()
                assert record.status == stage
                records[f"{direction}-{slug}"] = record
            partial = services.create_draft_record(
                user=users[sender], subject=f"{query} {direction} partial-confirmed", instructions="QA fixture only",
            )
            services.route_record(partial, [offices[recipient], offices[3]], user=users[sender], due_at=None)
            # The selected office's incoming copy has not been received. For
            # outgoing, one recipient has confirmed and the other has not.
            # Both belong in Pending Receipt at the selected office's desk.
            services.confirm_receipt(partial, user=users[3] if direction == "incoming" else users[recipient])
            if direction == "incoming":
                services.add_remark(partial, user=users[3], remark="This recipient has started reviewing the document.")
            partial.refresh_from_db()
            assert partial.status == Status.PENDING_RECEIPT
            records[f"{direction}-partial-confirmed"] = partial

        # These sets come from the known receipt/work events for office 1,
        # independently of the cached global status used by all-office views.
        expected = {
            direction: {
                Status.PENDING_RECEIPT: [
                    records[f"{direction}-pending-receipt"], records[f"{direction}-partial-confirmed"],
                ],
                Status.RECEIVED: [records[f"{direction}-received"]],
                Status.IN_PROCESS: [records[f"{direction}-in-process"]],
            }
            for direction in ("incoming", "outgoing")
        }
        client = Client()
        client.force_login(users[4])
        pages = []
        rendered_urls = set()

        def render(surface, url, selection="default", *, direction=None, stage=None, disabled=False):
            if url in rendered_urls:
                return next(page for page in pages if page["url"] == url)
            response = client.get(url, secure=True)
            assert response.status_code == 200, (url, response.status_code)
            context = response.context
            page = {"surface": surface, "url": url, "selection": selection, "html": response.content.decode()}
            if surface == "reports":
                page["groups"] = [
                    {"key": group["key"], "total": group["total"], "href": group["workspace_url"],
                     "stages": [{"status": item["status"], "total": item["total"], "href": item["workspace_url"]}
                                for item in group["stages"]]}
                    for group in context["direction_stages"]
                ]
                for group in page["groups"]:
                    assert group["total"] == 4
                    assert [item["total"] for item in group["stages"]] == [
                        len(expected[group["key"]][stage]) for stage, _ in STAGES
                    ]
            else:
                group_key = "desk_direction_groups" if surface == "dashboard" else "direction_filter_groups"
                page["groups"] = group_snapshot(context[group_key])
                if disabled:
                    assert all(group["disabled"] for group in context[group_key])
                else:
                    rows = context["attention_records"] if surface == "dashboard" else context["page_obj"].object_list
                    ids = {record.pk for record in rows}
                    if direction:
                        wanted = expected[direction][stage] if stage else [record for rows in expected[direction].values() for record in rows]
                    elif surface == "dashboard":
                        wanted = [records[key] for key in records if key.endswith(("pending-receipt", "partial-confirmed"))]
                    else:
                        wanted = list(records.values())
                    assert ids == {record.pk for record in wanted}, (url, ids, [record.pk for record in wanted])
                    page["record_ids"] = sorted(ids)
                    page["subjects"] = sorted(record.subject for record in wanted)
                    page["total"] = context["desk_queue"]["count"] if surface == "dashboard" else context["total"]
                    assert page["total"] == len(wanted)
                    if surface == "dashboard":
                        page["queue_label"] = context["desk_queue"]["label"]
                        page["tracking_url"] = context["desk_queue"]["tracking_url"]
                        for group in context[group_key]:
                            assert group["count"] == 4
                            assert [item["count"] for item in group["stages"]] == [
                                len(expected[group["key"]][stage]) for stage, _ in STAGES
                            ]
                if surface == "dashboard":
                    partial_response = client.get(
                        url, secure=True, HTTP_HX_REQUEST="true", HTTP_HX_TARGET="action-centre-queue",
                    )
                    assert partial_response.status_code == 200
                    page["partial_html"] = partial_response.content.decode()
                    assert "<html" not in page["partial_html"]
            pages.append(page)
            rendered_urls.add(url)
            return page

        office = str(offices[1].pk)
        shared = [("office", office), ("q", query), ("per_page", "100"), ("overdue", "no"),
                  ("offices", str(offices[0].pk)), ("offices", office)]
        for surface, base, common in (
            ("dashboard", "/", [("office", office)]),
            ("tracking", "/tracking/", shared),
            ("search", "/search/", [("mode", "tracking"), *shared]),
        ):
            initial = render(surface, base + "?" + urlencode(common))
            for group in initial["groups"]:
                direction = group["key"]
                render(surface, group["href"], direction, direction=direction)
                for stage in group["stages"]:
                    render(surface, stage["href"], f"{direction}-{stage['status']}", direction=direction, stage=stage["status"])
            all_common = [(key, "all" if key == "office" else value) for key, value in common]
            render(surface, base + "?" + urlencode(all_common), "all-offices", disabled=True)

        render("reports", "/tracking/reports/?" + urlencode({"office": office}))
        # These are the native cross-page destinations of dashboard and Reports
        # links, without the additional search/list preservation filters.
        for direction in ("incoming", "outgoing"):
            for stage in (None, *(value for value, _ in STAGES)):
                params = [("scope", direction), ("office", office)]
                if stage:
                    params.append(("status", stage))
                render("linked-tracking", "/tracking/?" + urlencode(params),
                       f"{direction}-{stage or 'all'}", direction=direction, stage=stage)

        assert len(pages) == 39
        detail_pages = []
        detail_record = records["incoming-partial-confirmed"]
        for user_index, stage in ((1, Status.PENDING_RECEIPT), (3, Status.IN_PROCESS)):
            client.force_login(users[user_index])
            for view in ("detail", "routing_slip"):
                url = reverse(f"tracking:{view}", args=[detail_record.pk])
                response = client.get(url, secure=True)
                assert response.status_code == 200, (view, user_index, response.status_code)
                context = response.context
                assert context["record"].display_status == stage
                page = {
                    "surface": view, "url": url, "html": response.content.decode(),
                    "actor": f"office-{user_index}", "office_name": offices[user_index].name,
                    "stage": stage, "stage_label": stage.label,
                    "stage_scope_label": context["stage_scope_label"],
                }
                if view == "detail":
                    page["can_confirm"] = context["can_confirm"]
                    page["can_act"] = context["can_act"]
                    assert page["can_confirm"] is (user_index == 1)
                    assert page["can_act"] is (user_index == 3)
                detail_pages.append(page)
        client.force_login(users[4])
        assert len(detail_pages) == 4
        output = ROOT / "docs/verification/direction-filter-pages.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({"pages": pages, "detail_pages": detail_pages, "office": office, "query": query,
                                      "raised_by": [str(offices[0].pk), office]}, indent=2), encoding="utf-8")
        transaction.set_rollback(True)
        print(f"Rendered {len(pages)} genuine pages and dashboard fragments, plus {len(detail_pages)} office detail/slip pages; all temporary records, users, offices and sessions rolled back.")


if __name__ == "__main__":
    main()

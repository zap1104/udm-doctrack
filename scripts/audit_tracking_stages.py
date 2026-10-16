"""Audit live tracking stages without changing documents, receipts, or history.

Run after the recipient-stage migration. Add --with-pages to check rendered
Dashboard, Tracking, Search, Reports, and CSV results as well as query stages.
All authenticated requests, sessions, and access logs roll back on exit.
Only the requested JSON report is saved outside the database transaction.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.db import transaction  # noqa: E402
from django.db.models import Prefetch  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment, teardown_test_environment  # noqa: E402
from django.utils import timezone  # noqa: E402

from apps.accounts.models import Office, User  # noqa: E402
from apps.tracking.models import (  # noqa: E402
    ACTIVE_STATUSES,
    COMPLETED_STATUSES,
    RecordActivity,
    Status,
    TrackingRecord,
)

WORKING_STAGES = (Status.PENDING_RECEIPT, Status.RECEIVED, Status.IN_PROCESS)
REPORTED_NUMBER = "UDM-OVPA-REC-2026-10-0044"
MAX_FAILURE_EXAMPLES = 50


def manual_stage(record, office=None):
    """Independent receipt/work calculation over already-loaded history rows."""
    if record.status == Status.DRAFT or record.status in COMPLETED_STATUSES:
        return record.status
    steps = [step for step in record.audit_steps if step.batch == record.current_batch]
    if office is not None:
        local = [step for step in steps if step.to_office_id == office.pk]
        if local:
            steps = local
    if any(step.received_at is None for step in steps):
        return Status.PENDING_RECEIPT
    for step in steps:
        if step.received_at is None:
            continue
        for activity in record.audit_work:
            activity_office = activity.work_office_id or activity.actor_office_id
            if (activity.batch == record.current_batch
                    and activity_office == step.to_office_id
                    and activity.created_at >= step.received_at
                    and not activity.message.startswith("Returned to tracking by ")):
                return Status.IN_PROCESS
    return Status.RECEIVED if steps else record.status


def manual_direction(record, office):
    if office is None:
        return None
    current = [step for step in record.audit_steps if step.batch == record.current_batch]
    if any(step.to_office_id == office.pk for step in current):
        return "incoming"
    if any(step.from_office_id == office.pk for step in current):
        return "outgoing"
    return "other"


def touches_office(record, office):
    return office is None or office.pk in {record.originating_office_id, record.current_office_id} or any(
        office.pk in {step.from_office_id, step.to_office_id} for step in record.audit_steps
    )


def loaded_records(user):
    return list(TrackingRecord.objects.visible_to(user).prefetch_related(
        Prefetch("routing_steps", to_attr="audit_steps"),
        Prefetch("activities", queryset=RecordActivity.objects.filter(
            event__in=[RecordActivity.Event.REMARK, RecordActivity.Event.ATTACHMENT],
        ), to_attr="audit_work"),
    ).order_by("pk"))


def replace_query(url, **changes):
    parts = urlsplit(url)
    pairs = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if key not in changes]
    pairs.extend((key, str(value)) for key, value in changes.items() if value is not None)
    return urlunsplit(("", "", parts.path, urlencode(pairs), ""))


class Audit:
    def __init__(self):
        self.failures = []
        self.failure_count = 0
        self.check_count = 0
        self.request_count = 0

    def check(self, condition, check, **detail):
        self.check_count += 1
        if not condition:
            self.failure_count += 1
            if len(self.failures) < MAX_FAILURE_EXAMPLES:
                self.failures.append({"check": check, **detail})
        return bool(condition)

    def get(self, client, url):
        self.request_count += 1
        try:
            with transaction.atomic():
                response = client.get(url, secure=True)
        except Exception as error:
            self.check(False, "request raised an exception", url=url, error=str(error))
            return None
        if not self.check(response.status_code == 200, "request returned 200",
                          url=url, actual=response.status_code):
            return None
        return response

    def page_records(self, client, url):
        """Walk every page, including pages beyond the application's All cap."""
        first = self.get(client, replace_query(url, per_page="100", page=1))
        if first is None:
            return None
        page = first.context["page_obj"]
        total = page.paginator.count
        records = list(page.object_list)
        for number in range(2, page.paginator.num_pages + 1):
            response = self.get(client, replace_query(url, per_page="100", page=number))
            if response is None:
                return None
            next_page = response.context["page_obj"]
            self.check(next_page.number == number, "pagination reaches requested page", url=url, page=number)
            self.check(next_page.paginator.count == total, "pagination count stays stable", url=url)
            records.extend(next_page.object_list)
        ids = [record.pk for record in records]
        self.check(len(ids) == len(set(ids)) == total, "pagination contains exactly its stated total",
                   url=url, stated=total, rows=len(ids), distinct=len(set(ids)))
        return records

    def csv_records(self, client, url):
        response = self.get(client, url)
        if response is None:
            return None
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        heading = next((index for index, row in enumerate(rows) if row and row[0] == "Tracking number"), None)
        if not self.check(heading is not None, "CSV contains document headings", url=url):
            return None
        metadata = next(row for row in rows if row and row[0] == "Exported rows")
        self.check(int(metadata[1]) == int(metadata[5]), "CSV export has not reached its row cap",
                   url=url, exported=int(metadata[1]), matching=int(metadata[5]), cap=int(metadata[3]))
        return [dict(zip(rows[heading], row, strict=True)) for row in rows[heading + 1:] if row]


def check_queries(audit, user, records, office):
    expected = {record.pk: manual_stage(record, office) for record in records}
    actual_rows = list(TrackingRecord.objects.visible_to(user).with_stage_for(office).values_list("pk", "stage"))
    actual = dict(actual_rows)
    audit.check(len(actual_rows) == len(actual), "annotated query has no duplicate records",
                user_id=user.pk, office=office.code if office else "global")
    audit.check(set(actual) == set(expected), "stage annotation preserves the visible population",
                user_id=user.pk, office=office.code if office else "global")
    for record in records:
        audit.check(actual.get(record.pk) == expected[record.pk], "query matches receipt/work stage",
                    user_id=user.pk, record_id=record.pk, tracking_number=record.tracking_number,
                    office=office.code if office else "global", expected=expected[record.pk], actual=actual.get(record.pk))
        if office is None:
            audit.check(record.status == expected[record.pk], "cached global stage matches receipt/work facts",
                        user_id=user.pk, record_id=record.pk, tracking_number=record.tracking_number,
                        expected=expected[record.pk], actual=record.status)
    counts = dict(Counter(expected.values()))
    directions = {}
    active = [record for record in records if record.status in ACTIVE_STATUSES]
    if office:
        for direction in ("incoming", "outgoing"):
            ids = {record.pk for record in active if manual_direction(record, office) == direction
                   and expected[record.pk] in WORKING_STAGES}
            groups = {stage: {pk for pk in ids if expected[pk] == stage} for stage in WORKING_STAGES}
            query_ids = {
                stage: set(TrackingRecord.objects.visible_to(user).with_stage_for(office)
                           .filter(pk__in=ids, stage=stage).values_list("pk", flat=True))
                for stage in WORKING_STAGES
            }
            audit.check(all(groups[stage] == query_ids[stage] for stage in WORKING_STAGES),
                        "direction filters match each recipient stage", user_id=user.pk,
                        office=office.code, direction=direction)
            audit.check(sum(map(len, query_ids.values())) == len(ids)
                        and set().union(*query_ids.values()) == ids,
                        "three stage filters are disjoint and complete", user_id=user.pk,
                        office=office.code, direction=direction)
            directions[direction] = {"total": len(ids), "stages": {stage: len(groups[stage]) for stage in WORKING_STAGES}}
    cached_mismatches = sum(record.status != manual_stage(record) for record in records)
    return {"office": office.code if office else "global", "visible_records": len(records),
            "cached_global_stage_mismatches": cached_mismatches,
            "stages": counts, "directions": directions}


def check_pages(audit, user, records, office):
    client = Client()
    client.force_login(user)
    office_param = (str(office.pk) if office else "all") if user.is_system_admin else None
    common = {"office": office_param}
    dashboard_url = replace_query("/", **common)
    reports_url = replace_query("/tracking/reports/", **common)
    dashboard = audit.get(client, dashboard_url)
    reports = audit.get(client, reports_url)
    if dashboard is None or reports is None:
        return
    stage_by_id = {record.pk: manual_stage(record, office) for record in records}
    active = [record for record in records if record.status in ACTIVE_STATUSES]
    report_population = [record for record in records if record.status not in COMPLETED_STATUSES
                         and (not (office and (user.is_system_admin or user.is_office_admin))
                              or touches_office(record, office))]
    wanted_report = Counter(stage_by_id[record.pk] for record in report_population
                            if stage_by_id[record.pk] in WORKING_STAGES)
    actual_report = {row["status"]: row["total"] for row in reports.context["by_status"]}
    audit.check(all(actual_report.get(stage, 0) == wanted_report.get(stage, 0) for stage in WORKING_STAGES),
                "Reports stages match the complete scoped population", user_id=user.pk,
                office=office.code if office else "global", actual=actual_report, expected=dict(wanted_report))
    audit.check(reports.context["total_records"] == len(report_population), "Reports total matches current work",
                user_id=user.pk, office=office.code if office else "global",
                actual=reports.context["total_records"], expected=len(report_population))
    for group in reports.context["direction_stages"]:
        wanted = [record for record in report_population
                  if manual_direction(record, office) == group["key"]
                  and stage_by_id[record.pk] in WORKING_STAGES]
        audit.check(group["total"] == len(wanted), "Reports direction total matches current-batch facts",
                    user_id=user.pk, office=office.code, direction=group["key"],
                    actual=group["total"], expected=len(wanted))
        audit.check(sum(item["total"] for item in group["stages"]) == group["total"],
                    "Reports direction stages add up to their total", user_id=user.pk,
                    office=office.code, direction=group["key"])
        for item in group["stages"]:
            expected_count = sum(stage_by_id[record.pk] == item["status"] for record in wanted)
            audit.check(item["total"] == expected_count, "Reports direction stage matches recipient facts",
                        user_id=user.pk, office=office.code, direction=group["key"], stage=item["status"],
                        actual=item["total"], expected=expected_count)
    groups = dashboard.context["desk_direction_groups"]
    if office is None:
        audit.check(all(group["disabled"] for group in groups), "global direction filters are unavailable",
                    user_id=user.pk)
    selections = [(None, stage) for stage in WORKING_STAGES]
    if office:
        selections.extend((direction, stage) for direction in ("incoming", "outgoing")
                          for stage in (None, *WORKING_STAGES))
        for group in groups:
            wanted = [record for record in active if manual_direction(record, office) == group["key"]
                      and stage_by_id[record.pk] in WORKING_STAGES]
            audit.check(group["count"] == len(wanted), "Dashboard direction count matches actual records",
                        user_id=user.pk, office=office.code, direction=group["key"],
                        actual=group["count"], expected=len(wanted))
            for child in group["stages"]:
                expected_count = sum(stage_by_id[record.pk] == child["status"] for record in wanted)
                audit.check(child["count"] == expected_count, "Dashboard stage count matches actual records",
                            user_id=user.pk, office=office.code, direction=group["key"], stage=child["status"],
                            actual=child["count"], expected=expected_count)
                linked = audit.page_records(client, child["tracking_url"])
                if linked is not None:
                    audit.check(len(linked) == child["count"], "Dashboard count link opens exactly its stated rows",
                                user_id=user.pk, url=child["tracking_url"], actual=len(linked), expected=child["count"])
    for direction, stage in selections:
        wanted = {record.pk: record for record in active
                  if (stage is None or stage_by_id[record.pk] == stage)
                  and (direction is None or (manual_direction(record, office) == direction
                                             and stage_by_id[record.pk] in WORKING_STAGES))
                  and (not (office and (user.is_system_admin or user.is_office_admin))
                       or touches_office(record, office))}
        params = {**common, "scope": direction, "status": stage}
        tracking_url = replace_query("/tracking/", **params)
        search_url = replace_query("/search/", mode="tracking", **params)
        for url in (tracking_url, search_url):
            rows = audit.page_records(client, url)
            if rows is not None:
                audit.check({record.pk for record in rows} == set(wanted), "complete filtered rows match receipt/work facts",
                            user_id=user.pk, url=url, expected=len(wanted), actual=len(rows))
                audit.check(all(record.display_status == stage_by_id[record.pk] for record in rows),
                            "displayed row statuses match receipt/work facts", user_id=user.pk, url=url)
        exported = audit.csv_records(client, replace_query(
            "/tracking/reports/export/", source="tracking", **params,
        ))
        if exported is not None:
            expected_numbers = {record.tracking_number for record in wanted.values()}
            actual_numbers = [row["Tracking number"] for row in exported]
            audit.check(len(actual_numbers) == len(set(actual_numbers)) and set(actual_numbers) == expected_numbers,
                        "CSV contains exactly the complete workspace selection", user_id=user.pk,
                        direction=direction, stage=stage, expected=len(expected_numbers), actual=len(actual_numbers))
            by_number = {record.tracking_number: stage_by_id[record.pk] for record in wanted.values()}
            audit.check(all(row["Status"] == dict(Status.choices).get(by_number.get(row["Tracking number"]))
                            for row in exported), "CSV statuses match recipient facts", user_id=user.pk,
                        direction=direction, stage=stage)
    for stage in WORKING_STAGES:
        exported = audit.csv_records(client, replace_query(
            "/tracking/reports/export/", **common, record_status=stage,
        ))
        if exported is not None:
            wanted = {record.tracking_number for record in report_population if stage_by_id[record.pk] == stage}
            numbers = [row["Tracking number"] for row in exported]
            audit.check(len(numbers) == len(set(numbers)) and set(numbers) == wanted,
                        "Reports CSV contains the complete report stage selection", user_id=user.pk,
                        office=office.code if office else "global", stage=stage,
                        actual=len(numbers), expected=len(wanted))


def reported_record_snapshot(audit):
    record = TrackingRecord.objects.filter(tracking_number=REPORTED_NUMBER).prefetch_related(
        Prefetch("routing_steps", to_attr="audit_steps"),
        Prefetch("activities", queryset=RecordActivity.objects.filter(
            event__in=[RecordActivity.Event.REMARK, RecordActivity.Event.ATTACHMENT],
        ), to_attr="audit_work"),
    ).first()
    if record is None:
        return {"tracking_number": REPORTED_NUMBER, "found": False}
    offices = {office.pk: office for office in Office.objects.all()}
    actual_global = TrackingRecord.objects.filter(pk=record.pk).with_stage_for().get().stage
    expected_global = manual_stage(record)
    recipients = []
    for step in record.audit_steps:
        if step.batch != record.current_batch:
            continue
        office = offices[step.to_office_id]
        actual = TrackingRecord.objects.filter(pk=record.pk).with_stage_for(office).get().stage
        expected = manual_stage(record, office)
        audit.check(actual == expected, "reported slip's recipient stage matches its receipt/work evidence",
                    office=office.code, expected=expected, actual=actual)
        if step.received_at is None:
            audit.check(actual == Status.PENDING_RECEIPT, "reported slip's unconfirmed recipient is Pending Receipt",
                        office=office.code, actual=actual)
        work = [activity for activity in record.audit_work
                if step.received_at is not None and activity.batch == record.current_batch
                and (activity.work_office_id or activity.actor_office_id) == office.pk
                and activity.created_at >= step.received_at
                and not activity.message.startswith("Returned to tracking by ")]
        recipients.append({"office": office.code, "received": step.received_at is not None,
                           "received_at": step.received_at.isoformat() if step.received_at else None,
                           "work_updates_after_receipt": len(work),
                           "expected_stage": expected, "query_stage": actual})
    audit.check(actual_global == expected_global, "reported slip's global stage matches recipient evidence",
                expected=expected_global, actual=actual_global)
    return {"tracking_number": REPORTED_NUMBER, "found": True, "stored_stage": record.status,
            "expected_global_stage": expected_global, "query_global_stage": actual_global,
            "current_batch": record.current_batch, "recipients": recipients}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-pages", action="store_true", help="Also check rendered pages, every pagination page, and CSV.")
    parser.add_argument("--report", type=Path, default=ROOT / "docs/verification/status-lifecycle-live-audit.json")
    args = parser.parse_args()
    audit = Audit()
    report = {"started_at": timezone.now().isoformat(), "database_changes": "all rolled back",
              "page_checks_requested": args.with_pages, "representatives": [], "warnings": []}
    setup_test_environment()
    try:
        with transaction.atomic():
            roles = (User.Role.USER, User.Role.ADMIN, User.Role.VIEWER, User.Role.SYSTEM_ADMIN)
            representatives = {}
            for user in User.objects.filter(is_active=True, role__in=roles).select_related("office").order_by("pk"):
                representatives.setdefault((user.role, user.office_id), user)
            report["roles_without_active_representatives"] = [role for role in roles
                                                               if not any(key[0] == role for key in representatives)]
            active_offices = list(Office.objects.filter(is_active=True).order_by("code"))
            for user in representatives.values():
                records = loaded_records(user)
                entry = {"user_id": user.pk, "role": user.role,
                         "assigned_office": user.office.code if user.office_id else None, "contexts": []}
                contexts = [None, *active_offices] if user.is_system_admin else [None, user.office]
                for office in {getattr(office, "pk", None): office for office in contexts}.values():
                    summary = check_queries(audit, user, records, office)
                    entry["contexts"].append(summary)
                    if args.with_pages and (user.is_system_admin or office is not None):
                        check_pages(audit, user, records, office)
                for record in records:
                    if record.status in WORKING_STAGES and not any(step.batch == record.current_batch for step in record.audit_steps):
                        warning = {"record_id": record.pk, "warning": "Working record has no current-batch routing steps"}
                        if warning not in report["warnings"]:
                            report["warnings"].append(warning)
                report["representatives"].append(entry)
            report["reported_record"] = reported_record_snapshot(audit)
            transaction.set_rollback(True)
    finally:
        teardown_test_environment()
    report.update({"finished_at": timezone.now().isoformat(), "checks": audit.check_count,
                   "requests": audit.request_count, "failure_count": audit.failure_count,
                   "failure_examples": audit.failures, "failure_examples_limit": MAX_FAILURE_EXAMPLES,
                   "passed": audit.failure_count == 0})
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": audit.check_count,
                      "requests": audit.request_count, "failures": audit.failure_count,
                      "representatives": len(report["representatives"]), "report": str(args.report)}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

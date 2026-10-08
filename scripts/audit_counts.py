"""Reconcile live page numbers without leaving database changes behind.

Run with the project's Python environment from the repository root. Login
sessions and any request logs are rolled back. No document is edited.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
from collections import Counter
from datetime import timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.db import transaction  # noqa: E402
from django.test import Client, override_settings  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402
from django.utils import timezone  # noqa: E402

from apps.accounts.models import Office, User  # noqa: E402
from apps.core.business_time import business_seconds_between, load_holidays, working_day_seconds  # noqa: E402
from apps.core.models import DocumentType, MetadataFieldDefinition, Tag, TagRule  # noqa: E402
from apps.core.notifications import unread_for  # noqa: E402
from apps.documents.models import COMPLETED_SOURCE, HISTORICAL_FILTER, Document  # noqa: E402
from apps.search.services import search_documents  # noqa: E402
from apps.tracking.models import COMPLETED_STATUSES, RoutingStep, Status, TrackingRecord  # noqa: E402


class Links(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.links = []
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.current = {**dict(attrs), "text": ""}

    def handle_data(self, data):
        if self.current is not None:
            self.current["text"] += data

    def handle_endtag(self, tag):
        if tag == "a" and self.current is not None:
            self.links.append(self.current)
            self.current = None


def replaced(url, **changes):
    parts = urlsplit(url)
    params = dict(parse_qsl(parts.query))
    params.pop("page", None)
    for key, value in changes.items():
        if value is None:
            params.pop(key, None)
        else:
            params[key] = value
    return parts.path + "?" + urlencode(params)


class Audit:
    def __init__(self):
        self.checked = 0
        self.failures = []
        self.cases = []
        self.label = ""
        self.responses = {}
        self.client = None
        self.sample_cache = {}
        self.integrity = {}

    def equal(self, name, actual, expected):
        self.checked += 1
        if actual != expected:
            self.failures.append({"case": self.label, "check": name, "actual": actual, "expected": expected})

    def get(self, url):
        parts = urlsplit(url)
        url = parts.path + ("?" + parts.query if parts.query else "")
        if url not in self.responses:
            response = self.client.get(url, secure=True)
            if response.status_code != 200:
                raise RuntimeError(f"{self.label}: {url} returned {response.status_code}")
            self.responses[url] = response
        return self.responses[url]

    def listed(self, url):
        context = self.get(url).context
        return context["report_records_count"] if "report_records_count" in context else context["total"]

    def scope(self, records, steps, office):
        if not office:
            return records
        touched = {step["record_id"] for step in steps if office.pk in (step["to_office_id"], step["from_office_id"])}
        return [row for row in records if row["pk"] in touched or office.pk in (row["originating_office_id"], row["current_office_id"])]

    def report(self, url, records, documents, steps):
        response = self.get(url)
        context = response.context
        scoped = self.scope(records, steps, context["filters"]["office"])
        ids = {row["pk"] for row in scoped}
        local_steps = [step for step in steps if step["record_id"] in ids]
        office = context["filters"]["office"]
        docs = [row for row in documents if not office or row["office_id"] == office.pk]
        statuses = Counter(row["status"] for row in scoped)
        current = [row for row in scoped if row["status"] not in COMPLETED_STATUSES]
        self.equal("report tracked documents", context["total_records"], len(scoped) if context["is_repository_report"] else len(current))
        self.equal("report Pending Receipt stage", context["pending_receipt"], statuses[Status.PENDING_RECEIPT])
        self.equal("report completed", context["completed_records"], sum(statuses[key] for key in COMPLETED_STATUSES))
        late = sum(bool(row["due_at"] and row["due_at"] < timezone.now() and row["status"] not in COMPLETED_STATUSES) for row in scoped)
        self.equal("report overdue", context["overdue_all"], late)
        self.equal("stage partition", sum(row["total"] for row in context["by_status"]), sum(row["status"] in (Status.PENDING_RECEIPT, Status.RECEIVED, Status.IN_PROCESS) for row in current))
        self.equal("repository report total", context["total_documents"], len(docs))
        self.equal("type chart partition", sum(row["total"] for row in context["document_types"]), len(docs))
        for row in context["by_status"]:
            self.equal("stage " + row["status"], row["total"], statuses[row["status"]])
            if not context["is_repository_report"]:
                self.equal("stage drilldown " + row["status"], self.listed(row["workspace_url"]), row["total"])
        for group in context.get("direction_stages", []):
            self.equal("direction partition", sum(row["total"] for row in group["stages"]), group["total"])
            self.equal("direction list", self.listed(group["workspace_url"]), group["total"])
            for row in group["stages"]:
                self.equal("direction status list", self.listed(row["workspace_url"]), row["total"])
        self.turnaround(context["turnaround"], scoped, local_steps)
        if not context["is_repository_report"]:
            self.trend(context["turnaround_trend"], scoped, local_steps)
        for state in ("due", "soon", "unscheduled"):
            self.equal("retention report " + state, context["retention"][state], self.listed(context["retention"][state + "_url"]))
        for row in context["document_months"]["rows"]:
            labelled_months = [item["month"] for item in context["document_months"]["rows"] if item["month"]]
            def belongs(doc, row=row, labelled_months=labelled_months):
                month = timezone.localtime(doc["created_at"]).date().replace(day=1)
                return month == row["month"] if row["month"] else month < min(labelled_months) if row["label"] == "Earlier" else month > max(labelled_months)
            added = [doc for doc in docs if belongs(doc)]
            self.equal("filed month", row["total"], len(added))
            self.equal("filed month from tracking", row["completed"], sum(doc["source"] == COMPLETED_SOURCE for doc in added))
        self.equal("repository chart partition", sum(row["total"] for row in context["document_months"]["rows"]), len(docs))
        if context["university_wide"]:
            flow = context["office_flow"]
            volume = context["office_volume"]
            self.equal("handovers", flow["handovers"], len(local_steps))
            self.equal("handover sender partition", sum(row["sent"] for row in flow["rows"]), sum(step["from_office_id"] is not None for step in local_steps))
            self.equal("confirmed handovers", volume["receipts"], sum(step["received_at"] is not None for step in local_steps))
            self.equal("receipt office partition", sum(row["cumulative"] for row in volume["rows"]), volume["receipts"])
        if context["is_repository_report"]:
            for row in context["document_types"]:
                self.equal("repository type drilldown", self.listed(row["workspace_url"]), row["total"])
        else:
            for link in Links(response.content.decode()).links:
                classes = link.get("class", "").split()
                if "stat-card" in classes:
                    if "gold" in classes:
                        self.equal("Pending Receipt headline link", self.listed(urljoin(url, link["href"])), context["pending_receipt"])
                        self.equal("rendered Pending Receipt", int(re.search(r"\b\d+\b", link["text"])[0]), context["pending_receipt"])
                    if "red" in classes:
                        self.equal("Overdue headline link", self.listed(urljoin(url, link["href"])), late)
                        self.equal("rendered Overdue", int(re.search(r"\b\d+\b", link["text"])[0]), late)

    def dashboard(self, url, records, steps):
        context = self.get(url).context
        scoped = self.scope(records, steps, context["scope"]["office"])
        active = [row for row in scoped if row["status"] != Status.COMPLETED]
        self.equal("dashboard tracking total", context["tracking_total_count"], len(active))
        self.equal("tracking-total link", self.listed(context["tracking_total_url"]), len(active))
        for queue in context["desk_queues"]:
            if queue["count"] is not None:
                self.equal("Action Centre " + queue["slug"], queue["count"], self.listed(queue["tracking_url"]))
        breakdown = context["breakdown"]
        self.equal("dashboard partition", sum(row["total"] for row in breakdown["slices"]), breakdown["total"])
        for row in breakdown["slices"]:
            self.equal("dashboard segment " + row["key"], row["total"], self.listed(row["url"]))
        rings = context["tracking_rings"]
        self.equal("pending-upload link", rings["pending_upload"]["total"], self.listed(rings["pending_upload"]["url"]))
        for ring in rings["rings"]:
            for mode in ("status", "overdue"):
                for row in ring[mode]["slices"]:
                    self.equal("ring slice", row["total"], self.listed(row["url"]))
        ids = {row["pk"] for row in scoped}
        local_steps = [step for step in steps if step["record_id"] in ids]
        self.turnaround(context["turnaround"], scoped, local_steps)
        self.trend(context["turnaround_trend"], scoped, local_steps, daily=True)
        for row in context["monthly"]["rows"]:
            month = row["month"]
            self.equal("cumulative created", row["created"], sum(timezone.localtime(item["created_at"]).date().replace(day=1) <= month for item in scoped))
            self.equal("cumulative handovers", row["transferred"], sum(timezone.localtime(step["sent_at"]).date().replace(day=1) <= month for step in local_steps))
            self.equal("cumulative completed", row["completed"], sum(bool(item["status"] in COMPLETED_STATUSES and item["completed_at"] and timezone.localtime(item["completed_at"]).date().replace(day=1) <= month) for item in scoped))

    def samples(self, records, steps):
        cache_key = frozenset(row["pk"] for row in records)
        if cache_key in self.sample_cache:
            return self.sample_cache[cache_key]
        holidays = load_holidays()
        now = timezone.now()
        samples = {"receipt": [], "processing": [], "lifetime": []}
        for step in steps:
            if step["sent_at"] and step["received_at"] and step["sent_at"] <= step["received_at"] <= now:
                samples["receipt"].append((timezone.localdate(step["received_at"]), business_seconds_between(step["sent_at"], step["received_at"], holidays), step["record_id"], (step["received_at"] - step["sent_at"]).total_seconds()))
        for row in records:
            end = row["completed_at"]
            if row["status"] in COMPLETED_STATUSES and end and row["created_at"] <= end <= now:
                samples["lifetime"].append((timezone.localdate(end), business_seconds_between(row["created_at"], end, holidays), row["pk"], (end - row["created_at"]).total_seconds()))
                if row["first_received_at"] and row["created_at"] <= row["first_received_at"] <= end:
                    samples["processing"].append((timezone.localdate(end), business_seconds_between(row["first_received_at"], end, holidays), row["pk"], (end - row["first_received_at"]).total_seconds()))
        self.sample_cache[cache_key] = samples
        return samples

    def turnaround(self, panel, records, steps):
        samples = self.samples(records, steps)
        for stage in panel["stages"]:
            selected = [sample for sample in samples[stage["key"]] if sample[0].replace(day=1) == panel["month"]]
            values = [sample[1] for sample in selected]
            self.equal("monthly turnaround sample count", stage["samples"], len(values))
            self.equal("monthly unique documents", stage["documents"], len({sample[2] for sample in selected}))
            self.equal("monthly zero observations", stage["zero_working_time"], sum(value == 0 for value in values))
            self.equal("monthly instant observations", stage["instantaneous"], sum(sample[3] == 0 for sample in selected))
            self.equal("monthly outside office hours", stage["outside_office_hours"], sum(sample[1] == 0 and sample[3] > 0 for sample in selected))
            self.equal("monthly turnaround mean", stage["average_seconds"], sum(values) / len(values) if values else None)
            self.equal("monthly elapsed mean", stage["average_calendar_seconds"], sum(sample[3] for sample in selected) / len(selected) if selected else None)
        closed = [row for row in records if row["status"] in COMPLETED_STATUSES and row["completed_at"] and row["due_at"] and row["created_at"] <= row["completed_at"] <= panel["as_of"] and timezone.localdate(row["completed_at"]).replace(day=1) == panel["month"]]
        self.equal("on-time denominator", panel["on_time_total"], len(closed))
        self.equal("on-time numerator", panel["on_time"], sum(row["completed_at"] <= row["due_at"] for row in closed))
        batches = {row["pk"]: row for row in records}
        outstanding = [step for step in steps if step["received_at"] is None and step["sent_at"] <= panel["as_of"] and step["batch"] == batches[step["record_id"]]["current_batch"] and batches[step["record_id"]]["status"] not in COMPLETED_STATUSES]
        self.equal("outstanding handovers", panel["awaiting_confirmation"], len(outstanding))
        self.equal("documents awaiting receipt", panel["awaiting_confirmation_documents"], len({step["record_id"] for step in outstanding}))
        pending_ids = {step["record_id"] for step in outstanding if batches[step["record_id"]]["status"] == Status.PENDING_RECEIPT}
        self.equal("waiting Pending Receipt documents", panel["awaiting_confirmation_pending_documents"], len(pending_ids))
        self.equal("waiting additional recipients", panel["awaiting_confirmation_other_documents"], len({step["record_id"] for step in outstanding} - pending_ids))

    def trend(self, chart, records, steps, daily=False):
        samples = self.samples(records, steps)
        for row in chart["rows"]:
            for key in samples:
                selected = [sample for sample in samples[key] if (sample[0] if daily else sample[0].replace(day=1)) == row["month"]]
                values = [sample[1] for sample in selected]
                self.equal("trend sample count", row[key + "_samples"], len(values))
                self.equal("trend unique documents", row[key + "_documents"], len({sample[2] for sample in selected}))
                self.equal("trend zero observations", row[key + "_zero_working_time"], sum(value == 0 for value in values))
                self.equal("trend working-day average", row[key], sum(values) / len(values) / working_day_seconds() if values else None)

    def repository(self, url):
        context = self.get(url).context
        self.equal("filed tab link", context["filed_count"], self.listed(context["filed_url"]))
        self.equal("pending tab link", context["pending_count"], self.listed(context["pending_url"]))
        self.equal("all-folders link", context["all_count"], self.listed(context["all_folders_url"]))
        for folder in context["type_folders"]:
            self.equal("type facet", folder["total"], self.listed(folder["url"]))
        if not context["pending_view"]:
            for tag in context["popular_tags"]:
                self.equal("tag facet", tag.visible_usage, self.listed(replaced(url, tag=tag.pk)))
            self.equal("retention link", context["retention_due_count"], self.listed(replaced(url, retention="due")))
        for folder in context["smart_folders"]:
            self.equal("office folder", folder["total"], self.listed(folder["url"]))
            self.equal("office pending folder", folder.get("pending_count", 0), self.listed(replaced(folder["url"], view="pending")))

    def run(self, *, quick=False):
        offices = list(Office.active.order_by("pk"))
        users = [User.objects.filter(role=role, is_active=True, office__isnull=False).order_by("pk").first() for role in User.Role.values]
        for user in filter(None, users):
            self.client = Client()
            self.client.force_login(user)
            self.responses = {}
            records = list(TrackingRecord.objects.visible_to(user).values("pk", "status", "originating_office_id", "current_office_id", "created_at", "completed_at", "first_received_at", "current_batch", "due_at"))
            documents = list(Document.objects.visible_to(user).filter(is_active=True).values("pk", "office_id", "source", "created_at"))
            steps = list(RoutingStep.objects.filter(record_id__in=[row["pk"] for row in records]).values("record_id", "from_office_id", "to_office_id", "batch", "sent_at", "received_at"))
            if user.is_system_admin:
                pending_ids = {step["record_id"] for step in steps if step["received_at"] is None and step["batch"] == next(row["current_batch"] for row in records if row["pk"] == step["record_id"])}
                self.integrity = {
                    "pending_stage_without_current_unreceived_step": sum(row["status"] == Status.PENDING_RECEIPT and row["pk"] not in pending_ids for row in records),
                    "completed_without_completion_timestamp": sum(row["status"] in COMPLETED_STATUSES and row["completed_at"] is None for row in records),
                    "completion_before_creation": sum(bool(row["completed_at"] and row["completed_at"] < row["created_at"]) for row in records),
                    "received_without_first_receipt_timestamp": sum(row["status"] in (Status.RECEIVED, Status.IN_PROCESS) and row["first_received_at"] is None for row in records),
                }
            picked = ["all", *(str(office.pk) for office in offices)] if user.is_office_admin else ["", "all", str(offices[-1].pk)]
            if quick:
                picked = ["all", str(user.office_id)] if user.is_office_admin else [""]
            for scope in picked:
                self.label = f"{user.role}:{user.office.code}:office={scope or 'default'}"
                previous = (timezone.localdate().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
                suffix = "?office=" + scope + "&month=" + previous
                before = len(self.failures)
                self.report("/tracking/reports/" + suffix, records, documents, steps)
                self.report("/documents/reports/" + suffix, records, documents, steps)
                self.dashboard("/" + suffix, records, steps)
                self.repository("/documents/" + suffix)
                self.cases.append(self.label)
                print(f"Checked {self.label}: {len(self.failures) - before} mismatches", flush=True)
            self.label = f"{user.role}:filtered lists"
            for extra in ("view=pending", "q=memorandum", f"source={HISTORICAL_FILTER}", "retention=unscheduled", "month=9", "view=folders&q=memorandum", "view=pending&q=memorandum&month=9"):
                self.repository("/documents/?" + extra)
            for status in Status.values:
                for extra in ("", "&overdue=yes", "&scope=incoming"):
                    url = f"/tracking/?status={status}{extra}"
                    page = self.get(url)
                    report = page.context
                    export_link = next(link["href"] for link in Links(page.content.decode()).links if link["text"].strip() == "Export CSV")
                    exported = self.get(export_link)
                    rows = list(csv.reader(io.StringIO(exported.content.decode())))
                    header = next(index for index, row in enumerate(rows) if row and row[0] == "Tracking number")
                    self.equal("CSV matches filtered list", len(rows) - header - 1, min(report["total"], 5000))
                    self.equal("CSV cap metadata", int(rows[2][1]), min(report["total"], 5000))
            badge = self.get("/notifications/count/").context
            self.equal("notification badge", badge["unread_notifications"], unread_for(user).count() if badge["notification_in_app_enabled"] else 0)
            browse = search_documents(user=user, limit=2, log=False)
            self.equal("search browse total beyond cap", browse.total_matches, len(documents))
            self.equal("search browse displayed", len(browse.results), min(2, len(documents)))
            tracking = self.get("/tracking/?per_page=100").context
            search = self.get("/search/?mode=tracking&per_page=100").context
            self.equal("tracking search total", tracking["total"], search["total"])
            self.equal("tracking search record set", sorted(row.pk for row in tracking["records"]), sorted(row.pk for row in search["results"]))
            if user.is_office_admin:
                admin = self.get("/administration/").context
                self.equal("active user card", admin["user_count"], self.get("/accounts/users/?status=active").context["page_obj"].paginator.count)
                if user.is_system_admin:
                    for name, model in (("document-types", DocumentType), ("tags", Tag), ("metadata-rules", TagRule), ("metadata-fields", MetadataFieldDefinition)):
                        self.equal("active master data " + name, model.objects.filter(is_active=True).count(), self.get(f"/administration/{name}/?status=active").context["page_obj"].paginator.count)
                    for tag in self.get("/administration/tags/?per_page=all").context["objects"]:
                        self.equal("live visible tag usage", tag.visible_usage, Document.objects.visible_to(user).filter(is_active=True, tags=tag).distinct().count())
                    self.equal("active offices", admin["office_count"], self.get("/administration/offices/?status=active").context["page_obj"].paginator.count)
                    self.equal("search query partition", sum(row["total"] for row in admin["top_searches"]), admin["search_analytics"]["queries"])
                    self.equal("search click partition", sum(row["clicks"] for row in admin["top_searches"]), admin["search_analytics"]["clicks"])


def main():
    setup_test_environment()
    audit = Audit()
    with override_settings(ALLOWED_HOSTS=["testserver"], SECURE_SSL_REDIRECT=False), transaction.atomic():
        try:
            audit.run(quick="--quick" in sys.argv)
        finally:
            transaction.set_rollback(True)
    result = {"checks": audit.checked, "scopes": len(audit.cases), "data_integrity": audit.integrity, "failures": audit.failures}
    print(json.dumps(result, indent=2, default=str))
    outputs = [arg for arg in sys.argv[1:] if arg != "--quick"]
    if outputs:
        Path(outputs[0]).write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return bool(audit.failures)


if __name__ == "__main__":
    raise SystemExit(main())

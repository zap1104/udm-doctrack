"""Read-only checks of filter choices, query results, and GET form preservation.

Uses the current local database. Sessions and request logs are rolled back.
Run from the repository root with the project's Python environment.
"""

from __future__ import annotations

import json
import os
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.db import transaction  # noqa: E402
from django.test import Client, override_settings  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402
from django.utils import timezone  # noqa: E402

from apps.accounts.models import Office, User  # noqa: E402
from apps.core.models import DocumentType, Tag  # noqa: E402
from apps.documents.models import Document  # noqa: E402
from apps.tracking.models import Status, TrackingRecord  # noqa: E402
from apps.tracking.services import pending_upload_for  # noqa: E402


class Forms(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.forms = []
        self.current = None
        self.feed(text)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "form":
            self.current = {"attrs": attrs, "hidden": {}}
            self.forms.append(self.current)
        elif tag == "input" and self.current and attrs.get("type") == "hidden" and "disabled" not in attrs:
            self.current["hidden"].setdefault(attrs.get("name"), []).append(attrs.get("value", ""))

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


class FilterAudit:
    def __init__(self):
        self.checked = 0
        self.failures = []
        self.roles = []

    def equal(self, label, actual, expected):
        self.checked += 1
        if actual != expected:
            self.failures.append({"role": self.user.role, "check": label, "actual": str(actual), "expected": str(expected)})

    def get(self, path, params=None):
        response = self.client.get(path, params or {})
        self.equal("page responds: " + path, response.status_code, 200)
        return response

    def run(self):
        for role in User.Role.values:
            self.user = User.objects.filter(role=role, is_active=True, office__isnull=False).order_by("pk").first()
            if not self.user:
                continue
            self.roles.append(role)
            self.client = Client()
            self.client.force_login(self.user)
            visible = Document.objects.visible_to(self.user).filter(is_active=True)
            tracking = TrackingRecord.objects.visible_to(self.user)
            self.repository(visible)
            self.search(visible)
            self.form_preservation()
            for bad in ("unknown", "²", "9" * 65):
                for path in ("/", "/tracking/", "/tracking/reports/", "/documents/reports/", "/documents/"):
                    self.get(path, {"office": bad, "offices": bad, "trend_year": bad, "status": bad,
                                    "record_month": bad, "record_type": bad, "year": bad, "month": bad,
                                    "document_type": bad, "tag": bad, "source": bad, "retention": bad,
                                    "owner": bad, "sort": bad, "scope": bad, "overdue": bad,
                                    "turnaround_stage": bad, "per_page": bad, "page": bad})
            for path in ("/tracking/reports/", "/documents/reports/"):
                for month in ("0001-01", "9999-12", "2026-1"):
                    response = self.get(path, {"record_month": month})
                    self.equal("invalid report month is cleared", response.context["record_month"], "")
            for path, data in (
                ("/tracking/", {"status": [Status.PENDING_RECEIPT, Status.IN_PROCESS], "per_page": "all"}),
                ("/search/", {"mode": "tracking", "status": [Status.PENDING_RECEIPT, Status.IN_PROCESS], "per_page": "all"}),
                ("/tracking/reports/", {"record_status": Status.PENDING_RECEIPT, "per_page": "all"}),
            ):
                response = self.get(path, data)
                key = "results" if path == "/search/" else "records" if path == "/tracking/" else "page_obj"
                rows = response.context[key]
                self.equal("tracking results stay within permitted rows", {row.pk for row in rows}.issubset(set(tracking.values_list("pk", flat=True))), True)
            if self.user.is_office_admin:
                self.administration()
            print(f"Checked filter validity for {role}", flush=True)

    def repository(self, visible):
        for office in [None, *Office.active.filter(documents__in=visible).distinct()]:
            params = {"office": office.pk} if office else {}
            base = visible.filter(office=office) if office else visible
            response = self.get("/documents/", {**params, "per_page": "all"})
            self.equal("folder result IDs", {row.pk for row in response.context["documents"]}, set(base.values_list("pk", flat=True)))
            form = response.context["form"]
            self.equal("type choices match accessible folder", set(form.fields["document_type"].queryset.values_list("pk", flat=True)), set(base.exclude(document_type=None).values_list("document_type_id", flat=True)))
            self.equal("tag choices match accessible folder", set(form.fields["tag"].queryset.values_list("pk", flat=True)), set(Tag.active.filter(documents__in=base).values_list("pk", flat=True)))
            for field, model_field in (("document_type", "document_type_id"), ("year", "year"), ("month", "document_date__month"), ("tag", "tags__pk"), ("source", "source")):
                value = base.exclude(**{model_field: None}).values_list(model_field, flat=True).first()
                if value:
                    result = self.get("/documents/", {**params, field: value, "per_page": "all"})
                    self.equal("repository filter " + field, {row.pk for row in result.context["documents"]}, set(base.filter(**{model_field: value}).values_list("pk", flat=True)))
        pending = pending_upload_for(self.user).filter(is_archived=False)
        result = self.get("/documents/", {"view": "pending", "retention": "due", "source": "DTS", "per_page": "all"})
        self.equal("unsupported filed filters do not claim to filter pending uploads", result.context["selected_filters"], [])
        self.equal("pending queue permission scope", {row.pk for row in result.context["pending_upload"]}, set(pending.values_list("pk", flat=True)))
        other = Office.active.exclude(pk__in=set(visible.values_list("office_id", flat=True)) | set(pending.values_list("originating_office_id", flat=True))).first()
        if other:
            denied = self.get("/documents/", {"office": other.pk})
            self.equal("unavailable folder returns no records", denied.context["total"], 0)

    def search(self, visible):
        response = self.get("/search/")
        form = response.context["form"]
        self.equal("search offices are permission scoped", set(form.fields["office"].queryset.values_list("pk", flat=True)), set(Office.active.filter(documents__in=visible).values_list("pk", flat=True)))
        self.equal("search includes retired types still used by visible records", set(form.fields["document_type"].queryset.values_list("pk", flat=True)), set(DocumentType.objects.filter(documents__in=visible).values_list("pk", flat=True)))
        for data in ({"office": "²"}, {"document_type": "9" * 65}, {"year": "²"}, {"date_from": "2026-10-20", "date_to": "2026-10-01"}):
            response = self.get("/search/", data)
            self.equal("invalid search displays form errors", bool(response.context["form"].errors), True)

    def form_preservation(self):
        year = str(timezone.localdate().year)
        params = {"office": self.user.office_id, "trend_year": year, "record_q": "memo", "record_status": Status.PENDING_RECEIPT, "per_page": "50"}
        response = self.get("/tracking/reports/", params)
        forms = Forms(response.content.decode()).forms
        year_form = next(form for form in forms if "report-year-picker" in form["attrs"].get("class", ""))
        for name in ("office", "record_q", "record_status", "per_page"):
            self.equal("year picker retains " + name, year_form["hidden"].get(name), [str(params[name])])
        self.equal("year picker does not duplicate controlled value", "trend_year" in year_form["hidden"], False)
        records_form = next(form for form in forms if form["attrs"].get("action") == "#report-records")
        self.equal("record filters retain page size", records_form["hidden"].get("per_page"), ["50"])
        reset = parse_qs(urlsplit(response.context["report_records_url"]).query)
        self.equal("record reset keeps selected chart year", reset.get("trend_year"), [year])
        if self.user.is_office_admin:
            params = {"status": [Status.PENDING_RECEIPT, Status.IN_PROCESS], "sort": "due_asc", "per_page": "50"}
            response = self.get("/tracking/", params)
            form = next(form for form in Forms(response.content.decode()).forms if "dashboard-scope" in form["attrs"].get("class", ""))
            self.equal("office picker retains every selected status", sorted(form["hidden"].get("status", [])), sorted(params["status"]))
            self.equal("office picker retains sort", form["hidden"].get("sort"), [params["sort"]])

    def administration(self):
        for bad in ("²", "9" * 65, "999999"):
            self.get("/accounts/users/", {"office": bad, "role": bad, "status": bad, "q": "admin"})
            self.get("/administration/audit-log/", {"office": bad, "action": bad, "event": bad, "since": bad, "until": bad, "access_since": bad, "access_until": bad})
        for slug in ("document-types", "tags", "metadata-rules", "metadata-fields"):
            self.get(f"/administration/{slug}/", {"status": "unknown", "q": "a", "page": "²"})
        for data in ({"kind": "unknown", "filter": "unknown"}, {"kind": "ROUTED", "filter": "unread"}):
            self.get("/notifications/", data)


def main():
    setup_test_environment()
    audit = FilterAudit()
    with override_settings(ALLOWED_HOSTS=["testserver"], SECURE_SSL_REDIRECT=False), transaction.atomic():
        try:
            audit.run()
        finally:
            transaction.set_rollback(True)
    result = {"checks": audit.checked, "roles": audit.roles, "failures": audit.failures}
    print(json.dumps(result, indent=2))
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return bool(audit.failures)


if __name__ == "__main__":
    raise SystemExit(main())

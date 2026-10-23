"""Build a source/URL inventory, without querying a database or calling providers.

Run with the project's Python: python scripts/audit_inventory.py
The resolver always uses config.audit_settings and audit slot inventory. Output
is a mapping ledger, not proof that a route, permission, or feature passed.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import inspect
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "docs/verification/systemwide-audit-2026-10-17"
MAPPED = "mapped/notproved"

# Manually reviewed business contracts. Each inventory item links to one domain.
# These describe expected boundaries; they are not automated authorization proof.
DOMAINS = {
    "accounts": {
        "behavior": "Sign in/out, lockout, password reset/change, session extension, profile and verified email; create/edit/suspend accounts and maintain internal offices.",
        "role_policy": "Anonymous may use sign-in/reset/lockout and token confirmation. Authenticated active users manage their own profile/session. SYSTEM_ADMIN administers globally; office ADMIN only administers their own office and must not gain system authority. Inactive users and retired-office writers are refused.",
        "scenarios": "Lockout/CSRF; password races; email ownership/token age; idle expiry; own-office administration; self-lockout and last-admin protections.",
    },
    "core": {
        "behavior": "Dashboard, notifications/read/count, memo/log printing, reports/CSV, audit log, master data and working calendar.",
        "role_policy": "Read data starts from the user's existing visibility scope. Notification rows also require access to their referenced object. SYSTEM_ADMIN alone has global administration; office ADMIN stays within its office. Calendar/master-data writes require their specific administrator authority.",
        "scenarios": "Own/shared/history scopes; OVPA/external collection separation; expected-ID reconciliation; overlapping facets and partial periods; hidden notification targets; holiday/break/zero-time boundaries.",
    },
    "tracking": {
        "behavior": "Internal draft/review/numbering, routing and current-batch receipts, remarks/files, completion, filing, reopen, grants, slips and signed attachments; stage/deadline/processing analytics.",
        "role_policy": "SYSTEM_ADMIN may administer globally; active staff/admin writes require legitimate office/custody/action authority. VIEWER remains read-only. Read grants and historical visibility do not automatically authorize mutation. Current-batch and recipient-specific authority must be rechecked after locks.",
        "scenarios": "Partial/multiple recipients; stale and double submits; receipt races; previous-batch exclusion; lifecycle/filing/reopen; append-only history; signed current-user/object downloads; SLA/calendar and numbering races.",
    },
    "external_tracking": {
        "behavior": "Separate external-office directory and record lifecycle: create, dispatch/return, internal/external receipt, work/remarks/files, complete/reopen, corrections, slips, exports and reports.",
        "role_policy": "Responsible-office ADMIN or SYSTEM_ADMIN controls manual external actions, corrections, reopen and filing. Responsibility follows confirmed internal custody, retains the last internal holder during external custody and falls back to the recording office before any internal receipt. Directory changes remain restricted to the registering-office ADMIN or SYSTEM_ADMIN; active directory entries are shared choices. Active USER/ADMIN at the current internal holder may perform permitted custody actions; only the pending recipient office confirms internal receipt. VIEWER cannot mutate. Visibility, revision and record state are rechecked for every action; external entries must not expand OVPA populations.",
        "scenarios": "Revision conflicts; confirmed-custody authority transfer; shared directory and protected editing; actual-time ordering; external custody/receipt wait separation; correction audit trail; external attachment signatures; collection-separated reports and filing.",
    },
    "documents": {
        "behavior": "OVPA repository/upload/review/edit/files, extraction/retry/status, private downloads, tags, pending filing and deletion review/recovery/purge.",
        "role_policy": "Repository reads use Document.visible_to and OVPA collection. Staff/admin writes require document authority; VIEWER cannot mutate. Deletion decisions require explicit review authority and fresh signed state. Private downloads require current visibility plus matching user/object/kind/time signature.",
        "scenarios": "Metadata/tag isolation; duplicate uploads; retained text after OCR failure; rollback of newly created storage only; queue failure after commit; reversed retry completion; review concurrency and purge failures.",
    },
    "external_repository": {
        "behavior": "External repository/upload/report/deletion queue and filing from external tracking, with separate collection and filing-office metadata.",
        "role_policy": "External collection remains separate from legacy OVPA documents. New filings use the responsible office; existing filed ownership remains unchanged. Repository read and mutation authority retain explicit document rules. SYSTEM_ADMIN alone is global; VIEWER remains read-only.",
        "scenarios": "Mixed-collection expected IDs; concurrent filing once; external directories; private files; inherited extraction/deletion rules; no legacy OVPA count or tag leakage.",
    },
    "search": {
        "behavior": "Repository and tracking search, filters, ranking/identifier priority, autocomplete and search-click logging.",
        "role_policy": "Every result, autocomplete term and click target must remain within current visibility and the requested collection. A result read does not grant mutation authority. Filters may narrow an authorized base population only.",
        "scenarios": "Exact-ID filter intersections; invalid/retired choices; relevance cap versus exhaustive repository; identifier priority; duplicate types/tags; pagination and query preservation.",
    },
    "jobs": {
        "behavior": "Receipt/deadline/external-followup chases, notification/throttle pruning, extraction generations, deleted-document purge and persisted schedules.",
        "role_policy": "Trusted worker execution, with current active actor/document/office checks where a task represents a user's mutation. Jobs must preserve visibility, current-batch scope, collection separation, history and retry idempotence.",
        "scenarios": "Repeated/overlapping jobs; stale actor; suppressed notifications; retention/legal holds; missing provider bytes; queue failure; superseded extraction; real providers remain unverified.",
    },
    "commands": {
        "behavior": "Database initialization, guarded demo seeding, administrator creation/login recovery, schedules/selfcheck, training export/reindex, deletion purge and stage reconciliation.",
        "role_policy": "Trusted operator commands are outside end-user HTTP roles. Destructive/seed operations require their existing safeguards. This inventory never invokes handle(), creates users, seeds, migrates or writes business data.",
        "scenarios": "Fresh isolated database; dry-run/apply distinctions; repeatability; invalid options; failure recovery; background enabled/disabled. A source listing is not an execution test.",
    },
    "raw_admin": {
        "behavior": "Django raw administration site and every registered model's generated list/add/change/delete/history/autocomplete routes.",
        "role_policy": "SystemAdminSite.has_permission requires Django active staff authority AND is_system_admin. Office ADMIN, staff and VIEWER must not enter raw admin; Django model permissions remain applicable within the site.",
        "scenarios": "Direct raw-admin URLs including login, model change/delete/history, autocomplete and password changes. Generated route discovery alone proves no permission scenario.",
    },
}


def relative(path):
    return Path(path).resolve().relative_to(ROOT).as_posix()


def source_files():
    return sorted(p for p in (ROOT / "apps").rglob("*.py") if not {"migrations", "__pycache__"}.intersection(p.parts))


def expression(node):
    return ast.unparse(node) if node is not None else ""


def literal(node, default=None):
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        return default


def domain_for(path, symbol=""):
    if "/management/commands/" in path:
        return "commands"
    if path.endswith("/tasks.py"):
        return "jobs"
    if "external" in path or symbol.startswith("External"):
        return "external_repository" if "/documents/" in path else "external_tracking"
    for domain in ("accounts", "core", "tracking", "documents", "search"):
        if f"/{domain}/" in path:
            return domain
    return "core"


def candidate_tests(tests, *needles):
    """Literal/symbol references are candidates, not assertion-level coverage."""
    result = []
    for path, source in tests.items():
        if any(needle and re.search(r"(?<![\w])" + re.escape(needle) + r"(?![\w])", source) for needle in needles):
            result.append(path)
    return result


def item(path, symbol, tests, **extra):
    return {
        "source": path, "symbol": symbol, "domain": domain_for(path, symbol),
        "status": MAPPED, "verification": "unverified",
        "candidate_test_files": candidate_tests(tests, symbol), **extra,
    }


def ast_inventory(files, tests):
    forms, services, jobs, commands, query_parameters, source_routes, dispatchers = [], [], [], [], [], [], []
    for path in files:
        name = relative(path)
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=name)
        if name.endswith("urls.py"):
            for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
                route_name = next((literal(k.value) for k in call.keywords if k.arg == "name"), None)
                if route_name:
                    source_routes.append({"source": name, "line": call.lineno, "name": route_name,
                                          "pattern": literal(call.args[0]) if call.args else None})
        is_form_file = "forms" in path.stem or path.stem == "filter_fields"
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and is_form_file:
                fields, factories, meta = [], [], []
                for member in node.body:
                    if isinstance(member, ast.Assign) and isinstance(member.value, ast.Call):
                        for target in member.targets:
                            if isinstance(target, ast.Name):
                                factory = expression(member.value.func)
                                if factory.endswith("Field") or factory.endswith("_field") or factory == "actual_time":
                                    fields.append(target.id)
                                    factories.append({"name": target.id, "factory": factory,
                                                      "required": next((literal(k.value, expression(k.value)) for k in member.value.keywords if k.arg == "required"), "default")})
                    if isinstance(member, ast.ClassDef) and member.name == "Meta":
                        for assignment in member.body:
                            if isinstance(assignment, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "fields" for t in assignment.targets):
                                meta = literal(assignment.value, expression(assignment.value))
                forms.append(item(name, node.name, tests, line=node.lineno,
                                  bases=[expression(b) for b in node.bases], declared_fields=fields,
                                  field_declarations=factories, model_meta_fields=meta,
                                  filter_form="Filter" in node.name or "Search" in node.name,
                                  note="AST declarations plus resolver-imported base_fields; no form instantiated."))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                row = item(name, node.name, tests, line=node.lineno,
                           signature=expression(node.args),
                           decorators=[expression(d) for d in node.decorator_list],
                           description=ast.get_docstring(node) or "",
                           private_helper=node.name.startswith("_"))
                if path.stem.endswith("services"):
                    services.append(row)
                if path.stem == "tasks":
                    jobs.append(row)
            if isinstance(node, ast.ClassDef) and "/management/commands/" in name and node.name == "Command":
                methods = [m.name for m in node.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
                commands.append(item(name, path.stem, tests, line=node.lineno, methods=methods,
                                     candidate_test_files=candidate_tests(tests, path.stem)))
        # Include query controls outside forms (reports, pagination, mode, HTMX).
        # Only explicit GET/getlist access is collected; indirect aliases are noted.
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
                action_name = next((t.id for t in node.targets if isinstance(t, ast.Name) and t.id in {"ACTION_FORMS", "functions"}), None)
                if action_name:
                    entries = [{"action": literal(k), "target": expression(v)} for k, v in zip(node.value.keys, node.value.values, strict=True) if isinstance(literal(k), str)]
                    if entries:
                        dispatchers.append(item(name, action_name, tests, line=node.lineno, entries=entries))
            key = None
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"get", "getlist"} and node.args:
                if "GET" in expression(node.func.value):
                    key = literal(node.args[0])
            if isinstance(node, ast.Subscript) and "GET" in expression(node.value):
                key = literal(node.slice)
            if isinstance(key, str):
                query_parameters.append(item(name, key, tests, line=node.lineno))
    return {"forms": forms, "service_actions": services, "jobs": jobs, "commands": commands,
            "query_parameters": query_parameters, "source_named_routes": source_routes,
            "action_dispatchers": dispatchers}


def resolver_inventory(tests, forms):
    """Import URL configuration under audit settings; block every DB connection."""
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.audit_settings"
    os.environ["DOCTRACK_AUDIT_SLOT"] = "inventory"
    os.environ["ENABLE_BACKGROUND_TASKS"] = "False"
    sys.path.insert(0, str(ROOT))
    from django.db.backends.base.base import BaseDatabaseWrapper

    def refuse_connection(*args, **kwargs):
        raise RuntimeError("Inventory forbids database connections.")

    BaseDatabaseWrapper.ensure_connection = refuse_connection
    import django
    django.setup()
    from django.forms import BaseForm
    from django.urls import URLPattern, URLResolver, get_resolver

    routes = []

    def walk(patterns, prefix="", namespaces=()):
        for entry in patterns:
            pattern = prefix + str(entry.pattern)
            if isinstance(entry, URLResolver):
                walk(entry.url_patterns, pattern, namespaces + ((entry.namespace,) if entry.namespace else ()))
            elif isinstance(entry, URLPattern):
                callback = entry.callback
                view = getattr(callback, "view_class", callback)
                module = getattr(view, "__module__", "")
                symbol = getattr(view, "__qualname__", repr(view))
                source = inspect.getsourcefile(view)
                try:
                    source_name = relative(source) if source else module
                except ValueError:
                    source_name = module
                route_name = ":".join((*namespaces, entry.name)) if entry.name else None
                row = item(source_name, symbol, tests, name=route_name, pattern=pattern,
                           named=bool(entry.name), namespaces=list(namespaces),
                           callback=f"{module}.{symbol}",
                           view_bases=[base.__name__ for base in getattr(view, "__mro__", ())],
                           candidate_test_files=candidate_tests(tests, route_name, symbol))
                if "admin" in namespaces:
                    row["domain"] = "raw_admin"
                routes.append(row)

    walk(get_resolver().url_patterns)
    for form in forms:
        module_name = form["source"].removesuffix(".py").replace("/", ".")
        module = sys.modules.get(module_name)
        cls = getattr(module, form["symbol"], None) if module else None
        if inspect.isclass(cls) and issubclass(cls, BaseForm):
            form["inherited_runtime_fields"] = list(cls.base_fields)
    return routes


def evidence_inventory(output):
    """Preserve red/error logs alongside green runs; never promote item status."""
    evidence = []
    for path in sorted(output.rglob("*.txt")):
        if path.name == "inventory-run.txt":
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        summaries = [line.strip() for line in text.splitlines() if re.search(r"\b\d+ (?:passed|failed|errors?|skipped)\b", line) and not line.startswith(("FAILED ", "ERROR ", "SKIPPED "))]
        evidence.append({"path": relative(path), "bytes": path.stat().st_size,
                         "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                         "reported_summary": summaries[-1] if summaries else None,
                         "classification": "reported-test-run" if summaries else "other-or-incomplete-log",
                         "claim_limit": "Historical log only; not final-revision or per-item proof."})
    return evidence


def markdown(data):
    lines = ["# Systemwide audit coverage inventory", "",
             "Generated by `scripts/audit_inventory.py`. This ledger separates discovered surfaces and candidate tests from executed proof. All individual inventory items remain **mapped/notproved**, with verification **unverified**. A test file reference does not mean a route or permission case passed.", "",
             "The final audit report owns final-revision suite outcomes. Historical red, error, skipped and green logs below are retained as evidence of their particular run only; they are not silently converted into a final pass.", "",
             "## Reproduce safely", "", "```powershell", ".\\.venv\\Scripts\\python.exe scripts\\audit_inventory.py", "```", "",
             "The script forces `config.audit_settings`, `DOCTRACK_AUDIT_SLOT=inventory` and disabled background work before Django setup; database connections are rejected. It never instantiates forms, executes service actions, invokes commands, or contacts providers. `--static-only` explicitly skips resolver discovery and cannot claim full route discovery.", "",
             "## Discovery totals", "", "| Surface | Items | Coverage status |", "| --- | ---: | --- |"]
    for label, count in data["counts"].items():
        lines.append(f"| {label.replace('_', ' ')} | {count} | {MAPPED} |")
    lines += ["", f"Resolver status: **{data['resolver_status']}**.", "",
              "`inventory.json` contains each resolved route (including raw admin and unnamed redirects), view/mixins, form declarations/inherited fields, explicit query keys, service/job signatures, command methods, source hashes, and candidate test files. Conditional source route declarations are listed separately so disabled settings do not hide them.", "",
              "## Feature and role ledger", "",
              "Each contract below is manually described from the application domains. The linked source/test mapping provides audit targets. These policy statements describe required behavior and still need scenario-specific evidence.", ""]
    for domain, contract in DOMAINS.items():
        routes = [r for r in data["routes"] if r["domain"] == domain]
        tests = sorted({p for section in ("routes", "forms", "service_actions", "jobs", "commands") for r in data[section] if r["domain"] == domain for p in r["candidate_test_files"]})
        lines += [f"### {domain.replace('_', ' ').title()}", "", contract["behavior"], "",
                  f"Role policy: {contract['role_policy']}", "", f"Required scenarios: {contract['scenarios']}", "",
                  f"Status: **{MAPPED}; unverified per role/scenario**. {len(routes)} resolved routes; {len(tests)} candidate test files.", ""]
        if routes:
            lines += ["Routes: " + ", ".join(f"`{r['name'] or '(unnamed) ' + r['pattern']}`" for r in routes) + ".", ""]
        if tests:
            lines += ["Candidate tests: " + ", ".join(f"`{p}`" for p in tests) + ".", ""]
    lines += ["## Form and filter fields", "", "Fields below combine AST declarations/Meta fields with inherited `base_fields` where the resolver imported the form. Mixins/widgets remain listed to avoid hiding dynamically built controls. Their class-level presence does not prove validation, permitted choices, or request behavior.", "", "| Source/class | Fields | Status |", "| --- | --- | --- |"]
    for row in data["forms"]:
        fields = row.get("inherited_runtime_fields", []) or row["declared_fields"]
        meta = row["model_meta_fields"]
        if isinstance(meta, (list, tuple)):
            fields = list(dict.fromkeys([*fields, *meta]))
        lines.append(f"| `{row['source']}:{row['line']}` `{row['symbol']}` | {', '.join('`' + x + '`' for x in fields) or '(mixin/widget; see JSON)'} | {MAPPED} |")
    lines += ["", "Additional explicit GET controls: " + ", ".join(f"`{key}`" for key in sorted({r["symbol"] for r in data["query_parameters"]})) + ".", "",
              "## Services, jobs and operator commands", "", "The JSON contains every top-level function in `*services.py`/`tasks.py`, including private helpers, and every project management command. Internal public service functions include query/analytics helpers as well as mutations. Runtime action dispatchers and dynamically built report controls require manual review beyond these static names.", ""]
    for section in ("service_actions", "jobs", "commands"):
        lines += [f"### {section.replace('_', ' ').title()}", "", "| Source | Symbols | Status |", "| --- | --- | --- |"]
        for source in sorted({r["source"] for r in data[section]}):
            symbols = [r["symbol"] for r in data[section] if r["source"] == source]
            lines.append(f"| `{source}` | {', '.join('`' + s + '`' for s in symbols)} | {MAPPED} |")
        lines.append("")
    for dispatcher in data["action_dispatchers"]:
        lines += [f"Dispatcher `{dispatcher['source']}:{dispatcher['line']}` `{dispatcher['symbol']}`: " + ", ".join(f"`{entry['action']}` -> `{entry['target']}`" for entry in dispatcher["entries"]) + f". Status: {MAPPED}.", ""]
    lines += ["## Existing run evidence", "", "These are captured summaries, not an assertion that every mapped item ran. Full-suite totals and final-revision interpretation belong in the final audit report.", "", "| Log | Captured outcome |", "| --- | --- |"]
    for ev in data["evidence_logs"]:
        if ev["reported_summary"]:
            summary = ev["reported_summary"].replace("|", "\\|")
            lines.append(f"| `{ev['path']}` | {summary} |")
    lines += ["", "## Gaps and limits", "",
              "- No route/feature/role/filter/job/command is marked passed by discovery. Compare exact assertions and run results before assigning an individual pass.",
              "- Test mappings are literal route-name or symbol references. They can include imports/comments, omit aliases/parameterized names, or refer to shared helpers. They are audit navigation aids, not measured branch or requirement coverage.",
              "- Resolver discovery uses the audit settings' conditional routes. `source_named_routes` also lists explicit project declarations; framework-generated routes come from the resolver.",
              "- Form classes may add/remove choices or fields at initialization. AST and inherited base fields do not execute that behavior. GET aliases, dynamically constructed query keys and dispatcher action values need manual inspection.",
              "- Source hashes identify the inspected files. Historical logs can predate later edits; their hashes identify logs, not a tested source revision.",
              "- Physical devices, screen readers, real storage/OCR/email/queue providers and deployment remain unverified here. Browser outcomes are tracked separately and cannot be inferred from this source inventory.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--static-only", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    output.relative_to(ROOT)  # output must stay in the project
    output.mkdir(parents=True, exist_ok=True)
    files = source_files()
    print(f"Scanning {len(files)} application source files; database/provider operations forbidden.", flush=True)
    tests = {relative(p): p.read_text(encoding="utf-8-sig") for p in sorted((ROOT / "tests").rglob("test*.py"))}
    data = ast_inventory(files, tests)
    print("AST inventory complete; resolving Django routes under audit settings.", flush=True)
    data["routes"] = []
    data["resolver_status"] = "not run (static-only)"
    if not args.static_only:
        data["routes"] = resolver_inventory(tests, data["forms"])
        data["resolver_status"] = "complete; no database connections permitted"
    data.update(schema_version=1, generated_at=datetime.now(UTC).isoformat(),
                settings="config.audit_settings", audit_slot="inventory",
                item_status_definition="mapped/notproved means discovered and linked, never passed",
                domains=DOMAINS, test_files=sorted(tests),
                source_hashes={relative(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
                evidence_logs=evidence_inventory(output))
    data["counts"] = {key: len(data[key]) for key in ("routes", "source_named_routes", "forms", "query_parameters", "service_actions", "jobs", "commands", "action_dispatchers", "test_files")}
    data["counts"]["named_resolved_routes"] = sum(r["named"] for r in data["routes"])
    data["counts"]["raw_admin_routes"] = sum(r["domain"] == "raw_admin" for r in data["routes"])
    (output / "inventory.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output / "coverage.md").write_text(markdown(data), encoding="utf-8")
    print(json.dumps({"resolver": data["resolver_status"], "counts": data["counts"], "output": relative(output)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Reconcile saved pytest node identities, without loading Django or a database."""

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def read_text(path):
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig")


def collected(path):
    nodes = [
        line.strip()
        for line in read_text(path).splitlines()
        if line.startswith("tests/") and "::" in line
    ]
    assert len(nodes) == len(set(nodes)), f"Duplicate collected nodes in {path}"
    return nodes


def junit_key(node):
    filename, rest = node.split("::", 1)
    prefix, bracket, parameters = rest.partition("[")
    parts = prefix.split("::")
    classname = filename[:-3].replace("/", ".")
    classname += "".join("." + part for part in parts[:-1])
    return classname, parts[-1] + bracket + parameters


def cases(path):
    result = []
    for case in ET.parse(path).iter("testcase"):
        assert case.find("failure") is None, f"Failed case in {path}: {case.attrib}"
        assert case.find("error") is None, f"Errored case in {path}: {case.attrib}"
        skip = case.find("skipped")
        result.append(
            {
                "key": (case.attrib["classname"], case.attrib["name"]),
                "outcome": "skipped" if skip is not None else "passed",
                "reason": skip.attrib.get("message", "") if skip is not None else None,
                "details": (skip.text or "").strip() if skip is not None else None,
            }
        )
    return result


main_nodes = collected(HERE / "full-suite-verified/collection.txt")
final_nodes = collected(HERE / "collection-final.txt")
main_keys = {junit_key(node): node for node in main_nodes}
final_keys = {junit_key(node): node for node in final_nodes}
assert len(main_keys) == len(main_nodes), "Ambiguous main JUnit identity conversion"
assert len(final_keys) == len(final_nodes), "Ambiguous final JUnit identity conversion"

worker_results = []
main_cases = []
for worker in range(4):
    path = HERE / f"full-suite-verified/worker-{worker}.xml"
    entries = cases(path)
    keys = {entry["key"] for entry in entries}
    assert len(keys) == len(entries), f"Duplicate worker {worker} testcase identities"
    worker_results.append(
        {
            "artifact": str(path.relative_to(HERE)).replace("\\", "/"),
            "reported": len(entries),
            "passed": sum(entry["outcome"] == "passed" for entry in entries),
            "skipped": sum(entry["outcome"] == "skipped" for entry in entries),
        }
    )
    main_cases.extend(entries)

reported_main_keys = {entry["key"] for entry in main_cases}
assert len(reported_main_keys) == len(main_cases), "Duplicate cases across workers"
assert reported_main_keys == set(main_keys), {
    "missing": sorted(set(main_keys) - reported_main_keys),
    "extra": sorted(reported_main_keys - set(main_keys)),
}

related = cases(HERE / "extraction-wording-related.xml")
related_keys = {entry["key"] for entry in related}
assert len(related_keys) == len(related), "Duplicate related test identities"
assert all(entry["outcome"] == "passed" for entry in related)
supplemental = [
    entry
    for entry in related
    if entry["key"][0] == "tests.test_extraction_report_wording"
]
supplemental_keys = {entry["key"] for entry in supplemental}
assert len(supplemental) == 2
assert not supplemental_keys.intersection(reported_main_keys)
assert related_keys - supplemental_keys <= reported_main_keys
assert reported_main_keys | supplemental_keys == set(final_keys), {
    "missing": sorted(set(final_keys) - reported_main_keys - supplemental_keys),
    "extra": sorted((reported_main_keys | supplemental_keys) - set(final_keys)),
}
assert len(main_nodes) == 7572
assert len(final_nodes) == 7574

coverage = json.loads(read_text(HERE / "full-suite-verified/coverage.json"))["totals"]
assert coverage["num_statements"] == 10588
assert coverage["covered_lines"] == 9212
assert coverage["missing_lines"] == 1376
assert coverage["covered_lines"] + coverage["missing_lines"] == coverage["num_statements"]

manifest = json.loads(read_text(HERE / "tested-source-before.json"))
source_changes = []
for entry in manifest["files"]:
    digest = hashlib.sha256((ROOT / entry["file"]).read_bytes()).hexdigest()
    if digest != entry["sha256"]:
        source_changes.append(
            {"file": entry["file"], "before_sha256": entry["sha256"], "after_sha256": digest}
        )
assert [entry["file"] for entry in source_changes] == ["templates/reports/reports.html"]
new_test = ROOT / "tests/test_extraction_report_wording.py"
assert new_test.relative_to(ROOT).as_posix() not in {
    entry["file"] for entry in manifest["files"]
}

skips = [
    {
        "node_id": main_keys[entry["key"]],
        "reason": entry["reason"],
        "details": entry["details"],
    }
    for entry in main_cases
    if entry["outcome"] == "skipped"
]
summary = {
    "main_full_run": {
        "source_commit": "cd4316aaca993b0218c9252d8683477739368a3f",
        "collected": len(main_nodes),
        "passed": sum(entry["outcome"] == "passed" for entry in main_cases),
        "skipped": len(skips),
        "failed": 0,
        "errors": 0,
        "exact_node_identity_reconciliation": True,
        "duplicate_nodes": 0,
        "workers": worker_results,
        "skips": skips,
    },
    "focused_report_wording_delta": {
        "junit_artifact": "extraction-wording-related.xml",
        "related_passed": len(related),
        "related_already_in_main": len(related) - len(supplemental),
        "new_passed": len(supplemental),
        "new_node_ids": sorted(final_keys[key] for key in supplemental_keys),
        "note": "The full run predates the final report-literal change. Its two new regressions and 54 existing related report cases passed afterward. No main cases are double-counted.",
    },
    "unique_main_plus_delta": {
        "final_collection_artifact": "collection-final.txt",
        "collected": len(final_nodes),
        "passed": sum(entry["outcome"] == "passed" for entry in main_cases) + len(supplemental),
        "skipped": len(skips),
        "failed": 0,
        "errors": 0,
        "exact_node_identity_reconciliation": True,
        "missing_nodes": [],
        "extra_nodes": [],
        "duplicate_nodes": 0,
        "note": "This is a union of verified runs, not a claim that one final-source full-suite run executed all 7574 cases.",
    },
    "main_statement_coverage": {
        "artifact": "full-suite-verified/coverage.json",
        "statements": coverage["num_statements"],
        "covered": coverage["covered_lines"],
        "missing": coverage["missing_lines"],
        "percent": coverage["percent_covered"],
        "percent_display": coverage["percent_covered_display"],
        "branch_coverage": False,
    },
    "source_manifest_reconciliation": {
        "before_artifact": "tested-source-before.json",
        "existing_files_checked": len(manifest["files"]),
        "changed_existing_files": source_changes,
        "new_test_file": {
            "file": new_test.relative_to(ROOT).as_posix(),
            "sha256": hashlib.sha256(new_test.read_bytes()).hexdigest(),
        },
        "note": "All 451 other files in the frozen 452-file manifest have identical bytes. The report template change is a display-only extraction hint, covered by the focused run.",
    },
    "reproduction": {
        "collection": "$env:DJANGO_SETTINGS_MODULE='config.audit_settings'; $env:DOCTRACK_AUDIT_SLOT='collection'; $env:ENABLE_BACKGROUND_TASKS='False'; $env:RUN_LIVE_BROWSER_AUDIT='0'; & .venv\\Scripts\\python.exe -m pytest --collect-only -q *> docs\\verification\\systemwide-audit-2026-10-17\\collection-final.txt",
        "reconciliation": ".venv\\Scripts\\python.exe docs\\verification\\systemwide-audit-2026-10-17\\reconcile_acceptance.py",
        "database_mutations": False,
    },
}
(HERE / "acceptance-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
print(json.dumps({key: summary[key] for key in ["unique_main_plus_delta", "main_statement_coverage"]}, indent=2))
print("Exact-ID reconciliation passed; acceptance-summary.json written.")

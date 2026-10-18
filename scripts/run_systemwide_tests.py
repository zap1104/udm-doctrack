"""Run every collected PostgreSQL regression in disjoint isolated workers.

No pytest plugin is needed. Exact node IDs are assigned once using pytest's
argument-file support; each worker gets its own database, files and coverage.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]

# Prepare sequentially before concurrent traffic. A previously interrupted
# worker may have left committed fixture rows, and opening/creating several new
# databases under load can exhaust the local server's connection timeout.
PREPARE_DATABASE = """
import os
import django
django.setup()
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.conf import settings
expected = 'test_doctrack_audit_' + os.environ['DOCTRACK_AUDIT_SLOT']
if not expected.startswith('test_doctrack_audit_full_'):
    raise RuntimeError('Only disposable full-suite databases may be prepared')
if connection.settings_dict['NAME'] != expected or connection.settings_dict['TEST']['NAME'] != expected:
    raise RuntimeError('Refusing to prepare a database outside the isolated audit target')
connection.creation.create_test_db(verbosity=0, autoclobber=True, serialize=False, keepdb=True)
if connection.settings_dict['NAME'] != expected:
    raise RuntimeError('The database target changed during preparation')
if settings.CACHES['default']['BACKEND'] != 'django.core.cache.backends.db.DatabaseCache':
    raise RuntimeError('Refusing to clear a cache outside the isolated database')
call_command('flush', interactive=False, verbosity=0)
call_command('createcachetable', verbosity=0)
cache.clear()
connection.close()
print('Prepared disposable database:', expected)
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=2)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/verification/systemwide-audit-2026-10-17/full-suite")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update(DJANGO_SETTINGS_MODULE="config.audit_settings", DOCTRACK_AUDIT_SLOT="collection",
                       ENABLE_BACKGROUND_TASKS="False", RUN_LIVE_BROWSER_AUDIT="0")
    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"], cwd=ROOT,
        env=environment, text=True, capture_output=True,
    )
    (output / "collection.txt").write_text(collected.stdout + collected.stderr, encoding="utf-8")
    if collected.returncode:
        print(collected.stdout[-4000:] + collected.stderr[-4000:])
        return collected.returncode
    nodes = [line for line in collected.stdout.splitlines() if line.startswith("tests/") and "::" in line]
    if not nodes or len(nodes) != len(set(nodes)):
        raise RuntimeError("Collection must contain unique concrete test node IDs.")
    groups = [nodes[index::args.workers] for index in range(args.workers)]
    assert sorted(node for group in groups for node in group) == sorted(nodes)
    started = time.monotonic()
    for index in range(args.workers):
        prepare_env = environment | {"DOCTRACK_AUDIT_SLOT": f"full_{index}"}
        prepared = subprocess.run(
            [sys.executable, "-c", PREPARE_DATABASE], cwd=ROOT, env=prepare_env,
            text=True, capture_output=True,
        )
        (output / f"prepare-{index}.txt").write_text(prepared.stdout + prepared.stderr, encoding="utf-8")
        if prepared.returncode:
            print(f"Worker {index} database preparation failed. See prepare-{index}.txt.", flush=True)
            return prepared.returncode
        print(f"Prepared isolated worker {index}.", flush=True)
    workers = []
    for index, group in enumerate(groups):
        arguments = output / f"worker-{index}-nodes.txt"
        arguments.write_text("\n".join(group) + "\n", encoding="utf-8")
        log = (output / f"worker-{index}.txt").open("w", encoding="utf-8")
        worker_env = environment | {"DOCTRACK_AUDIT_SLOT": f"full_{index}", "COVERAGE_FILE": str(output / f".coverage.{index}")}
        command = [sys.executable, "-m", "pytest", "@" + str(arguments), "-q", "--reuse-db",
                   "--maxfail=1",
                   "--junitxml=" + str(output / f"worker-{index}.xml"), "-o", "faulthandler_timeout=120",
                   "--cov=apps", "--cov-report=", "--cov-fail-under=0"]
        process = subprocess.Popen(command, cwd=ROOT, env=worker_env, stdout=log, stderr=subprocess.STDOUT)
        workers.append((process, log, index, len(group)))
    print(f"Collected {len(nodes)} tests; running {len(workers)} disjoint PostgreSQL workers.", flush=True)
    results = []
    for process, log, index, count in workers:
        result = process.wait()
        log.close()
        result_file = output / f"worker-{index}.xml"
        if not result_file.exists():
            raise RuntimeError(f"Worker {index} did not produce its test results.")
        cases = ElementTree.parse(result_file).findall(".//testcase")
        failures = [case.attrib for case in cases if case.find("failure") is not None or case.find("error") is not None]
        results.append({"worker": index, "exit_code": result, "assigned": count, "reported": len(cases),
                        "failed": failures, "skipped": sum(case.find("skipped") is not None for case in cases)})
        print(f"Worker {index}: exit {result}; {len(cases)}/{count} tests reported.", flush=True)
    summary = {"collected": len(nodes), "seconds": round(time.monotonic() - started, 2), "workers": results}
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if any(row["reported"] != row["assigned"] for row in results):
        raise RuntimeError("A worker did not report all assigned test IDs.")
    coverage_env = environment | {"COVERAGE_FILE": str(output / "combined.coverage")}
    combine = subprocess.run(
        [sys.executable, "-m", "coverage", "combine", "--keep", *[str(output / f".coverage.{i}") for i in range(args.workers)]],
        cwd=ROOT, env=coverage_env, text=True, capture_output=True,
    )
    (output / "coverage-combine.txt").write_text(combine.stdout + combine.stderr, encoding="utf-8")
    if combine.returncode:
        return combine.returncode
    subprocess.run([sys.executable, "-m", "coverage", "json", "-o", str(output / "coverage.json")], cwd=ROOT, env=coverage_env, check=True)
    coverage = subprocess.run([sys.executable, "-m", "coverage", "report", "--fail-under=60"], cwd=ROOT, env=coverage_env, capture_output=True, text=True)
    (output / "coverage.txt").write_text(coverage.stdout + coverage.stderr, encoding="utf-8")
    print(coverage.stdout.splitlines()[-1] if coverage.stdout else coverage.stderr, flush=True)
    return 1 if any(row["exit_code"] for row in results) else coverage.returncode


if __name__ == "__main__":
    raise SystemExit(main())

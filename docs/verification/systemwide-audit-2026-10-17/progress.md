# Audit ledger: docs/superpowers/plans/2026-10-17-systemwide-bug-audit.md

Baseline: bb84ac863937a27dc871e115db694b2e84a78f73.
Branch: codex/systemwide-bug-audit.

## Pre-flight and decisions

- User approved the complete in-chat plan; the saved plan carries its scope.
- Use the existing checkout on the requested new branch and separate disposable
  PostgreSQL databases per worker. No extra worktree was requested. Existing
  untracked user artifacts are preserved; no bulk staging or cleanup.
- Parallel tasks own disjoint code: diagnostic scripts; notification/download
  access; storage/extraction services. Root owns integration/browser/ledger.
  Shared interfaces: existing Document/Tracking visibility and current service
  behavior; any required cross-owner change is coordinated before editing.
- Each test process uses config.audit_settings and a distinct AUDIT_SLOT.
  No simultaneous processes use the same test database. No live service calls.

## Progress

- Setup: branch created; tracked baseline clean; audit test settings added.
- Initial serial baseline collected the original suite, then was intentionally
  stopped at about 25% to replace slow serial verification with disjoint audit
  workers. It was not a completed baseline pass. Calendar-dependent failures
  were investigated independently. Final full-suite results remain required.
- Diagnostic repairs: mixed collections, optional links, draft export labels,
  retired offices and OVPA tag usage. Initial RED 4; first combined GREEN 110.
  Independent review found inactive-tag and 500-row pagination assumptions;
  additional RED 3 and GREEN 3. Final combined 113 passed (185.59 seconds).
  Independent expected-ID scope and count contracts: filters-report.md.
- Access: notification visibility and user/object/kind-bound expiring download
  signatures repaired. Focused final 39 passed; related 442 passed. Independent
  task review found no remaining access defect.
- Storage/queue/full-retry generation repair: RED 13; final focused 19 passed
  with background processing enabled. Independent review found a remaining
  old-append/new-full-retry race. Four new RED cases, then four GREEN; related
  extraction/security/storage run: 40 passed. See append-report.md.
- Outer request rollback and extraction feedback: RED 4, GREEN 4; related
  processing suite 16 passed. Preserve detailed synchronous failure messages.
- Calendar: two tests incorrectly assumed the next day has business hours;
  fixtures pinned to known weekdays, 16 explicit boundary cases added.
  Combined calendar/External workflow run: 54 passed. The earlier calendar
  setup-errors log was a sandbox socket denial, not an application defect.
- Coverage inventory generated: 246 resolved routes (218 named, 161 raw admin),
  55 forms, 40 explicit GET controls, 95 service functions, 10 jobs, 10 commands.
  Existing-test mappings are not claims that each route has been exercised.
- Live browser: real login/CSRF and both approved filing journeys work. Third
  run exercised live filters and responsive pages for the first role, then
  navigation timed out entering the next role. Earlier checkbox failure was
  caused by the harness clicking a closed disclosure; the harness now opens it.
  Full browser acceptance, source-tied full suite, commands and final review
  remain pending. No push, deployment, real providers or live-data writes.

## Final verification continuation, 18 October local time

- Independent final code review approved inspected product repairs, with two
  diagnostic issues: smoke checks omitted signatures; browser check accepted
  empty downloads. Smoke RED proved eight 403 responses; repaired related run
  44 passed. Browser now requires the exact expected bytes, awaiting final run.
- Background-enabled processing/security/queued extraction/deletion/storage/
  append regression suite: 69 passed, two existing warnings, 35.22 seconds.
- Dependency compatibility passed. Installed pip-audit checked 72 packages,
  no known vulnerabilities, no skipped packages. No packages changed.
- Final lint passed; 108 templates checked without problems. JavaScript suite
  within this audit: 172 passed. README corrected to administrator-approved filing.
- Full-suite coordinator's first workers hit setup errors and were stopped;
  no completed result is claimed. Direct approved worker 1 subsequently made
  progress without errors; direct worker 0 is also running on a separate DB.
  Exact command arguments and per-worker logs are under full-suite/.
- Active ownership: root owns full_0/full_1 and final ledger; browser reviewer
  owns browser DB and its two harness files; smoke reviewer owns commands_review
  DB for migration/deployment/management checks. No shared database workers.
- Full worker lists predate the one added smoke regression. Reconcile the latest
  complete collection against the union of both worker JUnit results and the
  separately passing smoke case before claiming complete suite coverage.

## Final integration follow-up

- Full first final-source collection reconciled exactly: 7,527 IDs, 7,523 passed,
  four skipped, zero failures. Combined statement coverage 87%. This is now a
  previous source snapshot, not proof for the new repair below.
- Saved verified focused commits e1edaa2 (product safety/storage/generation) and
  f5f4810 (diagnostic/calendar regressions). Still no push or deployment.
- Additional reproduction found same-generation sibling extraction failure loss:
  two RED cases show a successful append clears a saved file's earlier failure
  and its notes. Generation-scoped failure latch and additive migration 0013 are
  being implemented and verified before another complete suite run.
- Added explicit populated Incoming/Outgoing x three-stage fixtures for all four
  roles to supplement the existing empty office-admin Incoming fixture skip.
- Expanded browser controls require corrected test interactions (range bounds,
  exact anchor identity, actual Previous/Next pagination). These were harness
  defects, not backend failures. Current source run remains required.
- Production-shaped deployment check passed with provider connections blocked;
  isolated schema/selfcheck/page evidence is documented in commands-report.md.

## Frozen revision and verification now running

- Product fixes committed locally: e1edaa2, 666b7b3, 05e49f3. Diagnostics/calendar:
  f5f4810. Isolated tooling, browser harness and expanded matrix: cd4316a.
- Follow-ups reproduced and repaired clean-state permission cancellation and
  index/suggestion finalization failures. Latest related background-enabled run:
  95 passed, including 15 sibling cases and six finalization/synchronous controls.
- Synchronous unexpected retry errors fully roll back the outer service
  transaction; prior valid state remains and the error propagates. Durable
  post-rollback FAILED recording is for standalone/background workers.
- Fresh independent combined review, then scoped cancellation recheck, found no
  remaining actionable issue in inspected changes. See independent-final-review.md.
- Expanded independent filter matrix: 137 passed, two existing warnings,
  237.39 seconds. Incoming/Outgoing x three stages populated for all four roles.
- Four-worker attempt in full-suite-final/ hit missing test DB setup after local
  connection timeouts; two workers stopped deliberately. Not a complete result.
- Runner now prepares and flushes only exact disposable full_N databases,
  sequentially, before concurrent traffic. Explicit cache isolation prevents
  inherited Redis configuration from touching external caches. Invalid-Redis
  probe passed using only the isolated root DB.
- CURRENT complete run: full-suite-verified-run.txt, output full-suite-verified/.
  All four preparations passed. 7,572 unique tests collected, workers full_0..3
  progressing. Do not claim a final pass until every JUnit/result is read.
- Browser stabilization: 306 checks/187 swaps/96 theme-width-role combinations
  passed. Subsequent run under heavy DB traffic timed out opening a connection;
  source unchanged. Browser agent is waiting for heavy tests to finish, then must
  rerun once on cd4316a and refresh schema/deployment evidence for that revision.
- tested-source-before.json hashes 452 source/test/assets files. Only two trailing
  blank lines were removed during code freeze; no statements changed. Compare
  final hashes before delivery. Reports and old raw logs are not source hashes.
- Remaining: completed full suite + exact-ID reconciliation/coverage, isolated
  final browser and config checks, final report/ledger numbers and docs commit.
  No push/deployment or business DB migration authorized in this task.

## Accepted final verification, 19 October 2026

- Complete run on cd4316a: 7,568 passed, four accounted-for skips, zero failures;
  7,572 unique collected cases across four disjoint workers. Statement coverage
  87% (9,212/10,588). The earlier interrupted runs are not acceptance evidence.
- Final real-browser run: one passed in 290.93 seconds; 306 checks, 187 actual
  filter updates, 321 navigations, 96 role/theme/width cases; zero JavaScript
  or unexpected HTTP errors. One deliberate superseded request was aborted.
- Fresh isolated schema/deployment validation on cd4316a passed: both additive
  migrations applied, six isolated document rows retained, zero pending/drift,
  production-shaped checks clean with zero provider connection attempts.
- Final wording-only finding: skipped extraction can retain existing searchable
  text. Reports now says “skipped or cancelled”, without changing counts/status.
  Two correct RED failures, then 56 related passes including those two new cases.
  Full suite was not rerun for this single literal. Final collection is 7,574;
  exact-ID reconciliation excludes the 54 overlapping focused cases.
- Final lint and all 108 templates pass after the label correction. Frozen
  source comparison: 451 of 452 original files identical, only Reports literal
  changed, plus its new regression file. No later backend edits.
- Remaining delivery work: save final evidence summary and local documentation
  commit, then verify branch/status. No push or deployment; business DB untouched.

- Exact final reconciliation passed: 7,574 final collected IDs equal the full
  7,572-case result plus the two new wording regressions, with no missing, extra
  or duplicate identities. See acceptance-summary.json. Reports correction saved
  as local commit 9e2d34c; documentation records this final product revision.

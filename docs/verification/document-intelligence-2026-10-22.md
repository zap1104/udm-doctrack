# Document intelligence verification ledger

Plan: `docs/superpowers/plans/2026-10-22-document-intelligence.md`, user-approved Native execution on 22 October 2026.
Product baseline: e974d30. Design/plan commits: de0ab72, 1b5fc00. Local branch: feature/documents-intelligence.

Implementation in progress. No push, merge, deployment, original database migration or live provider work.

## Task 1: processing contracts

- RED: six new contract tests failed for missing version-bound processing capability, before production changes. Log `.vercel/ai-ml-audit/task1-red.log` (private/ignored).
- Existing baseline: 50 passed before implementation; not final verification.
- New run/unit data is additive. Legacy text remains coverage unknown, not retrospectively verified.
- Ruling: Native PowerShell replaces bash-only skill bookkeeping scripts; in-place feature branch is explicitly approved by the execution plan. Unrelated untracked work remains intact.
- GREEN: 39 passed (six contract tests plus existing file-version services), two existing deprecation warnings, 18.75 seconds. `.vercel/ai-ml-audit/task1-green.log`.
- Focused ruff and Django checks passed; migration drift reported no changes. Migration `0015_extraction_units` applied only by the disposable test database. Migration-history probing warned that the deliberately nonexistent non-test audit database does not exist; no working database fallback was used.
- Source-unit extraction, chunk search, ML and automatic tags all remain disabled by default.

## Task 2: source readers

- RED: 12 reader/placement tests failed before implementation; a later TIFF full-frame-count regression also failed before its fix.
- GREEN: 27 passed in14.33seconds, two existing deprecation warnings; focused lint and diff whitespace checks passed. Logs `.vercel/ai-ml-audit/task2-final.log` and `task2-frame-red.log`.
- Windows binary wheels installed: Pillow13.0.0, pypdfium2 5.14.0. Installed metadata/licenses inspected. Actual PDF rendering and nested/reused placement-union fixtures passed. Hosting/Linux smoke remains pending.
- Root-cause repair: PdfPage lacks a context-manager API in the installed PDFium wrapper. Explicit close/finally preserves native text on renderer failure; missing-renderer regression passed. TIFF frame traversal no longer requests an unbounded whole-file frame count.
- Ruling: placed polygon union is a bounded512-square estimate with clipping uncertainty disclosed, not an exact readable-content guarantee. Complex clipping and near-blank appearance cannot qualify full coverage.
- No live OCR or original record processing occurred. New paths remain disabled by default.

## Task 3: guarded OCR units

- RED: ten provider fixture tests failed for missing guarded adapter; `.vercel/ai-ml-audit/task3-red.log`. All HTTP responses simulated.
- Additional RED regressions caught absent lossless page preparation, undisclosed partial-response truncation, malformed optional overlays and transparent image flattening.
- GREEN: 49 passed in11.39seconds across provider, native reader, existing OCR preservation/limits and queued authorization tests. Two existing deprecation warnings. Focused lint passed; `.vercel/ai-ml-audit/task3-final.log`.
- Wire fixture assertions verify actual multipart bytes <=1,000,000, one-page source mapping, preserved text after partial results, null missing confidence, guard revocation before retry, finite transport retries and resumable429 quota waits. Original PDF bytes remain unchanged; no live requests made.
- Ruling: image normalization flattens transparency onto white at original dimensions and records color/orientation changes; no silent resizing. A too-large safe image requires action rather than repeated lossy compression.

## Task 4: durable processing

- RED: initial eight recovery/transaction regressions failed before implementation. Additional reproductions covered stale leases, consent revocation, finalization failure, late files, true engine provenance, cross-office filing, disabled flags, fileless filing, physical counts, routing-context preservation and legacy run hashes.
- Current focused GREEN:27passed in34.75seconds; `.vercel/ai-ml-audit/task4-final.xml`. Earlier related run80passed in59.41seconds and q2-app-registry run31passed in38.57seconds; those earlier runs precede the latest settings/hash/context refinements and are not final branch verification.
- Provider I/O is outside database mutation locks in both entry modes; recovery preserves original actor identity. Queued invocations dispatch at most one OCR unit. Tests simulate worker interruption/recovery; real OS worker termination remains unverified.
- Additive0016/0017 introduce separate native/OCR candidates, recovery counters, bounded late-file discovery and a unique suggestion/run relation. These migrations have run only in disposable tests.
- Ruling: policy snapshot now includes nonsecret backend/language/extractor context, independent of numeric limits; settings changes require a fresh eligible intent. This prevents mislabelling resumed results and costs an explicit retry after configuration changes.
- Ruling: synchronous web work is bounded and may leave OCR work pending for the worker/recovery command. This avoids unbounded multipage provider loops; a worker is required for unattended scanning.
- Ruling: failure attention remains separate from readable prior text; stale workers cannot cancel a newer lease. The original no-files and routing-context filing behavior is preserved.
- Still required in later milestones: durable model/suggestion-stage recovery (Task9), complete chunk-based reads (Task5), scoped review UI/observability (Task11), vocabulary/model/deletion lifecycle and final live-worker/browser/security verification (Task13). These are not claimed complete here.
- Final task checks:27focused tests and34background-configured tests passed after settings/hash/context fixes. Focused lint, Django checks and migration drift passed. Background report `.vercel/ai-ml-audit/task4-bg-final.xml`;27focused report `task4-final.xml`. Original database remains unmigrated.

## Task 5: source chunk search (in progress)

- Four expected RED failures for missing chunk retrieval. `.vercel/ai-ml-audit/task5-red.xml`.
- Additional RED reproduced a null metadata vector suppressing a valid body rank. Corrected with independent null-safe metadata/body scoring.
- GREEN:65 passed in35.82seconds across new chunk tests and existing search, unified repository, identifier and master-data refresh regressions (`task5-related.xml`). Focused Ruff and migration-drift checks passed.
- Retrieval groups current authorized source chunks by document before the candidate cap. Late appendix evidence remains searchable beyond the legacy aggregate prefix, with escaped snippets and real source locators. Tiny-fixture PostgreSQL EXPLAIN was exercised; production query performance is not established by that fixture.

## Task 6: explicit tag provenance

- Four RED tests demonstrated missing review/provenance services. GREEN:45 passed in26.01seconds with existing file-version and master-data index tests (`task6-related.xml`).
- New append-only decisions distinguish positive, negative and unknown for an exact input revision and vocabulary. Complete review covers only its submitted canonical IDs. Assignment contributions preserve legacy human provenance without inventing an actor or timestamp.
- Canonical merges are system-admin only, reconcile distinct-document usage and leave historical review IDs untouched. Existing metadata and Django-admin tag edits use the same fresh authorization boundary. Automated application is still pending Task10.
- Additive core0013/documents0019–0020 preserve current IDs and backfill legacy contributions. No original database migration has run. Concurrency, full access matrix and live UI checks remain for final verification.

## Task 7: private training datasets

- Two expected RED failures preceded implementation. GREEN:8 passed in40.88seconds with review, background-review and storage rollback tests (`task7-final.xml`).
- Snapshots store bounded text, exact review IDs, labels, source revisions, vocabulary and deterministic family splits privately in PostgreSQL. They have no media URL or ordinary-user download route. Identical hashes, declared families and common tracking origins are joined transitively before splitting.
- Preparation is system-admin only, dry-run by default, office/collection scoped, excludes restricted records, and requires opt-in eligibility. Changed source content, eligibility, scope, vocabulary or reviews fail validation. Existing documents default to ineligible. No original records were exported or trained on.
- The legacy metadata export now explicitly documents that absent tags are unknown. Additive0021 is applied only in disposable tests. Dataset quality and production per-tag support remain unestablished.

## Task 8: real local training and safe reload

- Four expected RED failures preceded the implementation. Later regressions caught NumPy vocabulary scalar serialization and an unavailable private NumPy header API; both were replaced with portable integers and a supported bounded NPY header reader.
- GREEN:14 passed in23.28seconds across training, metrics, hostile artifacts, scoped datasets and explicit reviews (`task8-final.xml`). The full authored prepare → train → frozen evaluation → numeric reload → inference loop ran successfully; candidates remain inactive.
- Word(1,2) and character(3,5) TF-IDF features fit only training families. Numerical zone weights are2/1.5/1. Known-label masks exclude unknowns. Sigmoid calibration uses a separate partition only with20 independent examples per class. Unsupported labels remain unavailable; no invented negatives or cross-validation fallback.
- Tests verify fitted/reloaded prediction parity, held-out vocabulary exclusion, late text beyond200,000characters, single-class abstention, independent Wilson boundaries, scope/runtime/digest checks, compressed archive refusal and hostile array-header rejection. Artifacts contain bounded JSON and numeric NPY members, loaded with allow_pickle=False. They are stored privately in the registry, with no arbitrary path import.
- Optional Windows CPU wheels installed:scikit-learn1.9.1,NumPy2.5.4,SciPy1.18.1. The optional profile is pinned. Candidate reports record CPU/wall duration and sparse-matrix bytes; total process peak RAM and Linux wheel smoke remain unverified. Synthetic examples never qualify production automation. No real source records or provider calls were used.

## Task 9: activated inference and durable suggestion recovery

- RED tests demonstrated missing activation/inference, the extraction-to-suggestion crash gap, and a real transfer-during-inference scope leak. The final publication check now reloads both visibility and office membership.
- Combined GREEN:43 passed in123.12seconds (`task9-current-final.xml`), plus34 passed in77.79seconds with django-q2 registered in a separate disposable database (`task9-background.xml`). These are fixture tests, not a live worker run. A final narrower check follows the small evidence word-boundary change.
- The actual AIEngine now uses a private office/collection model, records current-document prediction provenance, and identifies rule metadata separately. Shadow preserves visible rules. Missing packages, corrupt bundles, changed source eligibility, stale content/model state and wrong scope produce truthful rule/manual fallback. Training never activates a model; activation and rollback are explicit system-admin services.
- Suggestion intent is saved alongside completed extraction publication. Interrupted inference resumes with its original actor, finite attempts and leases without repeating native extraction or OCR. Source and suggestion recovery share the dry-run command. Additive0023 preserves existing suggestions and marks unknown historical input revisions blank.
- Related regressions exposed a Task6 integration gap for inter-office filing. Initial explicit filing tags now use a narrowly checked initialization service; the filing actor gains no later repository edit right. Unreviewed rule suggestions are no longer silently recorded as confirmed filing labels. Existing assignments are preserved.
- Model inputs are bounded before loading complete unit text. Evidence has normalized offsets, current immutable filename/unit mappings and Unicode-safe matching. The cache holds one validated bundle; scope/runtime/digest checks remain mandatory. No production model is active, and no original data/database migration, push or deployment occurred.
- Final narrow GREEN:5 passed in31.41seconds after the evidence matching change (`task9-last.xml`). Focused Ruff, Django checks and migration drift passed. Real browser and live-worker checks are still pending Task13.

## Task 10: qualified automatic contributions

- Two missing-gate RED tests and a separate missing-publication-intent RED test preceded implementation. Current GREEN:22 passed in56.67seconds, including actual concurrent PostgreSQL human-confirmation/rollback (`task10-current-final.xml`).
- Application requires the global flags, active automatic model, current office/collection/content/vocabulary, trusted immutable prediction, explicit canonical-topic enablement, calibrated independent real-data support, frozen evaluation and complete safe source evidence. Interval gates are recomputed from independent counts; a claimed95% alone is insufficient. Legacy/partial training evidence remains suggestion-only.
- Synthetic fixtures remain blocked even after a crafted registry mode change. Tests verify no ownership, collection, retention or access changes, persistent human rejection across content changes, exact usage counts, and human contribution preservation during rollback. Arithmetic-only fabricated counts test the gate formula; they do not qualify a fixture model or establish production quality.
- Additive0024 persists a separate automatic-application intent with original actor, finite leases/attempts and fenced publication. An interrupted application resumes without repeating extraction or inference. The recovery command inventories all three stages. Automatic defaults remain off, with zero production-qualified topics.
- Full permission/lifecycle suites, browser UI and independent review remain pending. No live OCR, original database migration or deployment has occurred.

## Task 11: scoped source/review screens and private views (23 October)

- Five initial RED tests preceded the new screens. Additional RED tests reproduced misleading legacy green coverage and missing immutable review-name snapshots.
- Current GREEN:19 passed in36.77seconds (`task11-final.xml`), including source authorization, stale review conflicts, real CSRF enforcement, distinct form IDs, administrative eligibility, invalid model scopes, private labels and saved-filter validation after transfer. Earlier related23 passed in52.83seconds before the final name/family refinements; that earlier result is not the final branch check.
- Source units expose escaped, bounded normalized text and the existing signed original-version preview/download links. Unit IDs are joined to the authorized parent and current immutable version. Status separates semantic coverage from work state; legacy DONE no longer implies verified coverage. Latest transport diagnostics explicitly avoid a billing-total claim.
- Topic review uses explicit POSTs with no-JavaScript forms. Human, automatic and unknown legacy assignment origins are distinguished. New review label/actor snapshots preserve names at decision time; older unknown values stay blank. Eligibility is office-admin/own-document scoped, restricted sources are excluded, and Django administration cannot directly toggle document eligibility or model activation.
- Private saved views validate an allowlist against current authorized collection choices on creation and every open; repeated/unknown filters fail closed. They reuse repository URLs and do not create documents or copy files. Both collections link to applied-filter saving; unfinished browser inputs are not harvested.
- Additive documents0025–0026 and metadata-only core0014 are applied only in disposable tests. Source/index freshness, vocabulary lifecycle hardening, broad access/deletion tests, real browser matrix and independent review remain final-stage work. Template checks116 passed; focused Ruff and JavaScript syntax passed.

Task12: approved global aliases and administrator approval/merge controls implemented. Private ad-hoc tags do not invalidate the controlled vocabulary. Current21-test UI/training/vocabulary pass:46.35s; final5search/judgment regressions:18.82s, including plain-matching fallback evidence. Focused lint passed. Authored fixture judgments establish mechanics only. No real corpus or provider used.

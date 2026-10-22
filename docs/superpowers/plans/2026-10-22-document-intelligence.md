# DocTrack Document Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver version-bound extraction, truthful OCR coverage, source-aware search, controlled topic tags, and an executable local ML training/inference lifecycle without disrupting DocTrack.

**Architecture:** Extend current Documents services, q2 jobs and PostgreSQL retrieval. Isolate pure file readers, provider dispatch, transactional persistence, tag decisions and model tooling behind explicit interfaces. Existing files, collection semantics, role permissions, review UI and legacy status/search reads remain compatible behind independently controlled feature flags.

**Tech Stack:** Django 5.2, PostgreSQL/psycopg, Bootstrap 5/HTMX, django-q2, existing private storage/security; OCR.space Free; candidate additions Pillow/pypdfium2 and CPU scikit-learn/numpy/scipy, tested and pinned before use.

**Spec:** `docs/superpowers/specs/2026-10-22-document-intelligence-design.md`, approved by the user on 22 October 2026. Starting product revision `e974d303477411a64761d2154841050d730f97b1`; design commit `de0ab72`; current branch `feature/documents-intelligence`.

## Global Constraints

- **ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE.**
- Additive migrations only. No automatic migration of the original database, bulk external OCR, push, merge, deployment or destructive cleanup.
- OCR.space Free: maximum 1,000,000 submitted bytes; conservative whole multipart-body budget of 1,000,000 bytes; one PDF page per dispatch, never above the documented three-page provider ceiling.
- Automatic tagging defaults off. No synthetic dataset or arbitrary score can qualify a production tag.
- Automatic eligibility requires at least 95% observed precision, a one-sided 95% Wilson lower bound at least 95%, 100 independent predicted-positive family representatives, and at least 1% coverage, plus calibration/completeness/security gates and explicit enablement.
- Preserve `Document.objects.visible_to(user)`, OVPA/External collection separation, office-admin own-office scope, current-version file access and current-batch Tracking rules.
- Keep formats unchanged. No conversion APIs, cloud document viewer, generative model, new approval workflow, pgvector requirement or wholesale UI redesign.
- Preserve original files/checksums, immutable versions, human tags/rejections, existing extraction failure markers, private signatures and 30-day recovery.
- Retain actor identity and fresh authorization through all retries. Provider/render/model I/O must run outside long database transactions. Follow actor -> document -> processing -> assignment/tag lock order.
- Preserve unrelated untracked files, especially the three pending External custody test modules and their plan. Stage explicit task paths only.
- Use PowerShell and `scripts/start.ps1` for local startup. Real training exports, snapshots, model artifacts, keys and private QA fixtures never enter Git.

## Review Focus

1. A tiny compressed upload can expand into huge images/XML; Task 2 must return bounded partial/action-required outcomes rather than exhausting memory.
2. An actor or OCR setting can change between retries within one file; Task 3 must block the next dispatch without discarding already useful text.
3. A user can follow an old evidence link after permission or version changes; Task 11 must reauthorize and refuse superseded/current mismatches without leaking text.
4. An administrator can edit tags through Django admin while automation runs; Task 6 must retain human decisions and accurate counters under that concurrency.
5. A deployed worker can lack optional ML/rendering packages or load incompatible artifacts; Tasks 2 and 9 must expose truthful fallbacks while manual filing/search remain usable.

---

## Execution and verification conventions

Native execution in this session is recommended because these tasks share transactional and data-model interfaces. Obtain the required plan review and execution selection before product edits. Once selected, continue ordinary reversible implementation without additional design questions; escalate only actual scope changes or missing business decisions.

Work on the existing feature branch without creating a worktree unless the user selects one. The tracked tree was clean at plan creation. Keep a ledger at `docs/verification/document-intelligence-2026-10-22.md` with task status, actual commands/results, fixture/live boundaries and unresolved failures. The earlier **50 passed** baseline is historical once implementation begins, not final verification.

Disposable settings live in ignored `.vercel/ai-ml-audit/`. Existing `audit_settings.py` uses only `test_doctrack_ai_ml_audit_20261022`, temporary media and disabled provider credentials. Before adding tests, add a background settings variant that sets `ENABLE_BACKGROUND_TASKS=True` **before importing config.settings** so django-q2 is installed in the app registry. Add a separate isolated browser database/settings. Test configuration must explicitly replace both database NAME and TEST.NAME, storage, email and provider settings. Never print inherited connection credentials.

PowerShell test setup for commands below:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) '.vercel\ai-ml-audit')
function PT { & .\.venv\Scripts\python.exe -m pytest @args --ds=audit_settings -q }
function BG { & .\.venv\Scripts\python.exe -m pytest @args --ds=background_audit_settings -q }
# Define these in the same PowerShell invocation that runs PT/BG.
```

Use sandbox escalation where Python/Git/network needs it. Capture fresh red/green logs in the ignored audit directory. For each task, commit only its named source/tests/docs/migrations after focused verification; use `type(scope): summary`. Do not stage private logs or unrelated untracked work. Full verification is Task 13, not a reason to repeatedly run the full suite between tiny edits.

## File responsibilities and shared contracts

Existing `apps/documents/models.py` imports new model modules at its end, following the existing `DocumentFileVersion` pattern; keep dependencies as string FKs to avoid import cycles.

| New module | Responsibility |
| --- | --- |
| `apps/documents/processing_models.py`, `processing_contracts.py` | Runs/units, work-vs-coverage states, parser result dataclasses and legacy adapter. |
| `apps/documents/source_readers.py`, `pdf_assessment.py`, `ocr_provider.py` | Bounded pure native readers, placed-page geometry/render inspection, guarded OCR.space request/result handling. |
| `apps/documents/processing.py` | Durable intent, leases, unit persistence, generation/actor/version checks and publication. |
| `apps/search/chunks.py`, `aliases.py`, `evaluation.py` | Chunk index/retrieval, approved expansion, judged-query measurement. |
| `apps/documents/tag_models.py`, `tagging.py` | Predictions/reviews/contributions and permission-aware assignment/application/rollback. |
| `apps/core/vocabulary.py` | Canonical tags, aliases, audited merges and vocabulary revisions. |
| `apps/documents/ml/models.py`, `dataset.py`, `features.py`, `training.py`, `metrics.py`, `artifacts.py`, `lifecycle.py`, `inference.py` | Scoped snapshots/models, known labels/splits, features/train/evaluation, safe persistence and activation/inference. |
| `apps/documents/intelligence_views.py`, `intelligence_forms.py`, `saved_views.py` | Authorized evidence/review/model-administration UI and private filter views. |

Interfaces passed between tasks:

- `UnitResult(locator_key: str, ordinal: int, kind: str, raw_text: str, normalized_text: str, method: str, state: str, coverage: str, warnings: tuple[str, ...], truncated: bool, provider_confidence: float | None, metadata: dict)`; offsets in derived chunks reference its **normalized** text explicitly.
- `ExtractionPolicy`: validated immutable config with per-file page/frame/cell/archive limits, per-unit text bounds, rendering/pixel limits, request/body budgets and attempt/runtime limits. `UnitBatch` contains ordered units, expected-unit count nullable, omitted/unknown reasons and policy digest.
- `ContentInput(title: str, headers: str, body: str, source_refs: tuple, revision: str, complete: bool)`; all supported current units contribute or a disclosed resource limit makes complete false. No prefix-only model input masquerades as complete.
- `TagDecision(tag_id: int, action: str, score: float | None, source: str, engine_revision: str, input_revision: str, evidence: tuple, threshold: float | None)` where action is apply/suggest/abstain. Machine-write fields are only topic-tag contributions.
- Exact enum values, field constraints and evidence locators are defined in Task 1/6 before dependent code. Tests may use ordinary model factories, but production APIs must not contain test-only switches.

## Task 1: Version-bound processing schema, policies and compatibility

**Files:** Create `apps/documents/processing_models.py`, `processing_contracts.py`, `tests/test_processing_contracts.py`; modify Documents models, config/settings.py, `.env.example`, core checks; additive Documents migration after `0014`; initialize the coverage ledger.

**Interfaces:** Produce `resolve_file_version(document_file) -> DocumentFileVersion`, `ExtractionPolicy.from_settings()`, `coverage_summary(document) -> dict`, `legacy_ocr_status(batch: UnitBatch) -> str`. `ExtractionRun` is unique on file_version/generation/settings_digest; `ExtractionUnit` on run/locator_key. Run owns actor/purpose/attempts/lease/current-publication, unit owns nullable confidence and raw/normalized text. Default legacy coverage unknown.

- [ ] **1. Red:** Add `test_legacy_text_does_not_imply_complete_coverage`, `test_duplicate_run_and_locator_are_rejected`, `test_current_snapshot_must_match_root_checksum`, `test_confidence_absence_is_null` and `test_partial_work_never_adapts_to_green_done`. Required assertions include `assert coverage_summary(legacy_document)["coverage"] == "unknown"`, `assert unit.provider_confidence is None`, and `assert legacy_ocr_status(partial_batch) != "DONE"`. Duplicate identities raise database integrity errors; mismatched snapshot selection fails without mutating files.
- [ ] **2. Verify red:** `PT tests/test_processing_contracts.py`; expected missing-schema/interface assertions, not DB connectivity or import mistakes.
- [ ] **3. Implement:** Add the contracts/models and independently disabled source-unit/chunk/ML/auto flags. Keep existing OCR fields/status values intact; reject invalid/nonpositive resource settings through Django checks. Resolve current repository snapshot without changing file history or paths.
- [ ] **4. Verify green:** Re-run contract tests plus existing file-version tests. Apply additive migration on a disposable database and verify restart-safe unknown legacy coverage, unchanged file checksums/manual tags and no provider calls.
- [ ] **5. Commit:** `feat(extraction): add version-bound processing contracts` with explicit task paths/migration and fresh ledger results.

## Task 2: Bounded native readers and mixed-PDF assessment

**Files:** Create `source_readers.py`, `pdf_assessment.py`, `tests/test_source_units.py`, `tests/test_pdf_coverage.py`; modify extraction.py, requirements.txt and dependency/format documentation.

**Interfaces:** Consume Task 1 dataclasses. Produce `read_source_units(file_obj, filename: str, *, policy: ExtractionPolicy) -> UnitBatch` and `assess_pdf_page(page, rendered_page, *, policy) -> dict`. Readers do not write database rows or make network calls. The PDF assessment reports placed-image coverage, native-quality reasons, visual uncertainty and OCR-needed, not a blank guarantee.

- [ ] **1. Red:** Build real small parser fixtures: header plus scanned body, scanned later page, nested/transformed/reused PDF image placement, faint signature, late second attachment text, interleaved DOCX paragraph/table/header/footer/image and XLSX formula with absent cache. Assert ordered locators/text, OCR-needed units, nullable/limited coverage and unsupported legacy DOC/XLS/PPT/PPTX. Pin `assert assessment["ocr_needed"] is True`, `assert batch.units[-1].locator_key == "pdf:page:2"` for a two-page fixture, and `assert "late appendix phrase" in batch.units[-1].normalized_text` for the native late-content case.
- [ ] **2. Verify red:** `PT tests/test_source_units.py tests/test_pdf_coverage.py`; expected actual omitted content/incorrect coverage. Avoid relying solely on mocked page lists.
- [ ] **3. Dependencies:** Verify installed-platform Python wheels, upstream licenses/notices and vulnerability results before adding tested Pillow/pypdfium2 version pins. Install only those necessary packages; smoke-test rendering on Windows and use the CI platform for a separate compatibility result. Missing renderer must yield unknown/partial coverage with useful native text.
- [ ] **4. Implement:** Preserve DOCX block order/semantic locators, inspect embedded objects, enumerate frames and bound cell/XML/decoded-pixel work. Compose nested placement transforms, clip/union visible geometry; use bounded rendered evidence when native/image assessment is uncertain. Canonical overlapping OCR/native text is selected later, not blindly concatenated.
- [ ] **5. Verify green:** Run reader and existing extraction-limit tests, including `test_compressed_image_pixel_limit_is_bounded`, `test_missing_renderer_preserves_native_text_with_warning`, and corrupt/encrypted/large-dimension cases. Assert every omitted/unread unit has a reason and later supported units are retained within declared bounds.
- [ ] **6. Commit:** `feat(extraction): read bounded source units with coverage` and record exact dependency/platform results.

## Task 3: Free-tier OCR.space adapter with truthful partial outcomes

**Files:** Create `ocr_provider.py`, `tests/test_ocr_space_units.py`; modify existing extraction.py adapter/configuration and `.env.example`.

**Interfaces:** `ocr_unit(payload: bytes, filename: str, *, source_locator: str, policy: ExtractionPolicy, language_hint: str, authorize_dispatch: Callable[[], bool]) -> UnitResult`. Guard is invoked immediately before **every** dispatch, including retries. `prepare_unit_payload(...)` returns original mapping and explicit preprocessing notes, or action-required. Unit orchestration/persistence remains Task 4.

- [ ] **1. Red:** Add tests asserting one-page mappings, payload and multipart-body sizes <=1,000,000; partial OCRExitCode/per-result failure; missing result despite HTTP 200; absent confidence stays null; corrupt JSON and persistent validation fail safely; revoked guard blocks a second attempt. Pin `assert len(submitted_body) <= 1_000_000`, `assert result.provider_confidence is None` and `assert result.coverage != "complete"` for the missing-result fixture. Inspect actual captured wire payload rather than mocking an internal success flag; use fixture HTTP responses without live keys.
- [ ] **2. Verify red:** `PT tests/test_ocr_space_units.py`; failures must show oversized dispatch/false success/missing guard, not a real network dependency.
- [ ] **3. Implement:** Parse both string/integer result codes and overall/per-unit status, sanitize errors, respect finite retry+jitter and bounded Retry-After. Initial validated defaults retain timeout90seconds and two retries, with positive bounded delays and no unbounded quota loop. Prefer lossless single-page slicing; bounded rendering may be used with recorded loss. Oversize at safe quality requires action rather than silent downsampling. Preserve provider reading order, optional Azure configuration and truthful English/Filipino mapping.
- [ ] **4. Verify green:** Run provider tests plus OCR preservation/security/limits tests. Verify successful-unit text survives neighboring failures and guard refusal; no confidence percentage is invented and no secrets enter returned notes/logs.
- [ ] **5. Commit:** `fix(ocr): enforce provider limits and per-unit outcomes` with no live OCR requests or bulk existing-file processing.

## Task 4: Durable intent, finite recovery and short processing transactions

**Files:** Create processing.py, `management/commands/recover_extraction.py`, `tests/test_processing_recovery.py`, `tests/test_processing_transactions.py`; modify services.py, tasks.py, external_views.py upload transaction wrapper, extraction compatibility path and q2 settings/checks.

**Interfaces:** `request_processing(document, *, user, file_ids=None, replace=False) -> list[ExtractionRun]`; `process_run(run_id: int, *, expected_generation: int) -> dict`; `recover_processing(*, limit: int, now=None, dry_run: bool=True) -> dict`. Recovery uses saved actor/purpose; it never substitutes system authority. Conditional leases/version checks prevent duplicate publication.

- [ ] **1. Red:** Add commit/enqueue gap, crash after partial unit persistence, duplicate delivery, expired lease and finite exhaustion tests. Assert no provider I/O while document/actor transaction locks are held in either synchronous or background mode: `assert connection.in_atomic_block is False` at the provider boundary. Replay asserts `assert published_units.count() == expected_unit_count` and unchanged hashes. Add stale-version, revoked actor/OCR, deletion mid-call, late attachment and failed-finalization preservation cases.
- [ ] **2. Verify red:** `PT tests/test_processing_recovery.py tests/test_processing_transactions.py` and BG variant. Wrong failures from intentionally missing app registry must be fixed in QA settings first.
- [ ] **3. Implement:** Save upload/file/intent atomically, enqueue on_commit, process outside locks, then finalize with actor -> document -> run/unit locks and fresh generation/version checks. Each queued invocation dispatches at most one OCR unit, checkpoints and schedules continuation; it must not run a200-page provider loop under a single timeout. Initial worker timeout600seconds, delivery retry900seconds and max_attempts2, with lease expiry recorded independently. Preserve same-version last-good units and existing failure markers. Keep existing extraction_task API as a compatible adapter; do not bypass caller identity or abandon late-file discovery.
- [ ] **4. Integrate:** Existing no-background upload still saves then returns useful synchronous suggestions when bounded processing succeeds; extraction failure keeps saved files/manual review. Avoid calling providers inside the External view's outer upload atomic block. Preserve storage compensation for actual file-save failures.
- [ ] **5. Verify green:** Existing overlap/idempotence/queued-security/finalization/background-review and file-history suites plus new tests. Assert persisted unit results, original bytes, generations, counters and lease attempts. q2 configured retry exceeds bounded runtime with finite max_attempts; simulate interruption, do not claim exactly-once provider billing.
- [ ] **6. Commit:** `feat(extraction): persist recoverable processing intent` with command dry-run/no-provider defaults documented.

## Task 5: Current-unit chunk search and safe source snippets

**Files:** Create apps/search/chunks.py, `tests/test_source_chunk_search.py`; modify Documents model index construction, search/services.py and indexing.py; additive chunk/index-revision migration.

**Interfaces:** `publish_chunks(run, *, document, expected_revision: str) -> int`; `chunk_annotations(base_documents, raw_query: str) -> dict`; `source_snippet(document, tokens: list[str]) -> dict`. Add `SearchChunk` unique unit/ordinal/index_revision, GIN vector and normalized offsets; add document metadata-only vector/current index revision without deleting legacy search_vector/ocr_text.

- [ ] **1. Red:** Assert a late-appendix and attachment-two phrase is returned once with filename/locator; historical/superseded/deleted chunks are absent; restricted canary never appears in counts/snippets. A record with hundreds of matching chunks must not crowd out other documents before a capped document candidate window. Pin `assert {r.document.pk for r in response.results} == expected_ids` and `assert len(response.results) == len(expected_ids)` on an uncapped fixture, then assert the adversarial capped candidate set retains a separately relevant document.
- [ ] **2. Verify red:** `PT tests/test_source_chunk_search.py`; fixtures exceed the legacy 200,000-character aggregate so a passing prefix-only search is impossible.
- [ ] **3. Implement:** Chunk normalized text into bounded overlapping units with explicit offsets and current published revisions. Aggregate by document **before** result limits via correlated matching/max body rank. Keep metadata ranking separate from body rank to avoid counting legacy prefix text twice. Reuse escaping/highlight logic; include real source labels.
- [ ] **4. Integrate:** Keep legacy scoring available. New scoring version uses metadata rank plus bounded best-chunk body rank in the existing relevance formula; raw identifiers still override to100. Feature flag remains off until threshold/ranking comparison is recorded. Preserve Search capped-window and Repository exhaustive-ID contracts with unique deterministic final tie-breaking.
- [ ] **5. Verify green:** Run search, unified-search, identifier-priority, repository-audit and master-data refresh tests. Capture PostgreSQL EXPLAIN on the isolated fixture corpus. Test `<script>`, entities and hostile queries as escaped text, complete result IDs/facets and unchanged default controls.
- [ ] **6. Commit:** `feat(search): index current source units without duplicate results` and document score-version/snapshot-pagination limits.

## Task 6: Canonical vocabulary and contribution-aware human review

**Files:** Create apps/core/vocabulary.py, Documents tag_models.py/tagging.py, `tests/test_tag_review_provenance.py`, `tests/test_vocabulary_lifecycle.py`; modify Tag/TagRule models, Documents services/admin, core forms/views/index refresh; additive core/Documents migrations.

**Interfaces:** `canonical_tag(tag) -> Tag`; `merge_tags(source, target, *, user, reason: str) -> Tag`; `review_tag(document, *, user, tag_id: int, action: str, input_revision: str, vocabulary_revision: str) -> TagReview`; `replace_human_tags(document, tag_names, *, user) -> None`; `complete_tag_review(document, *, user, tag_ids: list[int], vocabulary_revision: str, input_revision: str) -> TagReview`. Produce immutable TagPrediction/TagReview, TagContribution and canonical TagAlias/governance fields.

- [ ] **1. Red:** Confirm/add/reject/unsure semantics, absent labels unknown, complete review only covers explicit tag IDs/revision, human addition survives automation replay, rejection survives content reprocessing, and stale content/vocabulary POST conflicts without mutation. Pin `assert set(document.tags.values_list("pk", flat=True)) == expected_human_ids`, `assert review.input_revision == submitted_revision` and `assert historical.suggested == original_json` after a merge. Redirects cannot cycle; inactive tags cannot auto-apply.
- [ ] **2. Verify red:** `PT tests/test_tag_review_provenance.py tests/test_vocabulary_lifecycle.py`.
- [ ] **3. Implement:** Fresh permission-aware service is the assignment mutation boundary. Human and automatic contributions materialize one M2M link; counters/index revision change with actual distinct assignment differences. Backfill current tags as legacy human contributions with unknown actor/time rather than fabricated provenance. Leave MetadataSuggestion history unchanged.
- [ ] **4. Integrate:** Route existing metadata review, editing and Django admin tag saves through the service. A concurrent admin human edit and machine contribution must preserve the human state and exact usage_count. Canonical aliases distinguish explicitly approved global vocabulary from document-derived terms. Office/admin permissions stay as currently enforced.
- [ ] **5. Verify green:** Add transaction/concurrency regressions and forged cross-office/revoked writer cases; run existing metadata/tag/master-data/filter/deletion/file-version tests. Rule/AI outputs cannot mutate ownership, grants, collection, workflow, retention or document types.
- [ ] **6. Commit:** `feat(tags): preserve explicit reviews and assignment provenance`.

## Task 7: Restricted immutable datasets and leakage-safe family splits

**Files:** Create apps/documents/ml package with models.py, dataset.py, `management/commands/prepare_tag_dataset.py`, `tests/test_tag_datasets.py`; modify Documents models import, metadata eligibility controls, export_training_data.py legacy help and additive migrations.

**Interfaces:** `prepare_dataset(*, user, office_id: int, collection: str, seed: int, dry_run: bool=True, synthetic: bool=False) -> TrainingSnapshot`; `known_labels(example, tag_ids: list[int]) -> dict[int, int | None]`; `split_families(examples, *, seed: int) -> dict[str, list[str]]`. Snapshot exact eligible text/hashes, known labels/provenance, vocabulary/feature recipe and source family/group split. Sources active and explicitly training-eligible, restricted content excluded by default.

- [ ] **1. Red:** Unknown does not become0, historical saved metadata does not fabricate negatives, same hashes/revisions/families never cross partitions, and no training vectorizer may see development/test rows. Pin `assert known_labels(example, [unknown_tag.pk])[unknown_tag.pk] is None` and `assert set(split["train"]).isdisjoint(split["test"])` for family keys, including duplicate chains. Snapshot remains reproducible after ordinary source edits; withdrawn/deleted/scope-changed sources invalidate it for use. Add office+collection canary and CLI actor-scope tests.
- [ ] **2. Verify red:** `PT tests/test_tag_datasets.py`; assert actual text/labels/hashes/split sets, not merely record counts.
- [ ] **3. Implement:** Private immutable snapshot storage and bounded dry-run inventory. Fixed seed42 and deterministic family assignment using persisted family keys; default partition proportions60/15/15/10 for train/calibration/development/test, report actual class/group support rather than forcing a valid-looking split. Eligibility defaults off on existing records.
- [ ] **4. Verify green:** Test task failure/storage compensation, missing files and reruns; snapshots/artifacts cannot be browsed through media/static/download routes. Add anchored ignores for any default local training/artifact directories; no real examples committed. Legacy export remains clearly historical and insufficient for supervised negatives.
- [ ] **5. Commit:** `feat(ml): prepare scoped explicit-label datasets`.

## Task 8: Real CPU training, calibration and qualified metrics

**Files:** Create ml/features.py, training.py, metrics.py, artifacts.py; `management/commands/train_tag_model.py`, `evaluate_tag_model.py`; requirements-ml.txt; `tests/test_tag_training.py`, `test_tag_metrics.py`, `test_model_artifacts.py`.

**Interfaces:** `build_features(settings: dict) -> FeatureUnion`; `train_candidate(snapshot_id: int, *, user, feature_settings: dict | None=None) -> TagModel`; `evaluate_candidate(model_id: int, *, user, partition: str) -> dict`; `wilson_lower(successes: int, trials: int, *, confidence: float=0.95) -> float | None`; `save_bundle(bundle, *, storage) -> dict`; `load_bundle(model, *, scope, runtime) -> ModelBundle`. Define `ModelBundle(schema_version: str, runtime_versions: dict, scope: dict, feature_settings: dict, branches: dict, classifiers: dict, evaluation: dict)` with `predict(inputs: list[ContentInput]) -> list[dict[int, float]]`; optional per-tag calibration lives in classifiers. Artifact coefficients/vocab/idf/sigmoid parameters are JSON/numeric arrays, never pickle.

- [ ] **1. Red:** Synthetic multi-label fixture with a late-topic signal; a row unknown for tagA but known for tagB must not train A as a negative. Assert single-class/insufficient tags unavailable, all fitting rows belong to train, calibration/development/holdout disjoint, and held-out-only canary vocabulary absent. Pin `assert wilson_lower(0, 0) is None`, `assert wilson_lower(95, 100) < 0.95`, `assert wilson_lower(100, 100) >= 0.95` and `assert synthetic_report["production_eligible"] is False`.
- [ ] **2. Verify red:** `PT tests/test_tag_training.py tests/test_tag_metrics.py tests/test_model_artifacts.py`; no production quality gate may pass because fixtures are synthetic.
- [ ] **3. Dependencies/features:** Install tested/pinned sklearn/numpy/scipy in an ML profile, imported lazily when enabled. Implement numerical title2/header1.5/body1 FeatureUnion weights with word(1,2) and char_wb(3,5) branches, max20,000 word and30,000 character features per zone. Bound total model/input memory, processing complete evidence or explicitly abstaining from automatic use on overflow; no arbitrary prefix substitution.
- [ ] **4. Train/calibrate:** Logistic regression per tag using only its known train rows (minimum two independent positives/two negatives for an experimental classifier). Shared vectorizers fit only eligible training text. Separate sigmoid calibration requires at least20 independent known examples of each class; otherwise uncalibrated/suggestion-only. Tune thresholds on development only; unsupported development support is disclosed, not repaired with invented labels.
- [ ] **5. Evaluate:** Freeze settings before final holdout. Report precision/recall/F1/coverage/abstention, reliability bins/Brier/log loss and language/length/office slices. One preselected deterministic representative per family supplies independent support/one-sided Wilson interval; zero predictions yields null/not-qualified. Enforce spec95% point+lower-bound,100 predictions,1% coverage and real-data requirements. Unit-test interval constants and boundary cases.
- [ ] **6. Artifact verification:** Numeric arrays load with allow_pickle=False after compressed/uncompressed size/member/dimension/finiteness checks and trusted-origin/runtime/schema/hash checks. Assert inference parity with the fitted pipeline across formats/Unicode/empty zones. Unsupported runtime/version/type is a safe failure, not arbitrary code loading.
- [ ] **7. Verify green:** Complete synthetic prepare->train->evaluate->safe reload->predict loop; capture actual CPU/RAM/versions and fixture-only timing. Include malformed/compressed artifact/huge vocabulary tests and exact missing-dependency fallback behavior.
- [ ] **8. Commit:** `feat(ml): train and evaluate real scoped topic classifiers`.

## Task 9: Model registry, real AIEngine inference and activation rollback

**Files:** Create ml/lifecycle.py, inference.py; `management/commands/activate_tag_model.py`; `tests/test_tag_model_lifecycle.py`, `test_ai_engine_inference.py`; modify suggestions.py, processing finalization and MetadataSuggestion-compatible adapter.

**Interfaces:** `activate_model(model_id: int, *, user, mode: str) -> TagModel`; `rollback_model(*, user, office_id: int, collection: str, target_model_id: int | None) -> TagModel | None`; `predict_tags(document, *, user=None) -> list[TagDecision]`; `content_input(document) -> ContentInput`. Candidate activation never follows training implicitly. Inference selects only a valid office+collection model and current evidence.

- [ ] **1. Red:** An actual synthetic-trained bundle changes AI suggestions; missing/corrupt/incompatible models retain rules/manual behavior and report the engine actually used. Pin `assert live_model_suggestion.engine == "ml"`, `assert missing_model_suggestion.engine == "rules"` and equivalent expected tag-ID sets after safe reload. Shadow logs predictions but preserves existing visible suggestions. Wrong-office/collection model selection fails closed and cannot leak training vocabulary/evidence.
- [ ] **2. Verify red:** `PT tests/test_tag_model_lifecycle.py tests/test_ai_engine_inference.py`.
- [ ] **3. Implement:** Private registry and explicit sysadmin activation/rollback, fresh source eligibility/scope/version checks, short activation locks and bounded process-local model cache keyed by model+artifact digest. Lifecycle invalidation evicts usable inference state. Evidence is current-document feature spans, not training-record text.
- [ ] **4. Integrate:** Preserve `suggest_metadata` and Suggestion fields; add optional document context without breaking existing calls. AIEngine loads real model when valid, combines topic decisions with separately identified rule metadata suggestions, and labels fallback accurately. Inference runs outside finalization locks; publication rechecks input/model revisions.
- [ ] **5. Verify green:** Train->shadow->suggestion->candidate switch->rollback with actual bundle loads; expired eligibility, missing packages, stale cache, simultaneous activation, revoked sysadmin and failed inference leave manual filing/search usable.
- [ ] **6. Commit:** `feat(ml): activate real inference with truthful fallback`.

## Task 10: Qualified automatic tags and contribution-safe rollback

**Files:** Extend tagging.py, ml/lifecycle.py and processing publication; create `management/commands/rollback_auto_tags.py`, `tests/test_automatic_tag_gates.py`, `test_tag_review_concurrency.py`.

**Interfaces:** `apply_tag_decisions(document, decisions: list[TagDecision], *, actor, expected_input_revision: str) -> dict`; `rollback_auto_tags(*, user, model_id: int, document_ids=None, dry_run: bool=True) -> dict`. Only current permitted topic-tag contributions may be written.

- [ ] **1. Red:** Global off, synthetic/unsupported evaluation, incomplete/suspect/truncated extraction, uncalibrated score, stale model/vocabulary, retired tag and rejected human decision all block automatic addition. Pin `assert document.tags.count() == 0` for a nonqualifying new record and `assert document.office_id == original_office_id` after a crafted payload; also compare original retention/types/grants/routing state.
- [ ] **2. Verify red:** `PT tests/test_automatic_tag_gates.py tests/test_tag_review_concurrency.py`.
- [ ] **3. Implement:** Per-tag apply/suggest/abstain gate with recorded thresholds/evaluation/evidence; explicit per-tag administrator enablement in addition to global mode. Rule auto-application requires its own valid evaluation, not a high rule score. Short locked contribution/index/counter update after inference; stale inputs return conflict/review, not overwrites.
- [ ] **4. Verify green:** Concurrent human add/reject/complete-review vs delayed automation, duplicate predictions, new content revision and release rollback. Removing an automatic contribution never removes a human-confirmed assignment. Dry run returns authorized affected IDs/counts without exposing other-office records.
- [ ] **5. Commit:** `feat(tags): gate automatic application on verified evidence`.

## Task 11: Review/evidence UI, scoped administration and saved views

**Files:** Create intelligence_views.py, intelligence_forms.py, saved_views.py; evidence/review/model/saved-view templates and `static/js/tag-review.js`; modify existing Documents urls/views/forms/admin/templates and External repository integrations; tests/test_intelligence_ui.py, test_saved_document_views.py; additive SavedDocumentView migration.

**Interfaces/routes:** GET `/documents/<pk>/extraction/units/` and `.../units/<unit_id>/`; POST `/documents/<pk>/tags/review/` and `.../tags/review-complete/`; scoped system-admin model list/actions under `/documents/intelligence/models/`; private saved-view list/create/open/delete routes under `/documents/views/`. Declare these before conflicting `<int:pk>` routes. Every unit/evidence ID is joined to its authorized parent/current version; no arbitrary path/URL accepted.

- [ ] **1. Red:** Render truthful readable/expected/unknown coverage, actual engine, safe filename+locator, simple confirm/reject/add/unsure and scoped complete-review. Test role/CSRF/stale revision and no-JS forms. For a revoked/old evidence link pin `assert response.status_code in {403, 404}` and `assert restricted_canary not in response.content.decode()`. Existing preview/download signatures still work unchanged.
- [ ] **2. Verify red:** `PT tests/test_intelligence_ui.py tests/test_saved_document_views.py`.
- [ ] **3. Implement:** Reuse Bootstrap/current themes and existing extraction status/review/detail components. Show partial reasons in disclosure and keep work-state separate from index freshness. No continuously submitting date/search fields. Accessible status/errors/focus; require explicit training eligibility scope and sysadmin activation through existing administrative navigation.
- [ ] **4. Saved views:** Strict filter allowlist, owner-only CRUD, validated collection and current permission intersection on every open. Reject invalid/repeated/unauthorized filter params without broadening. Opening views reuses existing repository URLs/queries/count semantics and preserves pagination/HTMX context.
- [ ] **5. Verify green:** Internal and External users/collections, office admins, viewers, suspended/transferred/revoked actors; Django admin cannot bypass tag/eligibility/model services. Test malicious HTML/query labels and unsupported source locator errors. No private artifact/vocabulary downloads.
- [ ] **6. Commit:** `feat(documents): expose scoped coverage and tag review`.

## Task 12: Approved alias search and judged retrieval evaluation

**Files:** Create search/aliases.py, evaluation.py, `management/commands/evaluate_search.py` in apps/search; controlled fixture `tests/fixtures/search_judgments.json`; tests/test_search_aliases.py, test_search_judgments.py; update services.py, search explanations and SEARCH_DESIGN.md.

**Interfaces:** `expand_query(user, query: str, *, collection: str, limit: int=8) -> list[str]`; `evaluate_search_judgments(user, judgments: list[dict], *, collection: str) -> dict`. Expansion uses only approved global aliases or canonical terms visible in current scope, never hidden derived vocabulary.

- [ ] **1. Red:** Original reference equality retains100 and priority; approved acronym/synonym recovers a record; another office's private alias/canary cannot expand/suggest/leak counts. Pin `assert exact.results[0].relevance == 100`, `assert original_result.relevance > expanded_only_result.relevance` for equal original/expanded text evidence, and exact allowed result-ID sets. Chunk-saturation results, explicit defaults/window counts and stable tied ordering remain correct.
- [ ] **2. Verify red:** `PT tests/test_search_aliases.py tests/test_search_judgments.py`.
- [ ] **3. Implement:** Separately score original and expanded tsqueries; `text_score=max(original_score, 0.5 * expanded_score)`, maximum8 bounded expansion terms. Keep raw-identifier/fuzzy signals tied to original query; label expansion in explanation. Version scoring semantics and test slider boundaries rather than treating rank-fusion as a percentage.
- [ ] **4. Evaluate:** Controlled judged queries include IDs, topical/body-only/late-file, spelling/acronyms, English/Filipino/Taglish and adversarial long files. Report candidate recall separately from precision/success/nDCG@5, corpus/result-window semantics and warm/cold latency/hardware. Real-world gains require real authorized judgments; fixture language mechanics do not prove OCR accuracy.
- [ ] **5. Verify green:** Run full search/unified/repository/count/masterdata regressions and both scoring versions. Capture separate lexical baseline/new results and EXPLAIN for restrictive office scopes. Semantic retrieval/learned reranking remains unimplemented unless separately justified.
- [ ] **6. Commit:** `feat(search): add approved scoped query expansion`.

## Task 13: Lifecycle reconciliation, rollout runbook and final verification

**Files:** Modify deletion/recovery/tasks, indexing hooks, purge command and model invalidation; create `management/commands/reconcile_processing.py`, `tests/test_intelligence_lifecycle.py`, `scripts/check_document_intelligence.cjs`, `docs/document-intelligence.md`; update README/.env.example, AI_ROADMAP.md, SEARCH_DESIGN.md, coverage ledger and CI dependency/test profile as necessary.

**Interfaces:** `reconcile_processing(*, limit: int, after_id: int=0, dry_run: bool=True, collection: str | None=None) -> dict`; `invalidate_training_sources(document_ids: list[int], *, reason: str) -> dict`. Finite batches repair current units/index revisions and pending intent without provider calls unless explicitly requested under original actor authorization. Permanent snapshot/artifact storage cleanup deduplicates keys and retains minimal noncontent audit receipts.

- [ ] **1. Red:** Removal hides all chunks/evidence/model training use immediately; restore revalidates index revision without reactivating retired model; exact30-day boundary purge removes derived/private snapshot content but preserves Tracking originals/shared blobs. Pin `assert storage.exists(original_tracking_key)` after purge and `assert not SearchChunk.objects.filter(unit__run__document_id=purged_id).exists()`. Storage failure is retryable; source withdrawal invalidates active/cached models and artifact cleanup retries idempotently.
- [ ] **2. Verify red:** `PT tests/test_intelligence_lifecycle.py` plus deletion/file-history regressions.
- [ ] **3. Implement/reconcile:** Extend current deletion and master-data services with bounded idempotent invalidation/reconciliation, not an unrestricted global manager or second scheduler. Add dry-run inventory and after-id checkpoint. Feature-flag rollback preserves coverage warnings, history/manual decisions and original files.
- [ ] **4. Full Python verification:** Run bare project pytest with isolated settings, full background-enabled regressions, ruff, template checks, Django/deployment checks, migration drift, isolated migration/backfill rerun, selfcheck and page smoke. Record all failures, including the three explicitly unrelated External custody modules if present; do not silently omit them or broaden this feature into that refactor.
- [ ] **5. JavaScript/browser verification:** Run `node --test tests/js/*.cjs` using the installed Node executable and the new Playwright script against isolated Django with real login/CSRF. Exercise coverage polling, review POSTs, stale revisions, preview/download, manual search, saved views, model failures and denied actions; both themes; widths320/390/768/900/901/1366; keyboard,200%zoom and JavaScript-disabled forms. Avoid changing user's existing server. Set isolated `DJANGO_SETTINGS_MODULE`/PYTHONPATH, then use `scripts/start.ps1 -PreviewOnly -Port <unused-port>`; do not run the guided setup that could migrate the working database.
- [ ] **6. Quality/platform verification:** Run fixture pipeline prepare/train/evaluate/activate/predict/rollback, dependency audit and Windows/CI-package smoke tests. Live OCR is an explicitly authorized tiny fixture smoke only when configured; otherwise skipped. No real-data precision/semantic/readiness claims. Record CPU/RAM/corpus/concurrency/cold-warm state for actual benchmarks.
- [ ] **7. Documentation:** Actual supported formats/coverage limits, Free-tier settings/quotas, worker timeout/retry/recovery, review labels/scope, eligibility, complete commands/options and private storage, train/calibrate/evaluate/enable/rollback, score versions and deployment prerequisites. List migration status, final revision, tests and remaining live/quality/browser/physical-device/screen-reader limits. No automatic original DB migration.
- [ ] **8. Independent final review:** Use the selected execution skill's fresh reviewer of the combined changes; resolve reproduced findings with regressions, re-run affected checks and report unresolved issues. Do not claim all features complete if AIEngine still falls back because no executable trained inference path exists.
- [ ] **9. Commit:** `fix(intelligence): reconcile lifecycle and document verified rollout`; no push/merge/deployment. Delivery must distinguish shipped capability, fixture verification, actual live checks, real-data quality and production enablement. With no qualified real data, production automatic tags remain0.

## Self-review and handoff

Spec sections A-E map to Tasks1-13; format/byte/runtime/permissions/history invariants have task-level tests. Semantic milestoneF is deliberately deferred with no placeholder interface. Exact threshold/interval, unknown-label, family-disjoint and artifact-compatibility requirements appear in Tasks7-10; manual/automatic concurrent provenance and shared-blob deletion appear in Tasks6/10/13. The five Review Focus conditions each have named task coverage. Task dependencies are sequential1->2->3->4->5->6->7->8->9->10->11->12->13; there is no undeclared service passed between implementers.

This plan is prepared for the required review. **No product code, dependencies or schema have been changed while preparing it.** After user review and execution selection, implement without repeating approvals for individual reversible steps. Native execution is recommended; subagent-driven execution remains available if the user prefers its per-task independent gates and higher context cost.

# DocTrack document processing and trainable topic tagging

Date: 22 October 2026. Status: design approved by the user on 22 October 2026; detailed implementation planning follows. Product implementation has not begun.

**ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE.**

## Intent, authority and evidence

Implement the user's attached Revision 2 prompt: uploaded files become searchable through native extraction and authorized OCR; visible coverage prevents incomplete text from being mistaken for complete evidence; qualified topic tags can be applied through controlled, reversible decisions; real local training, evaluation and inference replace the placeholder AI engine. Preserve the current visual conventions and functioning workflows.

The user confirmed **OCR.space Free** during this audit. Provider keys were not requested or exposed. Eligible training volume, approved vocabulary, hosting resources and real-world quality remain unknown. Those unknowns block live activation/quality claims, not fixture-based implementation.

Starting revision: `e974d303477411a64761d2154841050d730f97b1`. Local design branch: `feature/documents-intelligence`. Main is unchanged. Untracked External custody tests/plans and existing verification artifacts are unrelated and must remain untouched. No push, merge, deployment, original database migration, bulk external processing or production model activation is authorized by this task.

This document is the architectural design and milestone contract. A detailed executable implementation plan follows its required review. No product dependencies or behavior have changed at this checkpoint.

## Repository audit

| Source and section | Current behavior confirmed from source | Gap and intended change |
| --- | --- | --- |
| `apps/documents/extraction.py`, `_extract_pdf`, `extract_document_text` | Native text from several PDF pages is aggregated; 40 useful characters can bypass OCR for the whole file. Individual page exceptions are skipped. | Evaluate each page, including image-bearing pages with a text header. Persist source units and reasons for unread or uncertain content. |
| Same file, `_extract_docx` | Main paragraphs are followed by all tables; headers, footers and embedded images are not processed. Physical pages are not known. | Preserve supported block order and meaningful section/table/image locators; inspect embedded image content; mark unsupported coverage. |
| Same file, `_extract_xlsx` | Read-only cached values, bounded rows/columns/total cells, with a processing-limit note. | Store sheet/cell source locations; detect formula/cache limitations and images/objects that remain unread. Do not execute formulas. |
| Same file, `_prepare_ocr_payload`, `_ocr_space` | Whole-file OCR, optional image normalization, HTTP retries; top-level error flag checked; optional confidence stays null. | Bound request bytes/pages, parse overall and per-result codes, keep successful units, resume failures, and recheck authorization immediately before each dispatch. |
| `config/settings.py`, upload and OCR settings; `apps/core/utils.py`, `validate_upload` | Default upload accepts PDF, DOC/DOCX, XLS/XLSX, PPT/PPTX, TXT/CSV and JPEG/PNG/TIFF. Validator recognizes additional configurable formats. | Publish explicit support or unsupported outcomes for every accepted extension; never send unsupported Office binaries to image OCR as though that were a reader. |
| `apps/documents/tasks.py`, `_extract_document_task` | Generation fences, actor checks, current consent, duplicate-append protection, late-file discovery and preservation of usable text already exist. Document text is clipped to 200,000 characters. | Retain these protections; add version/run/unit state and bounded recovery. Search and model evidence must include later supported units. |
| `apps/documents/services.py`, `ingest_upload`, `_append_initial_file`, `re_extract_document` | Background dispatch uses commit hooks; queue submission failure marks the saved document failed. Synchronous OCR can run inside the upload or mutation transaction. | Persist pending intent transactionally, use short finalization transactions and move expensive I/O outside locks in both runtime modes. |
| `apps/documents/models.py`, `OcrStatus`, `Document`, `DocumentFile` | Legacy six-state OCR status, immutable current files, document-level OCR text and stored search vector. | Add separate work/coverage state with an explicit legacy adapter. Existing page counts stay physical-page counts where known. |
| `apps/documents/file_versions.py`; Documents migration `0014` | Immutable `DocumentFileVersion`, complete Tracking history copied on filing, current root and final snapshot can share a blob. | Bind processing to the matching immutable current snapshot and checksum. Do not reimplement versions, move files or change shared-blob deletion. |
| `apps/documents/suggestions.py`, `AIEngine` | Always delegates to rules and identifies that fallback. Rules inspect full supplied text, while keyword generation only reads its first 20,000 characters. | Add actual trained inference through the existing `Suggestion` interface; retain truthful fallback and current-document evidence. |
| `MetadataSuggestion`; `save_document_metadata`; `export_training_data` | Proposed/accepted metadata and reviewer are saved, but only the first 4,000 characters are retained. Export does not establish explicit per-tag negatives. | Preserve historical rows; introduce explicit review scope and known/unknown labels. Existing reviewed examples are not automatically a training truth set. |
| `apps/core/models.py`, `Tag`, `TagRule`; `services.set_tags` | Stable tag primary keys, description/category/active status, configurable rules, mutable assignment set and usage counters. | Reuse those IDs; add approved aliases, canonical redirects/revisions and contribution provenance. Serialize human/automatic writes through one service. |
| `services.suggested_tags_for`; `DocumentQuerySet.visible_to` | Tag choices use visible documents for ordinary users. Office-admin repository access is office-scoped. OVPA/External are separate collections. | Preserve these boundaries for evidence, sources, chunks, counts, saved views and models. Approved global vocabulary is explicitly distinguished from document-derived vocabulary. |
| `apps/search/services.py`, `search_documents` | PostgreSQL FTS plus trigrams and field bonuses; exact identifiers prioritized; Search is capped while Repository requests exhaustive evaluation. Escaping is performed before authored highlight markup. | Add distinct-document retrieval from current chunks, source-aware safe snippets and separately weighted alias expansion without changing slider semantics casually. |
| `apps/search/indexing.py`, `save_masterdata_form` | Office/type/tag/searchable-field changes refresh affected indexes with consistent locks and an after-commit additions pass. | Extend existing refreshes with revisioned chunk/index reconciliation; do not replace them with stale asynchronous-only updates. |
| `apps/documents/views.py`, Repository and metadata review; document templates | Office/type/tag folders, filters, review chips, extraction polling, previews/downloads and explicit Search controls already exist. | Extend these screens with coverage, per-tag actions and saved filtered views. Avoid a competing navigation or broad redesign. |
| `tasks.purge_deleted_documents`, deletion-review services | 30-day recovery, exact shared-key cleanup, retained Tracking originals and audited permanent removal. | Hide all derived content on removal; restore only current eligible indexes; extend purge to derived text, training snapshots and model invalidation. |

### Tests and migrations inspected

The audit read extraction limits, queued extraction security, overlap/idempotence, failed-OCR preservation, background-review and finalization tests; search scoring, identifier-priority and master-data refresh tests; current models and Documents migrations `0012`, `0013`, `0014`. It also inspected shared upload validation, permission locks and immutable file models. This is targeted source coverage, not a systemwide audit claim.

Fresh baseline: **50 passed, 0 failed, 0 skipped, 2 warnings in 25.16 seconds** across the ten selected extraction/queue/search modules. Both warnings concern existing `CheckConstraint.check` usage, in a Tracking model and migration. Tests used `test_doctrack_ai_ml_audit_20261022`, temporary local storage and disabled external OCR. The working database was not migrated or used for mutations. Raw local logs/configuration: `.vercel/ai-ml-audit/baseline.log`, `baseline.xml`, `audit_settings.py`; ignored and not committed.

Installed versions inspected: Django 5.2.17, psycopg 3.3.6, django-q2 1.11.1, pypdf 6.19.0, python-docx 1.2.0, openpyxl 3.1.5, pytest 9.1.1. Pillow, pypdfium2 and scikit-learn are not installed in this virtual environment. No parser, rendering or ML dependency has been installed during design.

### Documentation to correct during implementation

- `docs/AI_ROADMAP.md` still calls AI deliberately unbuilt, presents historical reviewed suggestions as training labels, gives unmeasured sample/quality expectations, and permanently forbids automatic tags. Replace these with the shipped lifecycle and evidence gates; retain the honest fallback explanation.
- `docs/SEARCH_DESIGN.md` contains field bonus examples that disagree with the additive bonuses in `_field_score`. It also describes the body vector as the full body although extraction aggregation clips content. Document the actual before/after scoring contract and limits.
- Extraction module comments say OCR is used only if native text is empty and describe a free-tier assumption. Update those comments with per-unit assessment and configured tier limits.
- `OcrLanguage.AUTO` currently says automatic English/Filipino, while OCR.space is mapped to English with a warning for Filipino. Use accurate labels without silently changing stored values.
- AGENTS refers to `git-github-guide-udm-doctrack.md`, but that guide is absent from this checkout. Follow the explicit branch/commit rules in AGENTS rather than inventing the missing guide.

## Approach

**Recommended: extend the current Django services with source-unit processing and a local CPU model.** This preserves private storage, existing jobs/review UI, signed downloads and PostgreSQL search. New tables express provenance or recovery guarantees that the current document-level text fields cannot represent.

Two alternatives were considered. Replacing the pipeline in a new independent service would duplicate authorization and lifecycle controls. An external generative model would introduce another document transfer and still would not establish page coverage or verified labels. Neither is needed for this request.

Keep extraction, search indexing, tag decisions and model lifecycle as separate modules with explicit contracts. A model failure must not stop uploads, manual metadata, native extraction or lexical search.

## A. Data and compatibility contracts

Additive migrations only. Names below are the intended public model/service names; implementation may split modules without introducing competing concepts.

| Model or extension | Purpose and key constraints |
| --- | --- |
| `ExtractionRun` | Document, current `DocumentFileVersion`, checksum, document generation, extractor/settings digest, actor or explicit maintenance purpose, work state, coverage state, completeness counts/unknown reason, lease/attempt/retry metadata. Unique `(file_version, generation, settings_digest)`. |
| `ExtractionUnit` | Run-local ordered locator, kind, raw and normalized text, method, provider confidence nullable, quality warnings, truncation, attempts, last-good same-version result, terminal/retry state. Unique `(run, locator_key)`. PDF page numbers and TIFF frame numbers are real; DOCX/sheet locators are semantic. |
| `SearchChunk` | Current unit, chunk ordinal, normalized offsets, bounded text/vector, index/content revisions. Unique `(unit, ordinal, index_revision)`; one published revision is selected atomically. GIN index for text. |
| `Tag` extensions and `TagAlias` | Reuse tag PK/description/active status; add canonical redirect, vocabulary revision and explicit global-approval/auto-eligibility controls. Alias uniqueness within a canonical vocabulary scope. Reject redirect cycles. |
| `TagPrediction` | Immutable source/model/rule/version, input revision, per-tag score/action/thresholds and evidence unit+offsets. Idempotent for `(document, input_revision, engine_revision, tag)`. |
| `TagReview` | Append-only confirm/reject/add/unsure/complete-review events; actor/office/time, input revision and enumerated tag/vocabulary scope. Unknown is not a negative. |
| `TagContribution` | Human/automatic contributions supporting each materialized Document.tags link. An automatic rollback cannot remove a surviving human contribution. Unique contribution identity prevents duplicate delivery effects. |
| `TrainingSnapshot`, `TrainingExample` | Restricted immutable dataset recipe, exact eligible text/hashes, label/provenance/vocabulary revisions, family/split and source refs. Unique example within snapshot. Published snapshots are immutable except lifecycle withdrawal/removal. |
| `TagModel`, evaluation and activation records | Private artifact manifest, scope, runtime/feature/schema/vocabulary versions, training snapshot, integrity, per-tag evaluation/thresholds/mode, current activation pointer and audited activation/rollback. At most one activation per office+collection scope. |
| `SavedDocumentView` | Named, validated repository-filter definition owned by a user, with explicit existing collection value. No file duplication or new sharing authority. |

No separate file-version tables, embedding tables or physical file conversion. Source/run/unit rows cascade with their repository document. Prediction/review history is not rewritten on a vocabulary merge. Training artifacts use private storage, never static files or Git.

Legacy `Document.ocr_text` remains a bounded compatibility summary while reads migrate to units. Legacy coverage starts **unknown**. Legacy manual tags are backfilled as human contributions without fabricated reviewer identities/times. Existing historical suggestions stay historical, with no inferred negative labels. No provider calls, model training or large reprocessing inside migrations.

Work states: pending, running, retry-wait, complete, blocked, unsupported, failed, action-required. Coverage is separately complete/partial/unknown with readable, expected and omitted counts. “Complete” work means processing ended, not that all content is readable. No heuristic `BLANK_VERIFIED` state. Old OCR status mapping stays centralized: pending/running map directly; fully readable success to DONE; partial failures to FAILED or SKIPPED with explicit coverage; legitimately empty supported text to EMPTY. Missing units never become a green success via the adapter.

## B. Source-unit extraction and OCR

### Formats

- **PDF:** enumerate bounded pages, extract native text per page, inspect placed image geometry and rendered content. Walk nested form objects with composed transforms, clip to page bounds and measure union area rather than summing overlapping resource boxes. Rendering supplies bounded visual evidence where geometry/native quality is insufficient. A header plus scanned body requires OCR or an incomplete warning. Conservative false alarms are preferable to declaring hidden content readable.
- **DOCX/DOTX when accepted:** ordered body blocks including tables, headers/footers and supported raster-image relationships. Store section/block/table/image locators, not page numbers. Read images even if body text is long; detect unread drawing/OLE/altChunk/unsupported objects. Never execute relationships, load remote URLs or treat visual alt text as extracted image text.
- **XLSX/XLSM when accepted:** bounded worksheet/cell ranges, coordinate/context and cached values, explicit missing-cache/formula/image/object warnings. Macro-enabled input is data only; no execution.
- **JPEG/PNG/TIFF and configured validated image formats:** enumerate supported frames within bounds, retain orientation/preprocessing provenance and source frame mappings. Oversized/ambiguous content is visible action-required/partial, not silently discarded.
- **TXT/CSV and configured validated text formats:** bounded streaming blocks with original offsets/encoding warnings. CSV fields are data, not executable spreadsheet expressions.
- **DOC/XLS/PPT/PPTX and any validated extension without a reader:** explicitly unsupported extraction with original preview/download fallback and manual metadata. Native PowerPoint/legacy binary readers are not implied by upload acceptance.

Storage safety budgets, per-unit text budgets, chunk sizes, rendering pixel/DPI bounds, worker runtime and model feature budgets are independent validated settings. Each limit records omitted coverage. Later units are processed/indexed within declared safety bounds rather than lost to a prefix-only document aggregate. Multiple attachments contribute independently.

Preserve native/provider order. Store native and OCR candidates separately; select one canonical text for an overlapping page/region through a versioned quality policy. Never concatenate a native header and a full OCR page blindly. An OCR error cannot erase a last-good result for the same version, and stale-version text cannot become current.

### Provider and queue contract

OCR.space Free has a documented 1 MB file and three-page PDF limit. Default conservatively to 1,000,000 maximum submitted bytes and one PDF page per call, with the entire multipart request also under the conservative budget. Record actual file/body sizes. This is deliberately stricter than a provider file-only interpretation; do not assume an account's daily/monthly remaining quota. Administrator configuration may express verified provider limits but not bypass opt-in.

Prefer single-page lossless PDF slicing when it fits; otherwise render a page within configured pixel/DPI limits. Do not repeatedly reduce quality until an unreadable image happens to fit. Record all lossy preprocessing; if safe readable processing cannot fit, retain native text and report action-required. Handle missing renderer and unsupported frame limits explicitly.

Parse numeric/string overall `OCRExitCode`, error flags and each `FileParseExitCode`; missing/extra result mappings are coverage problems. Retain successful units even when others fail. Confidence stays null if absent; heuristic quality and measured transcription metrics have separate fields.

Use finite per-unit retries, bounded exponential jitter and bounded Retry-After handling. Transient outages/rate limits may resume; malformed/encrypted/unsupported/persistent validation failures require intervention. Quota waits expose a finite resumable retry state. Store sanitized reason codes and metadata, not response bodies or credentials in logs.

Persist pending `ExtractionRun` intent in the same transaction as the saved document/version. Enqueue after commit. A bounded recovery command/scheduled invocation scans eligible pending/expired leases; it does not create a second queue. Unit/job identity and conditional lease/revision updates make duplicate delivery harmless to persisted effects, without promising exactly-once provider billing.

Read current actor, document activity, generation, selected version and OCR opt-in immediately before **each** external call and again at finalization. A user job retains its actor through every retry/recovery. The narrow existing maintenance path has explicit local-only eligibility unless separately authorized. Revocation after dispatch cannot retract transmitted bytes.

Locks follow actor -> Document -> processing rows -> Tag/assignment rows, reusing `active_writer` behavior and existing lock ordering. Provider/rendering/model I/O is outside database transactions. In synchronous mode, commit the upload/intent first, perform bounded local processing outside locks, then finalize; processing failure preserves the saved files and manual review. Preserve storage compensation and existing upload APIs with focused compatibility tests.

Task granularity must fit an enforceable runtime budget. Configure q2 retry above worker timeout, finite `max_attempts`, and include nested provider attempts in the budget. Test Windows interruption/expired lease recovery. Report oldest pending age, attempts, reason counts, submitted usage and index lag without document bodies.

## C. Controlled tags, review and organization

Extend the existing metadata review and document detail screens. Show a concise coverage summary, expandable unread-unit reasons, actual engine name and source-linked evidence. Keep file Preview/Download controls. Use small confirm/reject/unsure actions beside tag suggestions, plus Add tag and an explicit complete-review action scoped to the displayed vocabulary version/tag set. Leaving a tag untouched is unknown. Do not mark all vocabulary reviewed by saving ordinary metadata.

One permission-aware tag service handles manual and automatic changes under fresh document permissions. Its machine-write allowlist is topic-tag membership only. Office/security/grants/collection/routing/status/retention fields and document-type writes are forbidden to automation. Document types remain multi-label suggestions requiring existing authorized confirmation.

Automatic eligibility is per tag and input revision, requires complete acceptable coverage, current vocabulary, validated evaluation, explicit administrator enablement and the global switch. Retired/ineligible/canonical-invalid tags abstain. Rule scores are heuristics; rules require their own evidence/evaluation/enablement before automatic use. No unchecked free-word keyword becomes a canonical automatic tag.

Append-only human decisions outrank automation. Explicit rejection survives reprocessing/model changes; no automatic resurrection. Human additions remain on new content revisions. Changed evidence makes prior predictions stale and flags review, while preserving human state. Rollback removes only contributions exclusively supplied by the selected automatic release. Usage counts, assignments, provenance and index revision update atomically.

Merges remap current assignments and redirects with audited reasons. Historical prediction/review IDs, JSON evidence and snapshot vocabulary retain their original context. Duplicate human/automatic contributions collapse only in the materialized assignment set.

Saved views reuse the repository's office/type/tag/date/origin/deletion filters. Each is private to its owner; reopen by validating a strict filter allowlist and intersecting current permissions/collection. Invalid/inaccessible filters return feedback rather than widening the population. Counts are distinct documents; a file may appear in multiple views without being copied. Preserve existing folder/filter/pagination and manual Search behavior.

## D. Training, evaluation and inference

Implement executable CPU-only preparation/train/evaluate/activate/rollback commands and a real `AIEngine` adapter. Missing model, schema mismatch, invalid integrity or inference failure returns truthful rules/manual fallback. No candidate silently activates. Shadow records predictions without changing visible suggestions/tags; suggestion mode exposes reviewable predictions; automatic mode additionally enforces the per-tag gates.

Training eligibility is explicit, defaults off for existing records, and is independent of OCR consent. System administrators govern model activation; authorized document administrators can mark their own eligible records/review labels. Restrict artifacts and reports to their authorized administration scope. First deployed models are **office+collection scoped**; restricted documents are excluded by default, and a source's withdrawal/scope change invalidates dependent candidates/activations. Do not train a globally accessible vocabulary from private office text. A system-admin-only cross-office experiment must not become a staff model.

Snapshot exact normalized current unit text, title/header/body zones, hashes, allowed labels, source/eligibility revisions, canonical vocabulary and family/split decisions in private storage. Group exact duplicates, known revision chains and explicit related-document families. Optional near-duplicate grouping requires a recorded tested rule; sharing a blank form template is insufficient. Separate records are never merged by hash.

Labels are positive, negative or unknown. Train each tag's binary logistic regression on known-label rows only. Fit shared word/character vectorizers on the eligible training partition only; never on calibration, development or held-out text. A row unknown for one tag may teach another tag but is never a negative for the first. Single-class/insufficient-support tags are explicitly unavailable; candidate suggestions need sufficient known training examples of both classes.

Use explicit title/header/body `FeatureUnion` branches with numeric weights, each with bounded word and character n-gram vocabularies. Use all supported stored units within declared feature-resource bounds, not the first N characters. Any evidence sampling/overflow must be versioned, visible and evaluated on late-topic/long-file fixtures; incomplete model evidence disables automatic use. Reversible normalization affects model features, never exact identifier or source text storage.

Use deterministic family-disjoint train/calibration/development/final-test partitions. Verify class/group support before fitting; do not force cv=5 or synthesize negatives to obtain a split. Use sigmoid calibration on a separate supported calibration partition, otherwise clearly mark probabilities uncalibrated and keep automatic mode off. No isotonic baseline for small data. Model probabilities and calibration are evaluated at document level, with length/language/office slices; no unvalidated page-score maximum.

Choose per-tag suggestion/automatic thresholds on development only, with recorded metrics and coverage. Freeze the candidate and thresholds before final evaluation. Predeclare the initial automatic gate: at least 95% observed precision **and** a one-sided 95% Wilson lower confidence bound at least 95%, at least 100 independent predicted-positive families, at least 1% prediction coverage on an eligible independently sampled held-out population, usable calibration/class support and no failed security/completeness gate. Report recall/abstention, denominator, slices and uncertainty. These are conservative project policies, not measured quality claims. A rare tag that cannot satisfy them remains suggestion-only. Zero predictions means not qualified, not perfect precision. Synthetic data never qualifies an enabled production tag.

For that interval/support gate, select one eligible representative per held-out family deterministically before viewing model predictions. Count predicted-positive representatives, not correlated revisions or merely positive-labeled records. Report ordinary document-level metrics separately. Insufficient independent support prevents qualification; it is not repaired by counting the same family repeatedly.

Store trusted artifacts as bounded versioned JSON/numeric arrays for the supported TF-IDF/logistic/sigmoid components, loading numeric arrays without pickle. Check manifest provenance, exact runtime/schema/features/vocabulary, lengths, finite coefficients, expected digests and private trusted origin. Test prediction parity against the trained sklearn pipeline. Do not add an arbitrary pickle upload or mark a hash as sufficient trust. Pin tested sklearn/numpy/scipy versions in the ML dependency profile before training.

Evidence cites features found in this document's current units and offsets, never a training document. Contributing n-grams show model support, not proof of correctness. Keep prediction details scoped; never expose full model vocabulary/weights in the ordinary UI.

Reviewer queues combine reproducible random audits with uncertainty-driven review and report the sampling source. Click logs are not labels. Repeated releases use fresh final evaluation sets or declare test reuse; no hidden retuning on the same holdout.

## E. Search contract and lifecycle

Preserve `Document.objects.visible_to(user)` intersected with collection, activity and current version/revision constraints in every query and related endpoint. Rank distinct documents, not globally capped chunks. Use correlated grouped chunk matching/maximum bounded body score per authorized document, then apply the document candidate/result limit; long documents cannot crowd out other documents before aggregation. Benchmark adversarial many-chunk records and actual PostgreSQL EXPLAIN plans.

Use bounded source chunks with overlap and offsets; do not assemble unlimited text into one tsvector. Raw identifier equality still short-circuits to 100; normalized lookup is a separate signal. OCR O/0/I/1 alternatives are explicitly weaker and cannot link or identify a record automatically.

Reuse the shared relevance formula, thresholds and Search-vs-Repository defaults. Extend the body signal using the best source-unit score with tested normalization, and version any changed semantics before enablement. No arbitrary rank-fusion-to-percentage conversion. Approved aliases expand a separately scored component with an explicit smaller weight, visible expansion explanation and limits. Original exact identifiers/terms retain priority. Approved global acronyms must not be populated from another office's private queries/documents.

Snippets label filename and page/frame/section/sheet, using raw-text span marking and the existing one-pass escaped highlight approach. Do not trust `ts_headline` HTML. Preserve provider/native order; no source text executes or creates remote requests. Evidence/text endpoints reauthorize the document on every request and use private/no-store responses. Downloads retain existing exact-version signatures.

Search's capped evaluated/hidden/limited counts remain window-scoped and explicit; Repository evaluates all matching document IDs for pagination and facets. Compare full ID sets as well as totals. Add explicit unique final tie-breaking while preserving existing order semantics. Ordinary offset pagination is stable for an unchanged snapshot; document that concurrent edits can move results instead of promising snapshot consistency.

Publish chunks/current extraction and index revision atomically. Metadata/tag/type/alias/master-data changes trigger existing refresh logic plus revision checks. Bounded reconciliation repairs lag without overriding manual state. Extraction coverage and index freshness are separate displays.

Removal immediately hides derived text/chunks/evidence/saved-view memberships and invalidates training/model use. Recovery retains private data during the existing 30 days; restore revalidates current scope/revision and rebuilds derived indexes, without silently re-enabling a retired model. Permanent purge removes repository-derived extraction/snapshot/artifact objects with deduplicated storage cleanup, keeps minimal noncontent audit receipts and preserves original Tracking attachments. Affected active models are retired and rebuilt; no claim of instantaneous machine unlearning. Whole-snapshot invalidation/removal is acceptable when per-example removal would break reproducibility; the manifest records withdrawal rather than pretending an old snapshot is unchanged.

## UI and feature flags

Reuse Bootstrap components and current themes. Add extraction coverage/reason disclosure to existing status, review and detail views. Read-only viewers may inspect authorized results/evidence but cannot review/apply. Keep progressive enhancement, explicit Search, no-JS POST/GET fallbacks, CSRF, keyboard focus and existing viewport preservation.

Planned validated controls: source-unit extraction; chunk search; shadow/suggestion/automatic modes; global automatic off; model scope/activation; OCR request/page/quota/retry/runtime/rendering limits. Rollback disables model/tag automation and chunk reads independently while keeping honest partial/unknown coverage. Rollback does not delete provenance or roll back manual decisions.

Use local feature flags for rollout. No new LLM API keys, conversion services, external model APIs or automatic pgvector migration. OCR.space key configuration remains environment-only. Pillow/pypdfium2 and sklearn/numpy/scipy versions will be installed/pinned and smoke-tested on Windows and the project's CI platform during implementation, not claimed verified from a website alone.

## Dependency review and primary references

- [OCR.space API](https://ocr.space/ocrapi): verified Free 1 MB/3 PDF pages, aggregate partial-success code and per-result success/failure codes. The adapter will handle both levels rather than using HTTP status as success.
- [django-q2 configuration](https://django-q2.readthedocs.io/en/master/configure.html): finite attempts, retry above runtime and Windows termination behavior inform unit-sized jobs/recovery. Installed q2 is 1.11.1.
- [Django transaction hooks](https://docs.djangoproject.com/en/5.2/topics/db/transactions/#performing-actions-after-commit): commit hooks enqueue work; the application still needs durable intent for a crash between commit and enqueue.
- [pypdfium2 project](https://github.com/pypdfium2-team/pypdfium2) and [API/threading notes](https://pypdfium2.readthedocs.io/en/stable/python_api.html): permissive wrapper/PDFium licenses require bundled dependency notices; use supported platform wheels and serialize PDFium calls within a process. Evaluate actual placed nested object geometry. Windows/Linux wheel and renderer smoke checks remain pending.
- [Pillow license](https://pillow.readthedocs.io/en/stable/about.html): MIT-CMU; enforce decoded-pixel/frame bounds independently of upload size.
- [sklearn text features](https://scikit-learn.org/stable/modules/feature_extraction.html#text-feature-extraction), [FeatureUnion weights](https://scikit-learn.org/stable/modules/generated/sklearn.pipeline.FeatureUnion.html): implement numerical branch weights and train-only fitting.
- [Calibration](https://scikit-learn.org/stable/modules/calibration.html) and [model persistence](https://scikit-learn.org/stable/model_persistence.html): calibration needs independent data; small-data isotonic is unsuitable, pickle-family loads can execute code and cross-version loading is unsupported. Use bounded non-executable supported-component artifacts with parity tests.
- [PostgreSQL text-search limits](https://www.postgresql.org/docs/current/textsearch-limitations.html): bounded vectors avoid the documented size/position limits. Verify plans/behavior on the actual local and CI server versions, not only the current documentation.

These are design inputs, not successful local dependency/provider/model verification. No third-party performance or quality figure is adopted as measured DocTrack behavior.

## Milestones and acceptance gates

| Milestone | Working deliverable | Required evidence before moving on |
| --- | --- | --- |
| A: audit/contracts | This reviewable design, current-state baseline; after review, detailed plan and isolated migration/flag design | Existing invariants enumerated; no unrelated changes included. |
| B1: native units | Additive runs/units/chunks; PDF/DOCX/XLSX/text coverage and truthful legacy adapter | Mixed PDFs, late attachment/topic, ordered blocks, formula/unsupported coverage, malformed/encrypted/limit fixtures; same-version text preservation. |
| B2: OCR/recovery | Free-tier bounded dispatch, unit outcomes/retries, durable intent and leases | Partial/missing codes, payload sizes, revoke/cancel/generation/version checks, duplicate delivery, worker interruption and commit/enqueue failures; no live file bulk processing. |
| C: vocabulary/reviews | Canonical vocabulary, contribution-aware application, per-tag review and saved views | Human/rejection priority, merge history, manual counters, crafted requests, cross-office/collection canaries, no-JS and responsive review flow. |
| D: real ML | Reproducible snapshot/train/calibrate/evaluate commands, trusted artifact inference, shadow/suggestion activation and rollback | Unknown masks, family/split isolation, train-only vocabulary, artifact parity/corruption, no-model fallback and explicit engine labels. A-C alone are not task completion. |
| E: qualified automation/search | Per-tag evaluation gate, current source search/snippets/aliases, lifecycle reconciliation | Interval math/zero-prediction tests, no synthetic qualification, combined concurrent human/automatic review, complete result IDs/facets, long-document saturation, malicious text/query escaping, deletion/restore/withdrawal. |
| F: optional semantic | Deferred until judged lexical recall shows a gap and deployment/model/privacy prerequisites are met | No semantic-search or learned-reranking claim in this delivery. |

Each implementation milestone starts with failing regressions for the intended behavior, uses a focused local commit and updates a coverage ledger. Do not mark a milestone complete from code presence alone. Implement the real train/inference path even if no production dataset qualifies.

## Verification and delivery contract

- Run PostgreSQL regressions for extraction limits, idempotence/overlap/finalization, fresh actor checks, file histories, multi-tags/types, counts, search/repository windows, master-data refresh and 30-day deletion. Test both background-enabled and disabled execution; injected storage/provider/index/queue failures and concurrent review/apply/version/finalization.
- Add separate controlled parser/provider/model fixtures. Test every accepted extension's truthful outcome, malicious embedded content, unknown confidence, truncation/partial coverage and stable locators. Fixture mechanics are not real-world OCR accuracy.
- Train/evaluate a controlled synthetic model to prove the entire executable path; label its artifacts/report as nonproduction and forbid auto enablement. Add authorized real-data evaluation commands/runbook without fabricating eligible labels or training volume.
- Create a judged query fixture set for exact IDs, body-only/late attachment matches, spelling, approved acronyms/synonyms, English/Filipino/Taglish and long-record crowding. Measure candidate recall independently from ranking, precision/success/nDCG@5 and cold/warm latency with stated hardware/corpus. Do not advertise benchmark targets as results.
- Test office admins, system admins, staff, viewers, anonymous/suspended/transferred actors and revoked grants for source/evidence/review/model/report/export endpoints. Hash matches and timing must not disclose other offices' records.
- Run full project PostgreSQL/JavaScript suites, relevant background regressions, lint, template/system/deployment checks and migration drift. Keep original DB untouched. Exclude only documented unrelated unfinished External custody expectations; do not hide failures from the shipped scope.
- Verify changed UI with a real isolated Django browser session, CSRF, current responses, keyboard, no-JS, both themes and 320/390/768/900/901/1366 widths. Explicitly list any unperformed screen-reader/physical-device/live-provider/quality/performance checks.
- Backfill dry-run inventory first; bound work by IDs/batch count; do not mark legacy coverage complete. Add recovery/reindex/reprocessing commands with repeatable finite behavior. No bulk external OCR without separate authorized action.
- Update setup, environment/dependency instructions, format limits, architecture, review/training/evaluation/activation/rollback and recovery docs. Final report references the actual final revision, tests and migration status; distinguish capability, fixture mechanics, live integration, measured real quality and enablement.

## Review checkpoint and unresolved activation facts

The user supplied purpose, strict constraints and Free-tier choice, so ordinary reversible implementation choices do not need repeated permission questions. The applicable Superpowers architectural workflow requires review of the written spec before the detailed implementation plan, and the supplied prompt explicitly requires following that checkpoint. Request a single review of this concrete document before product implementation.

Before live activation only: confirm environment-only OCR configuration and remaining quota; establish approved canonical topic vocabulary and eligible explicit reviews; verify the worker host's memory/runtime/storage; obtain real held-out evaluations. Defaults remain rules/manual filing, shadow or suggestion-only ML, and **zero automatically qualified production tags** until evidence and authorized enablement exist.

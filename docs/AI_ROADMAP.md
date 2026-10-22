# Document intelligence: implemented capabilities and remaining work

The optional local topic model is executable. `AIEngine` runs a private, scoped CPU model when enabled and eligible, with the existing rules engine as a fallback. Source extraction, source-chunk retrieval, approved aliases, topic inference and automatic topic application all default to off. This is not evidence of production accuracy.

## Available capabilities

- Version-bound source units preserve native/OCR text, coverage, warnings and source locations. Supported native formats include PDF, DOCX/DOTX, XLSX/XLSM and UTF-8 text. Images and supported embedded images can use guarded OCR.space units when authorized. Legacy formats and unread objects are disclosed.
- Current-unit chunks recover text beyond the bounded legacy aggregate and identify its original file. Approved shared aliases can expand lexical queries at half text weight. This remains lexical retrieval, not semantic search.
- Explicit confirm/add/reject/unsure reviews provide topic labels. Missing labels remain unknown. An ordinary metadata review or an existing assignment does not supply negatives for every absent topic.
- Authorized administrators choose training eligibility. System administrators prepare private datasets per office and collection. Duplicate/source/template families stay within one fixed partition.
- Shared train-only word and character TF-IDF features feed per-topic logistic regressions. Known-label masks, separate sigmoid calibration when supported, development thresholds and frozen independent holdout metrics are implemented. Small or uncalibrated candidates remain suggestion-only.
- Private bounded JSON/numeric artifacts run without pickle. Explicit shadow, suggestion, activation and rollback actions retain source/vocabulary/runtime checks.
- Source evidence, human review, eligibility, model administration and private saved-filter views have UI support. Existing records can request fresh proposals without rerunning OCR or assigning tags.
- A scoped review queue mixes repeatable random audits with bounded uncertainty review and saves signed sampling provenance. Evaluation records final-test reuse and reports all-document metrics separately from independent-family qualification. Language-quality evaluation remains unavailable until reviewed language annotations exist.

## Boundaries

Models suggest approved subject topics. They do not change office ownership, collection, retention, access levels, routing, statuses, timers or file contents. Other metadata proposals remain identified as rules. Files stay in their original formats. There is no document conversion or public document-viewing service.

Automatic application requires the global flags, an active automatic model, explicit per-topic enablement, safe current evidence and qualified real-data evaluation. Gates include observed precision and a one-sided 95% Wilson lower bound both at least 0.95, at least 100 independent predictions, at least 1% coverage, separate calibration support and frozen development/test evidence. Authored fixtures never qualify. With no qualified real data, production automatic topics remain zero.

Source changes, withdrawal, deletion and approved vocabulary changes invalidate affected models. Restoring eligibility does not reactivate a model. Permanent source purge clears private snapshot text and fitted artifacts while retaining noncontent receipts. Human topic contributions survive automatic-release rollback.

## What still requires evidence

Real authorized reviewed training data, representative retrieval judgments, English/Filipino/Taglish OCR quality, deployment resource measurements, and deployment-specific worker/storage checks must be supplied and evaluated before production enablement. A count of reviewed documents alone cannot establish accuracy or calibration. Rules are a fallback, not a claim of measured superiority.

Document-type ML, entity-extraction ML, embeddings, learned reranking and external LLM integrations are not implemented by this change. Propose them separately only after measuring a concrete need.

Use [the implementation runbook](document-intelligence.md) for setup and commands and [the verification report](verification/document-intelligence-2026-10-22.md) for actual checks and limits. The older `export_training_data` output is historical metadata-review material; it is not the supervised topic dataset.

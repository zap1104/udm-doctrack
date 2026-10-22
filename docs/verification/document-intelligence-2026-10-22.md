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

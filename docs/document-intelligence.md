# Document processing and topic tagging

Implementation is in progress on the local `feature/documents-intelligence` branch. Source-unit extraction, chunk search, ML and automatic tags default off. This document will be extended with the actual verified commands as the approved plan is implemented. It does not claim a production model or live OCR validation.

## Native source readers

| Format | Source units and limits |
| --- | --- |
| PDF | Actual pages; bounded native extraction and visual assessment, including transformed/nested image placements. OCR-needed pages remain incomplete until processed. Near-blank is uncertain, never verified blank. |
| DOCX / configured DOTX | Ordered body paragraphs/tables and referenced headers/footers; embedded images are separately flagged for OCR. Unsupported objects/alternate content carry warnings. No invented page numbers. |
| XLSX / configured XLSM | Bounded worksheet/cell ranges with coordinates and cached formula values. Missing formula caches, embedded media/charts/objects and row/column/cell limits are disclosed. No formulas/macros execute. |
| TXT / CSV and configured MD / LOG | UTF-8 text ranges; invalid encoding produces a warning. Later ranges are preserved instead of a document-prefix-only extract. CSV is data only. |
| JPEG / PNG / TIFF and configured validated BMP/GIF/WebP | Bounded frame enumeration and pixel checks; OCR-needed frames are explicit. A frame limit makes remaining coverage unknown. Actual upload validation still determines what may be uploaded. |
| DOC / XLS / PPT / PPTX | Upload acceptance does not imply a native reader. Explicit unsupported extraction; original preview/download and manual metadata remain available. |

Limits distinguish uploaded bytes, expanded Office archives, per-unit stored text, number of source units, decoded pixels and rendering DPI. Gaps remain visible. Raw and normalized unit text are separate; chunk offsets will reference the normalized representation. This feature does not convert, replace or modify original files.

Placed-image coverage uses the union of transformed placement polygons on a bounded512-square mask, including nested forms and form bounding boxes. It estimates visible image regions, not readable characters. Unsupported complex clipping marks its upper-bound estimate uncertain. The cutoff is configurable; small unread image regions also retain warnings. No OCR accuracy claim follows from native text length or image coverage.

## Rendering dependencies

Windows CPython3.13 binary-wheel smoke tested: Pillow13.0.0 and pypdfium2 5.14.0. Pillow is MIT-CMU. pypdfium2 wrapper licenses are Apache2/BSD3; PDFium and its bundled dependencies include additional notices in the installed wheel. Preserve those notices in any redistributed package. Linux/hosting-platform verification remains pending.

PDFium calls are serialized per process because the native library is not thread-safe. Pages, bitmaps and documents are explicitly closed. pypdf6.19+ supplies context-local decompression limits; external JBIG2 helper execution is disabled for this reader. Missing renderer preserves native text with incomplete coverage rather than claiming success.

## Guarded OCR units

The new OCR.space unit adapter uses conservative1,000,000-byte file and whole-request budgets, checked against actual multipart bytes, and exactly one PDF source page per dispatch. It slices pages losslessly where possible; oversized pages may be rasterized at the configured DPI with explicit lossy-processing notes. Oversized images at that safe resolution require action instead of silent downsizing. Source image/frame orientation/color conversion and white alpha compositing are recorded; original bytes are unchanged.

Every transport attempt invokes a fresh authorization callback. Overall and per-page result codes and missing/mismatched results determine coverage; HTTP200 is insufficient. Optional confidence stays null when absent/unusable. Bounded transient retries use jitter. Rate/quota errors create finite resumable waits, not an automatic request loop. Returned errors are generic and do not expose keys or provider bodies.

OCR.space is configured to English in this adapter; Filipino/Taglish quality remains unmeasured. The adapter is wired into the opt-in unit pipeline and remains off by default. Live provider, real-data tagging/search quality, browser and deployment checks remain unverified.

## Scoped local topic models

Install the optional CPU profile with `.venv\Scripts\python.exe -m pip install -r requirements-ml.txt`. It is separate from the base web requirements. Model artifacts pin Python major/minor and exact NumPy/SciPy/scikit-learn versions; incompatible bundles fail closed. No pickle, public document service or new API key is used.

Only explicitly eligible, active, nonrestricted documents from the selected internal office and collection enter a dataset. Existing records default to ineligible. Explicit confirm/add/reject/unsure decisions are scoped to content and vocabulary revisions. Missing labels remain unknown. A complete review covers only its selected canonical tag IDs. A source change, withdrawn eligibility, new review or vocabulary change makes an old snapshot unusable.

System administrators can inventory with `manage.py prepare_tag_dataset --actor USER_ID --office OFFICE_ID --collection OVPA`; add `--apply` to save the private snapshot. Use `manage.py train_tag_model SNAPSHOT_ID --actor USER_ID`, then `manage.py evaluate_tag_model MODEL_ID --actor USER_ID`. Training creates an inactive candidate. `manage.py activate_tag_model MODEL_ID --actor USER_ID --mode shadow` explicitly enables shadow predictions; `suggestion` mode exposes model topic proposals. Other metadata proposals remain rule-based. `ENABLE_TAG_ML=True` and `SUGGESTION_ENGINE=ai` opt into inference. These commands are not run against original data during implementation.

Snapshots contain bounded text and review provenance in private PostgreSQL tables. Numeric artifacts remain in the private model registry with digests and origin/runtime checks, not media/static storage. Never copy private snapshots, coefficients or vocabularies into review packets or public downloads. The artifact reader accepts bounded uncompressed members, validates NPY headers before allocation and loads numeric arrays with `allow_pickle=False`.

The shared vectorizers fit only training families; per-topic classifiers use only known labels. Calibration, threshold selection and final evaluation use separate family partitions. Automatic qualification requires calibrated real-data evaluation, independent support, both precision gates and explicit enablement. Authored fixtures test mechanics and cannot qualify production automation. Uncalibrated scores are labelled suggestions, not confidence percentages.

Extraction and suggestion stages have separate durable intents. `manage.py recover_extraction --limit 100` inventories both; `--apply` recovers under the original actor and current permissions. Each stage has finite attempts and leases. A missing/invalid model preserves rule/manual operation. Source flags and automatic tagging remain disabled by default until migration, setup and deployment-specific verification are complete.

## Automatic contributions and rollback

Automatic application has a third durable stage. The same recovery command inventories it. It never borrows system authority when the original user is unavailable. A model in automatic mode still needs `ENABLE_AUTOMATIC_TAGS=True`, explicit per-tag administrator enablement, safe current evidence and qualified frozen real-data evaluation. Suggestions, human decisions and automatic contributions are separate records. Rules without their own qualified evidence do not auto-apply.

`manage.py rollback_auto_tags MODEL_ID --actor USER_ID` inventories affected document IDs. Add `--apply` to remove that release's automatic contributions. Human confirmations and unrelated contributions remain. These commands require system-administrator authority and are not run on original records during implementation. A human rejection or uncertainty blocks reapplication across later content processing until a later explicit decision changes it.

## Review screens and private repository views

Open **Source text and topic review** from a document. Coverage uses actual semantic units, with unknown expected counts preserved. Read normalized text beside the original-version preview/download. The original file remains authoritative for exact layout and signatures. A legacy DONE status is not evidence of complete source coverage.

Authorized editors can confirm, reject, add or leave a topic unsure. The optional complete-review panel labels only its explicitly covered tags. The assignment list distinguishes human confirmations, automatic contributions and legacy assignments with unknown provenance. New reviews snapshot their topic label, actor name and office at recording time; existing unknown history is not reconstructed.

Office administrators can grant or withdraw training eligibility for their own editable records in the collapsed training-permission panel. System administrators manage candidates under **Local topic models**. Document eligibility and model activation are protected service actions, not ordinary Django-admin field edits. Changing eligibility/family invalidates affected models and does not reactivate an old model on regrant.

**Save these filters** keeps the currently applied repository filters in a private named view. Opening it revalidates the filters and current access, including office transfers, grants and collection. Invalid/stale views display an error rather than widening the search. A saved view stores filters only; it does not copy, move or reclassify files. Ordinary explicit forms and authorized downloads remain available without JavaScript.

## Opt-in processing and recovery

After additive migrations on a disposable/approved database, `ENABLE_SOURCE_UNIT_EXTRACTION=True` selects the unit pipeline for uploads, filing and retries. It persists actor-bound intent in the upload transaction and enqueues after commit. Without a worker, local processing occurs after commit with a bounded number of invocations and at most one OCR unit per web submission; remaining work is visible/recoverable. Production scanning needs the existing q2 worker rather than long web requests. No original database was migrated during implementation.

Runs pin numeric processing policy, language, backend and a nonsecret provider endpoint identifier. Changed settings/permissions/generations and stale worker leases cannot publish old results. File bytes are hashed when read; an unknown immutable legacy checksum remains unknown on the file version while the measured run hash records actual evidence. Previously usable same-version text survives failed attempts. Physical PDF/image-page metrics remain separate from semantic Word/sheet units.

```powershell
# Select the intended isolated/approved Django settings first.
.\.venv\Scripts\python.exe manage.py recover_extraction --limit 100
.\.venv\Scripts\python.exe manage.py recover_extraction --limit 100 --apply
```

Dry run is the default. Apply requeues existing eligible intent with its original actor, never a replacement system identity. Three expired-lease recoveries or three unit retry rounds require explicit attention. q2 delivery retry900seconds exceeds timeout600seconds; delivery attempts are finite. A quota wait remains pending until a due recovery pass. No exactly-once provider billing guarantee is made.

Recovery queues intent; it does not run a worker. With background jobs disabled, an operator must explicitly resume eligible processing or use the existing local driver. Run `manage.py qcluster` for the existing django-q2 worker in a deployment that supports persistent workers. A serverless web request is not a replacement worker.

Turning off the source-unit flag preserves pending intent, stops its processing/recovery and retains coverage history. It does not undo bytes already dispatched, remove human metadata or claim unprocessed content was read. The current legacy path remains separately available while rollout verification continues.

## Lifecycle, reconciliation and resource bounds

Metadata/source/eligibility changes and explicit review changes disable affected model releases and clear the local bundle cache. Approved vocabulary changes invalidate releases; private unapproved ad-hoc tags do not. Immutable vocabulary snapshots preserve what was reviewed. A label can survive an unrelated vocabulary addition only when its recorded topic definition is unchanged; a newly introduced topic remains unknown. Restoring a document or its training permission does not reactivate an old release.

Deleting a repository record hides its evidence and chunks immediately. The existing 30-day recovery flow remains. Permanent deletion also clears any private snapshot payload and fitted artifacts containing that source, preserving noncontent identifiers/digests for audit. Repository cleanup deduplicates storage names and preserves shared objects and original Tracking attachments.

`manage.py reconcile_processing --limit 100 --after-id 0 --collection OVPA` inventories a finite batch without OCR or new inference. Add `--apply` to rebuild stale current-source indexes and invalidate inconsistent training snapshots. Continue from the reported `after_id`; choose EXTERNAL explicitly when needed. This command never substitutes an administrator for the original processing actor. Source coverage, processing state and search-index freshness are displayed separately.

Revision hashing streams every current unit rather than creating one combined text blob. A model input is bounded at one million characters with no prefix substitution. Snapshots allow at most 2,000 examples, 10,000 source files and 50 million text characters. File existence checks happen outside document locks; the file manifest is rechecked before snapshot publication. Missing storage/files reject preparation.

The feature recipe streams word/bigram and character n-grams. Each branch permits at most 100,000 raw distinct terms and two million sparse entries before allocation grows further; final vocabularies retain at most 20,000 word and 30,000 character features per zone. Over-budget inputs abstain or require action, rather than silently training on a prefix. Prediction evidence inspects a bounded set of actual nonzero contributions in the current input. It does not scan every model term or retain another user's feature vector in a shared cache.

PDF vector artwork/annotations trigger visual/OCR review instead of allowing a native header to imply complete reading. Word objects/charts/diagrams and spreadsheet macros/linked content remain explicitly incomplete when unsupported. Macros and external embedded resources are never executed or fetched. OCR completion is not signature verification or a claim of linguistic accuracy.

## Enabling and operating locally

1. Back up the database and private file storage. Review and apply the additive migrations to a disposable database first, then to the intended database only when authorized. Existing file paths and original bytes stay unchanged.
2. Install base dependencies and the optional `requirements-ml.txt` profile using a compatible Python. The verified local runtime is Windows Python 3.13; Python 3.12 Linux wheels are available. Downloaded wheels do not establish a Linux runtime test. Artifacts must be retrained after runtime changes.
3. Configure existing durable private storage, SMTP and a persistent q2 worker for production. For OCR.space Free, configure the existing API key privately, select the OCR backend, and honor each document's external-provider consent. No new service/key is required for local topic inference or previews.
4. Enable source units and source-chunk search separately after checking fixtures. Review source coverage before interpreting proposals. Optional `ENABLE_APPROVED_SEARCH_ALIASES=True` uses only explicitly approved global phrases; system administrators manage these under Approved topic vocabulary. It does not expose private ad-hoc terms.
5. Obtain explicit training eligibility and reviewed topic decisions, then run prepare, train and evaluate commands above. Activate shadow first. Enable `ENABLE_TAG_ML=True` and `SUGGESTION_ENGINE=ai` for scoped inference. Existing records have an explicit Generate current topic proposals action that reuses published text and does not assign tags.
6. Keep `ENABLE_AUTOMATIC_TAGS=False` until qualified real-data evaluation and per-topic approval exist. A fixture-trained model is always ineligible. Use release rollback and contribution rollback separately when appropriate; human decisions remain.

For live QA, `scripts/check_document_intelligence.cjs` accepts `--base-url`, `--fixture` and `--out`. It requires a loopback-only disposable server and a private fixture JSON containing fresh role credentials, collection-specific document IDs and a synthetic topic/model ID. Do not pass production credentials or commit the fixture. It drives real login and CSRF forms; it does not mock HTML or backend responses. Its report explicitly distinguishes CSS 200% zoom from native browser zoom.

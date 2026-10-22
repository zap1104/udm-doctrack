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

## Opt-in processing and recovery

After additive migrations on a disposable/approved database, `ENABLE_SOURCE_UNIT_EXTRACTION=True` selects the unit pipeline for uploads, filing and retries. It persists actor-bound intent in the upload transaction and enqueues after commit. Without a worker, local processing occurs after commit with a bounded number of invocations and at most one OCR unit per web submission; remaining work is visible/recoverable. Production scanning needs the existing q2 worker rather than long web requests. No original database was migrated during implementation.

Runs pin numeric processing policy, language, backend and a nonsecret provider endpoint identifier. Changed settings/permissions/generations and stale worker leases cannot publish old results. File bytes are hashed when read; an unknown immutable legacy checksum remains unknown on the file version while the measured run hash records actual evidence. Previously usable same-version text survives failed attempts. Physical PDF/image-page metrics remain separate from semantic Word/sheet units.

```powershell
# Select the intended isolated/approved Django settings first.
.\.venv\Scripts\python.exe manage.py recover_extraction --limit 100
.\.venv\Scripts\python.exe manage.py recover_extraction --limit 100 --apply
```

Dry run is the default. Apply requeues existing eligible intent with its original actor, never a replacement system identity. Three expired-lease recoveries or three unit retry rounds require explicit attention. q2 delivery retry900seconds exceeds timeout600seconds; delivery attempts are finite. A quota wait remains pending until a due recovery pass. No exactly-once provider billing guarantee is made.

Turning off the source-unit flag preserves pending intent, stops its processing/recovery and retains coverage history. It does not undo bytes already dispatched, remove human metadata or claim unprocessed content was read. The current legacy path remains separately available while rollout verification continues.

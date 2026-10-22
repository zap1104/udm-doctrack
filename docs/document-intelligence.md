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

OCR.space is configured to English in this adapter; Filipino/Taglish quality remains unmeasured. The adapter is not yet wired into the default runtime. Live provider, real-data tagging/search quality, browser and deployment checks remain unverified.

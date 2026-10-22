# Local file-preview dependencies

Pinned on 2026-10-22, served from DocTrack's static storage. No document is sent to a public viewer.

| Package | Version | License | Upstream |
| --- | --- | --- | --- |
| pdfjs-dist | 6.4.299 | Apache-2.0 | https://mozilla.github.io/pdf.js/ |
| docx-preview | 0.4.1 | Apache-2.0 | https://github.com/VolodymyrBaydalka/docxjs |
| jszip | 3.10.2 | MIT or GPL-3.0 | https://stuk.github.io/jszip/ |

Licenses are included in each package directory. `manifest.json` records the npm tarball SHA-512 integrity and each retained asset's SHA-256. Tarball integrity was checked before extracting the browser builds, CSS icons and PDF font/CMap resources. Source maps, tests and package executables are excluded. Dangling JavaScript source-map comments are removed so Django can collect the assets with its production manifest storage. Asset hashes describe the resulting distributed files.

PDF rendering uses only PDF.js page rendering and its selectable text layer. It does not instantiate PDF scripting, interactive forms, annotations or actions. XFA and dynamic evaluation are disabled. DOCX is a sanitized, approximate preview rendered into a scriptless, same-origin sandbox. External relationships, altChunks, embedded HTML/SVG, active objects and macros are removed from the preview copy; original files remain unchanged.

Update packages deliberately, recheck the manifest and licenses, and rerun hostile-file, resource-limit and live-browser tests. Version numbers alone are not a security guarantee.

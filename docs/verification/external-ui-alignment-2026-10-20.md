# External UI alignment verification

Date: 20 October 2026. Local branch: `codex/external-ui-alignment`, based on `bdae00dbaa5bd83cf713e6c0806bdda5744e87a7`. This report describes the uncommitted working tree. Nothing was pushed or deployed.

## Delivered

- External Tracking creation and routing use the internal form's sectioned cards, main form/guidance columns, step indicator, shared document-type picker, required markers, and accessible field errors.
- External Repository upload uses the internal upload layout, file box, extraction controls, review step indicator, and guidance. Its external-office picker remains required.
- Both multiple-selection controls use the existing searchable checkbox enhancer and retain native controls without JavaScript.
- External tabs indicate the current section. Search, additional filters, pagination, document rows, report cards, office directory, and empty states use the existing presentation patterns.
- Tracking details use the internal context/actions layout, attachment chips, timeline, and expandable recording/correction evidence. Original paper references now have a plain-language example on creation.
- Existing shared repository metadata review, details, deletion review, and editing screens remain shared.

## Preserved workflow and separation

- External Tracking and External Repository retain their own URLs, records, collection filtering, numbering, reports, and exports.
- Managing administrators manually record external receipt and work events. An external office does not acquire an account or confirm actions itself.
- Internal recipients keep their existing permission checks and server-timestamped confirmations.
- Single-recipient validation, current custody, actual versus recorded dates, identity snapshots, revision checks, CSRF, correction history, signed downloads, and filing rules remain intact.
- No models, migrations, status transitions, timers, grants, or numbering services were changed. The only query change annotates distinct file totals for External repository rows, avoiding one extra database query for every displayed document.
- Earlier DocTrack branding edits were preserved.

## Evidence

| Check | Result |
|---|---|
| Full PostgreSQL Django suite | 7,574 passed, 4 skipped, 2 existing Django deprecation warnings; 1,115.53 seconds |
| Final External workflow, view, repository, notification and UI regressions | 115 passed, 2 existing warnings |
| Background-enabled processing/security/extraction/deletion regressions | 46 passed, 2 existing warnings |
| JavaScript suite | 172 passed |
| Template checker | 109 templates, no problems |
| Ruff | All checks passed |
| Django system check | No issues |
| Migration drift | No changes detected |
| Final live browser layout checks | 132 combinations: 11 pages, light/dark, widths 320, 390, 768, 900, 901, 1366; no document overflow, duplicate main-content IDs, or JavaScript errors |
| Independent source review | Two findings repaired; final review found no remaining concrete issues |

The full Django suite ran before the last two widget-only attribute changes. The final 115-test External run, final 132-case browser run, template/lint checks, and final four focused UI/query regressions include those attributes. Historical full-suite results are not presented as a second final-state run.

The new picker and accessible-error regressions failed against the original templates before their fixes. The file-total regression failed before the distinct count annotation was added. Final four focused regressions all passed.

The four full-suite skips were two background-only cases, one role/filter fixture without routed rows, and the optional existing systemwide Chromium harness. The separate background run covered the background-only cases. The real External Chromium journeys described below ran independently of that optional harness.

### Real browser journeys

Used installed Chromium, a separate local Django server, real CSRF-protected login, a dedicated disposable PostgreSQL database, temporary local files, and fresh local-only sample credentials. The working database and public-demo database were not changed.

- Create an External draft and reach its review/send page using the actual searchable document-type checkboxes.
- Upload harmless text evidence with an external office selected; reach the shared metadata review page with OCR disabled.
- Change status filters through HTMX without replacing the document; preserve the advanced-filter panel and filtered pagination.
- Manually record external receipt to reach Received, then manually record external work to reach In Process.
- Open recording details by keyboard and render the detail page for print.
- Verify internal marker records are absent from External results, and filed External documents are absent from the main repository.
- Log in as a viewer; verify read-only details and HTTP 403 responses for creation, upload, and work actions.
- Inspect desktop and phone screenshots, including long attachment names, form guidance, and the shared picker presentation.

Local evidence, excluded from Git, is under `.vercel/external-uiqa/`: `browser-report.json`, `browser.log`, screenshots, and `external-detail-print.pdf`. Test logs are `.vercel/external-ui-full-tests.log`, `.vercel/external-ui-focused-final.log`, `.vercel/external-ui-background-tests.log`, and `.vercel/external-ui-js-tests.log`. Credentials are in a private ignored file and are not included here.

## Limits

Browser widths emulate screen sizes; physical phones and assistive technologies were not used. Live cloud OCR, object storage, and email providers were not contacted. Tests cannot prove the absence of every possible defect. The temporary public Cloudflare processes were already absent during the final check; this UI task did not create a new public deployment or URL.

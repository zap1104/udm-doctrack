# Repository redesign verification — 30 September 2026

Implemented an office-folder browser with per-office filed/pending totals, type shortcuts, compact file rows, labelled filters, retention review notices, and a separately paginated pending-upload list. The folder chooser collapses on phones and after entering a folder, and remains keyboard accessible without JavaScript.

## Access boundaries

All document lists, office/type folders, tag choices and tag usage totals derive from the user's visible documents. Pending rows and totals derive from tracking visibility. Inactive accounts receive empty document/tracking querysets. Requests for folders with no visible records show no folder identity or record contents. Restricted-document explicit grants remain honored. Review links use the existing tracking detail/approval flow; viewers and ordinary users receive a view link instead of an approval link. Existing download and approval permissions remain server-enforced.

## Validation

- 61 targeted tests passed: repository access/counts/search/pagination, existing filters, dashboard count reconciliation, approval permissions, reports navigation, and folder-column compatibility.
- Ruff and all 77 template checks passed. Two existing Django deprecation warnings remain.
- Rendered filed and pending views in the in-app browser at 320, 390, 768, 1024 and 1440 CSS pixels. No page horizontal overflow; form controls stayed within the viewport.
- Inspected desktop and mobile views in light and dark themes. Fixed an existing dark-mode mobile navigation icon that had insufficient visibility.
- Browser interactions verified: office selection, pending list, keyboard folder expansion, mobile navigation opening/closing, search with no results, and clearing filters.
- Approved one synthetic record in an isolated preview database: office totals changed from 31 filed / 3 pending to 32 filed / 2 pending. No real records were changed.
- UI verification used the available in-app browser. Native Safari, Firefox, and physical phones were not tested.

Screenshots use synthetic demo data and are saved under:

C:/Users/USER/.codex/visualizations/2026/09/28/01a0e731-ab88-7a12-b339-04f3266711b3/repository-ui/

No schema migration is needed for this repository redesign. Deployment must collect static files to include the new repository script. No commit or push was requested.

# Independent count and filter verification

Baseline: `bb84ac863937a27dc871e115db694b2e84a78f73`.
Branch: `codex/systemwide-bug-audit`.
All request tests used disposable PostgreSQL and temporary storage.

## Population contracts

| Surface | Entity and collection | Scope and counting rules |
| --- | --- | --- |
| OVPA Repository | Active `Document`, `OVPA` | Start with user visibility, then office ownership and metadata/search filters. Count documents once, even with several types, files or tags. |
| External Repository | Active `Document`, `EXTERNAL` | Same visibility boundary, separate collection. External additions must not change OVPA sets. |
| Internal Tracking / Tracking Search | `TrackingRecord`, separate from External tracking models | Visible records, endpoint filters and current-batch/office perspective. Draft and pending-filing labels are preserved. Export sets must match the filtered list across all pages. |
| Dashboard direction groups | Working internal `TrackingRecord` | Current movement relative to the selected internal office. Each direction's three stage subsets partition that direction. A partial parallel receipt can be received locally while still pending globally. |
| Type folders | Distinct documents belonging to each associated type | Overlapping facets; their sum can exceed the document total. |
| Repository type report | One primary type per document | Partition; its sum must equal the report's population total. |
| Ranked search | Visible matching documents above the requested threshold | Explicit evaluation/result caps may apply. Exhaustive repository filtering is a different contract. |

All-time totals and time-window charts are not interchangeable. Existing dedicated
report/date/calendar tests provide the period-specific checks; this additional
matrix does not claim exhaustive date-combination coverage. External tracking
workflow/filter cases in the existing suite are separate from the internal
Incoming/Outgoing matrix described below.

## Repairs to diagnostic scripts

`scripts/audit_counts.py` now excludes External records from OVPA expected sets,
uses the intended OVPA tag-usage population and tolerates chart rows without a
drilldown link. `scripts/audit_filters.py` compares displayed draft identifiers,
preserves intentionally searchable retired-office records, probes active tags,
and follows every page when the `all` page size is capped at 500.

The initial diagnostic regression failed in four cases. Independent review added
three failing cases for inactive tags and lists/pending lists with 501 records.
These were test-oracle defects; the mixed-collection expected-ID matrix did not
identify an additional application-filter defect requiring a product change.

## Accepted expanded result

`tests/test_systemwide_audit_oracles.py` and
`tests/test_systemwide_filter_matrix.py`: **137 passed**, two existing Django
deprecation warnings, **237.39 seconds**. Evidence: `filters-expanded-final.txt`.
This replaces the earlier 113-case result in `filters-final.txt` as the expanded
matrix's final focused run. The same cases are also part of the complete
7,572-case PostgreSQL collection on `cd4316a`; these overlapping totals must not
be added together.

Expected sets in the new matrix come from explicit fixture facts, without calling
production visibility, office-scope or filter functions to generate the answer.
It checks system administrators, office administrators, staff and viewers;
matching OVPA/External documents; malformed/repeated/foreign-office parameters;
exact document/tracking IDs; dashboard drilldowns and stage partitions; draft
pagination and CSV; and capped search versus exhaustive repository results.

The final expansion adds **24 populated direction/stage cases**:
four roles × Incoming/Outgoing × Pending Receipt/Received/In Process. Each case
queries both `/tracking/` and `/search/?mode=tracking` with the MED internal-office
perspective and its direction and stage. The fixture creates six known records,
one for each direction/stage pair, using endorsement, receipt and work-update
facts. Each response must contain exactly the expected single record ID and
display total one. Matching counts alone cannot pass with a substituted record.
These 24 cases strengthen a separate older office-admin Incoming test whose empty
fixture still causes its intentional skip; they do not relabel that skip as a pass.

Invalid parameters retain each endpoint's established behavior. Legacy lists
keep valid filters and show warnings. Search rejects invalid form fields.
External endpoints retain their own explicit error responses. Invalid inputs
never grant access outside the underlying visibility population. An endpoint
may intentionally ignore a bad filter within its permitted population; that is
different from silently expanding authorization.

## Limits

These 137 cases supplement the existing suite. They do not prove every possible
filter combination or every route/role. The live browser's comparison with a
normal full-page server response checks HTMX parity for displayed IDs/headings,
not independent expected data. The expected-ID matrix supplies that separate
data check. The coverage inventory and execution ledger retain unproved scenarios.

The final Reports hint change, verified separately by 56 focused report tests,
clarifies skipped/cancelled extraction wording. It does not change the queries,
filter behavior or populations verified here. Provider services remained
simulated; physical devices and screen-reader behavior were not verified by this
matrix.

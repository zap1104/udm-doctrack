# Turnaround graph verification

Verified locally on 2 October 2026. This covers the dashboard's daily graph,
Tracking Reports' yearly graph, their filters, tables, summaries, and interaction
code. Calculation definitions are in [TURNAROUND_TIME.md](TURNAROUND_TIME.md).

## Corrections made

- Equal values and values landing on the same rendered pixel use concentric
  rings. In Process stays pink and Total lifetime stays green, including the
  overlapping October values. Their averages are unchanged.
- Popup markers and SVG points share the exact rendered coordinates. Numeric
  coordinates are not translated into locale-specific decimal separators.
- A first click opens the selected date. Mouse movement, taps, keyboard arrows,
  Home, End, Escape, and outside clicks do not leave multiple popups visible.
  Popup width follows the available plot width.
- Empty periods produce an empty state without invented points or division by
  zero. Zero observations remain distinct from missing observations.
- Report year choices use valid local-date years and exclude future years.
  Their database query count remains at its original tested limit.
- Invalid working-week configuration and lunch covering the entire office day
  produce configuration errors rather than unusable calculations.
- Status markers meet the existing contrast and color-separation checks on
  khaki surfaces. Dark office badges use the actual dark surface. Hover arrows
  meet text contrast requirements. Print retains white paper surfaces.

## Automated coverage

| Area | Cases / evidence |
| --- | --- |
| Graph geometry | 4,096 combinations of absent, zero, fractional, equal, crossing, and large values across eight period lengths |
| Numeric localization | 32 chart renders across English, German, French, and Arabic |
| Independent arithmetic | 378 intervals checked against a separate quarter-hour office-time reference |
| Calendar | All 48 months across four years, including leap years; current-month and future-period boundaries |
| Counts and access | Dashboard, summaries, tables, and reports agree for multiple offices and user roles; forged office selection does not broaden visibility |
| Large data / queries | 1,000 documents and 5,000 handovers, including duplicate joined rows; receipt counts, distinct document counts, means, and query budgets checked |
| Interaction | 124 Node tests, including 10,000 deterministic mixed pointer, touch, click, and keyboard events |

The arithmetic tests check the configured schedule, lunch, weekends, and
zero-duration intervals. Existing business-time tests cover additional schedule
and holiday behavior. Monthly means are weighted by individual observations;
the number of daily dots is not the denominator.

## Execution results

The initial full suite completed with **6,106 passed, 22 failed, and 1 skipped**.
Application coverage was **75.33%**, above CI's 60% requirement. The failures
exposed outdated theme assertions, actual contrast/surface defects, and increased
report query counts. These were corrected. A subsequent explicit rerun of every
original failure plus the complete theme modules finished with **170 passed**.

The final calculation, rendering, filter, and report checks finished with:

- **4,625 passed** in the focused regression run.
- **4,684 passed** with `DJANGO_DEBUG=False`, including dashboard/report agreement.
- **124 passed** in the final Node interaction run.
- Lint passed, all **84 templates** parsed, Django system and deployment checks
  passed, and migration checks found no missing migrations.
- The live read-only count audit compared **5,639 figures across six scopes**
  with no mismatches. The controlled turnaround audit passed **44 checks** and
  rolled back its temporary records.

These runs overlap; their counts must not be added together. This initial
verification round explicitly rechecked the 22 original failures after the
fixes. The subsequent release also runs the complete suite before updating
main. One full-suite skip is an existing incoming-filter fixture
with no routed records, not a skipped graph test. Two existing Django 6
deprecation warnings remain in a constraint declaration and migration.

## Browser verification

The local in-app browser passed **24 responsive checks**:

- October's sparse daily data and September's 30-day view at 320, 390, 768, and
  1,440 pixels, in light and dark themes.
- The yearly report at those four widths in both themes.

Checks included page overflow, popup bounds, and one visible popup at a time.
Actual keyboard Home, End, and Escape behavior was checked. All four stage
filters were verified against the displayed lines, cards, and turnaround table:
one stage gives one line, one card, and two table columns; all stages give three
lines, three cards, and four dashboard table columns. The report year selector
was exercised with 2025, and an empty historical month remained an honest empty
state. No JavaScript console errors were observed during these checks.

Touch handlers are covered by automated event tests; physical phones, Safari,
and Firefox were not independently tested. Local Python is 3.13 and Node is 24;
GitHub CI uses Python 3.12 and now explicitly installs Node 22 for the interaction
tests. The corresponding GitHub pull request records remote CI results. No finite
test suite guarantees every possible browser, dataset, or configuration.

## Reproduce

Run from the repository root in PowerShell using the project environment:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_turnaround_graph_stress.py tests/test_turnaround_graph_calendar.py tests/test_turnaround_figures.py tests/test_dashboard_turnaround_filters.py tests/test_separate_reports.py tests/test_dashboard_reports_agreement.py -q
node --test tests/js/test_turnaround_chart.cjs
.\.venv\Scripts\python.exe -m pytest -q --cov=apps --cov-report=term-missing --cov-fail-under=60
.\scripts\start.ps1 -PreviewOnly -Port 8000
```

Pytest uses PostgreSQL test databases. The production-mode verification run used
an isolated test database. The university's document records were not changed.
Browser verification only signed into the existing local demo and read reports.
These local checks were performed before publishing the changes.

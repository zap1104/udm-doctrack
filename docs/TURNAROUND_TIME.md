# Turnaround time

The dashboard and Tracking Reports use the same permission-scoped records and
calculation service in `apps/core/analytics.py`. Office selection narrows that
scope; it does not grant access to other documents.

| Figure | What is measured | Count used for the average | Month assigned by |
| --- | --- | --- | --- |
| Pending Receipt | Sent to confirmed | Valid confirmed handovers, including multiple handovers of one document | Confirmation time |
| In Process | First receipt to completion, including transfers and waits across offices | Valid completed documents with a first receipt | Completion time |
| Total lifetime | Creation to completion | Valid completed documents | Completion time |

The Pending Receipt turnaround figure describes completed receipt waits. It is
different from the live count of documents currently in Pending Receipt.
Each card states its denominator. Receipt also states how many unique documents
those handovers belong to.

Working time excludes weekends, the configured lunch break, and active holidays
entered under Administration. By default, a working day is eight office hours
(8 AM to 5 PM, excluding lunch from noon to 1 PM). The labels and graph scale
follow the configured schedule. An elapsed day is always 24 hours and includes
all time the requester waited. Dashboard and Reports now display both clocks in
total hours and minutes, so readers do not have to compare eight-hour working
days with 24-hour elapsed days. The graph's y-axis also displays office hours.

The visible guide shows the configured schedule and an ordinary open-day
example. For the default schedule, 8 AM to 5 PM is nine elapsed hours minus
one lunch hour, leaving eight office hours. Eight hours is not a fixed deduction:
Monday 4 PM to Tuesday 9 AM is 17 elapsed hours minus 15 closed hours, leaving
two office hours (assuming neither day is a configured holiday).

Each stage's **View calculation** popup shows its actual average total wait minus average excluded time,
and total counted office time divided by the observation count. These values
use the same valid samples as the graph and cards. They measure elapsed time
inside the schedule, not how long staff actively worked on a document.

System administrators can change working days, opening/closing times and the
daily break under **Administration → Office schedule**. They can add, edit or
retire full-day holidays under **Administration → Holidays**. Breaks must be
inside a positive office window and cannot consume the whole day. Turning the
break off counts the full office window. Every saved schedule change is audited.
Until a schedule is saved, the deployment's configured defaults apply.
Changes affect both historical and current turnaround figures; timestamps and
total wait remain unchanged. A lazy snapshot keeps a request's cards, graphs,
exports and time labels on one schedule, with one schedule query per calculation.

Graph popups show total wait minus excluded time for that date. Small markers
use a yellow circle for Pending Receipt, a pink diamond for In Process and a
green cross for Total lifetime, matching the legend. Their transparent shapes
share a fixed ten-pixel footprint at every screen width and do not enlarge on
hover. Their centres and underlying values are not moved or rounded to zero.
When values overlap, the popup and table show each stage's exact displayed time.

The average is the sum of valid working durations divided by their count.
Monthly summaries use individual handovers or documents, not an unweighted
average of daily dots. For example, one document taking one hour on Monday and
three taking three hours on Tuesday have a monthly average of 2.5 hours, not
two hours. Daily and yearly chart points retain their precision; short positive
waits are not rounded to zero working days before plotting. Display labels
show total hours and whole minutes, or “under a minute.” Labels round down to
whole minutes; calculations retain exact seconds. The approximately-equal sign
in the displayed formulas allows for that rounding. Existing CSV duration
labels retain working-day units.

Zero working minutes is a valid measurement. An instant confirmation and an
interval entirely outside office hours both contribute zero to the average.
Only the latter is counted as an outside-hours interval. Both are excluded
from Fastest so an off-hours or instant observation does not mask the shortest
positive working-time observation. Slowest includes all valid observations.
No observations produce a dash and no chart point. Missing required or reversed
timestamps and future-dated confirmations or completions do not contribute.

Completed documents waiting for repository upload count as completed work.
Administrator approval and upload delays are excluded. On-time completion
uses only documents completed during the selected month with a valid lifetime
and a deadline. Finishing exactly at the deadline is on time. The percentage
is rounded to a whole number, with its numerator and denominator displayed.

“Waiting now” is a separate live count across all months. It includes only
unconfirmed handovers in each unfinished document's current batch, with a send
time no later than now. A document may already be Received or In Process while
another recipient has not confirmed, so the unique document count can exceed
the Pending Receipt stage count. The UI splits those two populations. These
open handovers enter the receipt average only after confirmation.

The Tracking CSV shows receipt time summed across a document's valid confirmed
handovers. This is a per-document total, not the per-handover average plotted
on the dashboard. Its processing and lifetime durations use the same timestamp
validity rules as the summaries.

To verify live numbers and known intervals without leaving database changes:

```powershell
.\.venv\Scripts\python.exe scripts\audit_counts.py --quick
.\.venv\Scripts\python.exe scripts\audit_turnaround.py
```

The first reconciles displayed counts, denominators, unique documents, zero
observations, and means across office and user scopes. The second verifies
known intervals inside a rolled-back transaction. Neither audit replaces the
full pytest suite required before pushing.

Verified on 3 October 2026: 5,639 live comparisons across six office/user
scopes and 44 known-interval checks passed with no mismatches. The rolled-back
workflow check passed all 21 steps; 244 pages were checked as four roles.
No verification records were retained.

Dashboard and Reports were checked at 320, 390, 768 and 1440 pixels in both
themes, with no page overflow. Markers stayed ten pixels wide and aligned
with their measured coordinates (less than 0.1 pixel of rounding difference).
All four stage selections, both available report years, sparse observations,
and the calculation popup were checked. Repeated clicks now close a selected
date even when the mouse moves slightly within it. Printed charts retain the
markers and omit interactive popups; reduced-motion settings skip the reveal.

The regression runs covered all 6,310 Python cases: 6,309 passed and one
fixture-specific case was skipped, with 77.59% combined code coverage against
CI's 60% requirement. These include 4,263 graph stress cases for
coincident, near-zero, absent and extreme measurements, calendar lengths and
locale-safe rendering. All 125 JavaScript chart checks passed, including
10,000 mixed interactions. Ruff, all 87 templates, Django's system and deployment
checks, and migration consistency passed. The production-settings test runs
use CI's demo-seeding flag only inside the isolated test process.

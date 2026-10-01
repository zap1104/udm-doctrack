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
all time the requester waited.

The average is the sum of valid working durations divided by their count.
Monthly summaries use individual handovers or documents, not an unweighted
average of daily dots. For example, one document taking one hour on Monday and
three taking three hours on Tuesday have a monthly average of 2.5 hours, not
two hours. Daily and yearly chart points retain their precision; short positive
waits are not rounded to zero working days before plotting. Display labels
show up to two nonzero units and whole minutes, or “under a minute.”

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

Verified on 1 October 2026: 5,631 live comparisons across six office/user
scopes and 44 known-interval checks passed with no mismatches. No verification
records were retained. Dashboard and Reports were checked at 390, 768, and
1440 pixels in both themes, with no page overflow. All four stage selections,
the empty current month, year selection, annual table agreement, and descending
tooltip order were also checked. Responsive evidence is saved under
`docs/verification/turnaround/`. Ruff, all 84 templates, and Django's system
check passed. Pytest was not run in this turn; it remains required before push.

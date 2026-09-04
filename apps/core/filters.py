"""One place that turns a query string into filters, for every page that has them.

There were four implementations of "filter by office", two of them called
`office`, taking the same-looking value and meaning different things:

    /tracking/?office=25    ->  6 records
    /tracking/?offices=25   ->  4 records

Same office, same page, parameter names differing by one letter. A third read a
*code* rather than a primary key and fell through to no filter when it did not
match one, and a fourth applied the filter to anybody while the others reserved
it for administrators. That is why fixing one pairing kept leaving the others:
each page was deciding for itself what a filter meant.

The two questions are both legitimate, and this module names them so they cannot
be confused again:

    as_office   "show me this page as that office" — the queue answers for it,
                and a queue that has no office to answer for narrows by the
                originating-or-current pairing instead. Always a primary key,
                always gated on `is_office_admin`.
    raised_by   "documents that office started" — originating office only, a
                list, open to everybody. This is the checkbox row on Tracking.

The URL parameters keep their existing names — `office` and `offices` — because
bookmarks, the dashboard's links and the smart folders all carry them, and
renaming would leave four names in play rather than two. The names above are
what the code calls them, which is where the confusion actually lived.

Nothing here silently drops a filter. A value that is supplied and refused is
recorded in `invalid`, so the page can say so: a filter that fails open is worse
than one that errors, because the reader believes they are looking at one office
and are looking at the whole university.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.http import QueryDict

from apps.accounts.models import Office
from apps.tracking.models import ACTIVE_STATUSES, Status

#: Statuses a page listing live records may be filtered by. OVERDUE is here and
#: is not a status: it is a deadline condition, accepted as a status value
#: because that is the vocabulary the filter row speaks.
ACTIVE_STATUS_VALUES = {status.value for status in Status if status in ACTIVE_STATUSES}
TRACKING_STATUS_VALUES = ACTIVE_STATUS_VALUES | {"OVERDUE"}

#: Reports covers finished work too, so it accepts the whole enum. Stated here
#: beside the narrower list rather than in two forms, because the difference is
#: deliberate and was previously only discoverable by trying a link: a link from
#: Reports to Tracking carrying `status=COMPLETED` widened instead of narrowing.
REPORT_STATUS_VALUES = {value for value, _ in Status.choices} | {"OVERDUE"}


@dataclass(frozen=True)
class ResolvedFilters:
    """What the query string actually asked for, after validation."""

    as_office: Office | None = None
    raised_by: list[Office] = field(default_factory=list)
    status: str = ""
    scope: str = ""
    owner: str = ""
    query: str = ""
    #: Parameter names that were supplied and refused. Never silently dropped.
    invalid: list[str] = field(default_factory=list)

    @property
    def raised_by_ids(self) -> list[str]:
        """As strings, which is what a template compares a pill against."""
        return [str(office.pk) for office in self.raised_by]


def _office_by_pk_or_code(raw: str) -> Office | None:
    """A primary key, or a code for the links that have always emitted one.

    The Repository's smart folders have been emitting codes since they were
    written and people have bookmarked them, so a code still resolves. It reads
    `Office.active` rather than `Office.objects`, which the Repository did not:
    an archived office went on filtering as though nothing had changed.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return Office.active.filter(pk=raw).first()
    return Office.active.filter(code__iexact=raw).first()


def resolve(request, *, statuses=TRACKING_STATUS_VALUES, allow_office=True) -> ResolvedFilters:
    """Read every filter this request carries, once.

    `allow_office` is for a page with no office control at all. `statuses` is
    the vocabulary that page speaks — see the two sets above.
    """
    params = request.GET
    user = request.user
    invalid: list[str] = []

    # The gate lives in tracking.services, next to the queues it governs, and is
    # imported here rather than reimplemented — two answers to "may this person
    # name another office" is exactly the shape of bug this module exists to end.
    from apps.tracking.services import scope_office

    as_office = None
    raw_office = (params.get("office") or "").strip()
    if raw_office and allow_office:
        as_office = scope_office(user, raw_office)
        if as_office is None:
            # Either the value names nothing, or this account may not pick. Both
            # are worth saying; the page decides how loudly.
            as_office = _office_by_pk_or_code(raw_office) if _may_pick(user) else None
            if as_office is None:
                invalid.append("office")
    elif raw_office and not allow_office:
        invalid.append("office")

    raised_by = []
    for raw in params.getlist("offices"):
        office = _office_by_pk_or_code(raw)
        if office is None:
            invalid.append("offices")
        else:
            raised_by.append(office)

    status = (params.get("status") or "").strip()
    if status and status not in statuses:
        invalid.append("status")
        status = ""

    return ResolvedFilters(
        as_office=as_office,
        raised_by=raised_by,
        status=status,
        scope=(params.get("scope") or "").strip(),
        owner=(params.get("owner") or "").strip(),
        query=(params.get("q") or "").strip(),
        invalid=sorted(set(invalid)),
    )


def _may_pick(user) -> bool:
    return bool(getattr(user, "is_office_admin", False))


def link(base: str, request=None, **overrides) -> str:
    """A link that changes some filters and keeps the rest.

    The Python-side twin of the `filter_url` template tag, for links built in a
    view — the dashboard's ring slices and stat cards. Without it a slice link
    carries `?status=` alone and drops the office the number was counted for,
    which is D1: the count says one office and the page it opens says all of
    them.

    `page` is always dropped: page four of the old filter is not page four of
    the new one. A value of None removes that parameter.
    """
    params = request.GET.copy() if request is not None else QueryDict(mutable=True)
    params.pop("page", None)
    for key, value in overrides.items():
        if value is None or value == "":
            params.pop(key, None)
        else:
            params.setlist(key, [str(value)])
    query = params.urlencode()
    return f"{base}?{query}" if query else base


#: Filter pairs that cannot both hold, with the reason in the reader's terms.
#:
#: These are empty by construction rather than empty today. `route_record`
#: refuses to send an office its own document, so "addressed to my office" and
#: "created by me" cannot both be true; a draft has never been routed, so it has
#: no direction at all. The page says so instead of showing an empty table and
#: letting the reader conclude the filter is broken.
IMPOSSIBLE_PAIRS = [
    (
        {"scope": {"incoming", "received", "pending-receipt", "inbox"}, "owner": {"mine"}},
        "Documents addressed to your office were created by another office, so "
        "that queue and “Files created by me only” cannot both apply.",
    ),
    (
        {"scope": {"incoming", "outgoing", "received", "pending-receipt", "inbox", "sent"},
         "status": {Status.DRAFT.value}},
        "A draft has not been sent yet, so it has no sending or receiving "
        "office and cannot appear in a routing queue.",
    ),
]


def impossible_reason(resolved: ResolvedFilters) -> str:
    """Why this combination can never match, or "" if it can."""
    for conditions, reason in IMPOSSIBLE_PAIRS:
        if all(getattr(resolved, key, "") in values for key, values in conditions.items()):
            return reason
    return ""

"""One page-size control, shared by every page that lists more than a screenful.

Each list page had its own hard-coded page size — 20 on Tracking, 24 in the
Repository, 25 on Notifications, a bare `[:300]` on the audit log — and none of
them let the reader change it. A records officer working through a queue wants
long pages; somebody glancing at the same queue on a laptop wants short ones.

The size travels in the querystring (`?per_page=`) rather than in the session,
so a link to a page is a link to the page somebody was actually looking at, and
so the two Tracking surfaces (the workspace list and search) cannot drift into
disagreeing about what a URL means.

Anything unrecognised falls back to the caller's default rather than raising:
these values arrive from stale bookmarks and hand-edited URLs, and a page size
is never worth a 500.
"""

from __future__ import annotations

from django.core.paginator import Paginator

#: The sizes offered, in the order they are shown.
PAGE_SIZE_OPTIONS = (10, 25, 50, 100)

#: The token for "as many as there are" — a word rather than a number, so it
#: cannot be confused with a size and so `?per_page=100000` is simply not in the
#: list and falls back to the default.
ALL_TOKEN = "all"

#: What "All" actually means. Unbounded is not an option on a table that grows
#: for the life of the installation — the audit log alone will pass this — so
#: "All" is a very large page rather than an infinite one. When a list does
#: exceed it the pagination nav stays on screen, so the reader can still reach
#: the rest instead of silently being shown a truncated list.
ALL_CAP = 500

#: The size a page uses when nobody has asked for one. Deliberately a member of
#: PAGE_SIZE_OPTIONS: the control marks the current size as active by matching
#: its token, and a default outside the list would leave every option looking
#: unselected.
DEFAULT_PAGE_SIZE = 25

#: Querystring keys. Two, because the audit screen carries two independent
#: lists on one page and one `page` between them would move both at once.
PAGE_PARAM = "page"
SIZE_PARAM = "per_page"

#: What the control renders: (token, label) pairs.
PAGE_SIZE_CHOICES = tuple(
    [(str(size), str(size)) for size in PAGE_SIZE_OPTIONS] + [(ALL_TOKEN, "All")]
)

_TOKENS = {token for token, _ in PAGE_SIZE_CHOICES}


def resolve_page_size(request, default: int = DEFAULT_PAGE_SIZE, param: str = SIZE_PARAM):
    """Return `(rows, token)` for this request.

    `rows` is what a Paginator wants; `token` is what the control compares
    against to decide which option is lit, and it is a string in both cases so
    the template's `==` never has to reason about types.
    """
    raw = (request.GET.get(param) or "").strip().lower()
    if raw == ALL_TOKEN:
        return ALL_CAP, ALL_TOKEN
    if raw in _TOKENS:
        return int(raw), raw
    return default, str(default)


def page_size_context(token: str, *, page_param: str = PAGE_PARAM, size_param: str = SIZE_PARAM) -> dict:
    """The variables `partials/_pagination.html` reads.

    Separate from `paginate` because one screen has the control without having
    pages: repository search returns a relevance ranking, where the size is the
    cut-off rather than a page length, so it wants these variables and no
    `page_obj` beside them.
    """
    return {
        "page_size": token,
        "page_size_choices": PAGE_SIZE_CHOICES,
        "page_size_all": ALL_TOKEN,
        "page_size_cap": ALL_CAP,
        "page_param": page_param,
        "size_param": size_param,
    }


def paginate(request, object_list, default: int = DEFAULT_PAGE_SIZE, *,
             page_param: str = PAGE_PARAM, size_param: str = SIZE_PARAM) -> dict:
    """Page `object_list` at the size this request asked for.

    Returns the context the list templates already expect (`page_obj`) plus the
    control's own variables, so a view updates its context with one call and its
    template gets both from one `{% pager %}`.
    """
    rows, token = resolve_page_size(request, default, param=size_param)
    page = Paginator(object_list, rows).get_page(request.GET.get(page_param))
    context = {"page_obj": page}
    context.update(page_size_context(token, page_param=page_param, size_param=size_param))
    return context

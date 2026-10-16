"""Processing time excludes the document's recorded receipt-wait intervals."""

from .business_time import business_seconds_between


def processing_seconds(start, end, steps, holidays):
    """Return working and elapsed seconds between receipt and completion.

    Steps are (batch, sent_at, received_at) tuples. Any outstanding recipient
    keeps a batch pending, so parallel waits are merged before subtraction.
    A new batch supersedes unresolved waits in the preceding batch. Invalid
    confirmed intervals do not become invented open waits. Endorsements dated
    after completion cannot cap earlier waits or alter completed processing.
    """
    steps = list(steps)
    starts = {}
    for batch, sent, _received in steps:
        if sent is not None and sent <= end:
            starts[batch] = min(starts.get(batch, sent), sent)
    batches = sorted(starts)
    next_start = {batch: starts[later] for batch, later in zip(batches, batches[1:], strict=False)}
    pauses = []
    for batch, sent, received in steps:
        if sent is None or sent > end or received is not None and received < sent:
            continue
        stop = min(received or end, next_start.get(batch, end), end)
        begin = max(sent, start)
        if begin < stop:
            pauses.append((begin, stop))
    pauses.sort()
    merged = []
    for begin, stop in pauses:
        if merged and begin <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], stop))
        else:
            merged.append((begin, stop))
    working = elapsed = 0.0
    cursor = start
    for begin, stop in [*merged, (end, end)]:
        if cursor < begin:
            working += business_seconds_between(cursor, begin, holidays)
            elapsed += (begin - cursor).total_seconds()
        cursor = max(cursor, stop)
    return working, elapsed

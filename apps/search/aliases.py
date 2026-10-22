"""Bounded expansion from explicitly approved public vocabulary only."""

import re
from functools import reduce
from operator import or_

from django.conf import settings
from django.contrib.postgres.search import SearchQuery
from django.db.models import Q, Value
from django.db.models.functions import Lower, StrIndex

from apps.documents.models import Collection, TagAlias


def combined_query(variants):
    return reduce(
        or_, [SearchQuery(value, config=settings.SEARCH_CONFIG, search_type="websearch") for value in variants]
    )


def expand_query(user, query, *, collection, limit=8):
    if not getattr(user, "is_authenticated", False) or not user.is_active or collection not in Collection.values:
        return []
    raw = " ".join(str(query).lower().split())
    if not raw or len(raw) > 512 or ("-" in raw and " " not in raw and any(char.isdigit() for char in raw)):
        return []
    limit = max(0, min(8, int(limit)))
    if not limit:
        return []
    approved = (
        TagAlias.objects.filter(
            is_active=True, tag__is_active=True, tag__canonical__isnull=True, tag__globally_approved=True
        )
        .annotate(_phrase=StrIndex(Value(raw), Lower("phrase")), _canonical=StrIndex(Value(raw), Lower("tag__name")))
        .filter(Q(_phrase__gt=0) | Q(_canonical__gt=0))
        .order_by("pk")
    )
    result = []
    for row in approved.values("phrase", "tag__name")[:4096]:
        for phrase, replacement in ((row["phrase"], row["tag__name"]), (row["tag__name"], row["phrase"])):
            if len(phrase) < 2:
                continue
            pattern = r"(?<!\w)" + re.escape(phrase) + r"(?!\w)"
            if not re.search(pattern, raw, flags=re.IGNORECASE):
                continue
            variant = re.sub(pattern, lambda _, value=replacement: value, raw, flags=re.IGNORECASE)
            if variant != raw and len(variant) <= 512 and variant not in result:
                result.append(variant)
                if len(result) == limit:
                    return result
    return result

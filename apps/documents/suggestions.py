"""Metadata and tag recommendation.

The engine returns a `Suggestion` — it never writes to the database on its own.
The uploader reviews and edits everything on screen before saving; that review
is exactly what makes the archive trustworthy.

Two engines share one interface:

* `RuleBasedEngine`  — ships today. Regex + keyword rules that an administrator
  can edit from the Administration screen (no code change, no retraining).
* `AIEngine`         — the placeholder for later. Because both engines return
  the same `Suggestion` object, switching is a settings change
  (`SUGGESTION_ENGINE=ai`) and the UI stays identical.

Every suggestion, plus what the user actually kept, is stored in
`MetadataSuggestion`. That table is the labelled dataset for training the AI
engine later — see `manage.py export_training_data`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from django.conf import settings

from apps.core.models import DocumentType, MetadataFieldDefinition, Tag, TagRule
from apps.core.utils import normalise_text, parse_date_guess

ENGINE_VERSION = "1.0"

# Document keywords that map to a document type when no rule exists yet.
DEFAULT_TYPE_HINTS = {
    "MEMO": ("memorandum", "memo to all", "office memorandum"),
    "LETTER": ("letter", "dear sir", "dear madam", "very truly yours"),
    "WORKORDER": ("work order", "job order", "repair request", "service request"),
    "PR": ("purchase request", "purchase order", "canvass", "requisition"),
    "ENDORSE": ("endorsement", "respectfully endorsed", "for your appropriate action"),
    "REPORT": ("report", "accomplishment report", "monthly report", "summary of"),
    "NOTICE": ("notice", "announcement", "advisory"),
}

STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "shall", "will", "are", "was", "were",
    "has", "have", "had", "not", "you", "your", "our", "all", "any", "may", "per", "into", "upon",
    "office", "university", "universidad", "manila", "please", "kindly", "thru", "through", "date",
    "subject", "attached", "hereby", "respectfully", "sir", "madam", "sincerely", "truly", "yours",
}

SUBJECT_PATTERNS = [
    re.compile(r"^\s*subject\s*[:\-]\s*(?P<value>.+)$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*re\s*[:\-]\s*(?P<value>.+)$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*title\s*[:\-]\s*(?P<value>.+)$", re.IGNORECASE | re.MULTILINE),
]
FROM_PATTERN = re.compile(r"^\s*from\s*[:\-]\s*(?P<value>.+)$", re.IGNORECASE | re.MULTILINE)
TO_PATTERN = re.compile(r"^\s*(?:to|for)\s*[:\-]\s*(?P<value>.+)$", re.IGNORECASE | re.MULTILINE)
DATE_PATTERNS = [
    re.compile(r"^\s*date\s*[:\-]\s*(?P<value>.+)$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"\b(?P<value>(?:January|February|March|April|May|June|July|August|September|October|"
               r"November|December)\s+\d{1,2},\s*\d{4})\b", re.IGNORECASE),
    re.compile(r"\b(?P<value>\d{1,2}/\d{1,2}/\d{4})\b"),
    re.compile(r"\b(?P<value>\d{4}-\d{2}-\d{2})\b"),
]
REFERENCE_PATTERN = re.compile(r"\b(?P<value>[A-Z]{2,}(?:-[A-Z0-9]{1,10}){2,})\b")
WORD_PATTERN = re.compile(r"[A-Za-z][A-Za-z\-']{3,}")


@dataclass
class Suggestion:
    """One proposal for a document. Confidence is 0.0 – 1.0 per field."""

    title: str = ""
    subject: str = ""
    document_type_id: int | None = None
    document_type_label: str = ""
    office_id: int | None = None
    office_label: str = ""
    document_date: date | None = None
    author_name: str = ""
    recipient_name: str = ""
    reference_number: str = ""
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    confidence: dict[str, float] = field(default_factory=dict)
    engine: str = "rules"
    engine_version: str = ENGINE_VERSION
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "subject": self.subject,
            "document_type_id": self.document_type_id,
            "document_type_label": self.document_type_label,
            "office_id": self.office_id,
            "office_label": self.office_label,
            "document_date": self.document_date.isoformat() if self.document_date else "",
            "author_name": self.author_name,
            "recipient_name": self.recipient_name,
            "reference_number": self.reference_number,
            "tags": self.tags,
            "metadata": self.metadata,
            "confidence": self.confidence,
            "engine": self.engine,
            "engine_version": self.engine_version,
        }


class BaseEngine:
    name = "base"

    def suggest(self, *, text: str, filename: str = "", office=None, user=None) -> Suggestion:
        raise NotImplementedError


class NullEngine(BaseEngine):
    """Suggests nothing — used when SUGGESTION_ENGINE=none."""

    name = "none"

    def suggest(self, *, text: str, filename: str = "", office=None, user=None) -> Suggestion:
        suggestion = Suggestion(engine=self.name)
        suggestion.title = _title_from_filename(filename)
        return suggestion


class RuleBasedEngine(BaseEngine):
    """Deterministic, explainable, and editable from the Administration screen."""

    name = "rules"

    def suggest(self, *, text: str, filename: str = "", office=None, user=None) -> Suggestion:
        text = normalise_text(text or "")
        head = text[:2000]
        suggestion = Suggestion(engine=self.name)

        # -- subject / title ------------------------------------------------
        subject = _first_match(SUBJECT_PATTERNS, head)
        if subject:
            suggestion.subject = _clean_line(subject)
            suggestion.title = suggestion.subject
            suggestion.confidence["title"] = 0.9
        else:
            suggestion.title = _title_from_filename(filename) or _first_strong_line(head)
            suggestion.confidence["title"] = 0.45 if suggestion.title else 0.0

        # -- from / to ------------------------------------------------------
        author = FROM_PATTERN.search(head)
        if author:
            suggestion.author_name = _clean_line(author.group("value"))[:150]
            suggestion.confidence["author_name"] = 0.75
        recipient = TO_PATTERN.search(head)
        if recipient:
            suggestion.recipient_name = _clean_line(recipient.group("value"))[:150]
            suggestion.confidence["recipient_name"] = 0.6

        # -- date -----------------------------------------------------------
        for pattern in DATE_PATTERNS:
            match = pattern.search(head)
            if match:
                parsed = parse_date_guess(_clean_line(match.group("value")))
                if parsed:
                    suggestion.document_date = parsed
                    suggestion.confidence["document_date"] = 0.8
                    break

        # -- reference / control number --------------------------------------
        reference = REFERENCE_PATTERN.search(head)
        if reference:
            suggestion.reference_number = reference.group("value")[:64]
            suggestion.confidence["reference_number"] = 0.7

        # -- document type ---------------------------------------------------
        self._apply_type_hints(suggestion, text)

        # -- office ----------------------------------------------------------
        self._apply_office(suggestion, text, office)

        # -- admin-configured rules (highest authority) -----------------------
        self._apply_rules(suggestion, text, filename)

        # -- keyword tags ------------------------------------------------------
        if len(suggestion.tags) < 6:
            for keyword in _keyword_candidates(text, limit=6 - len(suggestion.tags)):
                if keyword not in suggestion.tags:
                    suggestion.tags.append(keyword)
                    suggestion.confidence.setdefault(f"tag:{keyword}", 0.4)

        # -- metadata fields ----------------------------------------------------
        self._apply_metadata_fields(suggestion, text)

        if not text:
            suggestion.notes.append("No text could be read from the file, so only the filename was used.")
        return suggestion

    # -- internals ---------------------------------------------------------
    def _apply_type_hints(self, suggestion: Suggestion, text: str) -> None:
        lowered = text.lower()
        best_code, best_hits = None, 0
        for code, hints in DEFAULT_TYPE_HINTS.items():
            hits = sum(1 for hint in hints if hint in lowered)
            if hits > best_hits:
                best_code, best_hits = code, hits
        if not best_code:
            return
        document_type = DocumentType.objects.filter(code=best_code, is_active=True).first()
        if document_type:
            suggestion.document_type_id = document_type.pk
            suggestion.document_type_label = document_type.name
            suggestion.confidence["document_type"] = min(0.5 + 0.15 * best_hits, 0.95)

    def _apply_office(self, suggestion: Suggestion, text: str, office) -> None:
        from apps.accounts.models import Office

        lowered = text.lower()
        for candidate in Office.objects.filter(is_active=True):
            names = {candidate.name.lower(), candidate.short_name.lower()}
            if any(name and name in lowered for name in names):
                suggestion.office_id = candidate.pk
                suggestion.office_label = candidate.name
                suggestion.confidence["office"] = 0.85
                return
        if office is not None:
            suggestion.office_id = office.pk
            suggestion.office_label = office.name
            suggestion.confidence["office"] = 0.5

    def _apply_rules(self, suggestion: Suggestion, text: str, filename: str) -> None:
        lowered_full = text.lower()
        lowered_head = lowered_full[:2000]
        lowered_title = (filename or "").lower()

        for rule in TagRule.active.select_related("suggest_tag", "suggest_document_type", "suggest_office"):
            haystack = {
                TagRule.Field.FULL_TEXT: lowered_full,
                TagRule.Field.FIRST_PAGE: lowered_head,
                TagRule.Field.TITLE: lowered_title,
            }.get(rule.search_field, lowered_full)

            if not _rule_matches(rule, haystack):
                continue

            if rule.suggest_tag and rule.suggest_tag.name not in suggestion.tags:
                suggestion.tags.append(rule.suggest_tag.name)
                suggestion.confidence[f"tag:{rule.suggest_tag.name}"] = rule.confidence
            if rule.suggest_document_type_id:
                suggestion.document_type_id = rule.suggest_document_type_id
                suggestion.document_type_label = rule.suggest_document_type.name
                suggestion.confidence["document_type"] = max(
                    rule.confidence, suggestion.confidence.get("document_type", 0)
                )
            if rule.suggest_office_id:
                suggestion.office_id = rule.suggest_office_id
                suggestion.office_label = rule.suggest_office.name
                suggestion.confidence["office"] = max(rule.confidence, suggestion.confidence.get("office", 0))
            if rule.suggest_metadata_key:
                suggestion.metadata[rule.suggest_metadata_key] = rule.suggest_metadata_value
                suggestion.confidence[f"meta:{rule.suggest_metadata_key}"] = rule.confidence

    def _apply_metadata_fields(self, suggestion: Suggestion, text: str) -> None:
        """Fill admin-defined fields whose label appears as `Label: value` in the text."""
        head = text[:4000]
        for definition in MetadataFieldDefinition.active.all():
            if definition.key in suggestion.metadata:
                continue
            pattern = re.compile(
                rf"^\s*{re.escape(definition.label)}\s*[:\-]\s*(?P<value>.+)$",
                re.IGNORECASE | re.MULTILINE,
            )
            match = pattern.search(head)
            if match:
                suggestion.metadata[definition.key] = _clean_line(match.group("value"))[:500]
                suggestion.confidence[f"meta:{definition.key}"] = 0.7


class AIEngine(BaseEngine):
    """Placeholder for the trained model.

    Implement `suggest()` here later (call your model, map the output onto the
    same `Suggestion` fields) and set SUGGESTION_ENGINE=ai. Nothing else in the
    project has to change. Until then it falls back to the rules engine so the
    setting is always safe to flip.
    """

    name = "ai"

    def suggest(self, *, text: str, filename: str = "", office=None, user=None) -> Suggestion:
        suggestion = RuleBasedEngine().suggest(text=text, filename=filename, office=office, user=user)
        suggestion.notes.append("AI engine is not trained yet — rule-based results were used.")
        return suggestion


def get_engine() -> BaseEngine:
    name = (settings.SUGGESTION_ENGINE or "rules").lower()
    return {"rules": RuleBasedEngine, "none": NullEngine, "ai": AIEngine}.get(name, RuleBasedEngine)()


def suggest_metadata(*, text: str, filename: str = "", office=None, user=None) -> Suggestion:
    return get_engine().suggest(text=text, filename=filename, office=office, user=user)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _rule_matches(rule: TagRule, haystack: str) -> bool:
    pattern = (rule.pattern or "").strip().lower()
    if not pattern:
        return False
    if rule.match_type == TagRule.MatchType.CONTAINS:
        return pattern in haystack
    if rule.match_type == TagRule.MatchType.ALL_WORDS:
        return all(word.strip() in haystack for word in pattern.split(",") if word.strip())
    if rule.match_type == TagRule.MatchType.ANY_WORD:
        return any(word.strip() in haystack for word in pattern.split(",") if word.strip())
    if rule.match_type == TagRule.MatchType.REGEX:
        try:
            return re.search(rule.pattern, haystack, re.IGNORECASE) is not None
        except re.error:
            return False
    return False


def _first_match(patterns, text: str) -> str:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match.group("value")
    return ""


def _clean_line(value: str) -> str:
    return " ".join(str(value or "").split()).strip(" .:-")[:255]


def _first_strong_line(text: str) -> str:
    for line in (text or "").splitlines():
        cleaned = _clean_line(line)
        if len(cleaned) >= 12 and not cleaned.lower().startswith(("republic", "universidad", "university")):
            return cleaned[:120]
    return ""


def _title_from_filename(filename: str) -> str:
    if not filename:
        return ""
    stem = filename.rsplit(".", 1)[0]
    stem = re.sub(r"[_\-]+", " ", stem)
    stem = re.sub(r"\s{2,}", " ", stem).strip()
    return stem[:120].title() if stem else ""


def _keyword_candidates(text: str, limit: int = 5) -> list[str]:
    """Frequent, meaningful words — a cheap stand-in until the AI engine lands."""
    counts: dict[str, int] = {}
    for word in WORD_PATTERN.findall((text or "").lower()[:20000]):
        if word in STOPWORDS or len(word) < 5:
            continue
        counts[word] = counts.get(word, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [word for word, count in ranked[:limit] if count >= 2]


def existing_tag_names() -> list[str]:
    return list(Tag.active.values_list("name", flat=True))

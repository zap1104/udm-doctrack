# AI roadmap

The AI features are deliberately not built yet. This document explains that decision, and exactly where the model plugs in when the team is ready.

---

## Why rules first

1. **Rules work today.** A rules engine that gets the office right 80% of the time on day one beats a model that might get 90% after six months of data collection.
2. **Rules are explainable.** When a suggestion is wrong, an administrator opens Administration → Metadata rules and fixes it in half a minute. When a model is wrong, you retrain and hope.
3. **A model needs data that does not exist yet.** Training needs hundreds of reviewed documents. Rules generate exactly that data as a side effect of normal use.
4. **The demo has to work.** A prototype whose headline feature is an unreliable model is a prototype that fails in front of an audience.

The honest framing for the defence: *the AI interface is built and the training data is already being collected; the model is Phase 2 because rules are more useful and more correctable at this stage.*

---

## The plug-in point

`apps/documents/suggestions.py` defines one shape that every engine implements:

```python
@dataclass
class Suggestion:
    title: str = ""
    document_type_id: int | None = None
    office_id: int | None = None
    document_date: date | None = None
    reference_number: str = ""
    author_name: str = ""
    recipient_name: str = ""
    tags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    confidence: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
```

Three engines already exist:

| Engine | `SUGGESTION_ENGINE` | Status |
|---|---|---|
| `RuleBasedEngine` | `rules` | Working, the default |
| `NullEngine` | `none` | Suggests nothing |
| `AIEngine` | `ai` | Placeholder that currently falls back to rules |

Switching engines is one line in `.env`:

```
SUGGESTION_ENGINE=ai
```

Nothing else in the system changes. The review screen, the storage, the training-data capture and the audit trail all work identically, because they only ever see a `Suggestion`.

---

## The data you are already collecting

Every reviewed document writes a `MetadataSuggestion` row holding what was proposed, what the human kept, the text sample, and whether anything was edited.

```bash
python manage.py export_training_data --out training/metadata.jsonl
python manage.py export_training_data --edited-only    # the corrections
```

The corrected examples are the gold. They mark precisely where the rules failed.

**Rough targets:** 200 documents to start experimenting, 500+ before a model beats the rules, 1000+ for confident office and type classification.

---

## Phase 2 — a realistic build order

### 2a. Document type classification (easiest, highest value)

Predicting one of nine types from text is a straightforward classification problem. Start with TF-IDF plus logistic regression in scikit-learn — it trains in seconds on a laptop, is small enough to ship, and is a strong baseline. Only reach for a transformer if it clearly loses.

Ship it only when it beats the rules on a held-out set, and keep the human review step regardless.

### 2b. Tag recommendation

Multi-label classification. Harder, because tags are correlated and unevenly used. Recommend the top 3–5 with confidence and let the user click to accept — never auto-apply.

### 2c. Entity extraction

Pulling author, recipient, signatory and dates reliably. Filipino institutional documents have consistent structure but varied phrasing. A fine-tuned NER model helps here; so does simply writing better rules, so measure before assuming the model is needed.

### 2d. Semantic search

Embeddings plus `pgvector`, combined with the existing keyword ranking rather than replacing it. **Read `docs/SEARCH_DESIGN.md` before starting.** The hard part is not retrieval; it is explaining to a user why a document containing none of their words is the top result.

---

## Rules that must survive the AI phase

1. **A human always reviews before saving.** Confidence never becomes auto-apply.
2. **Show confidence in words, not decimals.** "Strong match" beats 0.87.
3. **Keep the original file forever.** Any extraction can be redone; a lost original cannot.
4. **Log which engine produced each suggestion.** `MetadataSuggestion.engine` and `engine_version` already do this — you will need it when a model regresses.
5. **The rules engine stays as a fallback.** If the model service is down, filing must continue.
6. **Never call the output accuracy.** It is a suggestion with a confidence.

---

## Where the model should run

| Option | Good | Bad |
|---|---|---|
| In-process scikit-learn | Simple, fast, no network | Ties model updates to deploys |
| django-q2 background task | Non-blocking, retryable | Slight delay before suggestions appear |
| Separate API service | Independent scaling and updates | Another service to run and secure |
| Third-party LLM API | Strong with no training data | Sends institutional documents to an outside vendor — likely unacceptable under the Data Privacy Act without review |

For a university records system holding personal data, start in-process or in a background task. Anything that sends document text off-campus needs a written data-sharing assessment first, not an afterthought.

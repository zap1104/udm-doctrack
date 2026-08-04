# Search design

The archive will hold thousands of documents. Search is what makes that useful instead of a landfill. This document explains exactly how a result gets its percentage — because a number nobody can explain is a number nobody should trust.

---

## The one terminology rule

**It is called *relevance*, never *accuracy*.**

- **Relevance** = how well this record matches the words you typed.
- **Accuracy** = whether the system is correct. That is a different claim, and this number does not make it.

An 82% result means "this record matches your query strongly", not "the system is 82% sure". Anyone reading `apps/search/services.py` will find the same wording. Expect this question at the defence.

---

## Why PostgreSQL and not Elasticsearch

Elasticsearch is excellent and unnecessary here. It means another service to install, secure, back up and pay for. PostgreSQL's full-text search handles hundreds of thousands of documents comfortably, and this archive will be far smaller. Fewer moving parts means fewer things that break the week before a defence.

---

## Step 1 — Documents are indexed into four weighted fields

When a document is saved, its text is split across four buckets. PostgreSQL assigns each a weight (A is strongest).

| Bucket | Weight | Multiplier | What goes in it |
|---|---|---|---|
| `index_title` | **A** | 1.00 | Title, reference number, tracking number |
| `index_meta` | **B** | 0.60 | Office name and code, document type, tags, author, recipient |
| `index_extra` | **C** | 0.25 | Description, searchable custom metadata fields |
| `ocr_text` | **D** | 0.10 | The full extracted body text |

A hit in the title counts ten times more than a hit buried in body text. That single decision does most of the work of making results feel sensible.

The four fields are combined into a `SearchVectorField` with a GIN index, so matching stays fast as the archive grows.

---

## Step 2 — Three scores per result

### Text score (weight 0.55)

PostgreSQL's `ts_rank_cd` compares the query against the weighted vector. Its output is unbounded, so it is squashed into 0–1:

```
text = rank / (rank + k)        where k = 0.06
```

This is a saturation curve. A document mentioning your term twenty times is better than one mentioning it twice, but not ten times better — and no single document can run away with the ranking.

### Fuzzy score (weight 0.20)

`pg_trgm` trigram similarity between the query and the title. This is what makes `memorandom` find `memorandum` and `Penafrancia` find `Peñafrancia`.

If the extension is not installed, this term is skipped and the other two are used. Search degrades; it does not break.

### Field score (weight 0.25)

Deterministic bonuses for matching in places that matter, checked in Python:

| Matched where | Bonus |
|---|---|
| Exact reference or tracking number | 1.00 (short-circuits to 100%) |
| Title contains the whole phrase | 0.85 |
| Title contains some words | 0.60 |
| Tag matches exactly | 0.70 |
| Office name or code | 0.50 |
| Document type | 0.40 |
| Custom metadata value | 0.45 |
| Body text only | 0.20 |

This score does double duty: it feeds the ranking **and** produces the "Matched in: Title, Tags" line under each result. Users trust a search engine that shows its reasoning.

---

## Step 3 — The formula

```
relevance % = 100 × (0.55 × text + 0.20 × fuzzy + 0.25 × field)
```

Weights live in `.env` so they can be tuned without touching code:

```
SEARCH_WEIGHT_TEXT=0.55
SEARCH_WEIGHT_FUZZY=0.20
SEARCH_WEIGHT_FIELD=0.25
SEARCH_RANK_SATURATION_K=0.06
```

They must sum to 1.0.

**Worked example.** Searching `preventive maintenance` against *"Preventive maintenance of clinic equipment"* (tagged `maintenance`, MED office):

```
text  = 0.34 / (0.34 + 0.06) = 0.85    →  0.55 × 0.85 = 0.4675
fuzzy = 0.62                            →  0.20 × 0.62 = 0.1240
field = 0.85 (whole phrase in title)    →  0.25 × 0.85 = 0.2125
                                                        --------
                                                          0.8040

relevance = 80%
```

---

## Step 4 — The 75% threshold

Results below `SEARCH_MIN_RELEVANCE_DEFAULT` (75) are hidden, and the count of what was hidden is shown:

> *12 more records matched below the 75% threshold.*

Three reasons this design is right:

1. **Nothing is silently dropped.** The user always knows more exists.
2. **It is adjustable.** A slider on the search page moves it from 0 to 100.
3. **It is a display filter, not a truth claim.** The system does not assert that a 74% match is wrong — only that it is probably not what you wanted first.

The original flowchart proposed showing 75–100%. This implements that, without pretending the number below the line is meaningless.

---

## Step 5 — Permissions are applied first

Filtering happens **before** ranking, not after. A user can never see a relevance score for a document they are not allowed to open. Ranking a document you cannot read would leak its existence, its title and its office through the result count.

---

## What gets logged

Every search writes to `SearchQueryLog`: the query, filters, result count, duration and user. This exists for one reason — **queries that return nothing are a to-do list**. If ten people search "clearance form" and find nothing, either it was never filed or it was filed under wording nobody would guess. The Reports page surfaces the most common searches.

---

## Tuning it

Symptoms and fixes:

| Symptom | Try |
|---|---|
| Right document ranks too low | Raise `SEARCH_WEIGHT_FIELD` and check the title is descriptive |
| Too many weak matches | Raise the threshold above 75 |
| Misspellings never match | Run `manage.py init_db` to install `pg_trgm` |
| Everything scores about the same | Lower `SEARCH_RANK_SATURATION_K` to spread scores out |
| Recent documents rank too low | Add a recency term (deliberately not built — argue for it first) |

After changing any weight: `make reindex`.

---

## Deliberately not built

- **Semantic / vector search** — a real improvement, but it needs embeddings, a vector extension, and an explanation of why a document with none of your words is the top result. Rules and text ranking are explainable today.
- **Recency boost** — sounds obvious, but a five-year-old policy is often exactly what you want. Needs real usage data first.
- **Personalised ranking** — makes results unpredictable and unauditable. Wrong for a records system.

---

## Where the code lives

| Thing | Location |
|---|---|
| Ranking implementation | `apps/search/services.py` |
| Index construction | `apps/documents/models.py` → `build_index_blobs()` |
| Weights and threshold | `config/settings.py`, overridable in `.env` |
| Search page | `templates/search/search.html` |
| Tests | `tests/test_search.py` |

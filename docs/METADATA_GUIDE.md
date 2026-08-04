# Metadata guide

Search can only find what metadata describes. A document filed as "scan001.pdf" with no office, no type and no tags is lost the moment nobody remembers it. This guide covers what the system captures, how it proposes values, and how to make the archive findable at scale.

---

## The three layers

### 1. Core fields — every document has them

| Field | Why it matters |
|---|---|
| **Title** | The single most heavily weighted search field. Write it as a person would search for it. |
| **Office** | Drives smart folders and permissions. |
| **Document type** | Memo, letter, work order… drives filters and retention. |
| **Document date** | The date on the paper, not the upload date. |
| **Year** | Filled from the date; the fastest filter people use. |
| **Reference number** | Original control number. An exact match jumps straight to 100% relevance. |
| **Author / recipient** | From and To. People search by person constantly. |
| **Signatory** | Who signed. Matters for approvals and audits. |
| **Access level** | Office, OVPA-wide, or restricted. |
| **Retention until** | When it may be disposed of. |

### 2. Tags — the shared vocabulary

Tags cut across offices and types. `urgent`, `for signature`, `procurement`, `incident report`.

Rules that keep tags useful:

- **Lower case, always.** `Urgent` and `urgent` must not both exist.
- **Two or three words maximum.**
- **A tag used once is noise.** If nobody reuses it, delete it.
- **Administrators curate the list.** Free-typing tags produces `procurment`, `procurement`, `Procurement` and `PROCUREMENT` within a month.

The Administration → Tags screen shows a usage count. Anything at 1 after a few months is a candidate for merging or deletion.

### 3. Custom metadata fields — per office

Administrators define extra fields under **Administration → Metadata fields**, and they appear on every review screen with no code change. The demo ships with:

| Key | Label | Type |
|---|---|---|
| `control_no` | Control number | Text |
| `fund_source` | Fund source | Choice |
| `amount` | Amount (PHP) | Number |
| `period_covered` | Period covered | Text |
| `physical_location` | Physical file location | Text |
| `confidential` | Contains personal data | Yes/No |

Mark a field **searchable** and its values join the weight-C search bucket. `physical_location` is the quiet hero: it tells you which cabinet the paper original sits in.

---

## How suggestions are produced

### Stage 1 — Get the text

`apps/documents/extraction.py`, in order:

1. **Text layer** — digital PDFs (`pypdf`), Word (`python-docx`), Excel (`openpyxl`), plain text. Instant, free, accurate.
2. **OCR** — only if fewer than 40 useful characters were found. Calls OCR.space, with Azure Document Intelligence as an optional fallback.

Most institutional documents are born digital, so most never touch OCR. That is a deliberate cost and speed decision.

**Extraction never raises.** A corrupt file produces an empty result and a note, and the document is still saved and filed by metadata.

### Stage 2 — Propose metadata

`apps/documents/suggestions.py` reads the text and proposes values. Extraction and suggestion are separate stages on purpose: OCR quality and suggestion quality are different problems with different fixes.

The rules engine looks for:

- `Subject:` / `Re:` lines → title
- `From:` / `To:` lines → author, recipient
- Dates in several Filipino office formats → document date
- Reference patterns like `2026-001`, `PR No. 45` → reference number
- Type keywords: *memorandum*, *work order*, *purchase request*, *endorsement*
- Office names and codes anywhere in the text
- Every admin-configured `TagRule`
- `Label: value` lines matching a defined metadata field

Each suggestion carries a confidence, shown as **Strong match**, **Likely** or **Check this** — words, not decimals, because a records officer should not have to interpret 0.72.

### Stage 3 — A human decides

The review screen shows the extracted text on the left and the editable suggestions on the right. **Nothing is saved until a person presses Save.**

This is the core design commitment. The system proposes; the officer disposes. A wrong suggestion costs a correction, never a wrong record.

---

## Every review trains the future model

When a document is saved, `MetadataSuggestion` stores:

- what the engine proposed
- what the human actually kept
- the text sample the engine saw
- whether the human changed anything

That is a labelled training example, produced by real work rather than invented for a dataset. After a few hundred documents:

```bash
python manage.py export_training_data --out training/metadata.jsonl
```

The corrections are the most valuable rows — they are exactly where the rules were wrong.

---

## Writing metadata that survives

**Titles.** Write what someone would type in three years.

| Bad | Good |
|---|---|
| `Memo` | `Memorandum on holiday work schedule, December 2026` |
| `scan001` | `Purchase request for 20 office chairs` |
| `Letter to VP` | `Letter to VPA requesting budget realignment for Q4 2026` |

**Dates.** Use the date printed on the document. Upload date is recorded separately and is nearly useless for finding things.

**Tags.** Three good tags beat ten vague ones. Ask: *would someone search this word?*

**Reference numbers.** Always fill this when the paper has one. An exact match short-circuits straight to 100% relevance — the fastest path in the whole system.

**Access level.** Anything with medical records, personnel matters or salaries should be Office or Restricted. The Data Privacy Act is not optional.

---

## Filing at scale

Thousands of documents make small habits decisive.

1. **Fill Office, Type and Year every time.** These three carry most filtering.
2. **Reuse existing tags.** The tag box autocompletes from tags already in use.
3. **Fill in `physical_location` for paper originals.** Digital search is worthless if nobody can find the physical copy.
4. **Batch by office and month.** Consistency comes from rhythm.
5. **Review the untagged count on Reports weekly.** It should trend to zero.
6. **Search for a document a week after filing it.** If you cannot find your own document, nobody else will.

---

## Adding a metadata rule

Administration → Metadata rules → Add.

Example — every disbursement voucher should carry a `budget` tag:

| Field | Value |
|---|---|
| Name | Disbursement voucher |
| Pattern | `disbursement voucher` |
| Match type | Contains |
| Search field | Full text |
| Suggest tag | `budget` |
| Suggest document type | Disbursement Voucher |
| Confidence | 0.85 |
| Priority | 20 |

Lower priority numbers run first. Test it by uploading a matching document and checking the review screen.

Rules are how the archive gets smarter with no AI at all — and they are correctable in thirty seconds by someone who cannot code.

# UDM DocTrack

**Records and Document Management System for the offices under the Office of the Vice President for Administration, Universidad de Manila.**

Two halves, one system:

- **Document Tracking (DTS)** — where is this document right now, who has it, and since when.
- **Document Management (DMS)** — once it is finished, file it so somebody can find it in three years.

---

## Get it running in five minutes

You need **Python 3.10–3.14**, **PostgreSQL 14+**, and **Git**.

```bash
git clone https://github.com/<your-team>/udm-doctrack.git
cd udm-doctrack

# macOS / Linux
bash scripts/start.sh

# Windows PowerShell
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
```

Then:

```bash
python manage.py runserver
```

Open <http://127.0.0.1:8000> and sign in:

| Account | Password | What they see |
|---|---|---|
| `admin` | `DocTrack2026!` | Everything, plus Administration |
| `records` | `DocTrack2026!` | Records office view |
| `med.staff` | `DocTrack2026!` | A normal user with one document waiting |

Stuck? Read **[docs/SETUP.md](docs/SETUP.md)** — it covers the errors people actually hit.

---

## Status

The deployment-readiness branch adds background extraction through django-q2 with a safe synchronous fallback, office-scoped in-app notifications, optional SMTP password recovery, `/healthz/` platform checks, deployment-blocking security checks, filtered report exports, pure-Python upload content sniffing, and CI coverage for self-check and page smoke verification. Review [docs/OPERATIONS.md](docs/OPERATIONS.md) before deploying with real records.

## What it does

**Tracking**
- Automatic tracking numbers: `UDM-OVPA-MED-2026-08-0001` — unique, readable, never reused.
- Routing to one or many offices, with instructions and a deadline.
- **Explicit receipt.** Sent is not received. Custody changes only when someone at the receiving office presses *Confirm receipt*, and the server writes the timestamp — not the user.
- Append-only history. Forwarding never overwrites an earlier step; nothing disappears.
- Printable routing slip carrying the full movement history.

**Documents**
- Completed records archive themselves into the repository, files and all.
- Text is read from digital PDFs, Word and Excel files for free. Scanned pages go to OCR only when there is no text layer; with background tasks enabled, the upload returns immediately and the repository shows a plain-language pending state.
- The system proposes a title, type, office, date and tags. **A person reviews and corrects before anything is saved.**
- Smart folders are saved views over metadata — one file, many folders, no duplicates on disk.

**Search**
- PostgreSQL full-text search with weighted fields, plus fuzzy matching for misspellings.
- Every result shows a **relevance** percentage and *why* it matched.
- The 75% minimum is a display filter you can move, not a claim about accuracy. See **[docs/SEARCH_DESIGN.md](docs/SEARCH_DESIGN.md)**.

**Control**
- Accounts are created by administrators. There is no public registration anywhere.
- Regular users see only what was routed to, originated by, assigned to, or explicitly shared with them.
- Append-only audit log for sign-ins, routing, receipts, downloads and master-data edits, plus office-level notifications with per-user read state.

---

## The stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Django 5.2 LTS | Supports Python 3.10–3.14, security updates to 2028 |
| Database | PostgreSQL 14+ | Handles the full-text search too — no Elasticsearch to run |
| Frontend | Django templates + Bootstrap 5 + HTMX | No build step, no npm, no React |
| Files | Local disk, Cloudflare R2, or Azure Blob | Set `STORAGE_BACKEND` in `.env` |
| OCR | OCR.space (free tier) | Azure Document Intelligence as an optional fallback |
| Jobs | django-q2 | Uses the database as its queue — no Redis |
| Security | django-axes, django-csp, Argon2, signed links | Lockouts, CSP headers, strong hashes, expiring downloads |

---

## Repository layout

```
udm-doctrack/
├── config/            Settings, root URLs, WSGI/ASGI
├── apps/
│   ├── core/          Dashboard, master data, audit log, shared utilities
│   ├── accounts/      Offices, users, roles, sign-in
│   ├── tracking/      Tracking records, routing, receipts   ← the DTS half
│   ├── documents/     Archive, extraction, metadata         ← the DMS half
│   └── search/        Relevance ranking
├── templates/         All HTML, grouped by app
├── static/            CSS, JS, images
├── docs/              Setup, Git workflow, checklist, design notes
├── scripts/           start.sh / start.ps1 (guided setup)
└── tests/             The rules that must never break
```

**Where the important logic lives:**

| Question | File |
|---|---|
| How is a tracking number generated? | `apps/tracking/services.py` |
| Who can see this record? | `apps/tracking/models.py` → `visible_to()` |
| How is relevance calculated? | `apps/search/services.py` |
| How is metadata suggested? | `apps/documents/suggestions.py` |
| How is text pulled from a file? | `apps/documents/extraction.py` |

Rule of thumb: **views never change data directly.** They call `services.py`. That is why the history can be trusted.

---

## Common commands

```bash
make run              # start the server
make test             # run the tests
make selfcheck        # exercise the whole workflow, then roll back
make smoke            # request every page as each role, then roll back
make templates        # lint the templates
make fixlogin         # repair sign-in problems
make migrations       # after changing a model
make migrate          # apply migrations
make seed             # reload demo data
make reindex          # rebuild the search index
make lint             # check code style
```

`selfcheck` and `smoke` answer different questions. `selfcheck` proves the
*rules* hold — create, route, receive, forward, complete, archive, search.
`smoke` proves the *pages* render, which is a separate failure: a template that
reads a context key its view never sets breaks only when somebody opens it.
Both run against the real database inside a transaction they roll back, so
neither leaves anything behind.

No `make` on Windows? Every command is just `python manage.py <thing>` (or
`python scripts/<thing>.py`) — see the `Makefile`.

---

## For the team

- **[docs/SETUP.md](docs/SETUP.md)** — install it, including the errors people actually hit
- **[docs/GITHUB_GUIDE.md](docs/GITHUB_GUIDE.md)** — how to push your work without breaking anyone else's
- **[docs/TEAM_CHECKLIST.md](docs/TEAM_CHECKLIST.md)** — who does what, in order
- **[docs/METADATA_GUIDE.md](docs/METADATA_GUIDE.md)** — why metadata decides whether search works
- **[docs/SEARCH_DESIGN.md](docs/SEARCH_DESIGN.md)** — the relevance formula, written out
- **[docs/AI_ROADMAP.md](docs/AI_ROADMAP.md)** — how the AI phase plugs in later
- **[docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md)** — a ten-minute walkthrough for the defence
- **[docs/APPLYING_UPDATES.md](docs/APPLYING_UPDATES.md)** — updating an existing checkout
- **[docs/BUGFIX_LOG.md](docs/BUGFIX_LOG.md)** — every bug found and fixed, with the lesson

---

## Status

Working prototype. Tracking, archiving, metadata review, search, reporting and administration all function end to end. The AI metadata model is deliberately not built yet — the interface for it exists (`apps/documents/suggestions.py`) and the system is already collecting labelled training data from every review.

Never commit `.env`, real documents, or personal data. The `.gitignore` blocks all three, but check before you push.

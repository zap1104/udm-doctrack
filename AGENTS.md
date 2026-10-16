# AGENTS.md

Persistent project context for Codex. Place this file at the root of the UDM DocTrack repository (`./AGENTS.md`).

## Project Overview

**UDM DocTrack** (also called UDM RDMS) — a capstone records and document management system for offices under the Office of the Vice President for Administration (OVPA) at Universidad de Manila.

## Tech Stack (non-negotiable — don't suggest alternatives)

- **Backend:** Django 5, PostgreSQL, `psycopg[binary]>=3.2.10,<3.4`
- **Frontend:** Bootstrap 5 with HTMX
- **Background jobs:** django-q2
- **Security:** django-axes, django-csp, Argon2 password hashing, signed download links
- **File storage:** Azure Blob or Cloudflare R2
- **Text extraction:** OCR.space (with OCR fallback for scanned docs)
- **Dev environment:** Windows, PowerShell. Use `scripts\start.ps1`, not a bash script, for local startup. Standard `git` commands work identically in PowerShell — only the project's own helper scripts are shell-specific.

## Django Apps

`core`, `accounts`, `tracking`, `documents`, `search`

## Known Pitfalls — do not reintroduce these

- **Custom User model manager:** must inherit from Django's `UserManager`, not a plain `models.Manager`. A plain manager breaks `get_by_natural_key()` on every login and silently increments django-axes failure counts, locking out the admin before first successful login.
- **`Tag.get_or_create_by_name()`** returns a `(tag, created)` tuple — always unpack it. Storing the tuple whole previously caused silent rollback of the admin account inside a `@transaction.atomic` block during `seed_demo`.
- **`.gitignore` patterns must be anchored** with a leading `/`. An unanchored `documents/` entry matched `apps/documents/` at any depth and excluded the entire Documents app from commits, causing `ModuleNotFoundError: No module named 'apps.documents'` on fresh clones.
- **Routing/inbox queries** must scope to `F("current_batch")` — unscoped queries matched unreceived steps from any batch, not just the current one.
- **Office naming:** MED means **"Maintenance and Engineering Division"**. Use this official name in seed data, tests, and docs.

## Git Workflow

Branch model: `main` (demo-ready, stable) ← `develop` (integration) ← `feature/<app>-<description>` or `fix/<description>`

- Never commit directly to `main` or `develop`.
- Commit message format: `type(scope): summary` (types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`)
- Full workflow reference: `git-github-guide-udm-doctrack.md` in the repo docs.

## Useful Management Commands

`init_db`, `seed_demo`, `reindex_documents`, `export_training_data`, `fix_login`, `create_admin`

## Repo Scaffolding Already in Place

`requirements.txt`, `.env.example`, `.gitignore`, `Makefile`, `Procfile`, `render.yaml`, GitHub Actions CI workflow, `scripts\start.ps1` / `scripts/start.sh`

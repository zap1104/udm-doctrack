# Setup guide

For someone who has never run a Django project. Follow it top to bottom; skipping steps is what causes the errors in the last section.

Roughly 20 minutes the first time, 2 minutes after that.

---

## 1. Install the three things you need

### Python 3.11 or newer

- **Windows** — <https://www.python.org/downloads/> → during install, **tick "Add Python to PATH"**. Missing this tick is the single most common cause of "python is not recognized".
- **macOS** — `brew install python@3.12`
- **Ubuntu** — `sudo apt install python3 python3-venv python3-pip`

Check it:

```bash
python --version      # Windows
python3 --version     # macOS / Linux
```

You need `3.11` or higher.

### PostgreSQL 14 or newer

- **Windows** — <https://www.postgresql.org/download/windows/>. **Write down the password** you set for the `postgres` user; you need it in step 3.
- **macOS** — `brew install postgresql@16 && brew services start postgresql@16`
- **Ubuntu** — `sudo apt install postgresql postgresql-contrib`

Check it:

```bash
psql --version
```

### Git

- **Windows** — <https://git-scm.com/download/win>
- **macOS** — `brew install git` (or it arrives with Xcode tools)
- **Ubuntu** — `sudo apt install git`

---

## 2. Get the code

```bash
git clone https://github.com/<your-team>/udm-doctrack.git
cd udm-doctrack
```

Never downloaded from GitHub before? Read [GITHUB_GUIDE.md](GITHUB_GUIDE.md) first.

---

## 3. Create the database

Open a terminal and start the PostgreSQL shell:

```bash
# Windows (use the SQL Shell / psql app from the Start menu)
psql -U postgres

# macOS / Linux
sudo -u postgres psql
```

Then paste these four lines:

```sql
CREATE DATABASE udm_doctrack;
CREATE USER udm WITH PASSWORD 'udmpass';
GRANT ALL PRIVILEGES ON DATABASE udm_doctrack TO udm;
ALTER DATABASE udm_doctrack OWNER TO udm;
\q
```

> Use a real password on a real server. `udmpass` is for your laptop only.

The owner line matters: it lets the app enable the search extensions itself in step 4.

---

## 4. Run the setup script

**macOS / Linux**

```bash
bash scripts/start.sh
```

**Windows PowerShell**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
```

This is a *guided* script: it checks each prerequisite, tells you plainly what
is missing, and stops rather than carrying on and leaving you with a half-built
system. It asks before changing anything, and it is safe to run repeatedly.

It does nine things:

1. Checks Python, PostgreSQL, whether the database server is actually *running*, and Git
2. Creates a virtual environment in `.venv` (a private Python just for this project)
3. Installs the dependencies
4. Copies `.env.example` to `.env` and generates a fresh secret key
5. **Tests the database connection**, and offers to create the database and user if it fails
6. Creates the database tables
7. Enables the `pg_trgm` and `unaccent` search extensions
8. Loads demo data, then *verifies the admin account actually works*
9. Collects static files, runs Django's system check, clears any login lockouts

Step 5 is the important one. The older `setup.sh` / `setup.ps1` scripts run the
same steps without checking, so if PostgreSQL was not running they would appear
to succeed while leaving the database empty.

If your database password is not `udmpass`, open `.env` and fix `POSTGRES_PASSWORD` before running the script again.

---

## 5. Start it

```bash
# macOS / Linux
source .venv/bin/activate
python manage.py runserver

# Windows
.\.venv\Scripts\Activate.ps1
python manage.py runserver
```

Open <http://127.0.0.1:8000>.

| Account | Password | Role |
|---|---|---|
| `admin` | `DocTrack2026!` | Administrator — sees everything |
| `records` | `DocTrack2026!` | Records officer |
| `med.staff` | `DocTrack2026!` | Regular user with a document waiting |

**`(.venv)` must appear at the start of your terminal prompt.** If it doesn't, activate the environment again — most "module not found" errors are this and nothing else.

---

## 6. Doing this every day

```bash
cd udm-doctrack
git pull                          # get your teammates' work
source .venv/bin/activate         # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt   # only if requirements.txt changed
python manage.py migrate          # only if models changed
python manage.py runserver
```

Press `Ctrl+C` to stop the server.

---

## Optional: turn on OCR for scanned documents

Without a key, digital PDFs and Word files still work perfectly — only scanned images are skipped.

1. Register free at <https://ocr.space/ocrapi> (no card required)
2. Put the key in `.env`:
   ```
   OCR_SPACE_API_KEY=your-key-here
   ```
3. Restart the server

The free tier allows 25,000 pages a month, which is more than a capstone demo will ever use.

---

## Optional: background jobs

Set `ENABLE_BACKGROUND_TASKS=True` in `.env` for production or for any installation that handles scanned documents. Uploads are saved quickly with a **Reading the document…** state; the worker performs text extraction and OCR after the database transaction commits, then rebuilds the search index. If the setting is false or django-q2 is unavailable, the laptop-safe synchronous path remains available.

Run the worker in a **second terminal** locally:

```bash
source .venv/bin/activate
python manage.py qcluster
```

On Render, create a separate worker process with `python manage.py qcluster`. If no worker is running, uploads remain pending and the health endpoint's `?deep=1` check reports the worker as unavailable. The web process still serves existing records.

## Optional: password recovery email

Password recovery is offered only when `EMAIL_BACKEND` is not the console backend and `EMAIL_HOST` is set. A university IT department typically needs to provide the SMTP hostname, port, TLS or SSL requirement, an authenticated service account, the approved sender address, and any relay or firewall allow-list entry. Set these values in the Render environment rather than committing them:

```dotenv
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.university.example
EMAIL_PORT=587
EMAIL_HOST_USER=doctrack@university.example
EMAIL_HOST_PASSWORD=use-the-secret-store
EMAIL_USE_TLS=True
DEFAULT_FROM_EMAIL=UDM DocTrack <doctrack@university.example>
```

Reset requests are rate-limited by source address, never reveal whether an address exists, and write an audit entry. Do not place document content or personal details in email.

---

## Errors people actually hit

### `python: command not found` / `'python' is not recognized`

Python is not on your PATH. On Windows, reinstall and tick "Add Python to PATH". On macOS/Linux, use `python3`.

### `psycopg.OperationalError: connection refused`

PostgreSQL is not running.

```bash
# macOS
brew services start postgresql@16
# Ubuntu
sudo systemctl start postgresql
# Windows: Services app → postgresql-x64-16 → Start
```

### `password authentication failed for user "udm"`

The password in `.env` does not match the one you set in step 3. Fix `POSTGRES_PASSWORD` in `.env`.

### `django.db.utils.ProgrammingError: relation "..." does not exist`

Migrations were not applied:

```bash
python manage.py makemigrations accounts core tracking documents search
python manage.py migrate
```

### `ModuleNotFoundError: No module named 'django'`

The virtual environment is not active. Look for `(.venv)` in your prompt.

```bash
source .venv/bin/activate          # macOS / Linux
.\.venv\Scripts\Activate.ps1       # Windows
```

### PowerShell: "running scripts is disabled on this system"

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

That lasts for the current window only, which is what you want.

### `permission denied to create extension "pg_trgm"`

Your database user is not the owner. Either run the `ALTER DATABASE ... OWNER TO udm;` line from step 3, or as the postgres superuser:

```sql
\c udm_doctrack
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;
```

Without it, search still works — you just lose misspelling tolerance.

### `Port 8000 is already in use`

Another server is still running. Use a different port:

```bash
python manage.py runserver 8001
```

### The page loads but has no styling

```bash
python manage.py collectstatic --noinput
```

Then hard-refresh: `Ctrl+Shift+R` (Windows) or `Cmd+Shift+R` (Mac).

### Search returns nothing even though documents exist

Rebuild the index:

```bash
python manage.py reindex_documents
```

---

## Starting over from scratch

```sql
DROP DATABASE udm_doctrack;
CREATE DATABASE udm_doctrack;
ALTER DATABASE udm_doctrack OWNER TO udm;
```

Then run the setup script again. Nothing outside the database is lost.

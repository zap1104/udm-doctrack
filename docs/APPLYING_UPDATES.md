# How to apply these updates

You already have the project cloned and running, so you do **not** need to start over. This is the safe way to bring an existing checkout up to date.

Roughly 10 minutes.

---

## The situation

Your repository is missing `apps/documents/` entirely (a `.gitignore` bug excluded it from every commit), and several files have since been fixed. The cleanest path is to replace the code files wholesale — your database, `.env`, and virtual environment all stay exactly as they are.

**Nothing below touches your data.** No records are deleted.

---

## Step 1 — Back up what is yours

From inside your project folder:

```powershell
copy .env .env.backup
```

That is the only irreplaceable file in the folder. Everything else is either code (about to be replaced) or regenerable.

---

## Step 2 — Commit or stash anything you changed yourself

If you have edited any files, save that work first:

```powershell
git status
```

If it lists changes you want to keep:

```powershell
git add .
git commit -m "My work before applying updates"
```

If you have not changed anything, skip to step 3.

---

## Step 3 — Replace the code

1. Download and unzip `udm-doctrack.zip`
2. Copy **everything** from inside the unzipped `udm-doctrack` folder into your existing project folder, overwriting when asked

In PowerShell, from the folder holding the unzipped copy:

```powershell
Copy-Item -Path ".\udm-doctrack\*" -Destination "C:\path\to\your\udm-doctrack" -Recurse -Force
```

Replace the destination with your actual path — from your screenshots that is `C:\users\rey\Vscode\udm-doctrack`.

3. Restore your `.env`:

```powershell
copy .env.backup .env
```

> `.env` is not in the zip by design — secrets never travel with code. That is why you backed it up.

---

## Step 4 — Update dependencies

The Django version changed (see "What changed" below), so this step is not optional:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt --upgrade
```

Expect Django to upgrade from 5.0.9 to 5.2.x.

---

## Step 5 — Update the database

```powershell
python manage.py makemigrations accounts core tracking documents search
python manage.py migrate
```

`apps/documents` has never had a migration generated (it was missing from your repo), so this will create one.

---

## Step 6 — Reload demo data and verify

```powershell
python manage.py seed_demo
python manage.py fix_login
python manage.py selfcheck
```

`selfcheck` is new — it creates a document, routes it, confirms receipt, forwards it, completes it, archives it, and searches for it, then rolls the whole thing back. It is the closest thing to a guarantee that the workflow actually works on your machine.

Every line should say `PASS`. If anything says `FAIL`, paste the output and I will fix it.

---

## Step 7 — Push to GitHub

This is the important one — your teammates' clones are broken in the same way yours was.

```powershell
git add -A
git status
```

**Read the `git status` output.** You should see a large number of files under `apps/documents/` listed as new. If you do not, the `.gitignore` fix did not take — open `.gitignore` and confirm there is no bare `documents/` line.

```powershell
git commit -m "Fix .gitignore excluding apps/documents; upgrade to Django 5.2 LTS; fix routing, seeding and login bugs"
git push
```

Then tell your groupmates to run:

```powershell
git pull
pip install -r requirements-dev.txt --upgrade
python manage.py migrate
```

---

## Step 8 — Commit the migrations

Once `makemigrations` has produced files, commit them so nobody else has to generate their own:

```powershell
git add apps/*/migrations/*.py
git commit -m "Add initial migrations"
git push
```

Conflicting migrations are the classic group-project disaster. One person generates them, everyone else just runs `migrate`.

---

## What changed

### Django 5.0.9 → 5.2 LTS

Your traceback showed you are running **Python 3.14**. Django 5.0 does not support it — the project was working by luck, not design. Django 5.2 is a long-term support release covering Python 3.10 through 3.14, so every teammate can run it whatever Python they installed, and it gets security updates until 2028.

### Exact pins → version ranges

`psycopg[binary]==3.2.3` had no wheel for your Python and stopped the install dead. Every dependency now uses a range, so pip picks a build that works on each machine. Two exceptions are pinned deliberately, with the reason in a comment — `django-csp<4.0`, because version 4 renamed every CSP setting and would silently stop sending security headers.

### MED is now Mechanical and Engineering

Office name, head, `med.staff`'s position, the `engineering` tag, the tag rule keywords (electrical, mechanical, plumbing, aircon, generator), and all sample records.

### Bug fixes

See `docs/BUGFIX_LOG.md` for all eight with explanations. The ones that would have bitten you:

- **Drafts could get permanently stuck.** Receiving offices lived only in the session; losing it left a draft that could never be sent, with no way to fix it.
- **Wrong office took custody** when routing to several offices at once.
- **Documents stuck in inboxes forever** after a partly-received batch was forwarded.
- **`seed_demo` rolled back your accounts** when a later step failed — the actual cause of "login details not working".

### New commands

| Command | What it does |
|---|---|
| `python manage.py selfcheck` | Runs the whole workflow against the real database, then rolls back |
| `python manage.py fix_login` | Finds and fixes missing accounts, bad passwords, lockouts |
| `python scripts/check_templates.py` | Catches template errors without needing Django |

---

## If something goes wrong

**Import errors after copying** — stale bytecode:

```powershell
Get-ChildItem -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
```

**`ModuleNotFoundError: No module named 'apps.documents'`** — the copy did not include it. Confirm:

```powershell
dir apps\documents
```

You should see `models.py`, `views.py`, `services.py`, `suggestions.py`, `extraction.py` and more.

**Migration conflicts** — you have migrations from before that do not match:

```powershell
python manage.py makemigrations --merge
```

**Want to start the database completely fresh** (deletes all records):

```sql
DROP DATABASE udm_doctrack;
CREATE DATABASE udm_doctrack;
ALTER DATABASE udm_doctrack OWNER TO udm;
```

Then `python manage.py migrate` and `python manage.py seed_demo`.

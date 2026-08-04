# GitHub guide

How the team shares work without overwriting each other. Written for people whose Git experience is "I've heard of it".

---

## The mental model

Think of the repository as the office filing cabinet.

- **`main`** is the master copy. It must always work.
- A **branch** is your own photocopy to scribble on.
- A **commit** is a saved snapshot of your scribbles, with a note about what changed.
- A **push** sends your snapshots to GitHub.
- A **pull request (PR)** asks the team to fold your changes back into the master copy.

Nobody edits the master copy directly. That is the whole discipline.

---

## Part 1 — Set up the repository (project lead, once)

1. Go to <https://github.com> → **New repository**
2. Name it `udm-doctrack`, set it to **Private**
3. Do **not** tick "Add a README" — the project already has one
4. On your computer:

```bash
cd udm-doctrack
git init
git add .
git commit -m "Initial commit: UDM DocTrack prototype"
git branch -M main
git remote add origin https://github.com/<your-team>/udm-doctrack.git
git push -u origin main
```

5. **Check that `.env` is not on GitHub.** Open the repo in a browser and look. If it is there, the database password is public — read the emergency section at the bottom.

6. Add your groupmates: **Settings → Collaborators → Add people**

7. Protect `main`: **Settings → Branches → Add rule** → branch name `main` → tick *Require a pull request before merging*. This makes accidental damage nearly impossible.

---

## Part 2 — Join the project (everyone else, once)

```bash
git clone https://github.com/<your-team>/udm-doctrack.git
cd udm-doctrack
```

Tell Git who you are:

```bash
git config --global user.name "Your Name"
git config --global user.email "your@email.com"
```

Then follow [SETUP.md](SETUP.md).

> GitHub will ask for a password when you push. It does not want your account password — it wants a **personal access token**: GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token → tick `repo` → copy it and paste it as the password. Save it somewhere; it is shown only once.

---

## Part 3 — The daily loop

This is the part you repeat forever. Five commands.

### 1. Start from the latest code

```bash
git checkout main
git pull
```

**Always pull before you start.** Most merge conflicts are caused by skipping this.

### 2. Make a branch for your task

```bash
git checkout -b feature/search-filters
```

Naming convention:

| Prefix | Use it for | Example |
|---|---|---|
| `feature/` | something new | `feature/print-routing-slip` |
| `fix/` | a bug | `fix/receipt-timestamp` |
| `docs/` | documentation | `docs/user-manual` |
| `ui/` | styling and layout | `ui/dashboard-cards` |

### 3. Do your work, then save it

```bash
git status                    # what did I change?
git add .                     # stage everything
git commit -m "Add year and office filters to search"
```

Commit often — every time something works. Small commits are easy to undo; one giant commit is not.

**Write messages a teammate can read:**

| Good | Bad |
|---|---|
| `Add confirm receipt modal to document detail` | `update` |
| `Fix tracking number collision on same-day records` | `fix bug` |
| `Add tests for office permission rules` | `asdf` |

### 4. Push to GitHub

```bash
git push -u origin feature/search-filters
```

After the first push, plain `git push` is enough.

### 5. Open a pull request

1. Go to the repo on GitHub — a yellow **"Compare & pull request"** banner appears
2. Click it, write what you changed and how to test it
3. Request a review from a groupmate
4. Once approved, **Merge pull request**
5. Delete the branch (GitHub offers a button)

Then go back to step 1 for your next task.

---

## Quick reference

```bash
git status                    # what's changed
git pull                      # get everyone's latest work
git checkout -b my-branch     # new branch
git add .                     # stage everything
git add path/to/file.py       # stage one file
git commit -m "message"       # save a snapshot
git push                      # send to GitHub
git checkout main             # switch back to main
git log --oneline -10         # last 10 commits
git diff                      # exactly what changed
```

---

## When things go wrong

### "I edited main by accident"

Move your work onto a branch — nothing is lost:

```bash
git checkout -b fix/my-work
git add .
git commit -m "My work"
git push -u origin fix/my-work
```

### "Merge conflict"

Two people changed the same lines. Git marks the spot:

```
<<<<<<< HEAD
their version
=======
your version
>>>>>>> your-branch
```

Open the file, delete the `<<<<<<<`, `=======` and `>>>>>>>` markers, keep the correct code (sometimes both halves), then:

```bash
git add .
git commit -m "Resolve merge conflict in views.py"
git push
```

Unsure which half is right? Ask the other person. Do not guess.

### "I want to undo my last commit"

```bash
git reset --soft HEAD~1     # undo the commit, keep the changes
git reset --hard HEAD~1     # undo the commit AND the changes — careful
```

### "I want to throw away my changes and start fresh"

```bash
git checkout -- .           # discard uncommitted changes
git checkout main
git pull
```

### "`git push` was rejected"

Someone pushed first:

```bash
git pull --rebase
git push
```

---

## Rules that keep the repo clean

**Never commit these:**
- `.env` — contains passwords and keys
- Real documents or personal data — this is a Data Privacy Act matter, not a style preference
- `.venv/` — thousands of files, different on every machine
- `media/` — uploaded files
- `db.sqlite3`

`.gitignore` already blocks all of them. Check `git status` before committing anyway.

**Always do these:**
- Pull before starting work
- Work on a branch, never on `main`
- Run `make test` before opening a PR
- Commit a migration file whenever you change a model — forgetting this breaks everyone's database

---

## Handling migrations as a team

Migration conflicts are the classic group-project disaster. Avoid them like this:

- Changed a model? Run `python manage.py makemigrations` and **commit the generated file** in `apps/<app>/migrations/`.
- Pulled someone's changes? Run `python manage.py migrate` immediately.
- Two migrations with the same number after a merge? Run `python manage.py makemigrations --merge`.
- **Never edit a migration someone else already pushed.** Make a new one.

---

## Emergency: `.env` reached GitHub

1. Change the database password immediately
2. Generate a new `DJANGO_SECRET_KEY`
3. Rotate the OCR API key
4. Remove the file from tracking:

```bash
git rm --cached .env
git commit -m "Remove .env from version control"
git push
```

The old value stays in the history, which is why steps 1–3 come first. Treat the leaked values as public forever.

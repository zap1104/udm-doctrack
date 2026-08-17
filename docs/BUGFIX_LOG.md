# Bug fix log

Bugs found and fixed after the first build, kept here so the team can see what
changed and why. All five were runtime-path bugs — Django's own system check
passes either way, which is exactly why they only appeared when the pages ran.

---

## 1. Sign-in crashed: `'ManagerFromUserQuerySet' object has no attribute 'get_by_natural_key'`

**File:** `apps/accounts/models.py`

The `User` model used `models.Manager.from_queryset(UserQuerySet)()`. A plain
`Manager` has no `get_by_natural_key()`, which is the method Django's
authentication backend calls to look a user up by username, so every sign-in
attempt raised `AttributeError` before it ever checked a password.

**Fix:** build the custom manager on Django's `UserManager` instead, which
keeps `create_user()`, `create_superuser()` and `get_by_natural_key()`.

```python
from django.contrib.auth.models import AbstractUser, UserManager
...
objects = UserManager.from_queryset(UserQuerySet)()
```

**Lesson:** a custom user model must always inherit its manager from
`UserManager`, never from `models.Manager`.

---

## 2. `seed_demo` crashed: `Cannot assign "(<Tag: urgent>, True)": "TagRule.suggest_tag" must be a "Tag" instance`

**File:** `apps/core/management/commands/seed_demo.py` (and `tests/conftest.py`)

`Tag.get_or_create_by_name()` returns a `(tag, created)` tuple, matching
Django's own `get_or_create` convention. The seed command stored the whole
tuple where a `Tag` was expected.

**Fix:** unpack it.

```python
tag, _created = Tag.get_or_create_by_name(name, category=category)
```

**Why it mattered more than it looks:** the whole command is wrapped in
`@transaction.atomic`, so this failure rolled back *everything* — including the
`admin` account created earlier in the same run. The symptom was "login details
not working", several steps away from the actual cause.

**Lesson:** an atomic seed command is all-or-nothing. If it prints a traceback,
assume nothing was saved.

---

## 3. Extraction notes never appeared on the review screen

**File:** `apps/documents/suggestions.py`

`Suggestion.notes` holds the human-readable explanations of what happened
during extraction ("No text could be read from the file…"). `as_dict()` did not
include the key, so `templates/documents/review.html` silently rendered nothing
— Django templates resolve a missing dict key to an empty string rather than
raising.

**Fix:** add `"notes": self.notes` to `as_dict()`.

**Lesson:** silent template failures are the hardest kind to notice. A missing
key looks identical to an empty list.

---

## 4. Documents stuck in an office inbox forever

**Files:** `apps/tracking/services.py`, `apps/tracking/views.py`

`inbox_for()`, `in_transit_from()` and the tracking list's `scope` filters
matched *any* routing step with `received_at IS NULL`, ignoring which batch it
belonged to.

When a document went to three offices and only one confirmed receipt before it
was forwarded, the other two kept seeing it in their inbox permanently. Worse,
clicking *Confirm receipt* then failed with "There is nothing to confirm for
your office on this record", because `confirm_receipt()` correctly scopes to
`record.current_batch` while the queues did not.

**Fix:** scope every queue to the current batch.

```python
routing_steps__batch=F("current_batch")
```

**Lesson:** when one function filters by a scope, every function that feeds it
must use the same scope, or the UI offers actions the service will refuse.

---

## 5. Setup scripts failed silently

**Files:** `scripts/start.ps1`, `scripts/start.sh` (new, replacing
`scripts/setup.ps1` and `scripts/setup.sh`)

The original scripts ran `migrate`, `init_db` and `seed_demo` without checking
whether each one succeeded. With PostgreSQL not running, all three failed while
the script reported success — producing a working `.venv` and `.env` alongside
a completely empty database.

**Fix:** new guided scripts that check prerequisites *before* doing any work
(including whether the database server is actually listening on port 5432),
test the database connection before running migrations, check the exit code of
every step, verify the admin account really exists at the end, and stop with a
plain-language message instead of continuing.

**Lesson:** a setup script that cannot fail loudly is worse than no setup
script, because it moves the error far away from its cause.

---

## Checks now available

```bash
python scripts/check_templates.py   # tag balance, includes, {% url %} names
python manage.py check              # Django's own system check
pytest                              # the behavioural rules
```

`check_templates.py` runs without Django installed, so it is usable in CI
before anything is configured.

---

## 6. `.gitignore` deleted an entire Django app from the repository

**File:** `.gitignore`

The line `documents/` was meant to keep uploaded records out of version
control. Git treats a pattern with no leading slash as "match at any depth", so
it also matched `apps/documents/` — the whole Documents app. It was never
committed, so every clone was missing it and Django failed at startup with
`ModuleNotFoundError: No module named 'apps.documents'`.

**Fix:** removed the pattern. `media/` is already ignored and `MEDIA_ROOT`
resolves there, so uploads were covered anyway — the extra line was pure risk.

**Lesson:** in `.gitignore`, anchor directory patterns with a leading slash
(`/media/`) unless you genuinely mean "anywhere in the tree". Verify with:

```bash
git check-ignore -v path/you/expect/to/be/tracked
```

---

## 7. A lost session made a draft impossible to send

**Files:** `apps/tracking/views.py`, `apps/tracking/forms.py`,
`templates/tracking/review.html`

The receiving offices chosen on step 1 of Create New DTS were kept only in the
Django session. If the browser closed, cookies were cleared, or the draft was
reopened days later, step 2 had no offices, `route_record()` raised "Select at
least one receiving office", and the screen offered no way to choose them
again — the draft was permanently stuck.

Separately, `Office.objects.filter(pk__in=ids)` returns rows in database order,
not the order the user picked, while `route_record()` treats the first office
as the one taking custody. The office shown as "1st receiver" on step 1 was not
reliably the office that got custody.

**Fix:** step 2 now renders a real `ReviewRouteForm`, pre-filled from the
session and editable, with a plain-language warning when the session was lost.
The picked order is restored by sorting against the remembered id list.

**Lesson:** the session is a cache, not storage. Any workflow spanning two
requests needs a path back when the cache is empty.

---

## 8. `seed_demo` rolled back accounts when sample records failed

**File:** `apps/core/management/commands/seed_demo.py`

The whole command was one `@transaction.atomic` block, so a failure while
creating *sample records* also undid the *user accounts* created earlier. The
symptom was "login details not working" — several steps away from the cause,
and impossible to diagnose without reading the traceback.

**Fix:** each phase commits independently. Master data and accounts land first;
sample records run inside a `try` that reports failure loudly without taking
the accounts down. Also added `manage.py fix_login`, which checks for missing
accounts, suspended accounts, unusable passwords and django-axes lockouts in
one pass.

**Lesson:** wrap a transaction around the unit that must be consistent, not
around the whole command. Optional demo data does not belong in the same
transaction as the accounts you need to sign in with.

---

## 9. Django version did not support the team's Python

**File:** `requirements.txt`

The project pinned `Django==5.0.9`. Django 5.0 supports Python 3.10–3.12; a
team member was running **Python 3.14**. It happened to work, but it was
unsupported — the kind of thing that produces an inexplicable failure at the
worst possible moment.

**Fix:** moved to `Django>=5.2.8,<6.0`. Django 5.2 is a long-term support
release covering Python 3.10 through 3.14, so every machine on the team works
regardless of which Python was installed, with security updates until 2028.

**Lesson:** check the framework's supported Python versions against what the
team is actually running, not against what you assume they are running.

---

## 10. Exact version pins broke installation

**File:** `requirements.txt`

`psycopg[binary]==3.2.3` had no wheel for Python 3.14. pip refused, printing
the available versions (3.2.10 upward) — and the old setup script then reported
`[ok] Dependencies installed` anyway, because it never checked the exit code.

**Fix:** all dependencies now use ranges (`>=x,<y`). Two are deliberately
capped with the reason in a comment: `django-csp<4.0` because version 4 replaced
every `CSP_*` setting with a single dict, which would silently stop sending
security headers, and `django-axes<7.0` for the same class of reason.

**Lesson:** exact pins guarantee reproducibility on one machine and breakage on
a teammate's. For an application a team installs on mixed environments, ranges
with tested upper bounds are the safer default. Cap a dependency only when you
know what breaks above the cap, and write the reason down.

---

## Tooling added in response to these bugs

| Command | Catches |
|---|---|
| `manage.py selfcheck` | Runtime failures anywhere in create → route → receive → forward → complete → archive → search |
| `manage.py fix_login` | Missing accounts, suspended accounts, unusable passwords, django-axes lockouts |
| `scripts/check_templates.py` | Unbalanced tags, missing includes, bad `{% url %}` names — without Django installed |
| `scripts/start.ps1` / `start.sh` | Missing prerequisites, a stopped database, and any step that fails |

The pattern across all ten bugs is the same: **a failure that reported success**.
A rolled-back transaction that printed a summary, an install script that printed
`[ok]` after an error, a `.gitignore` that quietly dropped an app, a template
that rendered nothing for a missing key. The tooling above exists to make
failures loud.

---

## 11. `makemigrations` crashed: `Could not find manager UserManagerFromUserQuerySet`

**Files:** `apps/accounts/models.py`, `apps/tracking/models.py`, `apps/documents/models.py`

This was introduced by the fix in bug #1. Building a custom manager with
`SomeManager.from_queryset(SomeQuerySet)()` creates an **anonymous** class
inline — Django names it something like `ManagerFromUserQuerySet` internally,
but that name exists nowhere importable. `makemigrations` needs to write the
default manager into the migration file by dotted import path, and an
anonymous inline class has no such path.

The same pattern was already sitting in two other models
(`TrackingRecord.objects` and `Document.objects`) since the first build — they
would have failed the same way the moment migrations were generated for those
apps.

**Fix:** give each manager a real, named class at module level:

```python
class UserManagerFromQuerySet(UserManager.from_queryset(UserQuerySet)):
    pass

class User(AbstractUser):
    objects = UserManagerFromQuerySet()
```

Now the manager has a genuine dotted path — `apps.accounts.models.UserManagerFromQuerySet`
— for `makemigrations` to write into the migration file.

**Lesson:** `Manager.from_queryset()` is fine to call directly on a line like
`objects = Manager.from_queryset(Qs)()` right up until the model needs a
migration — which every model does. Always name the result as a real class.
This is a common enough gotcha that it is called out in Django's own docs for
`from_queryset()`, and worth remembering for any future custom manager.


---

## 12. OCR held a Gunicorn worker and database transaction open

**Files:** `apps/documents/services.py`, `apps/documents/tasks.py`, `templates/documents/review.html`

Scanned uploads called the OCR provider inside `@transaction.atomic`. A 90-second provider timeout could occupy every web worker while an uncommitted document row was invisible to the worker and held database resources.

**Fix:** save the file and a `PENDING` document first, enqueue `extract_document_task` with `transaction.on_commit()`, and keep the synchronous path for installations without background tasks. The review screen polls with HTMX and uses plain language rather than exposing an internal status code.

**Lesson:** network-bound extraction belongs outside the request transaction. A queue is only safe when it cannot see a row before the transaction that created it commits.

---

## 13. A shared campus IP could lock out an office

**Files:** `config/settings.py`, `apps/accounts/axes_hooks.py`

The resolved django-axes 6.5.2 package treats a flat `AXES_LOCKOUT_PARAMETERS` list as independent keys. The previous `username, ip_address` configuration therefore made five failures from a shared NAT address lock the address for everyone.

**Fix:** use the deliberately chosen username-only policy and ensure the custom progressive countdown does not use IP telemetry as an active blocking key. The existing IP rows remain available for operational history and tests, but a colleague behind the same address is not locked by another username.

**Lesson:** lockout scope must be chosen for the network people actually use, not the network diagram one imagines.

---

## 14. Extension-only upload validation accepted disguised content

**Files:** `apps/core/utils.py`, `apps/documents/views.py`, `apps/tracking/views.py`

An HTML payload could be named `memo.pdf` and pass the extension check. Downloads also relied on browser behavior rather than explicitly setting `X-Content-Type-Options: nosniff`.

**Fix:** validate common PDF, Office ZIP, legacy Office, and image signatures with the Python standard library, reject obvious script-bearing text payloads, and set `nosniff` on every application file response.

**Lesson:** filenames are labels supplied by the client. Security validation must inspect bytes and must protect the response path too.

---

## 15. Production could report healthy while its database or storage was broken

**Files:** `apps/core/checks.py`, `apps/core/views.py`, `render.yaml`

The platform probe pointed at the login page, which could return 200 without touching PostgreSQL and also passed through authentication middleware.

**Fix:** add `/healthz/` with database, cache table, migration, storage, and optional worker checks; point Render at it; and add deployment-blocking system checks for insecure defaults.

**Lesson:** a health check must exercise the dependencies that make a request useful, while revealing only a boolean component result to unauthenticated callers.

# Deploying DocTrack on Vercel

Use the existing GitHub repository, `zap1104/udm-doctrack`, and the existing
Vercel project, `binding-of-rae/udm-doctrack`. Do not create a second project to
work around an access error. A plugin response of 403 requires resolving access
to that workspace before it can inspect or change deployments.

## Repository configuration

- Import the repository root, where `manage.py` and `requirements.txt` live.
- Use the Django framework preset. Leave Install Command and Output Directory
  at their framework defaults.
- `.python-version` selects Python 3.12, matching the CI Python version.
- Dependencies remain in `requirements.txt`. Ruff and pytest use `ruff.toml`
  and `pytest.ini`. Do not add a tooling-only `pyproject.toml`: Vercel can select
  it as a dependency manifest and fail with `No project table found`.
- `vercel.json` runs the existing production checks during the build. It does
  not migrate the database, create accounts, seed data, or change permissions.
- Vercel discovers `config.asgi.application` from the existing Django settings
  and collects static files automatically. No catch-all rewrite or new API
  handler is needed.
- `.vercelignore` keeps local credentials, uploaded files, and verification
  output out of source uploads. Keep the production database and uploaded files
  in their external services, not in the deployment bundle.

See [Vercel's Django documentation](https://vercel.com/docs/frameworks/full-stack/django).

## Configure services before building

Add values in the Vercel project's Environment Variables settings. Enter secrets
directly there; do not put them in Git, screenshots, build scripts, or chat.
Use separate credentials, database, and private bucket for Preview deployments.
Do not point previews at the live university database.

| Variable | Production value |
| --- | --- |
| `DJANGO_SECRET_KEY` | Stable random secret of at least 50 characters; preserve an existing valid production secret |
| `DJANGO_DEBUG` | `False` |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated exact deployed hostnames, without schemes; do not use `*` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Corresponding HTTPS origins, including schemes |
| `SITE_BASE_URL` | Canonical public HTTPS origin for account emails and QR links |
| `DATABASE_URL` | Reachable PostgreSQL connection URL, preferably the provider's pooled endpoint |
| `DB_SSLMODE` | `require`, or the provider's stricter verified TLS configuration |
| `DB_CONN_MAX_AGE` | `0` for this ASGI deployment; use the database provider's connection pool |
| `STORAGE_BACKEND` | `s3` for the existing Cloudflare R2 support, or configured Azure storage |
| `ENABLE_CSP` | `True` |
| `ENABLE_AXES` | `True` |
| `SECURE_SSL_REDIRECT` | `True` |
| `TRUST_PROXY_SSL_HEADER` | `True` behind Vercel's HTTPS proxy |
| `ALLOW_DEMO_SEED` | `False` |
| `ENABLE_BACKGROUND_TASKS` | `True` only with the matching worker below running |
| `EMAIL_BACKEND` | `django.core.mail.backends.smtp.EmailBackend` |
| `EMAIL_HOST`, `EMAIL_PORT` | SMTP host and TLS port from your provider |
| `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | Provider-issued SMTP credentials |
| `EMAIL_USE_TLS` | `True` |
| `DEFAULT_FROM_EMAIL` | Provider-verified sending address |

For R2, also configure `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
`AWS_STORAGE_BUCKET_NAME`, `AWS_S3_ENDPOINT_URL`, and `AWS_S3_REGION_NAME=auto`.
Keep the bucket private. Existing Django authorization and signed downloads
continue controlling document access.

Azure uses `AZURE_ACCOUNT_NAME`, `AZURE_ACCOUNT_KEY`, and `AZURE_CONTAINER`.
The default requirements install the S3 storage extra; Azure deployments also
need the documented `django-storages[azure]` dependency instead of the S3 extra.

OCR for scanned files requires the existing `OCR_SPACE_API_KEY` when using
OCR.space. Local text extraction does not require that key. Existing per-file
external OCR consent still applies.

Generate a new secret locally only for a new environment:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(64))"
```

Set it once in the intended environment. Do not generate a different secret at
each build: that would invalidate sessions and signed links.

## Database initialization and background worker

Use a controlled administration or worker environment with the intended
database and storage configured. Back up existing data before a release.
Review and run these commands against that intended database:

```powershell
python manage.py check --deploy --fail-level ERROR
python manage.py migrate --plan
python manage.py migrate --noinput
python manage.py init_db
python manage.py ensure_schedules
```

The PostgreSQL account must be allowed to enable `pg_trgm` and `unaccent`, or a
database administrator must enable them. Create the first administrator using
the existing `create_admin` command. Do not run `seed_demo` on real records.

Run `python manage.py qcluster` as a separate persistent worker using the same
reviewed revision, PostgreSQL database, private storage, and OCR settings as the
web application. A Vercel request or build command is not that worker. The worker
processes extraction and the registered maintenance schedules, including
permanent deletion after the existing 30-day recovery period. Provision it with
the existing stack rather than replacing django-q2 or creating unauthenticated
maintenance endpoints.

## Upload and download limits

Vercel documents a 4.5 MB request and response payload limit for Functions.
The existing DocTrack default permits individual uploads up to 25 MB and
supports multiple files. Those existing capabilities cannot be claimed to work
unchanged through a Vercel Function: multipart overhead and the combined request
size count toward the platform limit. A smaller per-file setting alone does
not solve combined uploads. Large proxied downloads also require verification.

Before enabling the existing full-size workflow for users, implement and test
authorized direct uploads to the private object store with server-validated
finalization, and verify signed direct downloads for every applicable route;
or use a web host that supports the current request sizes. This deployment
configuration does not add that workflow or silently reduce existing limits.
Small-file tests alone are not evidence that the 25 MB workflow works.

See [Vercel Functions limits](https://vercel.com/docs/functions/limitations#request-body-size).

## Verify the exact deployed revision

1. Check the deployment's commit SHA matches the intended GitHub commit.
2. Confirm dependency installation uses `requirements.txt`, Django production
   checks pass, and static collection succeeds.
3. Verify `/healthz/` reports database, cache, migrations, and storage healthy.
   With a worker configured, also check `/healthz/?deep=1` after it processes work.
4. Check HTTPS login, theme assets, office-scoped tracking and repository lists,
   search, reports, and a permitted and refused signed download.
5. Upload a disposable file to the intended test collection, confirm extraction
   completes, and verify it remains readable after another deployment. Verify
   single and multiple uploads near the platform limit as well as existing
   maximum-size requests before calling the upload workflow supported.
6. Use disposable Preview data to verify filing, deletion, recovery, and worker
   maintenance. Preserve existing production records during these checks.

A green build is not proof that external services or all workflows are ready.
Record which of these checks actually ran; do not report simulated local service
checks as a successful Vercel deployment.

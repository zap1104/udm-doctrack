# DocTrack setup and completion checklist

Prepared from the current local source on 15 October 2026. Mark each item
**Already set up / Not set up / Unsure**, name its owner, and record proof.
Record credential presence only. Keep real secrets outside this document and
outside Claude/Gemini chats.

| Item | What to prepare | Required when |
| --- | --- | --- |
| Approved capstone scope | Title/proposal/objectives, rubric, defense date, mandatory AI requirement, team capacity | Always |
| Hosting and budget | Chosen provider/plan/region, web process, separate worker, expected load and continuing cost | Online deployment |
| PostgreSQL | Database connection, SSL, `pg_trgm`/`unaccent`, migrations, connection allowance and durable backups | Always; separate demo/staging/production |
| Private file storage | Choose R2/S3-compatible or Azure Blob; private bucket/container, scoped credentials, originals and migration inventory | Production |
| HTTPS origin | Platform hostname or custom domain/DNS, canonical `SITE_BASE_URL`, allowed hosts and CSRF origins | Online deployment; custom domain purchase is optional |
| Django security | Unique secret, debug off, CSP, secure cookies/proxy settings, demo seeding off, secure admin credentials | Production |
| Worker and schedules | `ENABLE_BACKGROUND_TASKS=True`, running `qcluster`, `ensure_schedules`, queue/worker health and restart ownership | Production background extraction and maintenance |
| OCR | OCR.space key/endpoint/plan, approved external processing, languages, scan samples and quotas | External scanned-document OCR |
| Azure OCR fallback | Document Intelligence resource, endpoint/key, approved budget/data handling | Optional fallback |
| Email | SMTP account/sender, verified sender/DNS where required, TLS/port compatibility, recovery tests | Current production checks require configured email |
| Office data and policy | Official offices, accounts/roles, SLAs, types/tags/metadata, office hours/holidays, deletion decisions and backup disposal policy | Useful office operation and credible defense |
| Backup and recovery | Paired database/object-store backups, protected location, successful restore to another environment, rollback drill | Production |
| Evaluation and defense | Approved/anonymized examples, UAT protocol and results, performance targets, manuals/diagrams/slides, rehearsed demo and fallback | Defense |
| Release and support | Reviewed revision, passing CI, verified deployed commit, monitoring/logs, support owner and incident/update runbook | Production |

## Important gaps confirmed in the current files

- `render.yaml` defines only a free web service and database. It does not define
  a worker or enable background tasks. `Procfile` having a worker entry does not
  provision one in that blueprint.
- That blueprint includes `preDeployCommand`; current Render documentation
  limits that step to paid web services, private services and workers. Review
  the chosen plan and migration strategy before deployment.
- Render's free web services block ordinary SMTP ports 25/465/587. Its free
  PostgreSQL databases expire after 30 days. These constraints conflict with
  treating the current free blueprint as a durable office deployment.
- The default storage dependencies support S3/R2. Selecting Azure requires the
  matching Azure storage extra and configuration.
- `AIEngine` in `apps/documents/suggestions.py` currently falls back to rules.
  Gemini/Claude API keys are not prerequisites for the current system. A real
  model becomes required only if the approved capstone scope requires it.
- Source and dated local test results are available. Real provider delivery,
  cloud-storage operation, paired restore, actual hosting limits and manual
  screen-reader operation still need appropriate verification.
- Review older documentation for automatic archiving, Apply buttons, suggested
  backup features, model claims and demo reset instructions. Current filing
  requires administrator approval, and current dropdowns update through HTMX.
- The latest code changes remain local and uncommitted. Confirm the exact
  reviewed revision reaches the selected deployment before claiming release.

## Existing configuration names to collect

| Area | Existing variables |
| --- | --- |
| Application | `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `SITE_BASE_URL`, `TIME_ZONE`, `ENABLE_CSP`, `ALLOW_DEMO_SEED`, trusted-proxy settings |
| Database | `DATABASE_URL` or `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`; `DB_SSLMODE` |
| R2/S3 | `STORAGE_BACKEND=s3`, `AWS_STORAGE_BUCKET_NAME`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_S3_ENDPOINT_URL`, `AWS_S3_REGION_NAME` |
| Azure storage | `STORAGE_BACKEND=azure`, `AZURE_ACCOUNT_NAME`, `AZURE_ACCOUNT_KEY`, `AZURE_CONTAINER` |
| OCR | `OCR_BACKEND`, `OCR_SPACE_API_KEY`, `OCR_SPACE_ENDPOINT`, timeout/retry settings; optional `AZURE_DOCINT_ENDPOINT`, `AZURE_DOCINT_KEY` |
| Email | `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `DEFAULT_FROM_EMAIL` |
| Background jobs | `ENABLE_BACKGROUND_TASKS`, `Q_WORKERS`; process/schedule configuration |

An app upload limit is not a provider allowance. OCR.space currently documents
1 MB and 3 PDF pages for its free API tier; the sample app setting permits
25 MB uploads. Plan validation and clear handling of unsupported scans are
necessary. Digital files with usable text layers can still be extracted locally.

## Recommendations

1. Confirm the approved AI requirement first, then agree on separate defense
   and production milestones. This determines whether model work is essential.
2. Choose a hosting plan that supports the existing web, database, worker and
   email design before buying API subscriptions or changing code.
3. Set up one private storage provider, one compatible email service, and OCR
   only where needed. Validate them in staging with non-sensitive samples.
4. Prove a paired backup/restore and rehearse the demo from a clean environment.
   Keep real records and production credentials out of demonstration resets.
5. Send the same review prompt and evidence to both assistants. Compare their
   responses against source and acceptance checks; merge overlapping tasks and
   reject unsupported feature expansion.

## Provider references checked on 15 October 2026

- [Render deployment steps](https://render.com/docs/deploys): pre-deploy availability.
- [Render free-service limits](https://render.com/docs/free): SMTP restrictions,
  database expiry and ephemeral filesystem.
- [Render background workers](https://render.com/docs/background-workers):
  separate continuous worker service.
- [OCR.space API](https://ocr.space/ocrapi): API keys, supported tiers and file/page limits.
- [R2 S3 compatibility](https://developers.cloudflare.com/r2/api/s3/api/):
  provider-specific API capabilities; verify backup features rather than
  assuming full AWS S3 parity.

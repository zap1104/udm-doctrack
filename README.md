# UDM DocTrack

UDM DocTrack is a records and document management system for the Office of the Vice President for Administration (OVPA) at Universidad de Manila. It tracks documents as they move between offices and keeps filed records searchable.

## What the system does

| Area | Workflow |
| --- | --- |
| **OVPA Tracking** | Create slips, endorse documents to one or more offices, confirm receipt, record work and follow custody, deadlines and turnaround. Completed work can be submitted for filing. |
| **External offices** | Use a separate Tracking and Repository collection for UDM offices without DocTrack accounts. Authorized internal users record external actions and corrections. Internal recipients confirm their own receipts. |
| **Repository** | File completed slips or upload and scan documents directly. Review document types, tags and other metadata. Approved repository removals have a 30-day recovery period before permanent cleanup. |
| **Files** | Preserve original formats, preview supported files and upload revisions during Tracking. Each file retains up to five available versions, with audit information for older revisions. Filing locks file contents. |
| **Search and document processing** | Search metadata and permitted extracted text by keyword, identifier or filter. Optional OCR handles eligible scans. Relevance scores indicate match strength. Staff can review suggested metadata and tags. |
| **Dashboard and reports** | Review office-scoped queues, status and overdue counts, turnaround and repository activity. OVPA and External totals are separate. Filtered exports are available to authorized users. |
| **Access and administration** | Sign in with a password and an emailed code. Administrators manage office roles, per-user restrictions and read sharing. File access, notifications and audit history follow record permissions. |

## Get started

Use Python [3.12](.python-version), PostgreSQL 14 or later, and Git. Node.js is needed for JavaScript checks. See [`.node-version`](.node-version) for the CI version.

```powershell
git clone https://github.com/zap1104/udm-doctrack.git
Set-Location udm-doctrack
.\scripts\start.ps1
```

Follow the [local setup guide](docs/SETUP.md) to configure a development database and create your own administrator account. For hosted use, configure a dedicated PostgreSQL database, private file storage and working email delivery. Keep deployments that use real institutional records under university access and privacy controls.

## Documentation

| Guide | Covers |
| --- | --- |
| [Setup](docs/SETUP.md) | Local environment, database and first sign-in |
| [Operations](docs/OPERATIONS.md) | Deployment checks, private storage, background jobs, backups and recovery |
| [Email OTP and permissions](docs/EMAIL_OTP_AND_PERMISSIONS.md) | Enrollment, account recovery, permission restrictions and sharing |
| [External offices](docs/EXTERNAL_OFFICES.md) | External slips, custody, manual entries and filing |
| [Files and versions](docs/file-lifecycle.md) | Saved uploads, previews, version history and repository file locks |
| [Metadata and document types](docs/METADATA_GUIDE.md) | Cataloging and [multiple document types](docs/MULTIPLE_DOCUMENT_TYPES.md) |
| [Search](docs/SEARCH_DESIGN.md) | Find records and interpret relevance |
| [Document processing](docs/document-intelligence.md) | Text extraction, OCR, coverage and review |
| [Turnaround](docs/TURNAROUND_TIME.md) | Working hours, receipt waits and report calculations |
| [Deletion review](docs/DELETION_REVIEW.md) | Review, approval, recovery and cleanup |
| [Git workflow](docs/GITHUB_GUIDE.md) | Working on a branch and submitting a pull request |

## Development

DocTrack uses Django, PostgreSQL, Bootstrap and HTMX. Django-Q schedules background work. The main application areas are in [`apps/`](apps/), with templates in [`templates/`](templates/), static assets in [`static/`](static/) and tests in [`tests/`](tests/).

Run tests with a disposable PostgreSQL database and synthetic records.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
```

See the [security policy](SECURITY.md) for private reporting. Keep credentials, institutional records and detailed audit evidence outside the public repository.

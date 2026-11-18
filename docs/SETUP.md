# Local setup

Use Python 3.12, PostgreSQL 14 or later, and Git. Use a separate PostgreSQL database with a dedicated development user. Keep development and demo data on your own machine and use synthetic records.

## Windows PowerShell

```powershell
git clone https://github.com/zap1104/udm-doctrack.git
Set-Location udm-doctrack
.\scripts\start.ps1
```

The guided script handles the virtual environment, database checks, migrations and static assets. Point it at your development database. Decline the sample-data prompt if you want an empty installation.

## Set up manually

1. Create a local PostgreSQL database owned by a dedicated development role. Choose a unique password for that role.
2. Create and activate a Python virtual environment. Copy `.env.example` to `.env`. Set a fresh `DJANGO_SECRET_KEY`, your local database connection and the local development settings. Keep `.env` private.
3. Install the dependencies, then run the following with the virtual environment active.

   ```text
   python -m pip install -r requirements.txt
   python manage.py migrate
   python manage.py init_db
   python manage.py createsuperuser
   python manage.py runserver
   ```

Open <http://127.0.0.1:8000/>. The administrator command prompts for a new password. Add the offices and users you need through Administration.

## First sign-in

Email codes are required by default. Configure mail delivery and verify the administrator's address using the [email enrollment guide](EMAIL_OTP_AND_PERMISSIONS.md). For a private, loopback-only development instance, that guide explains temporary setup mode. Keep it off public deployments.

Keep any sample accounts confined to a disposable development database. Use unique credentials before granting someone access to an installation. Keep production demo seeding disabled.

## Common setup problems

| Symptom | Check |
| --- | --- |
| Python package import fails | Activate the virtual environment and install `requirements.txt` in that environment. |
| PostgreSQL refuses the connection | Check that the server is running and that the development host, port and credentials match `.env`. |
| A table is missing | Verify the selected database before running migrations. |
| Search extensions are unavailable | Use `python manage.py init_db --dry-run` and ask the database administrator to enable the listed extensions in the intended database. |
| Sign-in stops at email verification | Check [enrollment and account recovery](EMAIL_OTP_AND_PERMISSIONS.md) and verify actual email delivery. |
| The layout has no styling | Confirm static assets are available. For hosted deployments run `collectstatic` as part of the build. |

For hosted use, follow the [operations guide](OPERATIONS.md). Back up the database and file storage before updating an existing installation.

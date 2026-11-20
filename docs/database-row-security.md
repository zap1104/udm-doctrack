# PostgreSQL access for DocTrack

Django signs users in and verifies their email codes. On each request, the optional web database login applies row policies using the verified account ID. An office selected in the URL has no effect on the database identity.

## Before changing the web login

1. Back up the database and rehearse on its staging equivalent. Keep the migration owner and the maintenance worker on separate server-side connections. The Render deployment workflow includes a migration step. Arrange an owner-only migration step outside the web service before replacing its connection. Keep owner and worker credentials out of the web process.
2. Have the database administrator create a new password-authenticated login named `doctrack_web` in the target cluster. Use the provider's secret-management facility. Grant no role memberships, object ownership, schema creation, database creation, superuser, replication or RLS bypass. Give the worker access to the full set of storage references before enabling physical cleanup.
3. Run `python manage.py migrate` with the migration owner. PostgreSQL installations with Supabase client roles also remove their direct access to Django tables, explicit column grants and owned sequences after migrations. Keep the application schema out of public Data API exposure, or configure the DDL owner's default privileges to deny client grants to newly created tables and sequences. Shared databases need a separate review of defaults for other applications.
4. With the same owner, run `python manage.py install_request_rls --role doctrack_web`. This installs policies on Tracking, Repository, their routing and file history, extraction data, saved uploads and per-account preference tables. Repeat after changes to those tables. Review any other policies that apply to the web login. The separate worker must retain its intended access after installation.
5. In staging, set the web service's `DATABASE_URL` to the restricted login and set `DOCTRACK_WEB_ROLE=doctrack_web` on the same release. Verify the provider's connection pool preserves the database login and transaction-local settings. Test OTP login, inter-office routing, file previews, repository filing, cleanup and a worker job. Check both an allowed office and an unrelated office with the same browser and connection pool.
6. Schedule the production credential switch only after staging passes. If a release fails its checks, stop serving through the affected connection and roll back the web configuration through the agreed operator process. Preserve the public grant revocations.

## Protection boundary

The policies filter rows for the trusted Django backend. Django still checks action-specific permissions, enforces custody transitions, hides restricted fields on otherwise visible rows, and authorizes file downloads. Authentication infrastructure, queue management, and administrator audit screens continue to rely on Django. The database login is a server secret with access to application infrastructure. Giving it to a browser, SQL client or untrusted code would allow that client to choose the request identity.

For a staging regression run, use a disposable PostgreSQL database and run `pytest tests/test_request_row_security.py`. The policy installation tests need a test database administrator to create temporary restricted roles.

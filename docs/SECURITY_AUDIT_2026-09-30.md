# UDM DocTrack security audit — 30 September 2026

Inspected the current repository and implemented the fixes described below. SAFE means the specific inspected control passed source review/tests; it is not a claim that the whole deployment is secure. External configuration is explicitly marked unverified. Statuses below describe the working tree after fixes. No production environment or real external mail/storage provider was tested.

## Checklist results

| # | Control | Status |
|---|---|---|
| 1 | Protect admin routes | SAFE after fixes |
| 2 | Enforce server-side access control | NEEDS IMPROVEMENT |
| 3 | Row-level security | NEEDS IMPROVEMENT |
| 4 | Verify user email addresses | SAFE in tested code; delivery unverified |
| 5 | Securely hash passwords | SAFE in inspected account flows |
| 6 | Authentication tokens in local storage | SAFE for inspected browser code |
| 7 | Keep secrets server-side | SAFE for inspected application paths |
| 8 | Exclude env files and secrets from GitHub | SAFE for checked exclusion rules; scan has limits |
| 9 | Never write secrets to logs | NEEDS IMPROVEMENT outside application-controlled logs |
| 10 | Parameterized SQL | SAFE for inspected SQL paths |
| 11 | Validate and sanitize form inputs | NEEDS IMPROVEMENT |
| 12 | Protect against XSS | SAFE for reviewed rendering; CSP can improve |
| 13 | Validate and restrict uploads | NEEDS IMPROVEMENT |
| 14 | Verify webhook signatures | MISSING — not applicable to current routes |
| 15 | Rate-limit sensitive requests | NEEDS IMPROVEMENT at deployment edge |
| 16 | Restrict CORS | SAFE for current same-origin endpoints |
| 17 | Disable debug in production | SAFE in repository deployment path; live state unverified |
| 18 | Dependency vulnerabilities and outdated packages | NEEDS IMPROVEMENT in upgrade discipline |
| 19 | Overall codebase security scan | NEEDS IMPROVEMENT — residual findings remain |

## 1. Protect admin routes — SAFE after fixes

Code: [apps/core/admin_site.py:8](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/core/admin_site.py:8>), [apps/core/mixins.py:48](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/core/mixins.py:48>), [apps/accounts/views.py:220](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/accounts/views.py:220>).

The application administration screens use role gates and office-scoped querysets. The raw Django admin previously relied on Django staff/model permissions; a staff account with broad model permissions could escape application office scope. The new admin site also requires `is_system_admin`. Office administrators can no longer edit or reset staff, superuser, or system-admin accounts even if those accounts share their office. Regression tests cover both boundaries.

**Safest practical next step:** Keep raw admin limited to trusted system administrators; add MFA and restrict network access before exposing it externally.

## 2. Enforce server-side access control — NEEDS IMPROVEMENT

Code: [apps/tracking/views.py:60](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/tracking/views.py:60>), [apps/documents/views.py:39](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/documents/views.py:39>), [apps/documents/services.py:157](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/documents/services.py:157>), [apps/tracking/services.py:585](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/tracking/services.py:585>), [apps/documents/services.py:289](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/documents/services.py:289>).

HTTP read/write endpoints use login mixins, record visibility checks, custody checks, and object permissions. Fixed repository ownership transfer through crafted form submissions, uploader-based edits after a downgrade to viewer, duplicate-title disclosure across offices, and re-sharing by a user with only a viewing grant. Repository upload/edit/add-file services now enforce permissions as well. Some tracking services still rely on their view/caller for custody authorization; for example, `complete_record` checks viewer status but does not itself check `can_user_act`, and `archive_tracking_record` documents reliance on its authorized wrapper. No exposed HTTP bypass was found in the inspected call paths, but a future API or job could accidentally bypass those checks.

**Safest practical next step:** Add the same actor/custody checks to every tracking mutation and archive service, with explicit trusted job pathways and regression coverage for existing seed/workflow callers. Do not loosen view gates to compensate.

## 3. Row-level security — NEEDS IMPROVEMENT

Code: [apps/tracking/models.py:196](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/tracking/models.py:196>), [apps/documents/models.py:87](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/documents/models.py:87>), [apps/search/services.py:124](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/search/services.py:124>).

Row visibility is enforced in application querysets for tracking, repository, search, reports, and downloads. No PostgreSQL ENABLE ROW LEVEL SECURITY or CREATE POLICY statements were found in repository migrations. Fixed `RESTRICTED` repository visibility: office membership and routing history no longer grant implicit access; uploader, system administrators, and explicit grants retain access. Database owner privileges and policies on the deployed database were not inspected.

**Safest practical next step:** Use a least-privilege runtime database role. If database RLS is required, design it around office/user grants, system-admin access, and background jobs, then test tenant context and connection reuse before enabling it. An office_id-only policy would break legitimate routed/shared access.

## 4. Verify user email addresses — SAFE in tested code; delivery unverified

Code: [apps/accounts/models.py:230](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/accounts/models.py:230>), [apps/accounts/email_verification.py:4](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/accounts/email_verification.py:4>), [apps/accounts/views.py:88](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/accounts/views.py:88>), [apps/accounts/forms.py:34](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/accounts/forms.py:34>).

Email ownership verification was MISSING. Implemented a profile verification request, expiring account-bound tokens, CSRF-protected POST confirmation, and single-use invalidation. Tokens survive sign-in but become invalid after password/address changes or verification. Password reset now selects only verified addresses. Existing accounts remain able to sign in, but must verify their address before email recovery. SMTP delivery itself was not exercised against a real provider.

**Safest practical next step:** Apply the accounts migration, configure SMTP and an explicit HTTPS SITE_BASE_URL, and have existing users verify their addresses. Test delivery and link handling in the deployed environment.

## 5. Securely hash passwords — SAFE in inspected account flows

Code: [config/settings.py:293](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/config/settings.py:293>), [apps/accounts/forms.py:91](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/accounts/forms.py:91>), [apps/accounts/management/commands/create_admin.py:29](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/accounts/management/commands/create_admin.py:29>), [apps/accounts/management/commands/fix_login.py:83](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/accounts/management/commands/fix_login.py:83>).

Argon2 is the preferred hasher when installed, followed by Django password hashers. Creation/reset/change flows use Django UserCreationForm, SetPasswordForm, or set_password rather than storing plaintext. Production checks now reject absence of the preferred Argon2 hasher. Bootstrap administrator creation validates password strength and forces a first-use change. Production repair refuses the public demo password and bulk resets without a specific account. Actual deployed database password hashes were not dumped or inspected.

**Safest practical next step:** Keep Argon2 installed and upgrade password hashes naturally on login. Use unique credentials; avoid passing operational passwords in shell history where possible.

## 6. Authentication tokens in local storage — SAFE for inspected browser code

Code: [static/js/theme-init.js:18](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/static/js/theme-init.js:18>), [static/js/doctrack.js:1052](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/static/js/doctrack.js:1052>), [config/settings.py:377](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/config/settings.py:377>).

The localStorage reads/writes found in site JavaScript store the theme preference, not authentication tokens. Authentication uses server-backed Django sessions and HttpOnly cookies. CSRF tokens come from the DOM; the CSRF cookie is now HttpOnly too. No browser JWT, bearer-token store, or custom token API was found.

**Safest practical next step:** Preserve cookie/session authentication. Do not introduce browser storage of bearer tokens when adding APIs.

## 7. Keep secrets server-side — SAFE for inspected application paths

Code: [config/settings.py:103](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/config/settings.py:103>), [config/settings.py:639](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/config/settings.py:639>), [apps/documents/extraction.py:201](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/documents/extraction.py:201>).

Database, email, storage, and OCR credentials are loaded in server settings; provider calls happen in Python. No corresponding credential interpolation into templates or site JavaScript was found. Development placeholders and the demo password are public examples, not production credentials. Production now refuses a default or short signing key during settings initialization.

**Safest practical next step:** Configure real credentials through deployment secrets, rotate them if exposure is discovered, and keep external OCR opt-in for sensitive records.

## 8. Exclude env files and secrets from GitHub — SAFE for checked exclusion rules; scan has limits

Code: [.gitignore:3](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/.gitignore:3>), [.gitignore:5](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/.gitignore:5>).

Git ignores .env, .env.backup and .env.* except .env.example, plus key/PEM files. git ls-files and reachable-history filename checks found only .env.example among env/key paths. A local scan of 1,288 reachable historical text objects found zero matches for private-key blocks and GitHub/AWS/Google/Stripe credential patterns. This is not an entropy scan, does not cover every credential format, and cannot inspect deleted/unreachable objects or external forks.

**Safest practical next step:** Enable GitHub secret scanning/push protection where available and run a dedicated history scanner such as Gitleaks in CI. Rotate any confirmed leaked credential; deleting the file alone is insufficient.

## 9. Never write secrets to logs — NEEDS IMPROVEMENT outside application-controlled logs

Code: [apps/core/security.py:30](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/core/security.py:30>), [config/settings.py:662](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/config/settings.py:662>), [apps/core/management/commands/seed_demo.py:383](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/core/management/commands/seed_demo.py:383>), [apps/accounts/management/commands/fix_login.py:157](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/accounts/management/commands/fix_login.py:157>).

The demo seed printed its password; that output was removed. Added redaction for configured secret values, credential-bearing URLs, token/password assignments, and verification/reset paths in application/request/server console formatters. Provider failure messages returned to users are now generic for external OCR failures. Production console email is rejected because it prints recovery/verification links. Proxy, cloud-provider, SMTP, task-worker, and retained historical logs are not verifiable from the repository.

**Safest practical next step:** Configure upstream access logs to redact account tokens and query credentials; restrict log access/retention and inspect existing logs securely. Rotate credentials if old logs exposed them.

## 10. Parameterized SQL — SAFE for inspected SQL paths

Code: [apps/search/services.py:158](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/search/services.py:158>), [apps/core/management/commands/init_db.py:17](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/core/management/commands/init_db.py:17>), [apps/core/views.py:2168](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/core/views.py:2168>).

Request-driven search/filter operations use Django ORM expressions and parameterized queries. Raw cursor statements found are constant health/extension probes or management/migration DDL based on fixed/configured identifiers, not interpolated HTTP values. No request-controlled raw/extra SQL path was found.

**Safest practical next step:** Keep request values in ORM expressions or separate SQL parameter lists. Quote identifiers with database backend helpers if management code begins accepting user-supplied names.

## 11. Validate and sanitize form inputs — NEEDS IMPROVEMENT

Code: [apps/accounts/forms.py:64](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/accounts/forms.py:64>), [apps/documents/forms.py:104](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/documents/forms.py:104>), [apps/core/forms.py:50](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/core/forms.py:50>), [apps/documents/suggestions.py:320](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/documents/suggestions.py:320>).

Django forms validate roles, model choices, dates, field lengths, and selected office boundaries; server service checks backstop repository ownership. Added a file-count limit and bounded office lookup text. Output escaping, not stripping arbitrary markup from every field, protects display. Administrator-defined regex tag rules previously had no runtime timeout. They now use the regex engine in compatibility mode with a 50ms timeout; invalid or timed-out patterns fail closed. A pathological-pattern regression test covers this denial-of-service fix. Search/form limits and service authorization are not uniformly centralized.

**Safest practical next step:** Validate patterns before saving, monitor timeout frequency, and set bounded search/metadata inputs. Preserve the enforced regex timeout. Add malformed/oversized-input regression cases for remaining filter parsers.

## 12. Protect against XSS — SAFE for reviewed rendering; CSP can improve

Code: [apps/search/services.py:357](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/search/services.py:357>), [apps/core/utils.py:300](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/core/utils.py:300>), [apps/core/templatetags/doctrack.py:257](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/core/templatetags/doctrack.py:257>), [config/settings.py:431](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/config/settings.py:431>).

Templates normally autoescape. The search snippet rendered with |safe is produced by _highlight, which escapes every text segment and match before adding mark tags. QR SVG is generated by segno with escaped labels. The relevance bar now uses format_html with a clamped numeric width. Reviewed innerHTML uses only clearing or fixed literal markup; dynamic labels use textContent/DOM nodes. CSP rejects arbitrary inline scripts when enabled and production checks require it. Style unsafe-inline and broad CDN origins remain permitted; vendor assets are not checked into this checkout.

**Safest practical next step:** Self-host versioned Bootstrap/HTMX assets or add integrity attributes; narrow CSP origins and remove unsafe-inline styles during a tested frontend cleanup.

## 13. Validate and restrict uploads — NEEDS IMPROVEMENT

Code: [apps/core/utils.py:87](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/core/utils.py:87>), [config/settings.py:517](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/config/settings.py:517>), [apps/core/forms.py:50](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/core/forms.py:50>), [apps/documents/views.py:426](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/documents/views.py:426>), [config/urls.py:6](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/config/urls.py:6>).

Extension and byte-size checks, file signatures, and Office package structure were present. Added maximum decompressed size/member count, rejection of encrypted archives, unsupported content validators and colon filenames, and a ten-file request limit. Removed unrestricted DEBUG media serving. Download endpoints require login/object permission and send attachments with nosniff and no-store. External OCR is now off by default for uploads. Signature checks are not malware detection; legacy Office files, PDF parser workloads, macro payloads, and compressed PDF/image expansion can remain hazardous. Application file limits occur after some request-body handling.

**Safest practical next step:** Enforce request-body/file-count limits at the proxy, quarantine and malware-scan uploads before user download, isolate expensive parsing in bounded workers, and verify private cloud bucket/container permissions. Keep attachment downloads behind authorization.

## 14. Verify webhook signatures — MISSING — not applicable to current routes

Code: [config/urls.py:6](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/config/urls.py:6>), [apps/accounts/urls.py:8](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/accounts/urls.py:8>), [apps/documents/urls.py:7](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/documents/urls.py:7>), [apps/tracking/urls.py:7](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/tracking/urls.py:7>).

No webhook endpoint or inbound provider callback was found. OCR integrations send outbound requests. There is therefore no current webhook signature implementation to verify and no unverified webhook route discovered.

**Safest practical next step:** If a webhook is added, verify the provider signature over the untouched request body, check timestamp freshness and replay IDs, and reject before side effects. Do not invent a generic shared-secret callback now.

## 15. Rate-limit sensitive requests — NEEDS IMPROVEMENT at deployment edge

Code: [config/settings.py:388](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/config/settings.py:388>), [apps/core/security.py:13](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/core/security.py:13>), [apps/core/middleware.py:134](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/core/middleware.py:134>), [apps/accounts/views.py:63](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/accounts/views.py:63>).

Axes supplies per-account login lockout. Replaced the password-reset cache read/set race with atomic PostgreSQL row-locked counters. Added reset-IP/reset-email, verification, login-IP, password-change, and upload/re-extraction limits shared across workers. Counters retain hashed identities, not raw submitted addresses. A six-thread test verifies that only two concurrent attempts pass a limit of two. Counts expire; a pruning task is provided but is not scheduled automatically. Upload attachments, search, health probes, and distributed attacks still need resource/edge limits. Public health diagnostics were restricted to system administrators; the anonymous probe now performs only a cheap database check.

**Safest practical next step:** Configure trusted proxy IP handling only where forwarding headers are overwritten, add edge rate/body limits, and schedule prune_security_throttles. Tune limits to legitimate campus traffic and monitor rejects.

## 16. Restrict CORS — SAFE for current same-origin endpoints

Code: [apps/accounts/axes_hooks.py:279](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/accounts/axes_hooks.py:279>), [config/settings.py:112](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/config/settings.py:112>).

No general CORS middleware or cross-origin API policy is configured. Removed the lockout JSON response code that reflected the django-axes CORS setting into Access-Control-Allow-Origin. Current session endpoints operate same-origin; CSRF trusted origins are an explicit env list and are distinct from CORS. No credentialed wildcard CORS route was found.

**Safest practical next step:** Keep cross-origin access disabled unless an actual client needs it. If added, use exact origin allowlists and verify credentials/preflight behavior.

## 17. Disable debug in production — SAFE in repository deployment path; live state unverified

Code: [config/settings.py:104](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/config/settings.py:104>), [apps/core/checks.py:11](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/core/checks.py:11>), [render.yaml:8](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/render.yaml:8>), [Procfile:3](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/Procfile:3>).

DEBUG now defaults off. The development example can still explicitly enable it. Deployment checks reject DEBUG, weak/default keys, wildcard hosts, disabled CSP/lockout, missing Argon2, demo seeding, console/insecure email, and unsuitable storage. Render and the release command now run those checks before deployment. The blueprint uses private-cloud storage configuration and explicit host/email settings instead of broad .onrender.com hosts and temporary local storage. Actual deployed environment values, TLS termination, and platform execution of release hooks remain unverified.

**Safest practical next step:** Run check --deploy against the real production environment and verify headers/cookies/host rejection over HTTPS. Do not bypass release checks with a manually configured startup command.

## 18. Dependency vulnerabilities and outdated packages — NEEDS IMPROVEMENT in upgrade discipline

Code: [requirements.txt:12](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/requirements.txt:12>), [.github/workflows/ci.yml:94](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/.github/workflows/ci.yml:94>).

Fresh pip-audit of resolved requirements inspected 33 packages and found no known vulnerabilities. The audit environment uses Django 5.2.17; raised the allowed minimum to that current 5.2 LTS patch. Removed || true so CI dependency vulnerabilities fail the job. pip list --outdated found newer releases for boto3/botocore, charset-normalizer, filelock, msgpack, tooling, and newer major versions of axes, CSP, gunicorn and Django. Newer major does not imply a vulnerability; Django remains on the required supported 5.2 LTS line. The actual local project environment was also inspected: it originally contained unsupported Django 5.0.9, vulnerable pypdf 5.0.1, python-dotenv 1.0.1, sqlparse 0.5.5 and pytest 8.3.3. Django and compatible dependencies were updated. Raised minimum patched versions for these packages, including sqlparse explicitly and pytest in requirements-dev.txt, so existing installations cannot retain affected versions. The final installed-environment audit found no known vulnerabilities; this is still not a production inventory. Remote Bootstrap/HTMX assets are outside pip-audit coverage.

**Safest practical next step:** Refresh compatible patches and test them; plan supported-major migrations for security middleware/server packages separately. Add reproducible dependency constraints and frontend dependency/integrity scanning. Do not blindly upgrade CSP without its required configuration rewrite.

## 19. Overall codebase security scan — NEEDS IMPROVEMENT — residual findings remain

Code: [tests/test_security_audit.py:1](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/tests/test_security_audit.py:1>), [apps/core/checks.py:8](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/apps/core/checks.py:8>).

Reviewed app/config/template/browser/management code, scanned reachable Git history for five high-confidence secret formats, ran Bandit over apps/config/scripts, audited dependencies, and ran targeted authorization/session/security regressions plus workflow/page checks. Bandit reports were manually reviewed rather than treated as proof of vulnerabilities: generated QR mark_safe uses escaped labels; urlopen uses a configured HTTPS endpoint; demo PRNG is not used for security tokens. Residual issues below remain. This was a source/configuration audit and local testing, not a penetration test of a deployed service.

**Safest practical next step:** Treat the prioritized checklist below as release follow-up. Re-run all CI checks on the exact revision when preparing a push, then verify deployment settings and external controls against the running site.

## Additional findings and prioritized remaining checklist

### CRITICAL

- No confirmed unresolved critical exploit was established by this audit. If a deployed bucket or web-server media alias is public, private-document access could bypass the fixed application controls: verify that external configuration before release.
- Direct development media exposure was removed. Apply these changes and schema migrations before relying on the fixes.

### HIGH

- Verify production bucket/container privacy, proxy request-body limits, HTTPS/proxy-header trust, database/network access, real signing key, SMTP transport, and DEBUG=False. Repository settings cannot prove the deployed service has these controls.
- Add quarantine/malware scanning and bounded parser workers for user uploads; file signatures and Office ZIP limits are insufficient to certify files as safe.
- Add MFA for privileged administrators and preferably restrict raw admin network access. No MFA flow or dependency was found.

### MEDIUM

- Complete service-layer authorization for tracking mutations and archive functions so future API/job callers cannot bypass view checks.
- Consider PostgreSQL RLS after designing office grants, routed access, system administrators and background-job context; verify least-privilege DB roles now.
- Strengthen CSP/frontend supply-chain controls: self-host assets or use SRI and narrower script origins.
- Protect public healthz/search and remaining processing endpoints at the edge. Public health diagnostics were fixed: component readiness and deeper checks now require a system administrator; anonymous probes expose only status and run a cheap database query.
- Review proxy/cloud/task logs for historic recovery links or credentials; redact account-token paths upstream. Application formatter tests do not prove third-party logs are clean.
- Clarify shared tag vocabulary/privacy: repository/tag/autocomplete choices expose shared tag names; only allow sensitive names if catalog visibility is intentionally authorized. No document title leakage was found in the scoped title autocomplete.
- Refresh compatible package patches and plan tested major upgrades. Add frontend vulnerability scanning and reproducible constraints.

### LOW

- Administrator regex matching now has a tested 50ms timeout. Validate saved patterns and monitor timeout failures to preserve reliable tagging.
- Schedule apps.core.tasks.prune_security_throttles daily; the helper exists but no schedule was configured by this audit.
- Address the existing Django 6.0 CheckConstraint.check deprecation during a future migration cleanup. These warnings do not fail the current Django 5.2 tests.
- Replace command-line password arguments with secure prompts/environment input for operational CLI workflows to reduce shell-history/process-list exposure.
- Add broader credential/entropy history scanning and GitHub secret push protection; the custom historical pattern scan is deliberately limited.

## Validation and operational notes

- 204 targeted existing/new security, authorization, account, session, search and deployment regressions passed. Final focused reruns cover subsequent changes; see the final validation note appended below.
- All 21 workflow self-checks passed. Page smoke checks requested 244 pages as four roles with no page errors.
- Ruff, all 75 templates, Django checks, migration drift check, collectstatic and production deployment checks passed in isolated settings. Production checks used synthetic credentials, not a real bucket/SMTP connection.
- Dependency audit: 33 resolved packages; no known vulnerabilities reported by pip-audit at audit time.
- Git history: 1,288 reachable text objects; zero matches for five high-confidence credential/private-key pattern families. This cannot establish that every secret is absent.
- Bandit reported 18 findings (15 low, 3 medium, zero high); generated SVG, configured HTTPS requests and demo-only constants were manually reviewed. This does not prove the absence of other defects.
- Bandit findings and dependency/outdated/history JSON evidence are kept in the local temporary audit folder; no source or secret values were uploaded to a scanning service.
- Local schema inspection confirmed both new migrations were already applied. Other environments must install updated requirements and apply migrations; no production migration was performed.
- New migrations: accounts/0007_user_verified_email.py and core/0009_securitythrottle.py. Existing addresses are not assumed verified. Email recovery requires address verification; ordinary sign-in remains available.
- Restricted documents now honor explicit-grant semantics. An office user with only implicit membership/history access loses access to a RESTRICTED document until explicitly granted it; uploader and system administrators retain access.
- Demo seeding requires ALLOW_DEMO_SEED=True outside DEBUG. CI explicitly enables it only for isolated demo/test data and disables it for deployment checks.
- External OCR defaults off for new uploads; users may explicitly opt in. Tracking archives were already opted out by default.
- No commit or push was requested for this audit. Changes remain in the working tree.

## Primary security guidance

[Django 5.2 security documentation](https://docs.djangoproject.com/en/5.2/topics/security/) supports the ORM, CSRF, host, proxy, session and uploaded-content recommendations.
[OWASP File Upload Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html) supports layered validation, decompressed-size limits, quarantine, and storage isolation.
[Django supported releases](https://www.djangoproject.com/download/) lists 5.2.17 as the current 5.2 LTS patch at the time of review.

## Final verification after dependency updates

- The actual project virtual environment passed **100 focused security, deployment, permissions, viewer-role and search tests** after compatible dependency upgrades. Two existing Django 6 deprecation warnings remain.
- The final installed-environment dependency audit checked **69 packages**, including development tools: **zero known vulnerabilities** reported. The earlier resolved production-requirements scan checked 33 packages with zero known vulnerabilities.
- Actual project environment: dependency consistency check, Django system check, migration drift check, all 75 template checks, and Ruff passed. Three existing migration import blocks were reordered to satisfy the current CI linter; migration operations were unchanged.
- These checks supplement the earlier 204-test targeted run and workflow/page checks; they are not a claim that the full CI suite or a deployed penetration test was run.
- Patched dependency floors are declared in [requirements.txt](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/requirements.txt:12>) and [requirements-dev.txt](<C:/Users/USER/Desktop/Projects/DocTrack Capstone/requirements-dev.txt:5>). Other installations must upgrade their dependencies too.

# Isolated management and deployment configuration verification

Audit branch: `codex/systemwide-bug-audit`. Baseline revision: `bb84ac863937a27dc871e115db694b2e84a78f73`. The final schema and production-shaped configuration checks below ran against frozen audited source revision **`cd4316aaca993b0218c9252d8683477739368a3f`** on 18 October 2026, after the full-suite workers finished. The earlier seed, workflow, page-smoke, and negative configuration results are explicitly historical snapshots. The evidence was independently reread on 19 October; the commands were not rerun on that date. The later Reports text-only correction does not alter migrations, backend behavior, or deployment configuration and has separate focused verification.

## Database and command results

The saved command runs on 18 October 2026 explicitly verified the target name `test_doctrack_audit_commands_review`. They used `config.audit_settings`, which assigns both the normal and test database names to this isolated database. No command in this verification targeted the developer's business database.

| Check | Result | Evidence |
| --- | --- | --- |
| Final isolated migration run, frozen revision | Passed; migrations 0012/0013 already applied, no outstanding migrations or changes to apply, six existing document rows preserved | `isolated-schema-final.txt` |
| Final ordinary Django system checks, frozen revision | Passed; no issues, none silenced | `isolated-schema-final.txt` |
| Final migration drift, check and dry-run, frozen revision | Passed; no changes detected | `isolated-schema-final.txt` |
| Earlier isolated initialization and demonstration seed, recorded 00:08 | Completed, solely in the isolated target; not rerun after the final extraction repair | `isolated-command-smoke.txt` |
| Earlier end-to-end workflow self-check, recorded 00:08 | 21 checks passed; not a fresh result for the final extraction repair | `isolated-command-smoke.txt` |
| Earlier page and role smoke check, recorded 00:08 | 231 page requests across four roles; no page raised an error; exit 0; not rerun after the final extraction repair | `isolated-command-smoke.txt` |
| Final read-only migration confirmation, frozen revision | `documents.0012_document_extraction_generation` and `documents.0013_document_extraction_failed_generation` are applied in the isolated target | `deployment-shaped-check.txt` |

The workflow self-check covered draft creation, independent numbering, endorsement, pending receipt, receiving-office custody confirmation, work remarks, forwarding, completion, filing, searchability, and unrelated-office refusal. The smoke log also contains expected permission-denied diagnostics for refused actions. These are separate from unexpected page failures. A page smoke pass does not prove every state-changing control or real browser interaction works.

The initialization, seed, workflow self-check, and page smoke runs were not repeated during the final schema/configuration validation. Migration 0013 had been applied to this isolated target during the preceding audit pass. The final schema check found no migrations to apply, and the document count remained six.

## Negative deployment check

In the earlier 00:07 run, `check --deploy` directly against the deliberately local audit settings correctly failed with four DocTrack configuration errors: the audit secret is unsuitable for production, storage is local, CSP is disabled in that local configuration, and HTTPS redirect is disabled. Django also emitted five associated HTTPS, HSTS, cookie, and secret warnings. All nine findings are recorded in `isolated-schema-checks.txt`. This earlier negative result was retained as historical evidence, not rerun or relabelled as final-revision verification.

This is an expected rejection of a test environment, not a clean deployment result. The audit settings must not be deployed.

## Production-shaped configuration check

The final check used `config.audit_settings` and the same isolated database on frozen audited source revision `cd4316aaca993b0218c9252d8683477739368a3f`. Read-only migration queries confirmed migrations 0012 and 0013, then the database connection was closed. Temporary settings in that Python process enabled:

- A freshly generated secret, an explicit allowed host, HTTPS redirect, secure cookies, HSTS, and content-type protection.
- The installed CSP middleware, Axes, and the existing preferred Argon2 hasher.
- The installed private S3-compatible storage class, a placeholder private bucket, expiring signed URL options, and placeholder credentials.
- SMTP with encrypted transport and an explicit HTTPS site URL, using placeholder hosts and credentials.
- Disabled demonstration seeding.

The command was `check --deploy --fail-level WARNING`. It passed with **no issues and none silenced**, and the process exited 0. Socket connection functions were blocked during the check; there were **zero provider or database connection attempts during deployment checking**. The preceding migration queries in this configuration-only process were read-only. The separate final schema-check process independently confirmed both already-applied migrations, no pending migration plan, and the unchanged document count.

Evidence: `deployment-shaped-check.txt`. No `.env` file, persistent application setting, service configuration, or business record was changed. The ephemeral secret was not printed or saved.

## Limits and rollout implications

This verifies configuration guardrails and installed backend imports. It does not verify real cloud credentials, bucket/container permissions, actual upload/download durability, SMTP delivery, OCR quotas, production TLS/proxy behavior, a running background worker, deployment hosting, or backup restoration. Provider checks remain simulated as requested.

Migrations 0012 and 0013 were verified only in isolated audit databases. These results do not claim they have been applied to the developer's business database or any deployed database. The normal reviewed migration step remains necessary when this branch is integrated and released.

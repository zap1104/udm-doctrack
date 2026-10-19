# Access audit, 17 October 2026

Scope: notifications, internal/external private downloads, and read-only review
of account/administration access. No production database, storage, OCR or email
provider was used. PostgreSQL tests use `config.audit_settings`, audit slot
`access`, background tasks disabled, and temporary local files.

## Reproduced defects and focused repairs

1. **Notifications exposed objects an ordinary user/viewer could not read.**
   An own-office notice pointing to another user's draft or a restricted
   repository document appeared in the notification list and unread badge;
   individual and bulk read operations accepted it. Notices also remained
   visible after a specific object grant was revoked. `visible_to()` narrowed
   legacy record/document references only for office administrators, while
   external references already used current object visibility for every role.
   Apply the existing record/document visibility querysets to every role,
   retaining the original notification office and active-document constraints.

2. **Internal download links were unsigned.** Both internal tracking attachment
   and repository file endpoints returned bytes without a token and ignored
   missing, tampered, expired, other-user, other-file and wrong-kind tokens.
   Existing object access checks worked; the missing constraint was signed-link
   validation. Reuse the existing timestamp signer and TTL, bind the token to
   file kind, primary key and current user, and retain current object access
   checks before serving bytes. Existing URL routes are unchanged.

   The shared template tag generates signed links for the tracking attachment
   chip, repository primary Download button, repository file list and deletion
   review file list. Responses explicitly retain private/no-store and nosniff.
   Invalid/expired links return 403 with instructions to reopen the record or
   document for a fresh link. Routing print slips list filenames and contained
   no download links requiring changes.

## Evidence

- `access-red.txt`: **26 failed, 4 passed**, before product changes. Failures
  reproduce the two defects above; valid file bytes/cache headers and revoked
  object access were already correct.
- `access-green.txt`: initial focused suite **30 passed** after the repair.
- `access-lint.txt`: changed Python files pass Ruff.
- `access-templates.txt`: **108 templates checked**, no problems.
- `access-related.txt`: **442 passed**, 188.89 seconds, including external
  downloads/repository, notification lifecycle, all-role security, office
  boundaries, account administration, password races, login lockout and session
  expiry.
- `access-final.txt`: expanded focused suite **39 passed**, 20.52 seconds,
  including the final role, missing-file and deletion-review checks.
- Final Ruff and `git diff --check` pass. The latter reports existing Windows
  newline normalization notices, with no whitespace errors.

One existing external repository download test intentionally changed to follow
the emitted signed link and also reject its unsigned endpoint. Its four role
expectations and successful file-byte assertion remain intact. The new suite
covers both ordinary-user/viewer notification surfaces, revoked grants, both
internal file kinds and invalid signature classes, current access revocation,
all four allowed roles following rendered links, missing local files and the
deletion review link.

## Auxiliary access review and limits

Account management uses `administrable_by()`, actor-aware role/office form
choices, refreshed/locked actors for writes, scoped target locks and a serialized
last-system-admin invariant. Raw Django administration requires the system
administrator role in addition to normal Django staff permission. Profile
updates save only permitted personal fields. Existing related regression tests
exercise these boundaries; no further defect was confirmed in this review.

Browser/CSRF verification and final whole-project testing belong to the parent
audit. Real storage providers, physical devices, screen readers and deployed
behavior are unverified here. The existing Django 6 deprecation warning for
`CheckConstraint.check` remains outside this focused repair.

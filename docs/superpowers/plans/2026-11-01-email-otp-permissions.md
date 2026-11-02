# Email OTP and Permission Management Implementation Plan

Approved specification: the user's five-section Email OTP and Permission Management plan in this chat.

ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE.

Implement locally on `codex/email-otp-permissions`; no push, deployment, or migration of the original database.

## Task 1: Email OTP and enrollment

Add database-bound, single-use six-digit email challenges, five-minute codes, five attempts, 60-second resend delay and ten-minute password validation. Preserve account/IP send and verification limits from the approved specification. Authenticate only after consuming OTP. Gate existing sessions and Django administration. Add administrator-issued 24-hour enrollment invitations, secure email replacement, readiness and operator recovery commands. Preserve Axes, idle expiry and forced password changes.

## Task 2: Capability restrictions and record sharing

Add restrictive per-user policies for four existing areas, retaining role defaults and all object/custody checks. Provide system-administrator-only tables with effective-access explanations, stale-write protection and audited reasons. Reuse internal grants and add External read grants. Never infer mutation authority from a share. Enforce the policy in authorized querysets, services, files, saved uploads, search/extraction, exports, notifications and raw record administration.

## Task 3: Integrated verification and documentation

Add failing regressions before implementation. Run focused and full PostgreSQL suites, JavaScript, background checks, migrations, lint/templates and disposable-browser journeys. Obtain an independent review. Document actual results and limits, setup and recovery. Simulate email and storage; leave live delivery/activation to a readiness-checked operator.

## Review focus

- Password-only or raw-admin bypass, old sessions and forced-password redirect loops.
- Resend/replay races, delivery failures and stale account details.
- Metadata-only search and restricted historical/staged binaries.
- Stale policy and share edits, cross-office grants and custody boundaries.
- Verified-address replacement and last-administrator recovery without OTP bypass.

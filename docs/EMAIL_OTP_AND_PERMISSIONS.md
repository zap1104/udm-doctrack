# Email OTP, permissions and read sharing

Applying migrations to the intended database, sending real emails and enabling production access are separate operator actions. Back up the database and files before rollout.

## Enrollment and activation

Mandatory email OTP defaults to **on**. Every active account needs a verified email address, including office administrators and system administrators. Existing password-only sessions are refused when OTP is required. There are no remembered devices, backup codes or password-only fallback.

1. Keep the installation private while enrolling accounts. If existing accounts are not ready, use the explicit temporary setting `EMAIL_OTP_REQUIRED=False` only for setup. Restart the local process after changing settings. Do not expose setup mode publicly; deployment checks reject it.
2. Configure the existing Django email delivery: `EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS=True`, `DEFAULT_FROM_EMAIL` and the correct HTTPS `SITE_BASE_URL`. Keep credentials in the private environment, never Git or a shared chat. No additional API key is needed for this feature.
3. Review and apply the additive migrations to the intended database yourself:

   ```powershell
   .venv/Scripts/python.exe manage.py migrate --plan
   .venv/Scripts/python.exe manage.py migrate
   ```

4. Sign in as an authorized administrator during setup. Use **Administration → Users → Edit → Email security**, or **Django administration → Accounts → User permissions → Email security**. Set the registered address through assisted recovery when necessary, then send a verification invitation. Opening the invitation displays a confirmation screen; its owner must confirm it. Administrators cannot tick an address as verified. An invitation expires after 24 hours and never signs the recipient in. Existing signed-in email verification also works during setup.
5. Run the read-only readiness check:

   ```powershell
   .venv/Scripts/python.exe manage.py check_otp_readiness
   ```

   It rejects missing/unverified active accounts, test-only email backends, missing delivery configuration, or the absence of a ready system administrator with staff access and a usable password. It does not send mail or prove inbox delivery.
6. Set `EMAIL_OTP_REQUIRED=True`, restart, and test actual password-plus-code sign-in with a real mailbox. Test Django administration and password recovery as well. Confirm delivery before rollout; a successful provider connection alone is insufficient.
7. Run the existing deployment checks before deployment:

   ```powershell
   .venv/Scripts/python.exe manage.py check --deploy --fail-level ERROR
   ```

   `doctrack.E021` means temporary setup mode is still enabled. Other storage, HTTPS, email and security checks still apply.

Use `scripts/start.ps1` for local startup. The commands above are instructions; implementation verification does not run them on the original database. Demo passwords alone do not satisfy mandatory OTP. Only verified, individually controlled mailboxes should be used for real accounts.

## Sign-in and email changes

The password step creates an anonymous pending challenge. Successful login, the authenticated session and the normal login timestamp occur only after the email code succeeds. Django administration uses the same sign-in route and still requires an active system administrator with Django staff access.

| Control | Value |
|---|---|
| Code | Six cryptographically generated digits |
| Code lifetime | Five minutes |
| Pending password verification | Ten minutes |
| Wrong attempts | Five per pending sign-in |
| Resend delay | 60 seconds |
| Account sends | Three per 15 minutes and ten per hour |
| IP sends | Twenty per 15 minutes |
| Failed code checks | Ten per account and thirty per IP per 15 minutes |

Resending replaces the code, retains wrong-attempt counters and cannot extend the pending password window. Database-backed counters work across workers. Delayed delivery cannot reactivate superseded codes. Only a keyed digest is stored; codes are absent from sessions, URLs and audit entries. Mail delivery is synchronous with a ten-second connection timeout and a retry path; provider failures do not authenticate anyone.

Self-service address replacement requires the current password and recent OTP verification of the existing address. Use **Profile → Change registered email**. The current address remains active until the replacement is verified. Recent security verification lasts five minutes; the screen requests a new email code when necessary.

Changes to password, role, office, active/staff status or verified address invalidate old OTP proof. A legitimate own-password change preserves proof only for the session making that change. Other sessions must sign in again. Existing forced-password changes, lockouts and idle timeouts remain in effect.

Account administration preserves at least one ready system administrator: active, with Django staff access, a usable password and, when codes are required, a verified email. Both the normal user screen and Django administration refuse changes that would remove the last ready administrator. Django administration also refuses self-deletion, self-deactivation and removal of the actor's administrator role. A step down to office administrator requires another ready system administrator. Temporary enrollment mode does not require the replacement's email to be verified.

## Assisted recovery

An authorized administrator uses the user's **Email security** screen, confirms identity outside DocTrack, enters the replacement address and records the reason. A recent OTP check is required when mandatory OTP is on. The old account sessions/challenges become invalid and the replacement stays unverified until its invitation is confirmed. If email delivery fails after the reset, the reset remains unverified; retry sending an invitation after correcting delivery.

For an inaccessible last administrator, a trusted server operator can run:

```powershell
.venv/Scripts/python.exe manage.py recover_account_email --user administrator-name --email verified-owner@example.edu --reason "Identity checked in person; old mailbox inaccessible"
```

Replace the example values. This changes an active account's recovery address and sends verification, recording an operator-recovery audit entry. It does **not** mark the address verified, sign the person in, change the password or bypass OTP. Keep server access restricted. Existing password recovery remains available after ownership of the mailbox is established.

## User permissions

Open **Django administration → Accounts → User permissions**. Only system administrators with access to Django administration can use this table. Search by user, office or role, then open the user. Each applicable action has **Use role default** or **Block**, alongside its effective result. Save a reason. A revision check refuses stale forms rather than overwriting newer restrictions.

The four areas are independent: OVPA Tracking, External Tracking, OVPA Repository and External Repository. Reading, creation, metadata, files, routing, filing, sharing, exporting, extraction and deletion controls supplement existing authorization. They never elevate a Viewer, expand office authority, transfer custody or bypass receipt waits. Repository file contents stay locked for everyone. Metadata editing, initial upload and extraction use their own action checks.

Blocking record details removes that area's authorized population before rows, totals, search, charts, suggestions, exports and notifications are derived. Blocking file contents keeps allowed metadata visible but removes previews, original downloads, extracted text and body-derived search evidence. Blocking downloads alone leaves authorized previews available: a preview necessarily delivers readable content to the browser and is not copy protection.

Current and historical versions, old signed links, saved uploads, queued user-requested extraction and raw record administration also check the restrictions. Private maintenance jobs can still process both collections. Security administration stays available so an authorized system administrator can correct restrictions.

## Record sharing

Open **Django administration → Accounts → Record sharing**, choose one of the four areas and select a record. Existing explicit shares and inherited access sources appear separately. Share with either a named active user or one active internal office, supplying a reason. The table shows who granted access and when. Removing a share requires a reason and removes only that grant.

Shares permit reading only; restrictions, draft privacy, classification, office-administrator boundaries and existing routing rules still apply. They do not grant editing, custody, filing or administrative powers. Removing a share may leave access inherited from an office, uploader or routing participation. Sharing a Tracking record does not share its Repository document or another collection. External offices do not receive accounts or access grants.

Existing internal sharing remains available under its established authority. All sharing paths recheck current recipients and serialize against account changes. Policy and sharing audits retain actor, reason and before/after details. Files and historical records are not rewritten by this feature.

## Verification limits

The verification report records fresh automated results. Email is simulated for implementation tests; actual inbox delivery and activation remain operator checks. Browser checks use desktop Chromium emulation, not physical devices or a screen reader. Email OTP depends on mailbox security and delivery and is not phishing-resistant authentication. The implementation does not claim standards certification or the absence of every possible defect.

Design references: [OWASP OTP and MFA guidance](https://cheatsheetseries.owasp.org/cheatsheets/Multifactor_Authentication_Cheat_Sheet.html) and [Django session invalidation](https://docs.djangoproject.com/en/5.2/topics/auth/default/#session-invalidation-on-password-change).

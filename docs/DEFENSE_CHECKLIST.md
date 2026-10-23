# DocTrack presentation checklist

Use disposable demonstration records. Never rehearse deletion, seeding, account changes or cleanup against the university's original records.

## Before presentation day

- Use the revision identified in the latest defense verification report. Install its requirements in the presentation environment.
- Prepare separate sign-ins for a system administrator, an office administrator, staff in two different offices and a viewer. Check each sign-in in advance. Keep passwords outside slides, screenshots and Git.
- Prepare a short PDF with selectable text, a scanned sample if demonstrating OCR, a DOCX and a signed copy saved outside DocTrack. Use artificial names and content.
- Verify that the actual presentation machine can read and write its configured private storage. Azure/R2 credentials and network availability were not exercised by the isolated audit.
- If demonstrating OCR.space, check the configured key, consent and free-plan limits beforehand. Keep an already extracted demonstration document available. Local text extraction does not prove the external OCR service is available.
- If demonstrating password recovery or email, verify actual delivery separately. The audit uses simulated email and does not prove SMTP delivery.
- If background jobs are enabled, confirm the django-q2 worker is running and schedules are registered. A successful web page alone does not prove the worker is healthy.
- Back up the presentation database and its matching files together. Confirm how to restore that disposable copy.

## On the presentation machine

1. Start the existing Windows setup with `scripts\start.ps1`. Use the existing configuration for the intended environment; do not replace it with audit settings.
2. Open the intended address and sign in. Check the selected office, current date/time, both themes and a narrow window.
3. Open a repository file, its preview and its download. Check that the contents are readable and belong to the expected record.
4. Try a combined search, press **Search**, and verify its result. On the Dashboard, change an automatic control and verify the values change without losing your position.
5. Confirm the second office can receive a demonstration endorsement. Confirm the viewer cannot change records.
6. Keep the local address ready if a temporary public tunnel is used. A tunnel needs the local server and tunnel process to remain running; its availability is outside the automated audit.

## Suggested walkthrough

1. **Internal Tracking:** create a draft, review it, endorse it, confirm receipt from the receiving office, add work and upload a new file version with a short change note. Show that the next endorsement returns the new recipient to Pending Receipt.
2. **Filing:** complete the case, approve filing using an authorized administrator, and open the single repository document. Show its version history and that file contents are locked while authorized metadata corrections remain available.
3. **External workflow:** use its separate Tracking and Repository pages. Show shared external office choices, manual external receipt/work evidence and control following confirmed internal custody. External offices have no accounts.
4. **Counts and filters:** demonstrate a populated Incoming/Outgoing stage filter, selected-office perspective, Dashboard totals and its corresponding list or report. Explain that overdue can overlap a stage; it is not another partition to add to the total.
5. **Search and files:** demonstrate an identifier search, a combined filter and an older version preview. Explain that relevance is a ranking score and search may deliberately show only its disclosed top results.
6. **Administration:** use disposable records to show deletion review and recovery. The normal 30-day recovery period is followed by permanent repository cleanup. Do not change the clock or purge real records for the demonstration.

## Claims to keep accurate

- Original file formats are retained. DocTrack does not convert everything to PDF or create/cryptographically verify signatures.
- Five file versions remain available per logical file; older retired content retains audit evidence under the configured lifecycle policy.
- Repository files are read-only after initial upload/filing; metadata and extraction permissions are separate.
- The verified audit reduces presentation risk. It cannot guarantee that every possible bug is absent, certify ISO 25010 compliance, or prove live cloud services, physical devices and screen readers were tested.

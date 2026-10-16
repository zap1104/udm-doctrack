# External UDM offices

External support is for other UDM offices that do not have DocTrack accounts. It is a separate collection, not a change to the existing OVPA workflow. Existing offices, records, numbers, statuses and turnaround rules stay in place.

## Start an external record

1. Sign in as your office administrator and open **External Tracking**.
2. Open **External Offices** and add the UDM office to your office's directory. This does not create an account or send an invitation.
3. Create an external slip. Keep its original paper reference, choose document types and record its known source. Review the draft and send it to its next internal or external recipient.
4. For an already-filed document, use **External Repository → Upload external document** instead. Select its external counterpart and upload or scan the file, then review its metadata.

External slip numbers use an independent `UDM-EXT-...` series. They do not consume or replace OVPA tracking numbers. Each external slip has one active recipient. Use onward endorsements and returns to record its custody chain.

## Who records each action

| Action | Person allowed |
| --- | --- |
| Create a case or directory entry; record external receipt/work/history | Administrator of the managing internal office, or system administrator |
| Confirm an internal receipt | A records worker assigned to the receiving internal office |
| Add internal work or endorse onward | An authorized records worker in the confirmed internal custodian office |
| Correct manual external history or approve filing | Administrator of the managing internal office, or system administrator |
| Read and print | Authorized staff and viewers; drafts remain restricted |

**Managing office**, **confirmed custodian** and **pending recipient** answer different questions. The managing office remains responsible for recording outside actions while the physical paper moves elsewhere.

An external action shows its actual time separately from its recording time and encoder. The encoder is not identified as the external office's actual sender or recipient. Internal confirmations retain their actual user and server timestamp.

## Work, corrections and filing

Receipt leads to **Received**. An explicit work update leads to **In Process**. Supporting receipt evidence at an external office does not mark work started. A new endorsement returns the slip to **Pending Receipt**.

External corrections require a reason and preserve old/new values. They cannot rewrite internal confirmations or contradict later work and transfers. Endpoint corrections are restricted to unconsumed movements; a confirmed custodian cannot change without a transfer. Timestamp inputs preserve the original precision when only a note is edited. Unknown historical dates belong in annotations or attached evidence, not invented timer values.

Completing work moves it to **Completed – Pending Filing**. The managing administrator then files it once into **External Repository**. Filing copies the attachments and preserves the history link. A failed copy leaves the case pending filing. Once filed, custody history is read-only; administrators can add annotations.

## Filters, counts and turnaround

The existing OVPA pages continue to show their original collection. External views have their own counts, reports, exports and search. External tracking defaults to unfinished cases; filed history remains available explicitly and from repository history links.

`office=` selects an authorized internal perspective or owner. `external_office=` selects an external counterpart. Incoming, Outgoing and With another office divide the current working population; Draft and Pending Filing remain separate. An invalid filter returns an error and no matching results rather than silently showing a broader set.

External reports separate internal processing, receipt waits and external custody time. Received time counts as internal processing; a receipt wait pauses it. Overall time begins with the earliest known actual event. Missing measurements are shown as not recorded rather than zero. These calculations use the existing UDM business calendar and do not change OVPA metrics.

## Local setup and operations

No new API keys, external accounts or integrations are needed. Existing private storage, OCR settings, signed downloads and access controls are reused.

Apply the additive Django migrations and rebuild static files through the normal Windows setup. Start locally with `scripts\start.ps1`. If background jobs are enabled, run `python manage.py ensure_schedules` and keep the existing django-q2 worker running; this registers the external follow-up schedule alongside the existing schedules.

The existing deletion review and 30-day recovery policy also applies to the External Repository. Background extraction, indexing, backup and permanent cleanup operate on both collections, even though their user-facing totals are separate.

# Demo script

A ten-minute walkthrough for the defence. Rehearse it three times. The order matters — each step sets up the next.

**Before you start:** run `make seed`, open two browser windows (one normal, one private, so you can be two users at once), and have `http://127.0.0.1:8000` loaded in both.

---

## 0. The problem (30 seconds, no screen)

> "Right now, when a document leaves an office, the record of where it went is a signature in a logbook. If someone asks where a purchase request is, finding out means phoning three offices. And when the document is finished, it goes into a cabinet — findable only by whoever filed it, for as long as they remember.
>
> UDM DocTrack does two things: it tracks documents while they move, and it makes them findable after they stop."

---

## 1. Sign in (30 seconds)

Sign in as `admin` / `DocTrack2026!`.

> "There is no Register button anywhere in this system. Accounts are created by an administrator and tied to an office, because in a records system you must know which office an action came from."

Point at the dashboard cards.

> "My inbox, in transit, overdue, completed. A person sees their own workload first, not a database."

---

## 2. Create a document (90 seconds)

Click **+ Create New DTS**.

- Subject: `Request for repair of registrar air-conditioning unit`
- Type: Work Order
- Point at the locked origin office: *"This comes from my account. You cannot route a document as another office."*
- Receiving offices: pick **SUP** and **MED**
- Deadline: 3 days
- Instructions: `For immediate inspection and repair.`

Click **Review & create**.

> "Two steps on purpose. The tracking number is issued here — `UDM-OVPA-REC-2026-08-0004`. Office, year, month, sequence. Readable out loud over the phone, and never reused."

Click **Create & route**.

---

## 3. The critical distinction (90 seconds) — *the heart of the demo*

Stay on the detail page and point at the status pill: **In transit**.

> "This document has been sent. It has not been received. Those are different facts, and most logbooks cannot tell them apart."

Switch to the private window. Sign in as `supply.staff` / `DocTrack2026!`.

> "Different user, different office. The document is waiting in their inbox."

Open it, click **Confirm receipt**, accept the confirmation dialog.

> "Now custody has changed — and look at what was recorded: who confirmed it, which office, and the exact date and time. That timestamp comes from the server. The user cannot type it, backdate it, or edit it afterwards."

Point at the timeline.

> "This history is append-only. When this document gets forwarded, the record of this receipt does not change. Nothing in this system overwrites history."

---

## 4. Act and complete (60 seconds)

As `supply.staff`, add a remark: `Inspected. Parts ordered, repair scheduled Thursday.`

Then open the **Complete** tab, note `Repair completed and tested.`, tick **archive now**, and submit.

> "That is the whole tracking lifecycle. The document leaves the active queue and moves into the archive — automatically, with its files and its full history attached."

---

## 5. Upload a historical document (2 minutes)

Go to **Documents → Upload / scan**. Upload any PDF you brought.

> "This is the other half of the system: everything already sitting in the cabinets."

On the review screen, point left then right.

> "On the left, what the system actually read out of the file. Digital PDFs and Word documents are read directly — instant and free. A scanned page with no text layer goes to OCR.
>
> On the right, what the system proposes: a title, a type, an office, a date, tags. Notice the confidence labels — Strong match, Likely, Check this. Words, not decimals, because a records officer should not have to interpret 0.72."

Change something deliberately.

> "And here is the important part: **nothing is saved until I press Save.** The system proposes. The person decides. A wrong suggestion costs a correction, never a wrong record.
>
> One more thing happens invisibly here. The system stores what it suggested *and* what I kept. Every review becomes a labelled training example — so by the time we build the AI phase, the training data already exists, produced by real work."

Save it.

---

## 6. Search (2 minutes)

Go to **Search**. Type `electrical supplies`.

> "Every result shows a relevance percentage and — this line here — *why* it matched. Matched in: Title, Tags."

Point at the threshold slider.

> "The default hides anything below 75%, but it tells you how many it hid, and the slider moves. Nothing is silently dropped.
>
> And one point of vocabulary we are careful about: this is **relevance**, not accuracy. It measures how well a record matches your words. It is not a claim that the system is 82% correct. The formula is documented in `SEARCH_DESIGN.md` — four weighted fields, a text score, a fuzzy score for misspellings, and a field bonus."

Search a misspelling: `memorandom`.

> "Trigram matching. It still finds memorandum."

Search a tracking number: paste `UDM-OVPA-REC-2026-08-0004`.

> "An exact reference match short-circuits to 100% and goes straight to the top."

---

## 7. Permissions (60 seconds)

In the private window, sign in as `hr.staff`.

Go to Tracking.

> "Same system, different user. The air-conditioning work order is not here, because it was never routed to HR and nobody shared it. A regular user sees only what was routed to them, originated by them, assigned to them, or explicitly granted to them. That rule is enforced in the database query, not hidden in the interface — and it has its own test file."

---

## 8. Administration and audit (60 seconds)

Back as `admin`, open **Administration**.

> "Offices, document types, tags, metadata rules, custom metadata fields — all editable without touching code or redeploying. When the Procurement office wants a new field, an administrator adds it and it appears on every review screen."

Open **Audit log**.

> "Sign-ins, routing, receipts, downloads, master-data changes. Append-only. In a records system, the log of who did what is not a feature — it is the point."

---

## 9. Close (30 seconds)

> "To summarise: documents are tracked with provable custody, archived automatically when complete, described with metadata that a person reviews, and searchable with a ranking we can explain line by line.
>
> The AI metadata model is deliberately Phase 2. The interface for it is built, the engine is swappable with one setting, and the system is already collecting the training data. We chose rules first because rules work today and can be corrected in thirty seconds by someone who cannot code."

---

## Questions you will be asked

**"Why not Elasticsearch?"**
PostgreSQL full-text search handles this volume comfortably. Elasticsearch means another service to install, secure, back up and pay for. Fewer moving parts, same result at this scale.

**"What does 75% mean?"**
How well the record matches your query words — not system accuracy. Four weighted fields, three scores, documented formula, adjustable threshold.

**"Where is the AI?"**
Phase 2, deliberately. The plug-in point exists and training data is accumulating from every human review. Rules first because they are explainable and correctable now.

**"What if OCR misreads something?"**
A person reviews every suggestion before saving, and the original file is kept permanently so extraction can be rerun.

**"How do you know a document was really received?"**
Someone at the receiving office pressed a button. The server recorded who, which office, and the exact time. It cannot be edited afterwards, and the routing slip prints the full history.

**"What about the Data Privacy Act?"**
Access is office-scoped by default, documents can be marked restricted, every access is logged, downloads use expiring signed links, and passwords use Argon2. The remaining work is the formal privacy notice — that is on the checklist.

---

## If something breaks

- Server not responding → `Ctrl+C`, `python manage.py runserver`
- Data looks wrong → `make seed` resets it in seconds
- No wifi → you brought a screen recording
- A feature genuinely fails → say so plainly, note it as known, move on. Composure reads better than improvisation.

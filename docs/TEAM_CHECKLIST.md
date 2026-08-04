# Team checklist

Tick things off in order. Anything marked **[DONE]** already works in the prototype — verify it rather than rebuild it.

---

## Phase 0 — Everyone gets it running (day 1)

Nobody moves on until every member finishes this. A teammate who cannot run the project cannot contribute.

- [ ] Install Python 3.11+, PostgreSQL 14+, Git
- [ ] Clone the repository ([GITHUB_GUIDE.md](GITHUB_GUIDE.md))
- [ ] Run the setup script ([SETUP.md](SETUP.md))
- [ ] Sign in as `admin` and see the dashboard
- [ ] Sign in as `med.staff` and confirm receipt of the waiting document
- [ ] Run `make test` and watch it pass
- [ ] Make a trivial change, push a branch, open a PR, get it merged

**Done when:** every member has one merged pull request, however small.

---

## Phase 1 — Understand what exists (day 2–3)

- [ ] Read this file, `README.md`, and `docs/SEARCH_DESIGN.md`
- [ ] Walk the whole flow yourself: create a DTS → route → confirm receipt → remark → forward → complete → archive → search for it
- [ ] Open `apps/tracking/services.py` and trace what happens when a document is routed
- [ ] Open `apps/search/services.py` and find the three scores that make up relevance
- [ ] As `admin`, add a tag, a document type and a metadata rule under Administration
- [ ] Upload a scanned PDF and see how the metadata review screen behaves

**Done when:** anyone in the group can explain, without notes, why "sent" and "received" are different states.

---

## Phase 2 — Divide the work

Pick one owner per area. Everyone reviews everyone else's pull requests.

### Owner A — Tracking
- [ ] **[DONE]** Tracking numbers, routing, receipt, forwarding, completion
- [ ] Test with 50+ records and check the dashboard still reads clearly
- [ ] Add an email or in-app notification when a document arrives *(not built)*
- [ ] Bulk receipt: confirm several documents at once *(not built)*
- [ ] Verify the printed routing slip against a real UDM slip and adjust

### Owner B — Documents and metadata
- [ ] **[DONE]** Upload, extraction, suggestions, review screen, smart folders
- [ ] Sign up for an OCR.space key and test with real scanned documents
- [ ] Add metadata rules for each office's actual document wording
- [ ] Import a batch of historical documents and record how long it takes
- [ ] Write the retention rules per document type with the Records Office

### Owner C — Search
- [ ] **[DONE]** Weighted full-text search, fuzzy matching, relevance, threshold
- [ ] Load 500+ documents and measure search time (target: under 1 second)
- [ ] Assemble 20 realistic queries and check the right document ranks first
- [ ] Tune the weights in `.env` if a category consistently ranks wrong
- [ ] Add saved searches *(not built)*

### Owner D — Interface and documentation
- [ ] **[DONE]** All ten screens from the mockups
- [ ] Test every screen on a phone — the offices will use phones
- [ ] Check colour contrast and keyboard navigation
- [ ] Write a user manual with screenshots for office staff
- [ ] Prepare the defence slides and rehearse `docs/DEMO_SCRIPT.md`

### Owner E — Deployment and security
- [ ] **[DONE]** Settings, lockouts, CSP, Argon2, signed download links, audit log
- [ ] Deploy to Render or Azure and confirm HTTPS works
- [ ] Set up automatic database backups and **restore one** to prove it works
- [ ] Turn on file storage (R2 or Azure Blob) instead of local disk
- [ ] Write the data privacy notice — the system holds personal data

---

## Phase 3 — Test with real people (before the defence)

- [ ] Sit with staff from at least two offices and watch them use it. Say nothing; write down where they hesitate.
- [ ] Time a full cycle: create → route → receive → complete → find it again
- [ ] Ask each person to find a document they filed last month
- [ ] Fix the top three points of confusion
- [ ] Check every error message reads like a sentence, not a stack trace

**Done when:** someone who has never seen the system can route a document without being told how.

---

## Phase 4 — Defence preparation

- [ ] Rehearse `docs/DEMO_SCRIPT.md` end to end, three times
- [ ] Reset the demo data the morning of: `make seed`
- [ ] Prepare answers to the questions that will be asked:
  - *Why not Elasticsearch?* → PostgreSQL full-text search handles this volume; one fewer service to run and pay for.
  - *What does 75% relevance mean?* → How well a record matches the query words, not system accuracy. The formula is in `docs/SEARCH_DESIGN.md` and the threshold is adjustable.
  - *Where is the AI?* → Phase 2, deliberately. The interface exists and the system is already collecting labelled training data from every human review. Rules first because rules are explainable and correctable today.
  - *What if OCR misreads a document?* → A person reviews every suggestion before saving, and the original file is always retained.
  - *How do you know a document was really received?* → Someone at the receiving office pressed a button. The server recorded who, which office, and the exact time. It cannot be edited afterwards.
- [ ] Have a screen recording ready in case the wifi fails
- [ ] Bring the printed routing slip — it makes the accountability story tangible

---

## Weekly rhythm

**Every Monday**
- [ ] Everyone pulls `main` and runs `make migrate`
- [ ] Pick this week's tasks; one owner each
- [ ] Review any pull requests still open from last week

**Every Friday**
- [ ] Merge everything that is ready
- [ ] `make test` on `main` — it must pass
- [ ] Note what slipped and why

---

## The short version

If you only remember five things:

1. **Pull before you start. Branch before you code.**
2. **Never commit `.env` or real documents.**
3. **Commit the migration file whenever you change a model.**
4. **Run the tests before opening a pull request.**
5. **Views don't change data — services do.**

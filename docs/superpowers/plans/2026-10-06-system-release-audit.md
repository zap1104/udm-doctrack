# System accuracy audit and release plan

Goal: release the current authorized DocTrack UI improvements to main after
checking workflows, permissions, queries and displayed information throughout
the existing system. Fix confirmed defects without redesigning working behavior.

Mandatory rule: ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE.

Scope and constraints: Django/PostgreSQL/Bootstrap/HTMX remain; visible_to is
the base security boundary, SYSTEM_ADMIN alone crosses offices, routing respects
current_batch, history remains append-only, filing and custody rules remain.
Do not seed or mutate live user records during browser verification. Commit on
a codex branch and integrate into main through a reviewed, tested release.

- [x] Record baseline changes and run full regression suite.
- [x] Audit tracking/documents lifecycle and action permissions independently.
- [x] Audit metrics, filter/query composition, exports and linked-list agreement.
- [x] Audit UI, sessions, accounts/admin/security and accessibility behavior.
- [x] Reproduce confirmed defects; apply minimal corrections with RED/GREEN tests.
- [x] Verify full tests, JavaScript, lint, templates, Django checks, migrations,
      collected static assets, deployment checks and browser roles/responsiveness.
- [x] Review integrated changes; stage source/tests and relevant release evidence.
- [ ] Push release branch, verify exact head CI, merge to main, verify remote main
      and its CI. Do not force-push or discard existing changes.

Workers use separate PostgreSQL TEST database names through scratch settings
modules; final verification uses the unchanged project settings and full suite.
Transient packets, machine-specific AGENTS.md and raw prior AI reviews are not
application release files. Keep them locally; include useful authored release
documentation and verified screenshots.

Acceptance: displayed counts and links agree under allowed office scopes; action
controls match backend rules; invalid requests return usable errors; compound
operations do not leave partial records; full checks pass before integration.

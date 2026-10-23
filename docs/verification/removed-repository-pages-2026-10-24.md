# Removed optional repository screens

Base revision: `b78494c`. Requested removal of **My repository views** and **Local topic models**, followed by a push to main.

## Changes

- Removed both navigation entries, their pages, handlers, forms and HTTP actions.
- Removed **Save these filters** from Internal and External Repository and the unused link-building helper.
- Replaced the vocabulary page's model-screen link with Administration navigation.
- Updated the browser QA script and current documentation to match the available screens.
- Preserved database records, OCR/extraction, topic tagging, search, repository filtering and existing authorization. No schema migration or original-data operation is required.

## Verification

All database tests used a separate PostgreSQL test database on the owned loopback-only temporary cluster, with local temporary storage and providers disabled.

| Check | Result |
| --- | --- |
| New removal regressions before implementation | 15 failed for the existing links/pages/actions. |
| Final removal regressions | 15 passed in 3.55 seconds. Covers four roles, both repositories, retired GET/POST routes, preserved saved-view history and working vocabulary navigation. |
| Related source-review, saved-view-data, chunk-search, alias, automatic-tag and processing-contract tests | 33 passed in the compatibility run. That run also had 14 passing removal cases and one incorrect test expectation that viewers should see Upload; the corrected removal tests all passed afterward. Production viewer permissions were preserved. |
| JavaScript suite | 194 passed, zero failures. |
| Templates | 124 checked, no problems. |
| Ruff | `ruff check apps tests scripts` passed. |
| Django check | No issues. |
| Migration drift | No changes detected. |
| Browser QA script syntax / whitespace | Passed. |

The compatibility command selected `test_removed_repository_pages.py`, `test_intelligence_ui.py`, `test_saved_document_views.py`, `test_source_chunk_search.py`, `test_search_aliases.py`, `test_automatic_tag_gates.py` and `test_processing_contracts.py` under explicit disposable settings. The final focused command selected only `test_removed_repository_pages.py`. Raw logs/XML remain in the ignored local audit directory.

No fresh full-project run, physical-device checks or real browser journey is claimed for this focused removal. The existing pending External custody/context work and unrelated local settings edit are excluded from this commit.

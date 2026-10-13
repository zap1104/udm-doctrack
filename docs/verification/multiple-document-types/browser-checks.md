# Browser verification

Checked the local tracking creation form at `http://localhost:8000/tracking/new/` on 6 October 2026.

- Two checkbox selections populate the submitted multiple select and the main-type choices.
- Typing filters the available choices without removing existing selections.
- Space selects a checkbox and preserves an already chosen main type.
- Removing the main choice when one selection remains makes that remaining type the main type.
- Clear all empties both the type selection and main type.
- Clicking the Document types label focuses its searchable control.
- The checkbox group retains the field's help description. The hidden native select is excluded from the accessibility tree to avoid duplicate controls.
- Narrow phone layout: 320px viewport, 305px document width, no horizontal overflow. Remove and Clear all buttons have at least 44px height and remove buttons are 44px wide.
- Light and dark screenshots saved for desktop and phone layouts. Restored the original viewport and light theme afterward.
- No browser warning or error logs during these checks.

Selections were exercised without submitting a real tracking record. Saving, editing, filing, deadline/retention rules, filtering, and permission boundaries are covered by PostgreSQL regression tests. Manual screen-reader testing was not performed.

## Code review

The separate reviewer identified four issues which were corrected before the final regression run:

1. Replacing the multiple-select widget dropped its choices. Assigning its queryset after replacing the widget retains the choices; rendered-option checks cover this.
2. A native single-selection edit could retain an old main value. Empty and single selections now resolve before multiple-selection validation; native-form tests cover clearing and replacing selections.
3. Tracking search would query additional types separately for each result. Its listing now prefetches the selected types.
4. New service calls could bypass inactive-type rejection through a legacy exemption. New records now require active types; existing retired selections remain valid for edits.

Dashboard and repository pending previews did not previously show type names, so they were left unchanged.

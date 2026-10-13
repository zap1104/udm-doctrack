# Consistent office Reports charts

This presentation-only update makes the Incoming and Outgoing charts share the System Admin chart's design. Both use the same summary layout, 44px totals, View documents button, 48px stacked bar, rounded corners, and 20px spacing before the stage links. The admin view also uses these shared styles.

Counts, proportions, status colors, office scopes, destination URLs, and completion exclusions were preserved. Screen-reader labels for office stages now include their percentage. Incoming and Outgoing remain separate, each with Pending Receipt, Received, and In Process.

## Verification

- **86 focused tests passed**, covering current stage totals, exact drilldown records, all supported user roles, Reports audience rules, and template accessibility.
- All **90 templates** passed the template check. `git diff --check` passed.
- An independent read-only review found no actionable issues.
- The actual Reports view was rendered read-only for the local MED office and inspected with an isolated headless Chrome profile. Current counts remained Incoming 7 and Outgoing 5.
- Desktop light/dark and exact 390px light/dark previews were captured. A 320px light preview was also checked. The mobile preview frames account for the headless browser's minimum outer window width.
- Browser measurements verified two 48px bars, two 44px totals, six stage links at least 44px tall, and no document or link overflow at 320px and 390px. See `layout-checks.json`.
- The screenshots are rendered-template previews. No existing signed-in browser session, user account, or document record was changed.

No backend code, database schema, test code, or unrelated settings changed in this update. The previous full-suite run remains documented with the admin chart change; this presentation update was checked with the focused suite above. No commit or push was performed.

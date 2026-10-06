# Selecting multiple document types

Document types classify a tracking record and its attachments together. Choose all applicable types using the searchable checkbox list. Selections remain on the draft, review page, details, printed routing slip, and filed repository document. Repository metadata editing supports the same selection.

When two or more types are selected, choose a main document type from that selection. The main type controls the existing routing deadline rules and retention calculation. A single selection becomes its own main type. Clearing the selection leaves the type unspecified. Existing retention dates are preserved when editing repository metadata, as before.

For mixed records, the review and detail pages show the main type below the complete selection.

Repository type filters, type folders, and repository search match any selected type. A mixed document appears once in each matching result list. The repository report groups documents by **main type**, counting each document once. Its links use `type_scope=main` so the opened list agrees with the report count. Type folder links return to matching any selected type.

Existing single-type records and older submissions continue to work. Retired types already on a record remain selectable when editing it; newly adding a retired or unknown type is rejected.

## Applying the update

Run the committed migrations and collect static assets using the project's usual deployment process:

```powershell
python manage.py migrate
python manage.py collectstatic --noinput
```

The migrations add selection tables and copy each existing main type into its selection. They do not rewrite tracking or document rows, retention dates, file relationships, or routing data. Do not reseed or reset an existing database for this update.

Deploy the code, both migrations, and `static/js/document-types.js` together. The local database has already received the two migrations; migration preservation evidence is in `docs/verification/multiple-document-types/migration-verification.json`.

Removing the new selection tables would lose additional type choices. Keep them if temporarily returning to older application code; the original main-type field remains compatible.

# Offline vendor assets (optional)

By default the app loads Bootstrap and HTMX from a CDN. If the demo venue has no
internet, download these three files into this folder and they are used instead
automatically (see `apps/core/context_processors.py`):

- `bootstrap.min.css`      https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css
- `bootstrap.bundle.min.js` https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js
- `htmx.min.js`            https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js

Nothing else needs to change. Worth doing the week before a defence.

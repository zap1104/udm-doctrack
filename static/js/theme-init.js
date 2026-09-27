/* --------------------------------------------------------------------------
   Theme, before first paint.

   Loaded synchronously in <head>, ahead of the stylesheets, so a page opens in
   the reader's theme instead of flashing light and then turning dark. An
   external file rather than inline script, so the Content-Security-Policy's
   script-src 'self' covers it without being loosened.

   Writes both attributes: data-theme, which the app's stylesheet themes off,
   and data-bs-theme, which Bootstrap 5.3 themes its own components off, so the
   two cannot disagree. doctrack.js takes over from here: the toggle, and
   following the operating system while nobody has chosen.
-------------------------------------------------------------------------- */
(function () {
  "use strict";
  var theme = null;
  try {
    var stored = window.localStorage.getItem("doctrack-theme");
    if (stored === "dark" || stored === "light") theme = stored;
  } catch (error) {
    /* Storage blocked: fall through to the operating-system preference. */
  }
  if (!theme) {
    theme = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  document.documentElement.setAttribute("data-theme", theme);
  document.documentElement.setAttribute("data-bs-theme", theme);
})();

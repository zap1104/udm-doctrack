/* UDM DocTrack — theme bootstrap.
   A separate file, loaded early and un-deferred in <head>, on purpose: this
   has to run and finish *before* doctrack.css paints, so the page never
   flashes the default palette before switching to a saved one. Splitting it
   out of doctrack.js (which loads at the end of body) is what makes that
   possible; it also has to be an external file rather than inline markup,
   since CSP_SCRIPT_SRC in settings.py allows no 'unsafe-inline' when
   ENABLE_CSP is on — every script in this project is a real .js file for
   that reason, and this one is no exception.

   The picker itself (static/js/doctrack.js, section 7) is the only other
   piece of this feature. The two share this file's globals rather than
   each keeping their own copy of the storage key and the font table. */
(function () {
  "use strict";

  var STORAGE_KEY = "doctrack-theme";

  /* Each alternate theme sets --font-display to a face that is not Inter.
     Loading all six upfront would put five unused webfont families on every
     page for the majority of people who never leave the default palette, so
     the family is fetched only when its theme is actually active — and the
     stack in doctrack.css falls back to a system serif/sans meanwhile, so a
     slow or blocked font request costs the page its display face and
     nothing else.

     Midnight is deliberately absent: it uses JetBrains Mono, which base.html
     already loads for tracking numbers, so it needs no request of its own. */
  var THEME_FONTS = {
    navy: "https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,600;8..60,700&display=swap",
    maroon: "https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;800;900&display=swap",
    slate: "https://fonts.googleapis.com/css2?family=Archivo:wght@600;700;800&display=swap",
    burgundy: "https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700&display=swap",
    ocean: "https://fonts.googleapis.com/css2?family=Outfit:wght@500;600;700;800&display=swap"
  };

  function loadThemeFont(theme) {
    var href = THEME_FONTS[theme];
    if (!href) return;
    /* Switching back and forth must not stack duplicate <link>s. */
    if (document.querySelector('link[data-theme-font="' + theme + '"]')) return;
    var link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = href;
    link.setAttribute("data-theme-font", theme);
    document.head.appendChild(link);
  }

  /* Shared with the picker in doctrack.js so the two cannot drift apart. */
  window.DocTrackTheme = {
    STORAGE_KEY: STORAGE_KEY,
    loadFont: loadThemeFont
  };

  /* The sign-in page keeps its own fixed look regardless of what is picked
     for the app afterward — it has no picker of its own and is often seen
     on a shared or unfamiliar machine, where a leftover theme from whoever
     used it last is more confusing than reassuring. Path-checked rather
     than a body class, since this runs before <body> exists. */
  if (window.location.pathname.indexOf("/accounts/login") === 0) return;

  try {
    var theme = window.localStorage.getItem(STORAGE_KEY);
    if (theme && theme !== "default") {
      document.documentElement.setAttribute("data-theme", theme);
      if (theme === "midnight") {
        document.documentElement.style.colorScheme = "dark";
      }
      loadThemeFont(theme);
    }
  } catch (e) {
    /* Storage blocked (private browsing, locked-down profile, etc.) — the
       page just renders in the default palette, same as a first visit. */
  }
})();

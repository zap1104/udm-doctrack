"use strict";

// Native POST forms work without JavaScript. After a saved review, restore
// keyboard focus without adding a second scroll or changing other forms.
(() => {
  if (window.location.hash === "#training-eligibility") {
    const panel = document.getElementById("training-eligibility");
    if (panel) {
      panel.open = true;
      const summary = panel.querySelector("summary");
      if (summary && document.activeElement === document.body) summary.focus({ preventScroll: true });
    }
    return;
  }
  if (window.location.hash !== "#tag-review") return;
  const heading = document.getElementById("tag-review-title");
  if (heading && document.activeElement === document.body) {
    heading.focus({ preventScroll: true });
  }
})();

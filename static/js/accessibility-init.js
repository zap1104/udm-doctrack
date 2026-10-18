/* Native administration has its own HTML shell; apply the server's account
 * preferences before its styles paint. No shared-browser storage is used.
 */
(function () {
  "use strict";
  var seed = document.getElementById("accessibility-seed");
  if (!seed) return;
  try {
    var values = JSON.parse(seed.textContent);
    var root = document.documentElement;
    if (["12", "14", "16", "18"].indexOf(values.text_size) !== -1) root.setAttribute("data-a11y-text-size", values.text_size);
    root.setAttribute("data-a11y-contrast", values.high_contrast === true ? "high" : "standard");
    root.setAttribute("data-a11y-motion", values.motion === "reduce" ? "reduce" : "system");
    root.setAttribute("data-a11y-links", values.underline_links === true ? "underline" : "default");
    root.setAttribute("data-a11y-spacing", values.spacing === "roomy" ? "roomy" : "standard");
    root.setAttribute("data-a11y-targets", values.larger_targets === true ? "large" : "standard");
  } catch (_) { /* An unreadable seed leaves the original display intact. */ }
})();

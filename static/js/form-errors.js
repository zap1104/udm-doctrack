/* Put failed submissions and their invalid fields within keyboard reach. */
(function () {
  "use strict";
  var summary = document.querySelector("[data-error-summary]");
  if (!summary) return;
  summary.focus();
  summary.querySelectorAll('a[href^="#"]').forEach(function (link) {
    link.addEventListener("click", function (event) {
      var target = document.getElementById(link.getAttribute("href").slice(1));
      if (!target) return;
      var enhanced = target.closest(".multiselect");
      if (enhanced && target.matches("select")) {
        target = enhanced.querySelector(".multiselect-search") || target;
      }
      var disclosure = target.closest("details");
      if (disclosure) disclosure.open = true;
      event.preventDefault();
      target.focus();
      target.scrollIntoView({block: "center"});
    });
  });
})();

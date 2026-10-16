(function () {
  "use strict";
  function enhance() {
    document.querySelectorAll("[data-deletion-review-form]").forEach(function (form) {
      if (form.dataset.reviewReady) return;
      form.dataset.reviewReady = "true";
      var decision = form.querySelector('[name="decision"]');
      var dateGroup = form.querySelector("[data-review-next-date]");
      var date = form.querySelector('[name="next_review_date"]');
      var removal = form.querySelector("[data-review-removal]");
      var confirmed = form.querySelector('[name="confirmed"]');
      var submit = form.querySelector("[data-review-submit]");
      function update() {
        var deleting = decision.value === "DELETE";
        var scheduling = decision.value === "KEEP" || decision.value === "RESTORE";
        dateGroup.hidden = !scheduling && !dateGroup.querySelector(".field-errors");
        date.disabled = !scheduling && dateGroup.hidden;
        date.required = scheduling;
        removal.hidden = !deleting;
        confirmed.disabled = !deleting;
        confirmed.required = deleting;
        submit.textContent = deleting ? "Confirm removal from repository" : decision.value === "RESTORE" ? "Restore document" : scheduling ? "Keep document and save review date" : "Record decision";
        submit.classList.toggle("btn-danger", deleting);
        submit.classList.toggle("btn-udm", !deleting);
      }
      decision.addEventListener("change", function () {
        if (decision.value === "DELETE") date.value = "";
        else confirmed.checked = false;
        update();
      });
      update();
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", enhance);
  else enhance();
  document.addEventListener("htmx:afterSwap", enhance);
}());

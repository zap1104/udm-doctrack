/* Account preferences apply immediately and save in order, including resets. */
(function () {
  "use strict";
  var form = document.querySelector("[data-accessibility-form]");
  if (!form || !window.fetch) return;
  var root = document.documentElement;
  var defaults = {text_size: "14", high_contrast: false, motion: "system", underline_links: false, spacing: "standard", larger_targets: false};
  var names = Object.keys(defaults);
  var status = form.querySelector("[data-accessibility-status]");
  var retry = form.querySelector("[data-accessibility-retry]");
  var pending = null;
  var saving = false;

  function fields() {
    var values = {};
    names.forEach(function (name) {
      var field = form.elements.namedItem(name);
      if (field.dataset.preferenceOn) {
        values[name] = field.checked ? field.dataset.preferenceOn : field.dataset.preferenceOff;
      } else values[name] = typeof defaults[name] === "boolean" ? field.checked : field.value;
    });
    return values;
  }

  function apply(values) {
    root.setAttribute("data-a11y-text-size", values.text_size);
    root.setAttribute("data-a11y-contrast", values.high_contrast ? "high" : "standard");
    root.setAttribute("data-a11y-motion", values.motion);
    root.setAttribute("data-a11y-links", values.underline_links ? "underline" : "default");
    root.setAttribute("data-a11y-spacing", values.spacing);
    root.setAttribute("data-a11y-targets", values.larger_targets ? "large" : "standard");
  }

  function announce(message, failed) {
    status.textContent = message;
    retry.hidden = !failed;
  }

  async function savePending() {
    if (saving || !pending) return;
    saving = true;
    form.setAttribute("aria-busy", "true");
    while (pending) {
      var next = pending;
      pending = null;
      announce("Saving…", false);
      var data = new window.URLSearchParams();
      data.set("csrfmiddlewaretoken", form.elements.namedItem("csrfmiddlewaretoken").value);
      data.set("action", next.reset ? "reset" : "save");
      names.forEach(function (name) { data.set(name, String(next.values[name])); });
      try {
        var response = await window.fetch(form.getAttribute("action"), {
          method: "POST", credentials: "same-origin", keepalive: true,
          headers: {"Accept": "application/json"}, body: data,
        });
        if (!response.ok || response.redirected) throw new Error("Settings not saved");
        var result = await response.json();
        if (!result.preferences) throw new Error("Settings not saved");
        if (!pending) announce(next.reset ? "Defaults restored." : "Saved.", false);
      } catch (_) {
        // Keep the latest choice visible and retryable; never claim it is saved.
        pending = pending || next;
        announce("Changes aren’t saved yet. Retry, or reload and sign in again.", true);
        break;
      }
    }
    saving = false;
    form.removeAttribute("aria-busy");
  }

  function queue(reset) {
    if (reset) {
      names.forEach(function (name) {
        var field = form.elements.namedItem(name);
        if (field.dataset.preferenceOn) field.checked = defaults[name] === field.dataset.preferenceOn;
        else if (typeof defaults[name] === "boolean") field.checked = defaults[name];
        else field.value = defaults[name];
      });
    }
    var values = fields();
    apply(values);
    pending = {values: values, reset: reset};
    savePending();
  }

  form.addEventListener("change", function (event) {
    if (names.indexOf(event.target.name) !== -1) queue(false);
  });
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    queue(Boolean(event.submitter && event.submitter.hasAttribute("data-accessibility-reset")));
  });
  retry.addEventListener("click", savePending);
  var panel = form.closest("#accessibility-panel");
  if (panel) panel.addEventListener("hidden.bs.offcanvas", function () {
    // The dropdown link is hidden after opening the panel; return keyboard
    // focus to its visible account-menu button instead.
    var account = document.querySelector(".topbar-account-button");
    if (account) account.focus();
  });
  form.querySelector("[data-accessibility-save]").hidden = true;
  announce("Changes save automatically.", false);
})();

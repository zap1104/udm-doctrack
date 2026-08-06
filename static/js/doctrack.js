/* UDM DocTrack — small, dependency-free helpers.
   Everything here is progressive enhancement: if this file fails to load,
   every form still works as a plain HTML form. */
(function () {
  "use strict";

  /* ----------------------------------------------------------------------
     1. Searchable multi-select (used for "receiving offices")
     Turns a <select multiple> into a filter box + checkbox list.
  ---------------------------------------------------------------------- */
  function buildSearchableMultiSelect(select) {
    if (select.dataset.enhanced === "1") return;
    select.dataset.enhanced = "1";

    var wrapper = document.createElement("div");
    wrapper.className = "multiselect";

    var filter = document.createElement("input");
    filter.type = "search";
    filter.className = "form-control form-control-sm mb-2";
    filter.placeholder = select.dataset.placeholder || "Type to filter offices…";
    filter.setAttribute("aria-label", "Filter options");

    var list = document.createElement("div");
    list.className = "multiselect-list";

    var summary = document.createElement("div");
    summary.className = "multiselect-summary";

    function refreshSummary() {
      var chosen = Array.prototype.filter
        .call(select.options, function (option) { return option.selected; })
        .map(function (option) { return option.text; });
      if (!chosen.length) {
        summary.textContent = "No office selected yet.";
        return;
      }
      summary.innerHTML = "";
      chosen.forEach(function (label, index) {
        var chip = document.createElement("span");
        chip.className = "tag-chip";
        chip.textContent = (index === 0 ? "1st receiver: " : "") + label;
        summary.appendChild(chip);
      });
    }

    Array.prototype.forEach.call(select.options, function (option) {
      var row = document.createElement("label");
      row.className = "multiselect-option";

      var box = document.createElement("input");
      box.type = "checkbox";
      box.checked = option.selected;
      box.addEventListener("change", function () {
        option.selected = box.checked;
        refreshSummary();
      });

      var text = document.createElement("span");
      text.textContent = option.text;

      row.appendChild(box);
      row.appendChild(text);
      row.dataset.search = option.text.toLowerCase();
      list.appendChild(row);
    });

    filter.addEventListener("input", function () {
      var needle = filter.value.trim().toLowerCase();
      Array.prototype.forEach.call(list.children, function (row) {
        row.style.display = !needle || row.dataset.search.indexOf(needle) !== -1 ? "" : "none";
      });
    });

    select.classList.add("visually-hidden");
    select.parentNode.insertBefore(wrapper, select);
    wrapper.appendChild(filter);
    wrapper.appendChild(list);
    wrapper.appendChild(summary);
    wrapper.appendChild(select);
    refreshSummary();
  }

  document.querySelectorAll("select[multiple].js-searchable").forEach(buildSearchableMultiSelect);

  /* ----------------------------------------------------------------------
     2. File inputs: show what was picked, and warn on oversized files.
  ---------------------------------------------------------------------- */
  var maxMb = parseInt(document.body.dataset.maxUploadMb || "25", 10);

  document.querySelectorAll('input[type="file"]').forEach(function (input) {
    var readout = document.createElement("div");
    readout.className = "form-text mt-2";
    input.parentNode.appendChild(readout);

    input.addEventListener("change", function () {
      if (!input.files || !input.files.length) {
        readout.textContent = "";
        return;
      }
      var names = [];
      var oversized = [];
      Array.prototype.forEach.call(input.files, function (file) {
        names.push(file.name);
        if (file.size > maxMb * 1024 * 1024) oversized.push(file.name);
      });
      readout.textContent = names.length + " file(s): " + names.join(", ");
      if (oversized.length) {
        readout.classList.add("text-danger");
        readout.textContent += " — too large (limit " + maxMb + " MB): " + oversized.join(", ");
      } else {
        readout.classList.remove("text-danger");
      }
    });
  });

  /* ----------------------------------------------------------------------
     3. Guard rails on actions that cannot be undone.
     Receipt and completion are permanent, so ask once.
  ---------------------------------------------------------------------- */
  document.querySelectorAll("form[action*='/receipt/']").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      var ok = window.confirm(
        "Confirm that your office is taking custody of this document now?\n\n" +
        "The date and time are recorded permanently and cannot be edited later."
      );
      if (!ok) event.preventDefault();
    });
  });

  document.querySelectorAll("form[action*='/complete/']").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      var ok = window.confirm(
        "Mark this document as completed?\n\n" +
        "It leaves the active tracking queue and moves to the searchable archive."
      );
      if (!ok) event.preventDefault();
    });
  });

  /* ----------------------------------------------------------------------
     4. Stop double submissions — a second click must not create a second record.
  ---------------------------------------------------------------------- */
  document.querySelectorAll("form").forEach(function (form) {
    form.addEventListener("submit", function () {
      window.setTimeout(function () {
        form.querySelectorAll('button[type="submit"]').forEach(function (button) {
          button.disabled = true;
          if (!button.dataset.originalText) {
            button.dataset.originalText = button.textContent;
            button.textContent = "Working…";
          }
        });
      }, 0);
    });
  });

  /* ----------------------------------------------------------------------
     5. Keyboard shortcut: "/" focuses the top search box.
  ---------------------------------------------------------------------- */
  document.addEventListener("keydown", function (event) {
    if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey) return;
    var tag = (event.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return;
    var box = document.querySelector(".topbar-search input");
    if (box) {
      event.preventDefault();
      box.focus();
    }
  });

  /* ----------------------------------------------------------------------
     6. Sign-in lockout countdown (templates/accounts/lockout.html).
     Lives here rather than inline in the template so it still runs once
     ENABLE_CSP is switched on — CSP_SCRIPT_SRC permits no inline scripts.
  ---------------------------------------------------------------------- */
  (function () {
    var wrap = document.querySelector("[data-lockout-seconds]");
    if (!wrap) return;

    var clock = wrap.querySelector("[data-lockout-clock]");
    var status = wrap.querySelector("[data-lockout-status]");
    var heading = document.querySelector("[data-lockout-heading]");
    if (!clock) return;

    var seconds = parseInt(wrap.dataset.lockoutSeconds, 10);
    if (isNaN(seconds) || seconds < 0) seconds = 0;

    /* Count towards a fixed deadline instead of subtracting a second per tick.
       Browsers throttle timers in background tabs, so a decrementing counter
       falls behind real time — and this countdown is meant to read the same
       everywhere, including on a device that sat in the background. */
    var deadline = Date.now() + seconds * 1000;
    var timer = null;

    function pad(value) {
      return (value < 10 ? "0" : "") + value;
    }

    /* Waits reach hours at the higher lockout stages, where "120:00" would be
       nonsense — show HH:MM:SS once past an hour. */
    function format(total) {
      var hours = Math.floor(total / 3600);
      var minutes = Math.floor((total % 3600) / 60);
      return (hours > 0 ? pad(hours) + ":" : "") + pad(minutes) + ":" + pad(total % 60);
    }

    function finish() {
      clock.textContent = format(0);
      if (heading) heading.textContent = "Sign-in is unlocked";
      if (status) status.textContent = "The waiting time is over. You can try signing in again.";
      var back = wrap.dataset.lockoutRedirect;
      if (back) window.setTimeout(function () { window.location.href = back; }, 1500);
    }

    function tick() {
      var left = Math.round((deadline - Date.now()) / 1000);
      if (left <= 0) {
        if (timer) window.clearInterval(timer);
        finish();
        return;
      }
      clock.textContent = format(left);
    }

    tick();
    if (seconds > 0) timer = window.setInterval(tick, 1000);
  })();
})();

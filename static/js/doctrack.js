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
        chip.textContent = (index === 0 ? "Primary Receiver: " : "Additional: ") + label;
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
        "It leaves the active tracking queue and becomes available in Document Management."
      );
      if (!ok) event.preventDefault();
    });
  });

  /* ----------------------------------------------------------------------
     4. Stop double submissions — a second click must not create a second record.
  ---------------------------------------------------------------------- */
  document.querySelectorAll("form").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      window.setTimeout(function () {
        if (event.defaultPrevented) return;
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
     5. Phase 2 form helpers.
     These keep the existing backend field names while presenting the new UI.
  ---------------------------------------------------------------------- */
  document.documentElement.classList.add("phase2-js");

  function localDateValue(date) {
    var year = date.getFullYear();
    var month = String(date.getMonth() + 1).padStart(2, "0");
    var day = String(date.getDate()).padStart(2, "0");
    return year + "-" + month + "-" + day;
  }

  function startOfToday() {
    var now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), now.getDate());
  }

  document.querySelectorAll("[data-due-date-proxy]").forEach(function (wrapper) {
    var dateInput = wrapper.querySelector("[data-due-date-input]");
    var dueDaysInput = document.getElementById(wrapper.dataset.dueDaysId);
    if (!dateInput || !dueDaysInput) return;

    var today = startOfToday();
    dateInput.min = localDateValue(today);

    var initialDays = parseInt(dueDaysInput.value || "0", 10);
    if (initialDays > 0) {
      var initialDate = new Date(today);
      initialDate.setDate(initialDate.getDate() + initialDays);
      dateInput.value = localDateValue(initialDate);
    }

    function syncDueDays() {
      if (!dateInput.value) {
        dueDaysInput.value = "0";
        return;
      }
      var selected = new Date(dateInput.value + "T00:00:00");
      var difference = Math.ceil((selected - today) / 86400000);
      dueDaysInput.value = String(Math.max(0, difference));
    }

    dateInput.addEventListener("change", syncDueDays);
    if (wrapper.closest("form")) wrapper.closest("form").addEventListener("submit", syncDueDays);
  });

  document.querySelectorAll("[data-combined-notes]").forEach(function (wrapper) {
    var instructions = document.getElementById(wrapper.dataset.instructionsId);
    var remarks = document.getElementById(wrapper.dataset.remarksId);
    if (!instructions || !remarks) return;

    if (!instructions.value && remarks.value) instructions.value = remarks.value;

    function syncNotes() {
      remarks.value = instructions.value;
    }

    instructions.addEventListener("input", syncNotes);
    if (wrapper.closest("form")) wrapper.closest("form").addEventListener("submit", syncNotes);
  });

  var actionSelect = document.querySelector("[data-requested-action-select]");
  if (actionSelect) {
    var storedAction = window.sessionStorage.getItem("doctrackRequestedAction");
    if (storedAction) actionSelect.value = storedAction;
    actionSelect.addEventListener("change", function () {
      window.sessionStorage.setItem("doctrackRequestedAction", actionSelect.value);
      var label = actionSelect.options[actionSelect.selectedIndex].text;
      window.sessionStorage.setItem("doctrackRequestedActionLabel", label);
    });
  }

  document.querySelectorAll("[data-requested-action-review]").forEach(function (target) {
    var label = window.sessionStorage.getItem("doctrackRequestedActionLabel");
    if (label) target.textContent = label;
  });

  document.querySelectorAll("[data-due-date-display]").forEach(function (target) {
    var days = parseInt(target.dataset.dueDays || "0", 10);
    if (days <= 0) return;
    var dueDate = startOfToday();
    dueDate.setDate(dueDate.getDate() + days);
    target.textContent = dueDate.toLocaleDateString(undefined, {
      year: "numeric",
      month: "long",
      day: "numeric"
    });
  });

  document.querySelectorAll("[data-back-to-edit]").forEach(function (button) {
    button.addEventListener("click", function () {
      window.history.back();
    });
  });


  /* ----------------------------------------------------------------------
     Phase 6 mobile navigation and shared accessibility helpers.
  ---------------------------------------------------------------------- */
  var sidebarToggle = document.querySelector("[data-sidebar-toggle]");
  var sidebar = document.getElementById("main-sidebar");
  var sidebarClosers = document.querySelectorAll("[data-sidebar-close]");

  function setSidebarOpen(open) {
    document.body.classList.toggle("sidebar-open", open);
    if (sidebarToggle) sidebarToggle.setAttribute("aria-expanded", open ? "true" : "false");
    if (open && sidebar) {
      var firstLink = sidebar.querySelector("a, button");
      if (firstLink) window.setTimeout(function () { firstLink.focus(); }, 0);
    }
  }

  if (sidebarToggle && sidebar) {
    sidebarToggle.addEventListener("click", function () { setSidebarOpen(true); });
    sidebarClosers.forEach(function (closer) {
      closer.addEventListener("click", function () { setSidebarOpen(false); });
    });
    sidebar.querySelectorAll("a").forEach(function (link) {
      link.addEventListener("click", function () {
        if (window.matchMedia("(max-width: 900px)").matches) setSidebarOpen(false);
      });
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && document.body.classList.contains("sidebar-open")) {
        setSidebarOpen(false);
        sidebarToggle.focus();
      }
    });
    window.addEventListener("resize", function () {
      if (!window.matchMedia("(max-width: 900px)").matches) setSidebarOpen(false);
    });
  }

  document.querySelectorAll(".form-field--error input, .form-field--error select, .form-field--error textarea").forEach(function (field) {
    field.setAttribute("aria-invalid", "true");
  });

  /* ----------------------------------------------------------------------
     6. Keyboard shortcut: "/" focuses the top search box.
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
})();

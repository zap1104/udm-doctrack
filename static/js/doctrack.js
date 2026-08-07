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

    var fieldLabel = select.id ? document.querySelector('label[for="' + select.id + '"]') : null;
    var noun = select.dataset.itemNoun || "office";

    var wrapper = document.createElement("div");
    wrapper.className = "multiselect";
    wrapper.setAttribute("role", "group");
    if (fieldLabel) {
      wrapper.setAttribute("aria-label", fieldLabel.textContent.replace(/\*/g, "").trim());
    }

    var filter = document.createElement("input");
    filter.type = "search";
    filter.className = "multiselect-search";
    filter.placeholder = select.dataset.placeholder || "Type to filter offices…";
    filter.setAttribute("aria-label", "Filter the list");

    var list = document.createElement("div");
    list.className = "multiselect-list";

    var empty = document.createElement("div");
    empty.className = "multiselect-empty";
    empty.hidden = true;

    var footer = document.createElement("div");
    footer.className = "multiselect-footer";

    var count = document.createElement("span");
    count.className = "multiselect-count";
    /* Announced politely: selecting is the whole job of this control, so the
       running total is worth speaking, unlike a per-second countdown. */
    count.setAttribute("role", "status");
    count.setAttribute("aria-live", "polite");

    var clear = document.createElement("button");
    clear.type = "button";  // inside a <form>, an unqualified button submits it
    clear.className = "multiselect-clear";
    clear.textContent = "Clear all";
    clear.hidden = true;

    var summary = document.createElement("div");
    summary.className = "multiselect-summary";

    var entries = [];

    function setSelected(option, isSelected) {
      option.selected = isSelected;
      refresh();
    }

    function buildChips(chosen) {
      summary.innerHTML = "";
      chosen.forEach(function (option) {
        var chip = document.createElement("span");
        chip.className = "tag-chip";

        var text = document.createElement("span");
        text.textContent = option.text;
        chip.appendChild(text);

        var remove = document.createElement("button");
        remove.type = "button";
        remove.className = "multiselect-chip-remove";
        remove.innerHTML = "&times;";
        remove.setAttribute("aria-label", "Remove " + option.text);
        remove.addEventListener("click", function () {
          setSelected(option, false);
          filter.focus();
        });
        chip.appendChild(remove);

        summary.appendChild(chip);
      });
    }

    function refresh() {
      var chosen = [];
      entries.forEach(function (entry) {
        entry.box.checked = entry.option.selected;
        entry.row.classList.toggle("is-selected", entry.option.selected);
        if (entry.option.selected) chosen.push(entry.option);
      });

      count.textContent = chosen.length
        ? chosen.length + " of " + entries.length + " selected"
        : "No " + noun + " selected yet";
      clear.hidden = chosen.length === 0;

      buildChips(chosen);
    }

    Array.prototype.forEach.call(select.options, function (option) {
      var row = document.createElement("label");
      row.className = "multiselect-option";

      var box = document.createElement("input");
      box.type = "checkbox";
      box.checked = option.selected;
      box.addEventListener("change", function () {
        setSelected(option, box.checked);
      });

      var text = document.createElement("span");
      text.textContent = option.text;

      row.appendChild(box);
      row.appendChild(text);
      row.dataset.search = option.text.toLowerCase();
      list.appendChild(row);
      entries.push({ option: option, row: row, box: box });
    });

    filter.addEventListener("input", function () {
      var needle = filter.value.trim().toLowerCase();
      var visible = 0;
      entries.forEach(function (entry) {
        var match = !needle || entry.row.dataset.search.indexOf(needle) !== -1;
        entry.row.hidden = !match;
        if (match) visible += 1;
      });
      empty.hidden = visible !== 0;
      empty.textContent = 'No ' + noun + ' matches "' + filter.value.trim() + '".';
    });

    clear.addEventListener("click", function () {
      entries.forEach(function (entry) { entry.option.selected = false; });
      refresh();
      filter.focus();
    });

    /* The checkbox list replaces the native control, so the select is taken
       out of the tab order and the accessibility tree — left in both, a
       screen reader would announce every office twice. It stays in the DOM
       because it is what actually submits. */
    select.classList.add("visually-hidden");
    select.setAttribute("aria-hidden", "true");
    select.setAttribute("tabindex", "-1");

    /* The field's <label> still points at the now-hidden select, so send
       clicks on it to the filter box instead of a control nobody can see. */
    if (fieldLabel) {
      fieldLabel.addEventListener("click", function (event) {
        event.preventDefault();
        filter.focus();
      });
    }

    select.parentNode.insertBefore(wrapper, select);
    footer.appendChild(count);
    footer.appendChild(clear);
    wrapper.appendChild(filter);
    wrapper.appendChild(list);
    wrapper.appendChild(empty);
    wrapper.appendChild(footer);
    wrapper.appendChild(summary);
    wrapper.appendChild(select);
    refresh();
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

  /* ----------------------------------------------------------------------
     Deadline: reveal the calendar only when the answer is "set a deadline".
     The panel is rendered visible, so with this script blocked the field is
     still complete and usable — hiding is the enhancement, not the default.
  ---------------------------------------------------------------------- */
  document.querySelectorAll("[data-deadline-field]").forEach(function (field) {
    var panel = field.querySelector("[data-deadline-panel]");
    var dateInput = field.querySelector("[data-deadline-date]");
    var radios = field.querySelectorAll("[data-deadline-choice]");
    if (!panel || !radios.length) return;

    function chosen() {
      var picked = field.querySelector("[data-deadline-choice]:checked");
      return picked ? picked.value : "";
    }

    function sync(focusDate) {
      var wantsDate = chosen() === "date";
      panel.hidden = !wantsDate;
      if (dateInput) {
        /* The field is only required once a deadline is asked for, which no
           static `required` attribute can express. The server enforces the
           same rule; this just saves a round trip. */
        dateInput.required = wantsDate;
        if (!wantsDate) dateInput.value = "";
        if (wantsDate && focusDate) dateInput.focus();
      }
    }

    radios.forEach(function (radio) {
      radio.addEventListener("change", function () { sync(true); });
    });
    sync(false);
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
     Reports: tab switching.
     Lives here rather than inline in the template so it still runs once
     ENABLE_CSP is switched on — CSP_SCRIPT_SRC permits no inline scripts.
  ---------------------------------------------------------------------- */
  (function () {
    var tabs = document.querySelectorAll("[data-report-tab]");
    var panels = document.querySelectorAll("[data-report-panel]");
    if (!tabs.length) return;

    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        var target = tab.dataset.reportTab;
        tabs.forEach(function (item) {
          var active = item === tab;
          item.classList.toggle("active", active);
          item.setAttribute("aria-selected", active ? "true" : "false");
        });
        panels.forEach(function (panel) {
          panel.classList.toggle("active", panel.dataset.reportPanel === target);
        });
      });
    });
  })();

  /* ----------------------------------------------------------------------
     Print auditing.
     Printing happens entirely in the browser, so a paper copy would otherwise
     leave no trace at all. Pages that opt in carry a [data-print-log] element;
     opening a print dialog — via the button OR Ctrl+P — posts one entry.
  ---------------------------------------------------------------------- */
  (function () {
    var marker = document.querySelector("[data-print-log]");
    if (!marker) return;

    document.querySelectorAll("[data-print-trigger]").forEach(function (button) {
      button.addEventListener("click", function () { window.print(); });
    });

    var url = marker.dataset.printLogUrl;
    var token = marker.dataset.printLogCsrf;
    if (!url || !token) return;

    /* One entry per print dialog, not per page view: a user who prints twice
       has produced two copies, but a dialog they cancel is still an attempt
       worth recording once. */
    var logging = false;

    function recordPrint() {
      if (logging) return;
      logging = true;
      window.setTimeout(function () { logging = false; }, 2000);

      var body = new URLSearchParams();
      body.set("label", marker.dataset.printLog || "a page");
      body.set("reference", marker.dataset.printLogReference || "");
      body.set("target_type", marker.dataset.printLogTargetType || "");
      body.set("target_id", marker.dataset.printLogTargetId || "");

      window.fetch(url, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "X-CSRFToken": token,
          "Content-Type": "application/x-www-form-urlencoded",
        },
        body: body.toString(),
        keepalive: true,  // the tab may close the moment printing finishes
      }).catch(function () {
        /* Auditing must never break printing. A failed entry is logged
           server-side on the next successful request instead. */
      });
    }

    if ("onbeforeprint" in window) {
      window.addEventListener("beforeprint", recordPrint);
    } else if (window.matchMedia) {
      /* Safari has no beforeprint event; its print stylesheet media query
         flips instead. */
      var printQuery = window.matchMedia("print");
      printQuery.addEventListener("change", function (event) {
        if (event.matches) recordPrint();
      });
    }
  })();

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

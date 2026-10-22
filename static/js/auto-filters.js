/* GET-only progressive enhancement. Server views remain the source of truth. */
(function () {
  "use strict";
  if (!window.htmx) return;
  var main = document.getElementById("main-content");
  var status = document.getElementById("filter-update-status");
  if (!main || !status) return;
  // Previous versions cached the whole body. These workspaces restore only
  // main content from the server, so discard the incompatible HTMX cache.
  try { window.localStorage.removeItem("htmx-history-cache"); } catch (_) { /* storage may be unavailable */ }
  var message = status.querySelector("[data-filter-message]");
  var retry = status.querySelector("[data-filter-retry]");
  var timer = null;
  var active = null;
  var source = null;
  var snapshot = null;
  var baselines = new WeakMap();
  var requestStates = new WeakMap();
  var restoringFocus = false;
  var swapping = false;
  var interaction = 0;
  var filterRequests = new WeakSet();
  var failedResponses = new WeakSet();
  var pendingHistory = null;
  var historyRetryPath = null;

  function cancelHistory() {
    var previous = pendingHistory;
    pendingHistory = null;
    historyRetryPath = null;
    if (previous) previous.xhr.abort();
  }

  function cancelPending() {
    window.clearTimeout(timer);
    active = null;
    snapshot = null;
    window.htmx.trigger(main, "htmx:abort");
  }

  function filterSource(element) {
    return element && element.matches && element.matches("form[data-auto-filter], [data-filter-link]");
  }

  function automatic(form) {
    return form && form.method.toLowerCase() === "get" && form.getAttribute("data-filter-mode") === "change";
  }

  function editable(field) {
    return field.name && !field.disabled && !field.readOnly && field.matches("input, select, textarea") &&
      !/^(hidden|submit|button|file|password|reset)$/.test(field.type);
  }

  function fieldValue(field) {
    if (/^(checkbox|radio)$/.test(field.type)) return field.checked;
    if (field.multiple) return Array.prototype.map.call(field.selectedOptions, function (option) { return option.value; });
    return field.value;
  }

  function fieldStates(form) {
    return Array.prototype.filter.call(form.elements, editable).map(function (field, index) {
      return {key: field.id || field.name + ":" + index, field: field, value: fieldValue(field)};
    });
  }

  function sameValue(first, second) {
    return JSON.stringify(first) === JSON.stringify(second);
  }

  function discarded(form, trigger) {
    if (!trigger || !trigger.hasAttribute || !trigger.hasAttribute("data-filter-discard")) return false;
    var scope = trigger.getAttribute("data-filter-discard");
    return !scope || scope === "all" || scope === "#" + form.id;
  }

  function captureDrafts(state) {
    var drafts = [];
    main.querySelectorAll("form[data-auto-filter]").forEach(function (form) {
      if (automatic(form) || !form.id || discarded(form, state.element)) return;
      var values = fieldStates(form).filter(function (entry) {
        // An explicit pill or removal link wins over a pending value for the
        // same parameter. Unrelated search drafts still survive the update.
        if (state.replaced.has(entry.field.name)) return false;
        // A submitted form adopts its server values. Only edits made since
        // that request began remain a draft; other forms keep pending edits.
        var applied = state.element === form ? state.fields.get(entry.key) : baselines.get(entry.field);
        return !sameValue(entry.value, applied);
      }).map(function (entry) { return {key: entry.key, name: entry.field.name, value: entry.value}; });
      if (values.length) drafts.push({id: form.id, values: values});
    });
    return drafts;
  }

  function restoreDrafts(drafts) {
    drafts.forEach(function (draft) {
      var form = document.getElementById(draft.id);
      if (!form || !form.matches("form[data-auto-filter]") || automatic(form)) return;
      var fields = fieldStates(form);
      draft.values.forEach(function (entry) {
        var target = fields.find(function (candidate) { return candidate.key === entry.key && candidate.field.name === entry.name; });
        if (!target) return;
        var field = target.field;
        if (/^(checkbox|radio)$/.test(field.type)) field.checked = entry.value;
        else if (field.matches("select")) {
          var wanted = field.multiple ? entry.value : [entry.value];
          var available = Array.prototype.map.call(field.options, function (option) { return option.value; });
          if (!wanted.every(function (value) { return available.indexOf(value) !== -1; })) return;
          Array.prototype.forEach.call(field.options, function (option) { option.selected = wanted.indexOf(option.value) !== -1; });
        } else field.value = entry.value;
        // Relevance readouts are local helpers; do not dispatch input on text
        // fields, which would unnecessarily fetch autocomplete suggestions.
        if (field.type === "range") field.dispatchEvent(new Event("input", {bubbles: true}));
      });
    });
  }

  function regionFor(element) {
    return element && element.closest ? element.closest("[data-filter-region][id]") : null;
  }

  function focusReplacement(previous) {
    if (!previous) return null;
    if (previous.id) {
      var byId = document.getElementById(previous.id);
      if (byId && main.contains(byId)) return byId;
    }
    var oldRegion = regionFor(previous);
    var scope = oldRegion && document.getElementById(oldRegion.id) || main;
    var oldForm = previous.closest("form[id]");
    if (oldForm) scope = document.getElementById(oldForm.id) || scope;
    var oldNav = previous.closest("nav[aria-label]");
    if (oldNav) {
      var label = oldNav.getAttribute("aria-label");
      var navigation = Array.prototype.filter.call(scope.querySelectorAll("nav[aria-label]"), function (nav) {
        return nav.getAttribute("aria-label") === label;
      });
      var oldScope = oldRegion || previous.closest("form[id]");
      var oldNavigation = oldScope && Array.prototype.filter.call(oldScope.querySelectorAll("nav[aria-label]"), function (nav) {
        return nav.getAttribute("aria-label") === label;
      });
      scope = navigation[oldNavigation ? oldNavigation.indexOf(oldNav) : 0] || scope;
    }
    var text = previous.textContent.trim().replace(/\s+/g, " ");
    var controls = Array.prototype.filter.call(scope.querySelectorAll("a, button, input, select, textarea"), function (candidate) {
      return candidate.tagName === previous.tagName && candidate.name === previous.name &&
        candidate.type === previous.type && candidate.getAttribute("rel") === previous.getAttribute("rel") &&
        candidate.textContent.trim().replace(/\s+/g, " ") === text;
    });
    // Next/Previous and pills often have no ID, and their destination changes
    // on each page. Prefer the same destination, then the equivalent control.
    var equivalent = controls.find(function (candidate) { return candidate.getAttribute("href") === previous.getAttribute("href"); }) || controls[0];
    return equivalent || scope.querySelector("[aria-current]") || scope;
  }

  function tables() {
    return Array.prototype.map.call(main.querySelectorAll(".table-responsive"), function (table, index) {
      var region = regionFor(table);
      var local = region ? Array.prototype.indexOf.call(region.querySelectorAll(".table-responsive"), table) : index;
      return {key: table.id || (region ? region.id : "main") + ":table:" + local, element: table};
    });
  }

  function captureViewport(trigger) {
    if (typeof window.scrollTo !== "function") return null;
    var anchor = null;
    var score = Infinity;
    main.querySelectorAll("[data-filter-region][id]").forEach(function (region) {
      var rect = region.getBoundingClientRect();
      if (!rect.height || rect.bottom <= 0 || rect.top >= window.innerHeight) return;
      var middle = window.innerHeight / 2;
      var distance = rect.top <= middle && rect.bottom >= middle ? 0 : Math.min(Math.abs(rect.top - middle), Math.abs(rect.bottom - middle));
      var candidate = distance * 100000 + rect.height;
      if (candidate < score) {
        anchor = {id: region.id, top: rect.top, offset: middle - rect.top};
        score = candidate;
      }
    });
    var affected = regionFor(trigger);
    return {x: window.scrollX, y: window.scrollY, anchor: anchor, affected: affected && affected.id,
      interaction: interaction, tables: tables().map(function (entry) {
        return {key: entry.key, left: entry.element.scrollLeft, top: entry.element.scrollTop};
      })};
  }

  function headerInset() {
    var header = document.querySelector(".topbar");
    if (!header) return 0;
    var position = window.getComputedStyle(header).position;
    var rect = header.getBoundingClientRect();
    return /^(fixed|sticky)$/.test(position) && rect.top <= 1 ? Math.max(0, rect.bottom) : 0;
  }

  function restoreViewport(viewport) {
    if (!viewport || viewport.interaction !== interaction) return;
    var maximum = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
    var anchor = viewport.anchor && document.getElementById(viewport.anchor.id);
    var desired = viewport.y;
    var retained = false;
    if (anchor) {
      var rect = anchor.getBoundingClientRect();
      desired = window.scrollY + rect.top - viewport.anchor.top;
      retained = viewport.anchor.offset <= rect.height && desired >= 0 && desired <= maximum;
    }
    if (!retained) {
      var affected = viewport.affected && document.getElementById(viewport.affected);
      if (affected) {
        var bounds = affected.getBoundingClientRect();
        desired = window.scrollY + bounds.top + bounds.height / 2 - (window.innerHeight + headerInset()) / 2;
      } else desired = viewport.y;
    }
    window.scrollTo({left: viewport.x, top: Math.max(0, Math.min(maximum, desired)), behavior: "instant"});
  }

  function redirectedPage(xhr, path) {
    if (!xhr.responseURL) return null;
    var url = new URL(xhr.responseURL, window.location.href);
    var requested = new URL(path, window.location.href);
    return url.origin === window.location.origin && url.pathname !== requested.pathname ? url : null;
  }

  function announce(text, failed) {
    status.hidden = false;
    message.textContent = text;
    retry.hidden = !failed;
    status.classList.toggle("is-error", Boolean(failed));
    // Keep routine announcements accessible without taking up layout space.
    status.classList.toggle("visually-hidden", !failed);
  }

  function enhance() {
    var forms = main.querySelectorAll("form[data-auto-filter]");
    if (!forms.length) status.hidden = true;
    forms.forEach(function (form) {
      form.querySelectorAll("[data-filter-fallback]").forEach(function (button) { button.hidden = automatic(form); });
      if (!automatic(form)) fieldStates(form).forEach(function (entry) {
        if (!baselines.has(entry.field)) baselines.set(entry.field, entry.value);
      });
    });
    // Only list controls on the same page. Detail, export, print, and POST
    // actions retain their normal navigation/confirmation behavior.
    main.querySelectorAll("[data-filter-reset], .pill-toggle[href], .notification-filter, .page-size-option[href], .page-link[href], .active-filters a, .repository-type-folders a, .repository-tags a, .repository-view, .repository-breadcrumb a, .admin-filter-summary a, .report-scope-card a, .repository-retention a").forEach(function (link) {
      var url = new URL(link.href, window.location.href);
      if (url.origin !== window.location.origin || url.pathname !== window.location.pathname) return;
      link.setAttribute("data-filter-link", "");
      link.setAttribute("hx-get", link.getAttribute("href"));
      link.setAttribute("hx-target", "#main-content");
      link.setAttribute("hx-select", "#main-content > *");
      link.setAttribute("hx-swap", "innerHTML show:none");
      link.setAttribute("hx-push-url", "true");
      link.setAttribute("hx-sync", "#main-content:replace");
      link.setAttribute("hx-params", "none");
      window.htmx.process(link);
    });
    if (forms.length && !active) announce("Filters ready.", false);
  }

  document.addEventListener("change", function (event) {
    var field = event.target;
    var form = field.closest && field.closest("form[data-auto-filter]");
    if (!automatic(form) || !field.matches("select") || field.multiple) return;
    // Cancel immediately, before the debounce: an old response arriving in
    // that gap would replace the form holding the newest selection.
    cancelHistory();
    cancelPending();
    timer = window.setTimeout(function () {
      if (form.isConnected) window.htmx.trigger(form, "filter-change");
    }, 300);
  });
  document.addEventListener("submit", function (event) {
    if (filterSource(event.target)) { cancelHistory(); window.clearTimeout(timer); }
  }, true);
  document.addEventListener("click", function (event) {
    if (event.target.closest && event.target.closest("[data-filter-link]")) { cancelHistory(); window.clearTimeout(timer); }
  }, true);

  document.addEventListener("htmx:configRequest", function (event) {
    var form = event.detail.elt;
    if (!form || !form.matches("form[data-auto-filter]")) return;
    var group = form.getAttribute("data-filter-sync");
    if (!group || !automatic(form)) return;
    // Only explicitly related Dashboard/Reports selectors share live values.
    // Manual searches keep the other forms' applied, hidden carry fields.
    var values = {};
    main.querySelectorAll("form[data-auto-filter]").forEach(function (other) {
      if (!automatic(other) || other.getAttribute("data-filter-sync") !== group) return;
      Array.prototype.forEach.call(other.elements, function (field) {
        if (!field.name || field.disabled || !field.matches("select")) return;
        var selected = field.multiple ? Array.prototype.map.call(field.selectedOptions, function (option) { return option.value; }) : [field.value];
        values[field.name] = (values[field.name] || []).concat(selected);
      });
    });
    Object.keys(values).forEach(function (name) {
      event.detail.parameters[name] = values[name].length === 1 ? values[name][0] : values[name];
    });
  });

  document.addEventListener("htmx:beforeRequest", function (event) {
    if (!filterSource(event.detail.elt)) return;
    cancelHistory();
    source = event.detail.elt;
    active = event.detail.xhr;
    filterRequests.add(active);
    snapshot = null;
    var fields = new Map();
    if (source.matches("form")) fieldStates(source).forEach(function (entry) { fields.set(entry.key, entry.value); });
    var replaced = new Set();
    if (source.matches("[data-filter-link]")) {
      var applied = new URL(window.location.href).searchParams;
      var destination = new URL(source.href, window.location.href).searchParams;
      var keys = new Set();
      applied.forEach(function (_, name) { keys.add(name); });
      destination.forEach(function (_, name) { keys.add(name); });
      keys.forEach(function (name) {
        if (!sameValue(applied.getAll(name), destination.getAll(name))) replaced.add(name);
      });
    }
    requestStates.set(active, {element: source, fields: fields, replaced: replaced});
    main.setAttribute("aria-busy", "true");
    announce("Updating results…", false);
  });

  document.addEventListener("htmx:beforeSwap", function (event) {
    if (filterRequests.has(event.detail.xhr) && event.detail.xhr !== active) {
      event.detail.shouldSwap = false;
      return;
    }
    if (event.detail.xhr !== active) return;
    if (event.detail.xhr.status !== 200) return;
    // The sign-in page also has #main-content. Check redirects before accepting
    // that content so its form and page shell keep the correct URL.
    var url = redirectedPage(event.detail.xhr, window.location.href);
    if (url) {
      failedResponses.add(event.detail.xhr);
      event.detail.shouldSwap = false;
      window.location.assign(url.href);
      return;
    }
    var response = new DOMParser().parseFromString(event.detail.serverResponse, "text/html");
    if (!response.querySelector("#main-content")) {
      failedResponses.add(event.detail.xhr);
      event.detail.shouldSwap = false;
      announce("Could not update results. Previous results are still shown.", true);
      return;
    }
    var focused = document.activeElement;
    // Initial-page autofocus must not steal focus from the filter that caused
    // an in-place update (notably Search's query field).
    response.querySelectorAll("#main-content [autofocus]").forEach(function (field) { field.removeAttribute("autofocus"); });
    event.detail.serverResponse = response.documentElement.outerHTML;
    var state = requestStates.get(event.detail.xhr);
    snapshot = {xhr: event.detail.xhr, focus: main.contains(focused) ? focused : null, details: [],
      drafts: captureDrafts(state), viewport: captureViewport(state.element)};
    swapping = true;
    main.querySelectorAll("details[id]").forEach(function (detail) {
      snapshot.details.push({id: detail.id, open: detail.open});
    });
  });

  document.addEventListener("htmx:afterSwap", function (event) {
    if (event.detail.target !== main) return;
    enhance();
    if (snapshot) {
      snapshot.details.forEach(function (detail) {
        var replacement = document.getElementById(detail.id);
        if (replacement) replacement.open = detail.open;
      });
      restoreDrafts(snapshot.drafts);
      if (snapshot.viewport) {
        var replacements = tables();
        snapshot.viewport.tables.forEach(function (previousTable) {
          var target = replacements.find(function (entry) { return entry.key === previousTable.key; });
          if (target) { target.element.scrollLeft = previousTable.left; target.element.scrollTop = previousTable.top; }
        });
      }
      var previous = snapshot.focus;
      var replacement = focusReplacement(previous);
      if (replacement && !previous.isConnected &&
          (document.activeElement === document.body || document.activeElement === main)) {
        if (!replacement.matches("a[href], button, input, select, textarea, [tabindex]")) replacement.setAttribute("tabindex", "-1");
        restoringFocus = true;
        replacement.focus({preventScroll: true});
        restoringFocus = false;
      }
    }
    swapping = false;
  });

  document.addEventListener("htmx:afterSettle", function (event) {
    if (event.detail.target !== main || !snapshot || event.detail.xhr !== snapshot.xhr) return;
    var settled = snapshot;
    var finish = function () {
      if (snapshot !== settled) return;
      restoreViewport(settled.viewport);
      snapshot = null;
    };
    if (window.requestAnimationFrame) window.requestAnimationFrame(finish);
    else finish();
  });
  ["wheel", "touchmove", "pointerdown", "keydown"].forEach(function (name) {
    window.addEventListener(name, function () { interaction++; }, {passive: true});
  });
  document.addEventListener("focusin", function () {
    if (snapshot && !swapping && !restoringFocus) interaction++;
  });

  document.addEventListener("htmx:afterRequest", function (event) {
    if (event.detail.xhr !== active) return;
    main.removeAttribute("aria-busy");
    active = null;
    if (event.detail.successful && event.detail.xhr.status === 200 && !failedResponses.has(event.detail.xhr)) announce("Results updated.", false);
    else announce("Could not update results. Previous results are still shown.", true);
  });
  retry.addEventListener("click", function () {
    if (historyRetryPath) {
      // Retry this history entry without adding another entry or reloading the
      // page shell. HTMX's popstate handler resolves its current URL again.
      window.dispatchEvent(new PopStateEvent("popstate", {state: window.history.state}));
      return;
    }
    if (source && source.isConnected) window.htmx.trigger(source, source.matches("form") ? (automatic(source) ? "filter-change" : "submit") : "click");
  });
  document.addEventListener("htmx:historyCacheMiss", function (event) {
    var xhr = event.detail.xhr;
    var path = event.detail.path;
    cancelPending();
    cancelHistory();
    var request = {xhr: xhr, path: path};
    pendingHistory = request;
    main.setAttribute("aria-busy", "true");
    announce("Updating results…", false);
    function failed() {
      if (pendingHistory !== request) return;
      pendingHistory = null;
      historyRetryPath = path;
      main.removeAttribute("aria-busy");
      announce("Could not update results. Previous results are still shown.", true);
    }
    // HTMX 1.9.12 bypasses beforeSwap for history and does not honor cancelling
    // historyCacheMissLoad. Guard the native load before its swap handler runs.
    xhr.addEventListener("load", function (loadEvent) {
      if (pendingHistory !== request) { loadEvent.stopImmediatePropagation(); return; }
      if (xhr.status < 200 || xhr.status >= 400) {
        loadEvent.stopImmediatePropagation();
        failed();
        return;
      }
      var url = redirectedPage(xhr, path);
      if (url) {
        loadEvent.stopImmediatePropagation();
        pendingHistory = null;
        window.location.replace(url.href);
        return;
      }
      var response = new DOMParser().parseFromString(xhr.responseText, "text/html");
      var region = response.querySelector("[hx-history-elt], [data-hx-history-elt]");
      if (!region || region.id !== main.id) {
        loadEvent.stopImmediatePropagation();
        failed();
        return;
      }
      pendingHistory = null;
    }, {capture: true, once: true});
    xhr.addEventListener("error", failed, {once: true});
    xhr.addEventListener("timeout", failed, {once: true});
  });
  document.addEventListener("htmx:historyRestore", function () {
    cancelPending();
    cancelHistory();
    main.removeAttribute("aria-busy");
    enhance();
  });
  // Capture precedes HTMX's onpopstate handler. Cancel the older history XHR
  // before that handler starts the new one, rather than aborting its new load.
  window.addEventListener("popstate", function () {
    cancelPending();
    cancelHistory();
    main.removeAttribute("aria-busy");
  }, {capture: true});
  enhance();
})();

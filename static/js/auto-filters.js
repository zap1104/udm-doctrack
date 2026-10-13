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
  var filterRequests = new WeakSet();
  var failedResponses = new WeakSet();

  function cancelPending() {
    window.clearTimeout(timer);
    active = null;
    snapshot = null;
    window.htmx.trigger(main, "htmx:abort");
  }

  function filterSource(element) {
    return element && element.matches && element.matches("form[data-auto-filter], [data-filter-link]");
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
    if (!forms.length) { status.hidden = true; return; }
    forms.forEach(function (form) {
      form.querySelectorAll("[data-filter-fallback]").forEach(function (button) { button.hidden = true; });
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
    if (!active) announce("Filters update automatically.", false);
  }

  document.addEventListener("change", function (event) {
    var field = event.target;
    var form = field.closest && field.closest("form[data-auto-filter]");
    if (!form || !field.matches('select, input[type="date"], input[type="checkbox"], input[type="range"]')) return;
    // Cancel immediately, before the debounce: an old response arriving in
    // that gap would replace the form holding the newest selection.
    cancelPending();
    timer = window.setTimeout(function () {
      if (form.isConnected) window.htmx.trigger(form, "filter-change");
    }, 300);
  });
  document.addEventListener("submit", function (event) {
    if (filterSource(event.target)) window.clearTimeout(timer);
  }, true);
  document.addEventListener("click", function (event) {
    if (event.target.closest && event.target.closest("[data-filter-link]")) window.clearTimeout(timer);
  }, true);

  document.addEventListener("htmx:configRequest", function (event) {
    var form = event.detail.elt;
    if (!form || !form.matches("form[data-auto-filter]")) return;
    // Dashboard and Audit Log have independent filter forms. Use current
    // visible selections across them, rather than stale carried hidden values.
    var values = {};
    main.querySelectorAll("form[data-auto-filter]").forEach(function (other) {
      Array.prototype.forEach.call(other.elements, function (field) {
        if (!field.name || field.disabled || !field.matches("select, input") ||
            /^(hidden|submit|button|file)$/.test(field.type) ||
            (/^(checkbox|radio)$/.test(field.type) && !field.checked)) return;
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
    source = event.detail.elt;
    active = event.detail.xhr;
    filterRequests.add(active);
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
    snapshot = {focus: main.contains(focused) ? focused : null, details: []};
    main.querySelectorAll("details[id]").forEach(function (detail) {
      snapshot.details.push({id: detail.id, open: detail.open});
    });
  });

  document.addEventListener("htmx:afterSwap", function (event) {
    if (event.detail.target !== main) return;
    if (snapshot) {
      snapshot.details.forEach(function (detail) {
        var replacement = document.getElementById(detail.id);
        if (replacement) replacement.open = detail.open;
      });
      var previous = snapshot.focus;
      var replacement = previous && previous.id && document.getElementById(previous.id);
      if (replacement && !previous.isConnected &&
          (document.activeElement === document.body || document.activeElement === main)) {
        if (previous.matches('input[type="text"], input[type="search"], input:not([type])')) replacement.value = previous.value;
        replacement.focus({preventScroll: true});
      }
      snapshot = null;
    }
    enhance();
  });

  document.addEventListener("htmx:afterRequest", function (event) {
    if (event.detail.xhr !== active) return;
    main.removeAttribute("aria-busy");
    active = null;
    if (event.detail.successful && event.detail.xhr.status === 200 && !failedResponses.has(event.detail.xhr)) announce("Results updated. Filters update automatically.", false);
    else announce("Could not update results. Previous results are still shown.", true);
  });
  retry.addEventListener("click", function () {
    if (source && source.isConnected) window.htmx.trigger(source, source.matches("form") ? "filter-change" : "click");
  });
  document.addEventListener("htmx:historyCacheMiss", function (event) {
    var xhr = event.detail.xhr;
    var path = event.detail.path;
    // HTMX 1.9.12 bypasses beforeSwap for history and does not honor cancelling
    // historyCacheMissLoad. Guard the native load before its swap handler runs.
    xhr.addEventListener("load", function (loadEvent) {
      if (xhr.status < 200 || xhr.status >= 400) return;
      var url = redirectedPage(xhr, path);
      if (!url) return;
      loadEvent.stopImmediatePropagation();
      cancelPending();
      window.location.replace(url.href);
    }, {capture: true, once: true});
  });
  document.addEventListener("htmx:historyRestore", function () {
    cancelPending();
    main.removeAttribute("aria-busy");
    enhance();
  });
  window.addEventListener("popstate", cancelPending);
  enhance();
})();

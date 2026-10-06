/* Running-total bars use the same popup and emphasis as the ring slices. */
(function () {
  "use strict";

  var active = null;
  var dismissed = null;
  var pointerFocus = null;
  var pointerType = "mouse";

  function chartOf(node) {
    return node && node.closest ? node.closest("[data-hover-columns]") : null;
  }

  function itemsOf(chart) {
    return Array.prototype.slice.call(chart.querySelectorAll("[data-column-hover]"));
  }

  function clear() {
    if (!active) return;
    active.item.classList.remove("is-active");
    active.chart.classList.remove("has-active-column");
    active.tip.hidden = true;
    var live = active.frame.querySelector("[data-column-announce]");
    if (live) live.textContent = "";
    active = null;
  }

  function show(item, mode, clientX, clientY) {
    if (!item || item === dismissed) return;
    dismissed = null;
    var chart = chartOf(item);
    var frame = chart && chart.closest("[data-column-hover-frame]");
    var tip = frame && frame.querySelector("[data-column-tooltip]");
    if (!tip) return;
    var plot = chart.getBoundingClientRect();
    if (!plot.width || !plot.height) { clear(); return; }
    if (!active || active.item !== item) clear();
    active = {item: item, chart: chart, frame: frame, tip: tip, mode: mode};
    item.classList.add("is-active");
    chart.classList.add("has-active-column");

    var value = item.getAttribute("data-column-value");
    var label = item.getAttribute("data-column-label") + " \u00b7 " + item.getAttribute("data-column-month");
    tip.querySelector("[data-column-tooltip-value]").textContent = value;
    tip.querySelector("[data-column-tooltip-label]").textContent = label;
    var bar = item.querySelector(".column");
    var colors = {created: "var(--chart-created)", handover: "var(--status-pending)", completed: "var(--status-completed)"};
    tip.querySelector("[data-column-tooltip-key]").style.backgroundColor = bar
      ? window.getComputedStyle(bar).backgroundColor
      : colors[item.getAttribute("data-column-series")] || "var(--udm-muted)";
    tip.hidden = false;

    var box = frame.getBoundingClientRect();
    var hit = (bar || item).getBoundingClientRect();
    tip.style.maxWidth = Math.max(1, Math.min(240, box.width - 16)) + "px";
    var x = Number.isFinite(clientX) ? clientX : hit.left + hit.width / 2;
    var y = Number.isFinite(clientY) ? clientY : hit.top;
    tip.style.left = Math.max(8, Math.min(x - box.left - tip.offsetWidth / 2, box.width - tip.offsetWidth - 8)) + "px";
    var top = y - box.top - tip.offsetHeight - 12;
    var minimum = plot.top - box.top + 8;
    if (top < minimum) top = y - box.top + 18;
    tip.style.top = Math.max(minimum, Math.min(top, plot.top - box.top + plot.height - tip.offsetHeight - 8)) + "px";

    var live = frame.querySelector("[data-column-announce]");
    if (live) live.textContent = mode === "pointer" ? "" : label + ": " + value;
  }

  function initialize() {
    document.querySelectorAll("[data-hover-columns]").forEach(function (chart) {
      var frame = chart.closest("[data-column-hover-frame]");
      if (!frame || !frame.querySelector("[data-column-tooltip]")) return;
      itemsOf(chart).forEach(function (item) { item.removeAttribute("title"); });
      chart.querySelectorAll(".column-group").forEach(function (group) { group.removeAttribute("title"); });
    });
  }
  initialize();

  document.addEventListener("pointerdown", function (event) {
    pointerType = event.pointerType || "mouse";
    pointerFocus = chartOf(event.target);
  });

  document.addEventListener("pointermove", function (event) {
    if (event.pointerType === "touch") return;
    var item = event.target.closest && event.target.closest("[data-column-hover]");
    if (item) show(item, "pointer", event.clientX, event.clientY);
  });

  document.addEventListener("pointerout", function (event) {
    if (event.pointerType === "touch") return;
    var item = event.target.closest && event.target.closest("[data-column-hover]");
    var into = event.relatedTarget && event.relatedTarget.closest && event.relatedTarget.closest("[data-column-hover]");
    if (item && item !== into) {
      if (dismissed === item) dismissed = null;
      if (active && active.item === item && active.mode === "pointer") clear();
    }
  });

  document.addEventListener("click", function (event) {
    pointerFocus = null;
    var item = event.target.closest && event.target.closest("[data-column-hover]");
    if (!item) { clear(); dismissed = null; return; }
    if (pointerType === "touch" && active && active.item === item) { clear(); return; }
    dismissed = null;
    show(item, pointerType === "touch" ? "touch" : "pointer", event.clientX, event.clientY);
  });

  document.addEventListener("focusin", function (event) {
    var chart = event.target.matches && event.target.matches("[data-hover-columns]") ? event.target : null;
    if (chart && chart !== pointerFocus) {
      dismissed = null;
      var items = itemsOf(chart);
      show(items[items.length - 1], "keyboard");
    }
    pointerFocus = null;
  });

  document.addEventListener("focusout", function (event) {
    var chart = chartOf(event.target);
    if (chart && !chart.contains(event.relatedTarget)) { clear(); dismissed = null; }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      dismissed = active && active.item;
      clear();
      return;
    }
    var chart = chartOf(event.target);
    if (!chart) return;
    var items = itemsOf(chart);
    if (!items.length) return;
    var index = active && active.chart === chart ? items.indexOf(active.item) : -1;
    var next;
    if (event.key === "ArrowRight") next = index < 0 ? items.length - 1 : Math.min(items.length - 1, index + 1);
    else if (event.key === "ArrowLeft") next = index < 0 ? items.length - 1 : Math.max(0, index - 1);
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = items.length - 1;
    else return;
    event.preventDefault();
    dismissed = null;
    show(items[next], "keyboard");
  });

  window.addEventListener("resize", function () {
    if (active) show(active.item, active.mode);
  });
  window.addEventListener("beforeprint", clear);
  document.addEventListener("htmx:afterSwap", function () {
    if (active && !active.item.isConnected) clear();
    initialize();
  });
})();

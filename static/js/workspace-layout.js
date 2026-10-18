/* Present simple record tables as labelled rows when their container is narrow.
 * Keep the actual table semantics, links, form controls and selected values.
 * Interactive headers and charts retain their own existing responsive layouts.
 */
(function () {
  "use strict";
  function initializeTables() {
    document.querySelectorAll(".table-responsive > .table-udm").forEach(function (table) {
      if (table.dataset.recordLayoutReady || table.closest(".chart-table")) return;
      var head = table.querySelector("thead");
      var body = table.querySelector("tbody");
      if (!head || !body || head.querySelector("input, button, select, a, [colspan], [rowspan]")) return;
      var headers = Array.from(head.querySelectorAll("th"));
      if (!headers.length || body.querySelector("[rowspan]")) return;
      var rows = Array.from(body.querySelectorAll("tr"));
      // Complex comparison tables must not be silently given list semantics.
      if (rows.some(function (row) { return row.children.length !== headers.length && !row.querySelector("td[colspan]"); })) return;
      table.dataset.recordLayoutReady = "true";
      table.classList.add("responsive-record-table");
      table.setAttribute("role", "table");
      head.setAttribute("role", "rowgroup");
      head.querySelectorAll("tr").forEach(function (row) { row.setAttribute("role", "row"); });
      headers.forEach(function (header) { header.setAttribute("role", "columnheader"); });
      body.setAttribute("role", "rowgroup");
      rows.forEach(function (row) {
        row.setAttribute("role", "row");
        var primaryFound = false;
        Array.from(row.children).forEach(function (cell, index) {
          cell.setAttribute("role", "cell");
          if (cell.hasAttribute("colspan")) return;
          var text = headers[index].textContent.trim();
          if (/^select(?: for receipt)?$/i.test(text)) {
            cell.dataset.cellKind = "selection";
          } else if (!primaryFound) {
            cell.dataset.cellKind = "primary";
            primaryFound = true;
          } else if (index === headers.length - 1 && cell.querySelector(".btn, button")) {
            cell.dataset.cellKind = "actions";
          }
          var label = document.createElement("span");
          label.className = "record-cell-label";
          label.setAttribute("aria-hidden", "true");
          label.textContent = text;
          cell.insertBefore(label, cell.firstChild);
        });
      });
    });
  }
  initializeTables();
  document.addEventListener("htmx:afterSwap", initializeTables);
  document.addEventListener("htmx:historyRestore", initializeTables);
})();

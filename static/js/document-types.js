/* Type selection is a native multiple select without JavaScript. */
(function () {
  "use strict";
  document.querySelectorAll("[data-document-type-picker]").forEach(function (picker) {
    var selection = picker.querySelector("[data-document-type-selection]");
    var main = picker.querySelector("[data-main-document-type]");
    var mainField = picker.querySelector("[data-main-type-field]");
    if (!selection || !main || !mainField) return;
    function update() {
      var chosen = Array.from(selection.selectedOptions);
      var previous = main.value;
      main.replaceChildren(new Option("Choose the main type", ""));
      chosen.forEach(function (option) {
        main.add(new Option(option.textContent, option.value));
      });
      main.value = chosen.length === 1 ? chosen[0].value
        : (chosen.some(function (option) { return option.value === previous; }) ? previous : "");
      mainField.hidden = chosen.length <= 1 && main.getAttribute("aria-invalid") !== "true";
      main.required = chosen.length > 1;
      main.setAttribute("aria-required", String(chosen.length > 1));
    }
    // The existing checkbox picker and its Clear/Remove buttons update the
    // underlying select before these bubbling events reach this container.
    picker.addEventListener("change", function (event) {
      if (event.target !== main) update();
    });
    picker.addEventListener("click", function (event) {
      if (event.target.closest("button")) update();
    });
    update();
  });
})();

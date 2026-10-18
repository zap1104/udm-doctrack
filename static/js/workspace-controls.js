/* Bencho Search adapted to the existing repository GET form on every page.
 * Hover changes the width of one box without stealing focus. The native field
 * keeps keyboard shortcuts, Enter submission, and the touch keyboard intact.
 */
(function () {
  "use strict";
  function initializeSearch() {
    var form = document.querySelector(".workspace-search");
    if (!form || form.dataset.searchReady) return;
    var input = form.querySelector("input[type='search']");
    var trigger = form.querySelector(".workspace-search-trigger");
    if (!input || !trigger) return;
    var hover = window.matchMedia && window.matchMedia("(min-width: 576px) and (hover: hover) and (pointer: fine)");
    var hovering = false;
    var expanded = false;
    form.dataset.searchReady = "true";

    function setOpen(open) {
      // Touch and small screens keep the real field visible without a hover step.
      expanded = Boolean(open || !hover || !hover.matches);
      form.classList.toggle("is-open", expanded);
      trigger.setAttribute("aria-expanded", String(expanded));
      trigger.setAttribute("aria-label", expanded ? "Focus repository search" : "Open repository search");
      input.tabIndex = expanded ? 0 : -1;
      if (expanded) input.removeAttribute("aria-hidden");
      else input.setAttribute("aria-hidden", "true");
    }
    function closeIfIdle() {
      if (!hovering && !form.contains(document.activeElement) && !input.value.trim()) setOpen(false);
    }
    form.addEventListener("pointerenter", function (event) {
      if (!hover || !hover.matches || event.pointerType === "touch") return;
      hovering = true;
      setOpen(true);
    });
    form.addEventListener("pointerleave", function () {
      hovering = false;
      closeIfIdle();
    });
    // A mouse click does not drive expansion. Keyboard activation is explicit.
    trigger.addEventListener("click", function () { if (expanded) input.focus(); });
    trigger.addEventListener("keydown", function (event) {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      setOpen(true);
      input.focus();
    });
    // The existing '/' shortcut and error-summary links focus the real input.
    input.addEventListener("focus", function () { setOpen(true); });
    input.addEventListener("keydown", function (event) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setOpen(false);
      trigger.focus();
    });
    form.addEventListener("focusout", function (event) {
      if (!form.contains(event.relatedTarget) && !hovering && !input.value.trim()) setOpen(false);
    });
    if (hover && hover.addEventListener) {
      hover.addEventListener("change", function () {
        setOpen(hovering || Boolean(input.value.trim()) || input === document.activeElement);
      });
    }
    trigger.hidden = false;
    form.classList.add("is-enhanced");
    setOpen(Boolean(input.value.trim()) || document.activeElement === input);
  }
  initializeSearch();
  document.addEventListener("htmx:afterSwap", initializeSearch);
  document.addEventListener("htmx:historyRestore", initializeSearch);
})();

/* Bencho Label input and Upload dropzone adapted to real Django fields.
 * Labels, IDs, descriptions, validation, and submitted field names stay native.
 * The dropzone does not simulate progress or upload before the form is submitted.
 */
(function () {
  "use strict";

  function enhanceLabels() {
    document.querySelectorAll(".form-field > input.form-control").forEach(function (input) {
      if (input.dataset.labelReady || !/^(text|email|password|tel|url|number)$/.test(input.type)) return;
      var field = input.parentElement;
      var label = field.querySelector("label.form-label");
      // Long instructions and compound widgets keep their full, permanent labels.
      if (!label || label.htmlFor !== input.id || label.textContent.trim().length > 64) return;
      var frame = document.createElement("div");
      frame.className = "label-input";
      input.dataset.labelReady = "true";
      if (!input.hasAttribute("placeholder")) input.setAttribute("placeholder", " ");
      field.insertBefore(frame, label);
      frame.appendChild(label);
      frame.appendChild(input);
    });
  }

  function enhanceUploads() {
    document.querySelectorAll('input[type="file"]').forEach(function (input) {
      if (input.dataset.dropzoneReady || input.closest(".app-dropzone")) return;
      input.dataset.dropzoneReady = "true";
      var zone = document.createElement("div");
      zone.className = "app-dropzone";
      var intro = document.createElement("div");
      intro.className = "dropzone-intro";
      var icon = document.createElement("span");
      icon.className = "dropzone-icon";
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = "↑";
      var copy = document.createElement("span");
      copy.textContent = input.multiple ? "Drop files here, or choose files below" : "Drop a file here, or choose a file below";
      intro.appendChild(icon);
      intro.appendChild(copy);
      var status = document.createElement("div");
      status.className = "dropzone-status";
      status.setAttribute("role", "status");
      status.setAttribute("aria-live", "polite");
      input.parentNode.insertBefore(zone, input);
      zone.appendChild(intro);
      zone.appendChild(input);
      zone.appendChild(status);

      function hasFiles(event) {
        return event.dataTransfer && Array.from(event.dataTransfer.types || []).indexOf("Files") !== -1;
      }
      zone.addEventListener("dragover", function (event) {
        if (!hasFiles(event) || input.disabled) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "copy";
        zone.classList.add("is-dragging");
      });
      zone.addEventListener("dragleave", function (event) {
        if (!zone.contains(event.relatedTarget)) zone.classList.remove("is-dragging");
      });
      zone.addEventListener("drop", function (event) {
        if (!hasFiles(event)) return;
        event.preventDefault();
        zone.classList.remove("is-dragging");
        if (input.disabled) return;
        var files = event.dataTransfer.files;
        if (!files || !files.length) return;
        if (!input.multiple && files.length > 1) {
          status.textContent = "Choose one file for this field. Your current selection has been kept.";
          return;
        }
        try {
          input.files = files;
          // Existing file-size checks and filename readouts still run unchanged.
          input.dispatchEvent(new Event("change", {bubbles: true}));
          status.textContent = "Selected. Submit the form to continue.";
        } catch (_error) {
          status.textContent = "Use the file chooser below to select these files.";
        }
      });
      input.addEventListener("change", function () { status.textContent = ""; });
    });
  }

  function initialize() { enhanceLabels(); enhanceUploads(); }
  initialize();
  document.addEventListener("htmx:afterSwap", initialize);
  document.addEventListener("htmx:historyRestore", initialize);
})();

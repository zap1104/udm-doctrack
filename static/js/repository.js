/* Keep the folder chooser within reach without pushing mobile results away. */
(function () {
  "use strict";
  var folders = document.querySelector("[data-repository-folders]");
  if (!folders) return;
  var wideScreen = window.matchMedia("(min-width: 768px)");
  function setInitialLayout() { folders.open = wideScreen.matches && folders.dataset.repositoryFolderSelected !== "true"; }
  setInitialLayout();
  wideScreen.addEventListener("change", setInitialLayout);
})();

// A preview returns to its exact version, including a collapsed history row.
function restoreFileFocus() {
  const id = window.location.hash.slice(1);
  if (!id.startsWith('file-version-')) return;
  const link = document.getElementById(id);
  if (!link) return;
  let ancestor = link.parentElement;
  while (ancestor) {
    if (ancestor.tagName === 'DETAILS') ancestor.open = true;
    ancestor = ancestor.parentElement;
  }
  link.focus({ preventScroll: true });
  link.scrollIntoView({ block: 'center', behavior: 'instant' });
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', restoreFileFocus, { once: true });
else restoreFileFocus();

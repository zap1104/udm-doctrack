/* Dashboard presentation only. Forms, links and server-scoped data stay intact. */
(() => {
  'use strict';
  const key = 'doctrack-dashboard-view';
  let active;
  try { active = sessionStorage.getItem(key); } catch (_) { /* Storage is optional. */ }

  function hashPanel(root) {
    if (!location.hash) return null;
    let id;
    try { id = decodeURIComponent(location.hash.slice(1)); } catch (_) { return null; }
    const target = document.getElementById(id);
    return target && root.contains(target) ? target.closest('[data-dashboard-panel]') : null;
  }

  function select(root, name, focus = false) {
    const tabs = Array.from(root.querySelectorAll('[data-dashboard-tab]'));
    if (!tabs.some(tab => tab.dataset.dashboardTab === name)) name = 'overview';
    active = name;
    tabs.forEach(tab => {
      const selected = tab.dataset.dashboardTab === name;
      tab.setAttribute('aria-selected', String(selected));
      tab.tabIndex = selected ? 0 : -1;
      if (selected && focus) tab.focus();
    });
    root.querySelectorAll('[data-dashboard-panel]').forEach(panel => {
      panel.hidden = panel.dataset.dashboardPanel !== name;
    });
    try { sessionStorage.setItem(key, name); } catch (_) { /* Private browsing fallback. */ }
    // Existing charts measure their width again when their panel becomes visible.
    requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
  }

  function initialize() {
    document.querySelectorAll('[data-dashboard-workspace]').forEach(root => {
      if (root.dataset.dashboardTabsReady) return;
      root.dataset.dashboardTabsReady = 'true';
      const linkedPanel = hashPanel(root);
      const analyticsLink = /[?&](ring|month|turnaround_stage)=/.test(location.search);
      select(root, linkedPanel ? linkedPanel.dataset.dashboardPanel : (active || (analyticsLink ? 'statistics' : 'overview')));
      root.querySelector('[role="tablist"]').hidden = false;
    });
  }

  document.addEventListener('click', event => {
    const tab = event.target.closest('[data-dashboard-tab]');
    if (tab) select(tab.closest('[data-dashboard-workspace]'), tab.dataset.dashboardTab);
  });
  document.addEventListener('keydown', event => {
    const tab = event.target.closest('[data-dashboard-tab]');
    if (!tab || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const root = tab.closest('[data-dashboard-workspace]');
    const tabs = Array.from(root.querySelectorAll('[data-dashboard-tab]'));
    let index = tabs.indexOf(tab);
    if (event.key === 'Home') index = 0;
    else if (event.key === 'End') index = tabs.length - 1;
    else index = (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    event.preventDefault();
    select(root, tabs[index].dataset.dashboardTab, true);
  });
  window.addEventListener('hashchange', () => {
    const root = document.querySelector('[data-dashboard-workspace]');
    const panel = root && hashPanel(root);
    if (!panel) return;
    select(root, panel.dataset.dashboardPanel);
    requestAnimationFrame(() => document.getElementById(decodeURIComponent(location.hash.slice(1)))?.scrollIntoView());
  });
  document.addEventListener('htmx:afterSwap', initialize);
  document.addEventListener('htmx:historyRestore', () => {
    document.querySelectorAll('[data-dashboard-workspace]').forEach(root => {
      delete root.dataset.dashboardTabsReady;
    });
    initialize();
  });
  // Paper retains every explanation and record detail, then restores the screen.
  let printDetails = [];
  window.addEventListener('beforeprint', () => {
    if (printDetails.length) return;
    printDetails = Array.from(document.querySelectorAll('[data-dashboard-workspace] details.dashboard-disclosure'))
      .map(element => ({element, open: element.open}));
    printDetails.forEach(({element}) => { element.open = true; });
  });
  window.addEventListener('afterprint', () => {
    printDetails.forEach(({element, open}) => { element.open = open; });
    printDetails = [];
  });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize);
  else initialize();
})();

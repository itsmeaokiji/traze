// Apply a saved local preference before the first stylesheet paint. This is an
// external same-origin script, so the existing script-src 'self' policy applies.
(() => {
  let theme = 'dark';
  try { if (localStorage.getItem('traze-theme') === 'light') theme = 'light'; }
  catch { /* Use the default when local preference storage is unavailable. */ }
  document.documentElement.dataset.theme = theme;
})();

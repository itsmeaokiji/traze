import { useLayoutEffect, useState } from 'react';

export type Theme = 'dark' | 'light';
export const THEME_KEY = 'traze-theme';

function storedTheme(): Theme {
  try { return localStorage.getItem(THEME_KEY) === 'light' ? 'light' : 'dark'; }
  catch { return 'dark'; }
}

export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(storedTheme);
  useLayoutEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  const target = theme === 'dark' ? 'light' : 'dark';
  function toggle() {
    setTheme(target);
    try { localStorage.setItem(THEME_KEY, target); }
    catch { /* Presentation still works when preference storage is unavailable. */ }
  }
  return <button type="button" className="theme-toggle" onClick={toggle} aria-label={`Switch to ${target} mode`} aria-pressed={theme === 'light'} title={`Switch to ${target} mode`}>
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      {target === 'light' ? <><circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5" /></> : <path d="M20.9 13.1A9 9 0 0 1 10.9 3.1 9 9 0 1 0 20.9 13.1Z" />}
    </svg>
  </button>;
}

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import themeInit from '../public/assets/theme-init.js?raw';
import ThemeToggle, { THEME_KEY } from './ThemeToggle';
import App from './App';

beforeEach(() => { localStorage.clear(); delete document.documentElement.dataset.theme; });
afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); delete document.documentElement.dataset.theme; });

describe('theme preference', () => {
  it('starts dark, switches without submission, and restores the saved light choice on remount', () => {
    const submit = vi.fn();
    const first = render(<form onSubmit={submit}><ThemeToggle /></form>);
    expect(document.documentElement.dataset.theme).toBe('dark');
    fireEvent.click(screen.getByRole('button', { name: 'Switch to light mode' }));
    expect(document.documentElement.dataset.theme).toBe('light');
    expect(localStorage.getItem(THEME_KEY)).toBe('light');
    expect(submit).not.toHaveBeenCalled();
    first.unmount();
    render(<ThemeToggle />);
    expect(screen.getByRole('button', { name: 'Switch to dark mode' }).getAttribute('aria-pressed')).toBe('true');
    expect(document.documentElement.dataset.theme).toBe('light');
  });

  it('rejects an invalid preference and continues toggling when storage access is denied', () => {
    localStorage.setItem(THEME_KEY, 'sepia');
    const first = render(<ThemeToggle />);
    expect(document.documentElement.dataset.theme).toBe('dark');
    first.unmount();
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('Controlled storage denial'); });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('Controlled storage denial'); });
    render(<ThemeToggle />);
    fireEvent.click(screen.getByRole('button', { name: 'Switch to light mode' }));
    expect(document.documentElement.dataset.theme).toBe('light');
    fireEvent.click(screen.getByRole('button', { name: 'Switch to dark mode' }));
    expect(document.documentElement.dataset.theme).toBe('dark');
  });

  it('applies the saved choice in the pre-paint external script and safely defaults when storage fails', () => {
    // Execute owned bootstrap code in the isolated test DOM, not in a browser.
    const run = () => new Function(themeInit)();
    localStorage.setItem(THEME_KEY, 'light');
    run();
    expect(document.documentElement.dataset.theme).toBe('light');
    localStorage.setItem(THEME_KEY, 'invalid');
    run();
    expect(document.documentElement.dataset.theme).toBe('dark');
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('Controlled storage denial'); });
    expect(run).not.toThrow();
    expect(document.documentElement.dataset.theme).toBe('dark');
  });

  it('changes presentation during an active job without starting or restarting a lookup', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      csrf_token: 'controlled-theme-token', receipts: [],
      active_job: { id: 'a'.repeat(32), operation: 'lookup', block_input: 5243117, block_number: 5243117,
        started_at: new Date().toISOString(), state: 'running', receipt_id: null, events: [] },
      resources: { available: true, session_limit_bytes: 100000000, total_response_bytes: 0 },
      limits: { response_mb_per_lookup: 40, bitcoin_body_attempts: 20, ancestry_links: 256 },
    })));
    vi.stubGlobal('fetch', fetch);
    const view = render(<App />);
    await waitFor(() => expect((screen.getByLabelText('Hemi block number or hash') as HTMLInputElement).disabled).toBe(true));
    await screen.findByRole('heading', { name: /Searching Hemi/ });
    const before = fetch.mock.calls.length;
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Switch to light mode' })); });
    expect(document.documentElement.dataset.theme).toBe('light');
    expect(fetch.mock.calls.length).toBe(before);
    expect(screen.getByRole('heading', { name: /Searching Hemi/ })).toBeTruthy();
    view.unmount();
  });
});

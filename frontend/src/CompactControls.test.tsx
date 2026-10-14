import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ordinaryData from '../../demo_results/mainnet_5243117.json';
import App from './App';
import ThemeToggle, { THEME_KEY } from './ThemeToggle';
import type { Job, Receipt, WorkspaceState } from './types';

function state(active_job: Job | null = null): WorkspaceState {
  return { csrf_token: 'controlled-token', receipts: [], active_job,
    resources: { available: true, session_limit_bytes: 100000000, total_response_bytes: 0 },
    limits: { response_mb_per_lookup: 40, bitcoin_body_attempts: 20, ancestry_links: 256, session_response_mb: 100 } };
}
function response(value: unknown) { return new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } }); }

describe('Compact controls retain accessible actions', () => {
  it('keeps the icon-only theme action named, titled and outside form submission', () => {
    localStorage.removeItem(THEME_KEY);
    const submit = vi.fn(); render(<form onSubmit={submit}><ThemeToggle /></form>);
    const light = screen.getByRole('button', { name: 'Switch to light mode' });
    expect(light.textContent).toBe('');
    expect(light.getAttribute('title')).toBe('Switch to light mode');
    expect(light.querySelector('svg')!.getAttribute('aria-hidden')).toBe('true');
    fireEvent.click(light);
    expect(screen.getByRole('button', { name: 'Switch to dark mode' }).getAttribute('aria-pressed')).toBe('true');
    expect(submit).not.toHaveBeenCalled();
    localStorage.removeItem(THEME_KEY);
  });

  it('keeps the search icon decorative and submits the same exact hash/range once', async () => {
    const receipt = structuredClone(ordinaryData) as unknown as Receipt;
    const hash = receipt.query.block_hash!;
    const finished: Job = { id: 'a'.repeat(32), operation: 'lookup', block_number: 5243117, block_input: hash,
      started_at: new Date().toISOString(), state: 'complete', receipt_id: 'evidence', events: [] };
    const fetch = vi.fn((path: string, options?: RequestInit) => Promise.resolve(response(path === '/api/state' ? state()
      : options?.method === 'POST' ? finished : { id: 'evidence', filename: 'evidence.json', label: 'Controlled', receipt,
        verification: { proofs_checked: receipt.proofs.length, current_chain_membership_checked: false } })));
    vi.stubGlobal('fetch', fetch); render(<App />);
    await waitFor(() => expect((screen.getByRole('button', { name: 'Find publication' }) as HTMLButtonElement).disabled).toBe(false));
    const action = screen.getByRole('button', { name: 'Find publication' });
    expect(action.getAttribute('aria-label')).toBe('Find publication');
    expect(action.getAttribute('title')).toBe('Find publication');
    expect(action.querySelector('svg')!.getAttribute('aria-hidden')).toBe('true');
    fireEvent.change(screen.getByLabelText('Hemi block number or hash'), { target: { value: hash } });
    fireEvent.change(screen.getByLabelText('From Bitcoin block'), { target: { value: '965931' } });
    fireEvent.change(screen.getByLabelText('Through Bitcoin block'), { target: { value: '965931' } });
    fireEvent.submit(document.getElementById('lookup-form')!);
    fireEvent.submit(document.getElementById('lookup-form')!);
    await screen.findByRole('heading', { name: 'Publication found' });
    const posts = fetch.mock.calls.filter(([, options]) => options?.method === 'POST');
    expect(posts).toHaveLength(1);
    expect(JSON.parse(posts[0][1]!.body as string)).toEqual({ block: hash, bitcoin_range: [965931, 965931] });
  });

  it('retains the operation name and disabled state while rechecking sources', async () => {
    const job: Job = { id: 'b'.repeat(32), operation: 'refresh', block_number: 5243117,
      started_at: new Date().toISOString(), state: 'running', receipt_id: null, events: [] };
    const fetch = vi.fn().mockResolvedValue(response(state(job)));
    vi.stubGlobal('fetch', fetch); const view = render(<App />);
    await screen.findByRole('heading', { name: 'Rechecking sources…' });
    const action = screen.getByRole('button', { name: 'Recheck in progress' }) as HTMLButtonElement;
    expect(action.disabled).toBe(true);
    expect(action.title).toBe('Recheck in progress');
    expect(action.querySelector('.lookup-icon')!.classList.contains('is-pending')).toBe(true);
    expect(fetch.mock.calls.every(([, options]) => options?.method !== 'POST')).toBe(true);
    view.unmount();
  });
});

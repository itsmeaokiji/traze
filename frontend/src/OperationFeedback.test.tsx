import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ordinary from '../../demo_results/mainnet_5243117.json';
import negative from '../../demo_results/scoped_no_match.json';
import App from './App';
import type { Job, Receipt, ReceiptWrapper, WorkspaceState } from './types';

function packet(kind = 'found', id = 'a'.repeat(32)): ReceiptWrapper {
  const receipt = structuredClone(kind === 'found' || kind === 'recorded' || kind === 'conflict' ? ordinary : negative) as unknown as Receipt;
  receipt.validation = { scope: 'controlled_provider_responses', live_public_reads: false };
  if (kind === 'recorded') receipt.mode = 'offline_catalogue';
  if (kind === 'incomplete') receipt.status = 'incomplete';
  if (kind === 'conflict') {
    receipt.status = 'incomplete';
    receipt.context!.current_view_confirmed = false;
    receipt.issues = [{ code: 'source_conflict' }];
  }
  return { id, label: 'Controlled feedback case', filename: 'evidence.json', receipt,
    verification: { proofs_checked: receipt.proofs.length, current_chain_membership_checked: false } };
}
function state(mode: 'indexed-only' | 'discovery' = 'indexed-only', active_job: Job | null = null): WorkspaceState {
  return { serving_mode: mode, active_job, csrf_token: 'controlled-token', receipts: [],
    resources: { available: true, usable_response_bytes: 33399983, session_limit_bytes: 100000000, source_operations_available: true },
    limits: { response_mb_per_lookup: 40, bitcoin_body_attempts: 20, ancestry_links: 256, session_response_mb: 100 } };
}
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
async function ready() {
  await waitFor(() => expect((screen.getByRole('button', { name: 'Find publication' }) as HTMLButtonElement).disabled).toBe(false));
}
function staged(value = packet()) {
  const pending: ((value: Response) => void)[] = [];
  const fetch = vi.fn((path: string) => path === '/api/state' ? Promise.resolve(response(state()))
    : new Promise<Response>(resolve => pending.push(resolve)));
  vi.stubGlobal('fetch', fetch);
  return { fetch, release: (index = 0, data: unknown = value, status = 200) => pending[index](response(data, status)) };
}
function status() { return document.getElementById('completion-status')!; }
function search() {
  const button = screen.getByRole('button', { name: 'Find publication' });
  button.focus(); fireEvent.click(button);
  return button;
}

describe('operation completion feedback', () => {
  it.each([
    ['found', 'Publication found'], ['negative', 'No match in checked range'],
    ['incomplete', 'Search incomplete'], ['conflict', 'Evidence retained · source conflict'],
    ['recorded', 'Recorded publication evidence'],
  ])('announces the qualified %s outcome through deliberate heading focus', async (kind, title) => {
    const value = packet(kind), { release } = staged(value);
    render(<App />); await ready(); search();
    await act(async () => release());
    const heading = await screen.findByRole('heading', { name: title });
    await waitFor(() => expect(document.activeElement).toBe(heading));
    expect(status().textContent).toContain(title);
    expect(status().getAttribute('aria-live')).toBe('off');
    expect(screen.queryByRole('button', { name: 'View result' })).toBeNull();
    if (kind === 'incomplete' || kind === 'conflict') expect(status().textContent).toContain('incomplete result');
    if (kind === 'recorded') expect(status().textContent).toContain('Current source views were not rechecked');
  });

  it('preserves another control and offers an explicit result action with polite feedback', async () => {
    const { release } = staged(); render(<App />); await ready(); search();
    const theme = screen.getByRole('button', { name: 'Switch to light mode' });
    theme.focus();
    await act(async () => release());
    await screen.findByRole('button', { name: 'View result' });
    expect(document.activeElement).toBe(theme);
    expect(status().getAttribute('aria-live')).toBe('polite');
    fireEvent.click(screen.getByRole('button', { name: 'View result' }));
    expect(document.activeElement).toBe(screen.getByRole('heading', { name: 'Publication found' }));
  });

  it('also preserves attention when the user scrolls away without moving DOM focus', async () => {
    const { release } = staged(); render(<App />); await ready(); const origin = search();
    fireEvent.wheel(document.body);
    await act(async () => release());
    await screen.findByRole('button', { name: 'View result' });
    expect(document.activeElement).toBe(origin);
    expect(status().getAttribute('aria-live')).toBe('polite');
  });

  it('uses the existing error alert without a duplicate completion announcement', async () => {
    const { release } = staged(); render(<App />); await ready(); search();
    await act(async () => release(0, { error: { code: 'indexed_evidence_unavailable' } }, 404));
    const alert = await screen.findByRole('alert');
    await waitFor(() => expect(document.activeElement).toBe(alert));
    expect(alert.textContent).toContain('does not mean no publication exists');
    expect(status().textContent).toBe('');
  });

  it('preserves attention for a failed request and offers View error', async () => {
    const { release } = staged(); render(<App />); await ready(); search();
    const theme = screen.getByRole('button', { name: 'Switch to light mode' }); theme.focus();
    await act(async () => release(0, { error: { code: 'indexed_evidence_unavailable' } }, 404));
    await screen.findByRole('button', { name: 'View error' });
    expect(document.activeElement).toBe(theme); expect(status().textContent).toBe('');
    fireEvent.click(screen.getByRole('button', { name: 'View error' }));
    expect(document.activeElement).toBe(screen.getByRole('alert'));
  });

  it('opens a collapsed invalid range field and rejects it without a request', async () => {
    const fetch = vi.fn(() => Promise.resolve(response(state('discovery')))); vi.stubGlobal('fetch', fetch);
    render(<App />); await ready();
    fireEvent.change(screen.getByLabelText('From Bitcoin block'), { target: { value: '965931' } });
    search();
    expect(document.activeElement).toBe(screen.getByLabelText('Through Bitcoin block'));
    expect((document.querySelector('.scope-options') as HTMLDetailsElement).open).toBe(true);
    expect(fetch.mock.calls).toHaveLength(1); expect(status().textContent).toBe('');
  });

  it('does not announce a restored completed receipt as a new search or steal focus', async () => {
    const value = packet(); window.history.replaceState(null, '', '/?receipt=' + value.id);
    const fetch = vi.fn((path: string) => Promise.resolve(response(path === '/api/state' ? state('discovery') : value)));
    vi.stubGlobal('fetch', fetch); render(<App />);
    const heading = await screen.findByRole('heading', { name: 'Publication found' });
    expect(document.activeElement).not.toBe(heading); expect(status().textContent).toBe('');
    expect(screen.queryByRole('button', { name: 'View result' })).toBeNull();
  });

  it.each(['refresh', 'continue'] as const)('labels an explicit %s separately from a new lookup', async operation => {
    const original = packet(operation === 'continue' ? 'incomplete' : 'found');
    if (operation === 'continue') original.continuation = { available: true, reason: 'next_segment', receipt_sha256: 'b'.repeat(64), bitcoin_range: [965931, 965950] };
    const next = packet(operation === 'continue' ? 'negative' : 'found', 'c'.repeat(32));
    if (operation === 'refresh') next.receipt.mode = 'live_metadata_refresh';
    window.history.replaceState(null, '', '/?receipt=' + original.id);
    const fetch = vi.fn((path: string, options?: RequestInit) => Promise.resolve(response(path === '/api/state' ? state('discovery')
      : options?.method === 'POST' ? { id: 'd'.repeat(32), operation, state: 'complete', receipt_id: next.id, events: [] }
        : path.endsWith(next.id) ? next : original)));
    vi.stubGlobal('fetch', fetch); render(<App />); await ready();
    const button = await screen.findByRole('button', { name: operation === 'continue' ? 'Continue search' : /Recheck sources/ });
    button.focus(); fireEvent.click(button);
    await waitFor(() => expect(status().textContent).toContain(operation === 'continue' ? 'Search segment finished.' : 'Source recheck finished.'));
    if (operation === 'refresh') expect(status().textContent).toContain('Original scan coverage is unchanged');
    expect(fetch.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1);
  });

  it('announces a resumed running job once without treating reload as permission to move focus', async () => {
    const value = packet(); const running: Job = { id: 'b'.repeat(32), operation: 'lookup', state: 'running', block_number: 5243117,
      started_at: new Date().toISOString(), receipt_id: null, events: [] };
    vi.stubGlobal('fetch', vi.fn((path: string) => Promise.resolve(response(path === '/api/state' ? state('discovery', running)
      : path.startsWith('/api/jobs/') ? { ...running, state: 'complete', receipt_id: value.id } : value))));
    render(<App />);
    await waitFor(() => expect(status().textContent).toContain('Resumed search finished.'), { timeout: 3000 });
    expect(document.activeElement).not.toBe(screen.getByRole('heading', { name: 'Publication found' }));
    expect(screen.getByRole('button', { name: 'View result' })).toBeTruthy();
  });

  it('does not repeat completion on state polling, but announces an identical explicit search again', async () => {
    vi.useFakeTimers(); const { release, fetch } = staged();
    await act(async () => { render(<App />); });
    const messages: string[] = [];
    const observer = new MutationObserver(() => { if (status().textContent) messages.push(status().textContent!); });
    observer.observe(status(), { childList: true, characterData: true, subtree: true });
    await act(async () => { search(); release(); });
    expect(status().textContent).toContain('Publication found');
    await act(async () => { await vi.advanceTimersByTimeAsync(8100); });
    expect(messages).toHaveLength(1);
    expect(fetch.mock.calls.filter(([path]) => path === '/api/state').length).toBeGreaterThanOrEqual(4);
    await act(async () => { search(); });
    expect(status().textContent).toBe('');
    await act(async () => { release(1); });
    expect(messages).toHaveLength(2); observer.disconnect();
  });

  it('drops aborted completion work after unmount', async () => {
    const { release, fetch } = staged(); const { unmount } = render(<App />); await ready(); search(); unmount();
    await act(async () => release());
    expect(document.getElementById('completion-status')).toBeNull();
    expect(fetch.mock.calls.filter(([path]) => path === '/api/state')).toHaveLength(1);
  });
});

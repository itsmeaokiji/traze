import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ordinaryData from '../../demo_results/mainnet_5243117.json';
import negativeData from '../../demo_results/scoped_no_match.json';
import App from './App';
import type { Job, Receipt, ReceiptWrapper, WorkspaceState } from './types';

// Retained real proof bytes, with explicitly controlled HTTP source responses.
function wrapper(data: unknown = ordinaryData, id = 'example-ordinary'): ReceiptWrapper {
  const receipt = structuredClone(data) as Receipt;
  receipt.validation = { scope: 'controlled_provider_responses', live_public_reads: false };
  return { id, label: 'Controlled result', filename: 'evidence.json', receipt,
    verification: { proofs_checked: receipt.proofs.length, current_chain_membership_checked: false } };
}
function state(active_job: Job | null = null): WorkspaceState {
  return {
    csrf_token: 'controlled-local-token', active_job,
    receipts: [{ id: 'example-ordinary', label: 'Ordinary block', origin: 'saved_example', block_number: 5243117, status: 'found' }],
    resources: { available: true, total_response_bytes: 72170485, session_limit_bytes: 100000000 },
    limits: { response_mb_per_lookup: 40, bitcoin_body_attempts: 20, ancestry_links: 256, session_response_mb: 100 },
  };
}
function job(receipt_id: string | null = null): Job {
  return { id: 'a'.repeat(32), operation: 'lookup', block_input: 5243117, block_number: 5243117, started_at: new Date().toISOString(), state: receipt_id ? 'complete' : 'running', receipt_id, events: [{ at: new Date().toISOString(), message: 'Reading controlled sources' }] };
}
function respond(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } }); }
async function ready() { await waitFor(() => expect((screen.getByRole('button', { name: 'Find publication' }) as HTMLButtonElement).disabled).toBe(false)); }
function fetchMock(handler: (path: string, options?: RequestInit) => Response | Promise<Response>) {
  const mocked = vi.fn((path: string, options?: RequestInit) => handler(path, options));
  vi.stubGlobal('fetch', mocked);
  return mocked;
}
async function openOrdinarySaved() {
  fireEvent.click(screen.getByText('Saved results', { exact: true }));
  fireEvent.click(await screen.findByRole('button', { name: 'Open saved Ordinary block for Hemi 5,243,117' }));
}

describe('React bridge workflow', () => {
  it('opens saved evidence without POSTs and preserves the exact transaction/download references', async () => {
    const evidence = wrapper();
    const fetch = fetchMock(path => respond(path === '/api/state' ? state() : evidence));
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', Object.create(navigator, { clipboard: { value: { writeText }, configurable: true } }));
    render(<App />);
    await ready();
    expect((document.getElementById('saved-results') as HTMLDetailsElement).open).toBe(false);
    await openOrdinarySaved();
    await screen.findByRole('heading', { name: 'Publication found' });
    expect((document.getElementById('saved-results') as HTMLDetailsElement).open).toBe(false);
    expect(document.activeElement).toBe(document.querySelector('#saved-results > summary'));
    expect(fetch.mock.calls.every(([, options]) => options?.method !== 'POST')).toBe(true);
    expect(screen.getByRole('link', { name: /Download JSON/ }).getAttribute('href')).toBe('/api/receipts/example-ordinary/download');
    expect(screen.getByRole('link', { name: evidence.receipt.proofs[0].txid }).getAttribute('href')).toBe(`https://blockstream.info/tx/${evidence.receipt.proofs[0].txid}`);
    fireEvent.click(screen.getByRole('button', { name: 'Copy transaction ID' }));
    await screen.findByRole('button', { name: 'Copied' });
    expect(writeText).toHaveBeenCalledWith(evidence.receipt.proofs[0].txid);
    expect((document.getElementById('developer-diagnostics') as HTMLDetailsElement).open).toBe(false);
  });

  it('submits an exact hash and range once, sends the local token, and polls the accepted job', async () => {
    const evidence = wrapper();
    const hash = '0x' + '3C'.repeat(32);
    evidence.receipt.query.input_type = 'hash';
    evidence.receipt.query.requested_block_hash = hash.toLowerCase();
    const fetch = fetchMock((path, options) => respond(path === '/api/state' ? state()
      : options?.method === 'POST' ? job()
        : path.startsWith('/api/jobs/') ? job('example-ordinary') : evidence));
    render(<App />);
    await ready();
    fireEvent.change(screen.getByLabelText('Hemi block number or hash'), { target: { value: hash } });
    fireEvent.change(screen.getByLabelText('From Bitcoin block'), { target: { value: '965931' } });
    fireEvent.change(screen.getByLabelText('Through Bitcoin block'), { target: { value: '965931' } });
    const form = document.getElementById('lookup-form')!;
    fireEvent.submit(form);
    fireEvent.submit(form);
    await screen.findByRole('heading', { name: 'Publication found' }, { timeout: 3000 });
    const posts = fetch.mock.calls.filter(([, options]) => options?.method === 'POST');
    expect(posts).toHaveLength(1);
    expect(posts[0][0]).toBe('/api/lookup');
    expect(JSON.parse(posts[0][1]!.body as string)).toEqual({ block: hash.toLowerCase(), bitcoin_range: [965931, 965931] });
    expect(posts[0][1]!.headers).toEqual({ 'Content-Type': 'application/json', 'X-Local-Token': 'controlled-local-token' });
    expect(fetch.mock.calls.some(([path]) => path.startsWith('/api/jobs/'))).toBe(true);
    expect((screen.getByLabelText('Hemi block number or hash') as HTMLInputElement).value).toBe(hash.toLowerCase());
  });

  it('reconnects to an existing job after mounting without creating another lookup', async () => {
    const evidence = wrapper();
    const fetch = fetchMock(path => respond(path === '/api/state' ? state(job()) : path.startsWith('/api/jobs/') ? job('example-ordinary') : evidence));
    render(<App />);
    await screen.findByRole('heading', { name: 'Publication found' }, { timeout: 3000 });
    expect(fetch.mock.calls.every(([, options]) => options?.method !== 'POST')).toBe(true);
    expect(fetch.mock.calls.filter(([path]) => path.startsWith('/api/jobs/'))).toHaveLength(1);
  });

  it('rechecks sources while retaining proof success and original coverage when the new view conflicts', async () => {
    const original = wrapper();
    const refreshed = wrapper();
    refreshed.id = 'refreshed';
    refreshed.receipt.status = 'incomplete';
    refreshed.receipt.mode = 'live_metadata_refresh';
    refreshed.receipt.context = { ...refreshed.receipt.context, current_view_confirmed: false, checked_at: '2026-10-05T05:00:00Z' };
    refreshed.receipt.selection = { current_proof_index: null, best_observed_proof_index: 0 };
    refreshed.receipt.issues = [{ code: 'source_conflict', message: 'Controlled source conflict' }];
    const fetch = fetchMock((path, options) => respond(path === '/api/state' ? state() : path === '/api/refresh' && options?.method === 'POST' ? { ...job('refreshed'), operation: 'refresh' } : path.endsWith('/refreshed') ? refreshed : original));
    render(<App />);
    await ready();
    await openOrdinarySaved();
    await screen.findByRole('heading', { name: 'Publication found' });
    fireEvent.click(screen.getByRole('button', { name: /Recheck sources/ }));
    await screen.findByRole('heading', { name: 'Evidence retained · source conflict' });
    expect(within(document.getElementById('source-check-list')!).getByText('Source views conflicted at the recorded check')).toBeTruthy();
    expect(within(document.getElementById('verification-list')!).getAllByText('Passed')).toHaveLength(4);
    expect(screen.getByText('Sources returned conflicting data. Chain status could not be confirmed.')).toBeTruthy();
    expect(document.getElementById('coverage-summary')!.textContent).toContain('Coverage from the original search');
    expect(refreshed.receipt.coverage).toEqual(original.receipt.coverage);
    expect(JSON.parse(fetch.mock.calls.find(([path]) => path === '/api/refresh')![1]!.body as string)).toEqual({ receipt_id: 'example-ordinary' });
  });

  it('keeps a no-match result scoped without displaying a publication transaction', async () => {
    const evidence = wrapper(negativeData);
    fetchMock(path => respond(path === '/api/state' ? state() : evidence));
    render(<App />);
    await ready();
    await openOrdinarySaved();
    await screen.findByRole('heading', { name: 'No match in checked range' });
    expect(within(document.getElementById('result-summary')!).getByText(/Publications may exist outside this range/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Copy transaction ID' })).toBeNull();
  });

  it('labels a terminal source-recheck timeout accurately and keeps saved evidence accessible', async () => {
    const evidence = wrapper();
    const failed = { ...job(), operation: 'refresh', state: 'error', error: { code: 'time_budget_exhausted', message: 'Controlled recheck deadline' } };
    const fetch = fetchMock((path, options) => respond(path === '/api/state' ? state()
      : options?.method === 'POST' ? failed : evidence));
    render(<App />); await ready();
    await openOrdinarySaved();
    await screen.findByRole('heading', { name: 'Publication found' });
    fireEvent.click(screen.getByRole('button', { name: /Recheck sources/ }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('Source recheck timed out.'));
    expect(screen.getByRole('alert').textContent).not.toContain('Controlled recheck deadline');
    await ready(); await openOrdinarySaved();
    await screen.findByRole('heading', { name: 'Publication found' });
    expect(fetch.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1);
  });

  it('rejects invalid input before a POST and keeps backend diagnostics out of the main error', async () => {
    const fetch = fetchMock((_path, options) => options?.method === 'POST' ? respond({ error: { code: 'busy', message: 'Internal ledger detail that must stay in diagnostics' } }, 409) : respond(state()));
    render(<App />);
    await ready();
    fireEvent.change(screen.getByLabelText('Hemi block number or hash'), { target: { value: '0x123' } });
    fireEvent.submit(document.getElementById('lookup-form')!);
    expect(fetch.mock.calls.every(([, options]) => options?.method !== 'POST')).toBe(true);
    expect(document.activeElement).toBe(screen.getByLabelText('Hemi block number or hash'));
    fireEvent.change(screen.getByLabelText('Hemi block number or hash'), { target: { value: '5243117' } });
    fireEvent.submit(document.getElementById('lookup-form')!);
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe('Another search is already running. Wait for it to finish.'));
    expect(document.getElementById('operation-error')!.closest('details')!.open).toBe(false);
    expect(document.getElementById('operation-error')!.textContent).toContain('Internal ledger detail');
  });

  it('stops page polling on unmount without posting a cancellation to the server', async () => {
    vi.useFakeTimers();
    const fetch = fetchMock(() => respond(state(job())));
    let unmount!: () => void;
    await act(async () => { ({ unmount } = render(<App />)); });
    unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(2400); });
    expect(fetch.mock.calls).toHaveLength(1);
    expect(fetch.mock.calls[0][1]!.signal!.aborted).toBe(true);
  });
});

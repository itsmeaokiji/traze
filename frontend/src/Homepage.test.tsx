import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import positive from '../../demo_results/mainnet_5243117.json';
import negative from '../../demo_results/scoped_no_match.json';
import App from './App';
import type { Job, Receipt, ReceiptWrapper, WorkspaceState } from './types';

const headline = 'Trace Hemi blocks to Bitcoin publications.';
function state(): WorkspaceState {
  return { serving_mode: 'discovery', csrf_token: 'controlled-token', active_job: null,
    receipts: [{ id: 'example-ordinary', label: 'Ordinary block', origin: 'saved_example', block_number: 5243117, status: 'found' }],
    resources: { available: true, session_limit_bytes: 100000000, usable_response_bytes: 33399983, source_operations_available: true },
    limits: { response_mb_per_lookup: 40, bitcoin_body_attempts: 20, ancestry_links: 256, session_response_mb: 100 } };
}
function wrapper(data: unknown = positive): ReceiptWrapper {
  const receipt = structuredClone(data) as Receipt;
  receipt.validation = { scope: 'controlled_provider_responses', live_public_reads: false };
  return { id: 'example-ordinary', filename: 'evidence.json', label: 'Controlled recorded example', receipt,
    verification: { proofs_checked: receipt.proofs.length, current_chain_membership_checked: false } };
}
function job(done = false): Job {
  return { id: 'a'.repeat(32), operation: 'lookup', state: done ? 'complete' : 'running', receipt_id: done ? 'example-ordinary' : null,
    block_number: 5243117, block_input: 5243117, started_at: '2026-10-29T00:00:00Z', events: [] };
}
function response(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status }); }
function mock(current = state(), evidence = wrapper()) {
  const fetch = vi.fn((path: string, options?: RequestInit) => Promise.resolve(response(path === '/api/state' ? current
    : options?.method === 'POST' ? job(true) : evidence)));
  vi.stubGlobal('fetch', fetch); return fetch;
}
async function ready() { await screen.findByRole('heading', { name: headline }); }
function enterBlock(value = '5243117') {
  fireEvent.change(screen.getByLabelText('Hemi block number or hash'), { target: { value } });
}

describe('rich home entry and focused explorer transitions', () => {
  it('shows the approved copy and a recorded connector without acquiring or opening evidence', async () => {
    const fetch = mock(); render(<App />); await ready();
    expect(screen.getByText('Hemi–Bitcoin publication explorer')).toBeTruthy();
    expect(screen.getByText('Start with a Hemi block number or hash. Follow the keystone connection, inspect Bitcoin transactions, and review the supporting evidence.')).toBeTruthy();
    expect(screen.getByRole('list', { name: 'Recorded Hemi to Bitcoin example' }).querySelectorAll('li')).toHaveLength(3);
    expect(screen.getByText('Traze currently runs locally. Public-source discovery depends on provider availability and configured search limits.')).toBeTruthy();
    expect(document.querySelectorAll('#lookup-form')).toHaveLength(1);
    expect((screen.getByLabelText('Hemi block number or hash') as HTMLInputElement).value).toBe('');
    expect(screen.getByPlaceholderText('Enter a block number or 0x-prefixed hash')).toBeTruthy();
    expect(screen.getByRole('link', { name: /Read API docs/ }).getAttribute('href')).toBe('/api-docs');
    expect((document.getElementById('home-search-options') as HTMLDetailsElement).open).toBe(false);
    expect(fetch.mock.calls.every(([path, options]) => path === '/api/state' && !options?.method)).toBe(true);
  });

  it('links to the included saved example without opening evidence before navigation', async () => {
    const fetch = mock(); render(<App />); await ready();
    expect(screen.getByRole('link', { name: /Inspect this example/ }).getAttribute('href')).toBe('/?receipt=example-ordinary');
    expect(fetch.mock.calls.every(([path, options]) => path === '/api/state' && !options?.method)).toBe(true);
  });

  it('keeps the FAQ collapsed and returns focus without clearing input or starting a search', async () => {
    const fetch = mock(); render(<App />); await ready(); enterBlock('5243119');
    const faq = screen.getByRole('region', { name: 'Before you search.' });
    const disclosures = Array.from(faq.querySelectorAll('details'));
    expect(disclosures).toHaveLength(6);
    expect(disclosures.every(item => !item.open)).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Go to search' }));
    expect(document.activeElement).toBe(document.getElementById('block-input'));
    expect((screen.getByLabelText('Hemi block number or hash') as HTMLInputElement).value).toBe('5243119');
    expect(screen.getByRole('heading', { name: headline })).toBeTruthy();
    expect(fetch.mock.calls.every(([path, options]) => path === '/api/state' && options?.method !== 'POST')).toBe(true);
  });

  it.each(['discovery', 'indexed-only', 'hosted'] as const)('describes the actual %s source-search capability in the FAQ', async mode => {
    const current = state();
    if (mode !== 'discovery') current.serving_mode = 'indexed-only';
    if (mode === 'hosted') current.deployment = 'hosted-candidate';
    const fetch = mock(current); render(<App />); await ready();
    const faq = screen.getByRole('region', { name: 'Before you search.' });
    const answer = faq.querySelectorAll('details')[2].querySelector('p')!;
    expect(answer.textContent).toBe(mode === 'discovery'
      ? 'Traze reuses compatible stored evidence first. When needed and permitted, it can search public sources within the installation’s limits.'
      : 'This installation searches collected records only. It does not start live discovery.');
    expect(fetch.mock.calls.every(([path, options]) => path === '/api/state' && options?.method !== 'POST')).toBe(true);
  });

  it('offers a real recorded JSON link when the included example is unavailable', async () => {
    const current = state(); current.receipts = []; const fetch = mock(current);
    render(<App />); await ready();
    expect(screen.queryByRole('link', { name: /Inspect this example/ })).toBeNull();
    expect(screen.getByRole('link', { name: /View recorded example/ }).getAttribute('href'))
      .toBe('https://github.com/itsmeaokiji/traze/blob/main/demo_results/mainnet_5243117.json');
    expect(fetch.mock.calls.every(([path]) => path === '/api/state')).toBe(true);
  });

  it('keeps malformed input on the homepage with focus on the invalid field', async () => {
    const fetch = mock(); render(<App />); await ready();
    fireEvent.change(screen.getByLabelText('Hemi block number or hash'), { target: { value: '0x123' } });
    fireEvent.submit(document.getElementById('lookup-form')!);
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(screen.getByRole('heading', { name: headline })).toBeTruthy();
    expect(document.activeElement).toBe(document.getElementById('block-input'));
    expect(fetch.mock.calls.every(([, options]) => options?.method !== 'POST')).toBe(true);
  });

  it('rejects an empty homepage search without substituting an example or making a request', async () => {
    const fetch = mock(); render(<App />); await ready();
    fireEvent.submit(document.getElementById('lookup-form')!);
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect((screen.getByLabelText('Hemi block number or hash') as HTMLInputElement).value).toBe('');
    expect(document.activeElement).toBe(document.getElementById('block-input'));
    expect(screen.getByRole('heading', { name: headline })).toBeTruthy();
    expect(fetch.mock.calls.every(([path, options]) => path === '/api/state' && options?.method !== 'POST')).toBe(true);
  });

  it('opens every options disclosure before focusing an invalid Bitcoin range', async () => {
    const fetch = mock(); render(<App />); await ready();
    enterBlock();
    fireEvent.change(screen.getByLabelText('From Bitcoin block'), { target: { value: '965931' } });
    fireEvent.submit(document.getElementById('lookup-form')!);
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect((document.getElementById('home-search-options') as HTMLDetailsElement).open).toBe(true);
    expect((document.querySelector('.scope-options') as HTMLDetailsElement).open).toBe(true);
    expect(document.activeElement).toBe(document.getElementById('range-end'));
    expect(screen.getByRole('heading', { name: headline })).toBeTruthy();
    expect(fetch.mock.calls.every(([, options]) => options?.method !== 'POST')).toBe(true);
  });

  it('removes introductory sections as a valid search starts and submits only once', async () => {
    let finish!: (value: Response) => void;
    const fetch = vi.fn((path: string, options?: RequestInit) => path === '/api/state' ? Promise.resolve(response(state()))
      : options?.method === 'POST' ? new Promise<Response>(resolve => { finish = resolve; }) : Promise.resolve(response(wrapper())));
    vi.stubGlobal('fetch', fetch); render(<App />); await ready();
    enterBlock();
    fireEvent.submit(document.getElementById('lookup-form')!); fireEvent.submit(document.getElementById('lookup-form')!);
    expect(screen.queryByRole('heading', { name: headline })).toBeNull();
    expect(screen.getByRole('heading', { name: 'Publication evidence' })).toBeTruthy();
    expect(screen.getByLabelText('Hemi block number or hash')).toBeTruthy();
    expect(fetch.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1);
    expect(JSON.parse(fetch.mock.calls.find(([, options]) => options?.method === 'POST')![1]!.body as string))
      .toEqual({ block: 5243117, bitcoin_range: null });
    await act(async () => { finish(response(job(true))); });
    await screen.findByRole('heading', { name: 'Publication found' });
  });

  it.each(['not_found_within_coverage', 'incomplete'] as const)('keeps %s in the focused result workspace', async status => {
    const evidence = wrapper(negative); evidence.receipt.status = status;
    mock(state(), evidence); render(<App />); await ready();
    enterBlock();
    fireEvent.submit(document.getElementById('lookup-form')!);
    await waitFor(() => expect(document.getElementById('result')).toBeTruthy());
    expect(screen.queryByRole('heading', { name: headline })).toBeNull();
    expect(screen.getByLabelText('Hemi block number or hash')).toBeTruthy();
    expect(document.getElementById('result')!.textContent).not.toContain('first publication confirmed');
  });

  it('keeps a rejected source operation in the focused error workspace', async () => {
    vi.stubGlobal('fetch', vi.fn((path: string, options?: RequestInit) => Promise.resolve(path === '/api/state' ? response(state())
      : options?.method === 'POST' ? response({ error: { code: 'busy', message: 'Controlled busy source' } }, 409) : response(wrapper()))));
    render(<App />); await ready(); enterBlock(); fireEvent.submit(document.getElementById('lookup-form')!);
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(screen.queryByRole('heading', { name: headline })).toBeNull();
    expect(screen.getByRole('heading', { name: 'Publication evidence' })).toBeTruthy();
  });

  it('loads a direct saved-result link without the introductory sections or any POST', async () => {
    window.history.replaceState(null, '', '/?receipt=example-ordinary'); const fetch = mock(); render(<App />);
    expect(screen.queryByRole('heading', { name: headline })).toBeNull();
    await screen.findByRole('heading', { name: 'Publication found' });
    expect(screen.queryByRole('heading', { name: headline })).toBeNull();
    expect(fetch.mock.calls.every(([, options]) => options?.method !== 'POST')).toBe(true);
  });

  it('loads an exact Hemi record directly without the homepage', async () => {
    const hash = '0x' + '3c'.repeat(32); window.history.replaceState(null, '', '/records/hemi/' + hash);
    const fetch = mock(); render(<App />); await screen.findByRole('heading', { name: 'Publication found' });
    expect(screen.queryByRole('heading', { name: headline })).toBeNull();
    expect(fetch.mock.calls.some(([path]) => path === '/api/v1/blocks/' + hash)).toBe(true);
  });

  it('resumes an existing job without rendering the homepage or starting a second job', async () => {
    const current = state(); current.active_job = job();
    const fetch = vi.fn((path: string) => Promise.resolve(response(path === '/api/state' ? current
      : path.startsWith('/api/jobs/') ? job(true) : wrapper())));
    vi.stubGlobal('fetch', fetch); render(<App />);
    await screen.findByRole('heading', { name: 'Publication found' }, { timeout: 3000 });
    expect(screen.queryByRole('heading', { name: headline })).toBeNull();
    expect(fetch.mock.calls.filter(([path]) => path.startsWith('/api/jobs/'))).toHaveLength(1);
  });

  it.each([['exact hash', '0x' + '3c'.repeat(32)], ['legacy block number', undefined]])(
    'shows the recorded %s while a resumed job is still running', async (_label, blockInput) => {
      const current = state(); current.active_job = job(); current.active_job.block_input = blockInput;
      const fetch = vi.fn((path: string) => Promise.resolve(response(path === '/api/state' ? current : job())));
      vi.stubGlobal('fetch', fetch); render(<App />);
      await screen.findByDisplayValue(blockInput ?? '5243117');
      expect(screen.queryByRole('heading', { name: headline })).toBeNull();
      expect(fetch.mock.calls.every(([path]) => path === '/api/state')).toBe(true);
    });

  it('uses indexed-mode copy and a read-only indexed request from the same homepage search', async () => {
    const current = state(); current.serving_mode = 'indexed-only'; const fetch = mock(current);
    render(<App />); await ready();
    expect(document.getElementById('input-help')!.textContent).toContain('Search uses indexed evidence');
    expect(document.getElementById('home-search-options')).toBeNull();
    expect(screen.queryByText(/New searches use public sources/)).toBeNull();
    enterBlock();
    fireEvent.submit(document.getElementById('lookup-form')!); await screen.findByRole('heading', { name: 'Publication found' });
    expect(fetch.mock.calls.some(([path]) => path === '/api/v1/blocks/5243117')).toBe(true);
    expect(fetch.mock.calls.every(([, options]) => options?.method !== 'POST')).toBe(true);
  });

  it('keeps exhausted discovery warnings visible outside the closed options disclosure', async () => {
    const current = state(); current.resources.source_operations_available = false;
    current.resources.blocked_reason = 'cumulative_response_bytes'; mock(current); render(<App />); await ready();
    const warning = screen.getByText(/The remaining allowance cannot reserve another full Bitcoin block/);
    expect(warning.closest('#home-search-options')).toBeNull();
    expect((document.getElementById('home-search-options') as HTMLDetailsElement).open).toBe(false);
  });

  it('does not describe a hosted indexed installation as locally run or show local saved controls', async () => {
    const current = state(); current.deployment = 'hosted-candidate'; current.serving_mode = 'indexed-only'; mock(current);
    render(<App />); await ready();
    expect(screen.getByText('This installation serves indexed evidence. Search availability depends on collected coverage.')).toBeTruthy();
    expect(screen.queryByText(/Traze currently runs locally/)).toBeNull();
    expect(screen.queryByText('Saved results', { exact: true })).toBeNull();
  });
});

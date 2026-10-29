import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { IndexUpdateStatus } from './IndexUpdateStatus';
import type { IndexUpdate } from './types';

const id = 'a'.repeat(32);
function state(extra: Partial<IndexUpdate> = {}): IndexUpdate {
  return { state: 'pending', reason: 'index_busy', attempts: 1, can_retry: true,
    automatic_retry_pending: true, source_requests: 0, ...extra };
}
function respond(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status }); }
function view(initial?: IndexUpdate, enabled = true) {
  return render(<IndexUpdateStatus initial={initial} receiptId={id} token="controlled-token" enabled={enabled} busy={false} />);
}

describe('Local evidence index handoff', () => {
  it('adds nothing to legacy/example results without a handoff', () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    const result = view();
    expect(result.container.textContent).toBe(''); expect(fetch).not.toHaveBeenCalled();
  });

  it('links indexed proof-only evidence without claiming a full scan or new source check', () => {
    view(state({ state: 'indexed', publication_count: 1, record_url: '/records/publications/' + 'b'.repeat(64),
      body_scope: { complete: 0, proof_only: 1, unavailable: 0 } }));
    expect(screen.getByText('Index updated.')).toBeTruthy();
    expect(screen.getByRole('link', { name: /Open indexed publication/ }).getAttribute('href')).toContain('/records/publications/');
    expect(screen.getByText(/At this index update/).textContent).toContain('original saved search coverage is unchanged');
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('keeps duplicate and no-positive-publication outcomes distinct', () => {
    const result = view(state({ state: 'already_indexed', publication_count: 4 }));
    expect(screen.getByText('Already indexed.')).toBeTruthy();
    result.rerender(<IndexUpdateStatus initial={state({ state: 'indexed', publication_count: 0 })} receiptId={id} token="token" enabled busy={false} />);
    expect(screen.getByText('Search data indexed.')).toBeTruthy();
    expect(screen.getByText(/No applicable publication record was added/)).toBeTruthy();
    expect(screen.queryByRole('link')).toBeNull();
  });

  it('sends exactly one receipt-only local retry with the CSRF token', async () => {
    const fetch = vi.fn().mockResolvedValue(respond(state({ state: 'indexed', publication_count: 4 })));
    vi.stubGlobal('fetch', fetch);
    view(state({ automatic_retry_pending: false }));
    fireEvent.click(screen.getByRole('button', { name: 'Retry index update' }));
    await screen.findByText('Index updated.');
    expect(fetch).toHaveBeenCalledTimes(1);
    const [path, options] = fetch.mock.calls[0];
    expect(path).toBe('/api/index-retry');
    expect(options.method).toBe('POST');
    expect(JSON.parse(options.body)).toEqual({ receipt_id: id });
    expect(options.headers['X-Local-Token']).toBe('controlled-token');
  });

  it('polls only the local status endpoint and stops when indexed', async () => {
    vi.useFakeTimers();
    const fetch = vi.fn().mockResolvedValue(respond(state({ state: 'indexed', publication_count: 1 })));
    vi.stubGlobal('fetch', fetch);
    view(state());
    await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0][0]).toBe('/api/index-updates/' + id);
    expect(fetch.mock.calls[0][1].method).toBeUndefined();
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('does not poll indefinitely after automatic attempts stop', async () => {
    vi.useFakeTimers(); const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    view(state({ automatic_retry_pending: false, attempts: 8 }));
    expect(screen.getByRole('status').textContent).toContain('Automatic retries have stopped.');
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(fetch).not.toHaveBeenCalled();
  });

  it('keeps a retry failure in the index area without starting lookup or refresh', async () => {
    const fetch = vi.fn().mockResolvedValue(respond({ error: { code: 'index_unavailable', message: 'Controlled index failure' } }, 503));
    vi.stubGlobal('fetch', fetch);
    view(state({ automatic_retry_pending: false }));
    fireEvent.click(screen.getByRole('button', { name: 'Retry index update' }));
    await screen.findByRole('alert');
    expect(screen.getByText('Index update pending.')).toBeTruthy();
    expect(fetch.mock.calls.every(([path]) => path === '/api/index-retry')).toBe(true);
  });

  it('exposes no write action in read-only mode and aborts a retry on unmount', async () => {
    const first = view(state({ state: 'not_queued' }), false);
    expect(screen.queryByRole('button')).toBeNull(); first.unmount();
    let signal!: AbortSignal;
    vi.stubGlobal('fetch', vi.fn((_path, options) => { signal = options.signal; return new Promise<Response>(() => {}); }));
    const result = view(state({ state: 'not_queued', automatic_retry_pending: false }));
    fireEvent.click(screen.getByRole('button', { name: 'Add saved evidence to index' }));
    await waitFor(() => expect(signal).toBeDefined());
    result.unmount(); expect(signal.aborted).toBe(true);
  });
});

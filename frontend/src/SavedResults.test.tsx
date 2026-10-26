import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { SavedResults } from './SavedResults';
import type { SavedHistoryPage, SavedResult } from './types';

const example: SavedResult = { id: 'example-ordinary', label: 'Ordinary block', origin: 'saved_example', block_number: 5243117, status: 'found' };
function result(n: number, extra: Partial<SavedResult> = {}): SavedResult {
  return { id: n.toString(16).padStart(32, '0'), label: 'Browser result', origin: 'browser_saved',
    block_number: 5223465, status: 'not_found_within_coverage', completed_at: '2026-10-24T15:00:00Z', mode: 'live_lookup', ...extra };
}
function page(items: SavedResult[], next: string | null = null): SavedHistoryPage {
  return { items, page: { limit: 10, next_cursor: next }, order: 'saved_file_desc', metadata_only: true };
}
function respond(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status }); }
function mount(extra: Partial<Parameters<typeof SavedResults>[0]> = {}) {
  const onSelect = vi.fn();
  const view = render(<SavedResults receipts={[example, result(3), result(2), result(1)]} ready busy={false} onSelect={onSelect} {...extra} />);
  fireEvent.click(screen.getByText('Saved results', { exact: true }));
  return { onSelect, ...view };
}
async function history() { fireEvent.click(screen.getByRole('button', { name: 'View saved history' })); await screen.findByText(/Page 1 loaded/); }

describe('Saved results and read-only history', () => {
  it('separates examples and two recent results without fetching history eagerly', () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    mount();
    expect(screen.getByRole('region', { name: 'Examples' }).querySelectorAll('li')).toHaveLength(1);
    expect(screen.getByRole('region', { name: 'Recent results' }).querySelectorAll('li')).toHaveLength(2);
    expect(screen.getByText(/does not contact public sources or refresh/)).toBeTruthy();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('opens an example, collapses the panel and returns focus to its summary', () => {
    const { onSelect } = mount();
    fireEvent.click(screen.getByRole('button', { name: 'Open saved Ordinary block for Hemi 5,243,117' }));
    expect(onSelect).toHaveBeenCalledWith(example.id);
    expect((document.getElementById('saved-results') as HTMLDetailsElement).open).toBe(false);
    expect(document.activeElement).toBe(document.querySelector('#saved-results > summary'));
  });

  it('pages older/newer results with GETs and keeps same-block operations distinct', async () => {
    const older = result(1, { mode: 'live_search_continuation', segment: 2 });
    const recheck = result(2, { mode: 'live_metadata_refresh', status: 'incomplete' });
    const fetch = vi.fn((path: string) => Promise.resolve(respond(path.includes('cursor=') ? page([older]) : page([recheck], 'safe-next'))));
    vi.stubGlobal('fetch', fetch);
    mount(); await history();
    let region = screen.getByRole('region', { name: 'Saved history' });
    expect(within(region).getByRole('button', { name: /Source recheck · incomplete/ })).toBeTruthy();
    expect(within(region).getByText(/Completed: Oct 24, 2026/)).toBeTruthy();
    fireEvent.click(within(region).getByRole('button', { name: 'Older results' }));
    await screen.findByText(/Page 2 loaded/);
    region = screen.getByRole('region', { name: 'Saved history' });
    expect(within(region).getByRole('button', { name: /Search segment 2 · no match in checked range/ })).toBeTruthy();
    expect(within(region).getByRole('button', { name: 'Older results' }).getAttribute('aria-disabled')).toBe('true');
    fireEvent.click(within(region).getByRole('button', { name: 'Newer results' }));
    await screen.findByText(/Page 1 loaded/);
    expect(fetch.mock.calls.map(([path]) => path)).toEqual(['/api/saved-history', '/api/saved-history?cursor=safe-next', '/api/saved-history']);
    expect(fetch.mock.calls.every(call => (call as unknown[])[1] && !((call as unknown[])[1] as RequestInit).method)).toBe(true);
  });

  it('opens an older history row by its exact id, without a new lookup', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(respond(page([result(45)]))));
    const { onSelect } = mount(); await history();
    fireEvent.click(within(screen.getByRole('region', { name: 'Saved history' })).getByRole('button', { name: /Open saved Search/ }));
    expect(onSelect).toHaveBeenCalledExactlyOnceWith(result(45).id);
    expect(document.activeElement).toBe(document.querySelector('#saved-results > summary'));
  });

  it('shows empty history as empty, without implying a new source search', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(respond(page([]))));
    mount({ receipts: [] }); await history();
    expect(screen.getByText('No saved results on this page.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Older results' }).getAttribute('aria-disabled')).toBe('true');
  });

  it('keeps malformed/unavailable rows visible and unopenable', async () => {
    const item = result(5, { available: false, status: null, block_number: null });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(respond(page([item]))));
    const { onSelect } = mount(); await history();
    const button = within(screen.getByRole('region', { name: 'Saved history' })).getByRole('button', { name: /Saved file unavailable/ }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.click(button); expect(onSelect).not.toHaveBeenCalled();
    expect(screen.getByText(/They have not been opened or removed/)).toBeTruthy();
  });

  it('reports request failure and retries the same page rather than starting acquisition', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(respond({ error: { code: 'invalid_history_cursor', message: 'Controlled cursor error' } }, 400))
      .mockResolvedValueOnce(respond(page([result(1)])));
    vi.stubGlobal('fetch', fetch);
    mount(); fireEvent.click(screen.getByRole('button', { name: 'View saved history' }));
    await screen.findByRole('alert');
    expect(screen.getByRole('alert').textContent).toContain('Start history again');
    fireEvent.click(screen.getByRole('button', { name: 'Retry history' }));
    await screen.findByText(/Page 1 loaded/);
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(fetch.mock.calls.every(([path]) => path === '/api/saved-history')).toBe(true);
  });

  it('aborts a pending history read when collapsed, with no late results', async () => {
    let pending!: AbortSignal;
    let release!: (value: Response) => void;
    vi.stubGlobal('fetch', vi.fn((_path, options) => { pending = options.signal; return new Promise<Response>(resolve => { release = resolve; }); }));
    mount(); fireEvent.click(screen.getByRole('button', { name: 'View saved history' }));
    await screen.findByText('Loading saved history…');
    fireEvent.click(screen.getByText('Saved results', { exact: true }));
    expect(pending.aborted).toBe(true);
    await act(async () => release(respond(page([result(1)]))));
    expect(screen.queryByText(/Page 1 loaded/)).toBeNull();
  });

  it('aborts reads on unmount and does not select a result automatically', async () => {
    let signal!: AbortSignal;
    vi.stubGlobal('fetch', vi.fn((_path, options) => { signal = options.signal; return new Promise<Response>(() => {}); }));
    const { unmount, onSelect } = mount();
    fireEvent.click(screen.getByRole('button', { name: 'View saved history' }));
    await screen.findByText('Loading saved history…');
    unmount(); expect(signal.aborted).toBe(true); expect(onSelect).not.toHaveBeenCalled();
  });

  it('refreshes to page one and disables selection/navigation while workspace is busy', async () => {
    const fetch = vi.fn().mockImplementation((path: string) => Promise.resolve(respond(path.includes('cursor=') ? page([result(1)]) : page([result(2)], 'next'))));
    vi.stubGlobal('fetch', fetch);
    const { rerender, onSelect } = mount(); await history();
    fireEvent.click(screen.getByRole('button', { name: 'Older results' }));
    await screen.findByText(/Page 2 loaded/);
    fireEvent.click(screen.getByRole('button', { name: 'Refresh history' }));
    await screen.findByText(/Page 1 loaded/);
    rerender(<SavedResults receipts={[example]} ready busy onSelect={onSelect} />);
    expect((screen.getByRole('button', { name: 'Older results' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: /Open saved Ordinary block/ }) as HTMLButtonElement).disabled).toBe(true);
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
  });

  it('retains pagination focus during loading and ignores unavailable activations', async () => {
    let release!: (value: Response) => void;
    const fetch = vi.fn().mockResolvedValueOnce(respond(page([result(2)], 'older')))
      .mockImplementationOnce(() => new Promise<Response>(resolve => { release = resolve; }));
    vi.stubGlobal('fetch', fetch);
    mount(); await history();
    const older = screen.getByRole('button', { name: 'Older results' });
    older.focus(); fireEvent.click(older);
    await screen.findByText('Loading saved history…');
    expect(document.activeElement).toBe(older);
    expect((older as HTMLButtonElement).disabled).toBe(false);
    expect(older.getAttribute('aria-disabled')).toBe('true');
    fireEvent.click(older); expect(fetch).toHaveBeenCalledTimes(2);
    await act(async () => release(respond(page([result(1)]))));
    await screen.findByText(/Page 2 loaded/);
    expect(document.activeElement).toBe(older);
    fireEvent.click(older); expect(fetch).toHaveBeenCalledTimes(2);
  });
});

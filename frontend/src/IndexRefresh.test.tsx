import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { BrowseIndex } from './ExplorerViews';

const summary = { index_kind: 'maintained', counts: { processed_bodies: 1, supported_publications: 1,
  associated_publications: 1, unassociated_publications: 0, unsupported_publications: 0, failed_or_pending_bodies: 0 },
  bitcoin_height_bounds: [965931, 965931], source_observations_current: false, history_complete: false, last_import_stored_at: null };
const item = (id: string) => ({ id: id.repeat(64), record_url: '/records/publications/' + id.repeat(64),
  bitcoin_height: 965931, bitcoin_block_hash: 'b'.repeat(64), height_conflict: false, txid: id.repeat(64),
  vout_index: 1, keystone_height: 5243125, keystone_id: 'd'.repeat(64), state: 'associated', association_available: true });

describe('Explicit indexed refresh', () => {
  it('fetches newly indexed rows, retains filters/view and returns to the first page with GET only', async () => {
    window.history.replaceState(null, '', '/browse?view=timeline&bitcoin_height=965931&state=associated&cursor=older');
    const calls: string[] = [];
    vi.stubGlobal('fetch', vi.fn((route: string, options?: RequestInit) => {
      expect(options?.method).not.toBe('POST'); calls.push(route);
      return Promise.resolve(new Response(JSON.stringify(route === '/api/v1/index' ? summary : {
        items: route.includes('cursor=older') ? [item('a')] : [item('a'), item('c')], page: { next_cursor: null },
      })));
    }));
    render(<BrowseIndex />);
    await waitFor(() => expect(document.querySelectorAll('[data-publication-id]').length).toBe(1));
    fireEvent.click(screen.getByRole('button', { name: 'Refresh index' }));
    await waitFor(() => expect(document.querySelectorAll('[data-publication-id]').length).toBe(2));
    const query = new URLSearchParams(window.location.search);
    expect(query.get('view')).toBe('timeline'); expect(query.get('bitcoin_height')).toBe('965931');
    expect(query.get('state')).toBe('associated'); expect(query.has('cursor')).toBe(false);
    expect(calls.filter(p => p === '/api/v1/index')).toHaveLength(2);
    expect(calls.every(p => p.startsWith('/api/v1/index') || p.startsWith('/api/v1/publications?'))).toBe(true);
  });

  it('refreshes on the first page too, with no source-job or cursor side effect', async () => {
    window.history.replaceState(null, '', '/browse');
    let reads = 0;
    vi.stubGlobal('fetch', vi.fn((route: string) => {
      if (route === '/api/v1/index') reads += 1;
      return Promise.resolve(new Response(JSON.stringify(route === '/api/v1/index' ? summary : { items: [], page: { next_cursor: null } })));
    }));
    render(<BrowseIndex />);
    await waitFor(() => expect((screen.getByRole('button', { name: 'Refresh index' }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole('button', { name: 'Refresh index' }));
    await waitFor(() => expect(reads).toBe(2));
    expect(window.location.pathname).toBe('/browse'); expect(window.location.search).toBe('');
    expect(screen.queryByText(/source search in progress/i)).toBeNull();
  });
});

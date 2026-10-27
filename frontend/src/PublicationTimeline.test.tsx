import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { BrowseIndex, PublicationRecordView } from './ExplorerViews';
import { PublicationTimeline } from './PublicationTimeline';
import { browseLocation, browseReturnLabel, withBrowseReturn } from './browseNavigation';
import { timelineGroups, timelineKeystoneKey } from './timelineRecords';
import type { IndexSummary, PublicationSummary } from './types';

const hash = (character: string) => character.repeat(64);
const summary: IndexSummary = { index_kind: 'catalogue', counts: { processed_bodies: 14, supported_publications: 21, associated_publications: 6, unassociated_publications: 15, unsupported_publications: 0, failed_or_pending_bodies: 0 }, bitcoin_height_bounds: [965032, 965045], source_observations_current: false, history_complete: false, last_import_stored_at: '2026-10-22T09:20:00Z' };
function publication(character = 'a', overrides: Partial<PublicationSummary> = {}): PublicationSummary {
  return { id: hash(character), record_url: '/records/publications/' + hash(character), bitcoin_block_hash: hash('b'), bitcoin_height: 965045, height_conflict: false, txid: hash(character), vout_index: 1, keystone_height: 5200225, keystone_id: hash('c'), state: 'associated', association_available: true, ...overrides };
}
const respond = (value: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(value), { status }));
const page = (items: PublicationSummary[], next: string | null = null) => ({ items, page: { limit: 20, next_cursor: next } });
function mount(records: PublicationSummary[]) { return render(<PublicationTimeline records={records} returnTo="/browse?view=timeline" />); }

describe('indexed timeline identity and meaning', () => {
  it('groups exact Bitcoin hashes and never merges different hashes at one height', () => {
    const records = [publication(), publication('d'), publication('e', { bitcoin_block_hash: hash('f') })];
    const groups = timelineGroups(records);
    expect(groups.map(group => [group.hash, group.records.length])).toEqual([[hash('b'), 2], [hash('f'), 1]]);
    mount(records);
    expect(document.querySelectorAll('.timeline-group')).toHaveLength(2);
    expect(document.querySelectorAll('[data-publication-id]')).toHaveLength(3);
  });

  it('orders known heights and separates inconsistent, unknown and explicitly conflicting height observations', () => {
    const records = [publication('1', { bitcoin_height: 1 }), publication('2', { bitcoin_block_hash: hash('2'), bitcoin_height: 2 }), publication('3', { bitcoin_block_hash: hash('3'), bitcoin_height: 3 }), publication('4', { bitcoin_block_hash: hash('3'), bitcoin_height: 4 }), publication('5', { bitcoin_block_hash: hash('4'), bitcoin_height: null }), publication('6', { bitcoin_block_hash: hash('5'), bitcoin_height: 10, height_conflict: true })];
    expect(timelineGroups(records).map(group => [group.height, group.heightConflict])).toEqual([[2, false], [1, false], [null, true], [null, false], [null, true]]);
    mount(records);
    expect(screen.getAllByText('Conflicting heights')).toHaveLength(2);
    expect(screen.getByText('Height not recorded')).toBeTruthy();
    expect(screen.getAllByText('Not placed at a known height')).toHaveLength(3);
  });

  it('does not use a partially missing height as an established position', () => {
    expect(timelineGroups([publication(), publication('d', { bitcoin_height: null })])[0].height).toBeNull();
  });

  it('orders encoded keystone heights within a block without mutating the source page', () => {
    const records = [publication('a', { keystone_height: 5200250 }), publication('d'), publication('e', { keystone_height: null, keystone_id: null, state: 'unsupported' })];
    const before = structuredClone(records);
    expect(timelineGroups(records)[0].records.map(item => item.keystone_height)).toEqual([5200225, 5200250, null]);
    expect(records).toEqual(before);
  });

  it('highlights the same exact keystone across blocks while leaving a same-height different ID separate', () => {
    const records = [publication(), publication('d', { bitcoin_block_hash: hash('e'), bitcoin_height: 965046 }), publication('f', { keystone_id: hash('f') })];
    mount(records);
    fireEvent.click(screen.getAllByRole('button', { name: /Highlight keystone.*ID c/ })[0]);
    expect(document.querySelectorAll('.timeline-row.is-selected')).toHaveLength(2);
    expect(screen.getByText('2 publications highlighted on this page')).toBeTruthy();
    expect(screen.getByRole('button', { name: /ID f/ }).getAttribute('aria-pressed')).toBe('false');
    fireEvent.click(screen.getByRole('button', { name: 'Clear highlight' }));
    expect(document.querySelectorAll('.timeline-row.is-selected')).toHaveLength(0);
    expect(timelineKeystoneKey(records[0])).not.toBe(timelineKeystoneKey(records[2]));
  });

  it('keeps contradictory payload heights separate even if an identifier is repeated', () => {
    expect(timelineKeystoneKey(publication())).not.toBe(timelineKeystoneKey(publication('d', { keystone_height: 5200250 })));
  });

  it('keeps pending and unsupported payloads explicit without asserting checked Hemi evidence', () => {
    mount([publication('a', { state: 'pending', association_available: false }), publication('d', { state: 'unsupported', association_available: false, keystone_height: null, keystone_id: null })]);
    expect(document.querySelectorAll('.timeline-row.is-associated')).toHaveLength(0);
    expect(document.querySelectorAll('.timeline-row.is-pending')).toHaveLength(2);
    expect(screen.getByText('Hemi evidence unavailable')).toBeTruthy();
    expect(screen.getByText('No Hemi association asserted')).toBeTruthy();
    expect(screen.getAllByRole('button', { name: /Highlight keystone/ })).toHaveLength(1);
    expect(document.body.textContent).toContain('not fresh source checks');
  });

  it('does not label an inconsistent availability flag as metadata available', () => {
    mount([publication('a', { association_available: false })]);
    expect(screen.getByText('Hemi evidence unavailable')).toBeTruthy();
    expect(document.querySelector('.timeline-row.is-associated')).toBeNull();
  });

  it('binds inspection to the exact publication output and preserves timeline return context', () => {
    const p = publication(); mount([p]);
    const href = screen.getByRole('link', { name: `Inspect publication ${p.txid}, output 1` }).getAttribute('href')!;
    expect(new URL(href, window.location.origin).pathname).toBe(p.record_url);
    expect(new URL(href, window.location.origin).searchParams.get('return')).toBe('/browse?view=timeline');
    expect(screen.getByText(/Only this page is grouped/)).toBeTruthy();
    expect(document.body.textContent).not.toContain('earliest observed');
  });

  it('retains distinct output records even when their transaction IDs match', () => {
    const first = publication(); const second = publication('d', { txid: first.txid, vout_index: 2 });
    mount([first, second]);
    const one = screen.getByRole('link', { name: `Inspect publication ${first.txid}, output 1` });
    const two = screen.getByRole('link', { name: `Inspect publication ${first.txid}, output 2` });
    expect(new URL(one.getAttribute('href')!, window.location.origin).pathname).toBe(first.record_url);
    expect(new URL(two.getAttribute('href')!, window.location.origin).pathname).toBe(second.record_url);
  });

  it('explains unshown intervals without calling them proof of missing publications', () => {
    mount([publication(), publication('d', { bitcoin_height: 965040, bitcoin_block_hash: hash('e') })]);
    expect(screen.getByText('Intervening Bitcoin heights are not shown')).toBeTruthy();
    expect(document.body.textContent).toContain('this view does not decide which');
  });

  it('has an explicit scoped empty state', () => {
    mount([]);
    expect(screen.getByText(/No indexed records match/)).toBeTruthy();
    expect(screen.queryByRole('list', { name: 'Indexed publication timeline' })).toBeNull();
  });
});

describe('timeline browse workflow', () => {
  it('loads a direct timeline URL with existing filters through only the paginated publication read API', async () => {
    window.history.replaceState(null, '', '/browse?view=timeline&keystone_height=5200225&bitcoin_height=965045&state=pending&cursor=controlled-cursor');
    const fetch = vi.fn((path: string, options?: RequestInit) => {
      expect(options?.method).not.toBe('POST');
      expect(path === '/api/v1/index' || path.startsWith('/api/v1/publications?')).toBe(true);
      return respond(path === '/api/v1/index' ? summary : page([publication('a', { state: 'pending', association_available: false })]));
    });
    vi.stubGlobal('fetch', fetch); render(<BrowseIndex />);
    await screen.findByRole('list', { name: 'Indexed publication timeline' });
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(fetch).toHaveBeenCalledWith('/api/v1/publications?limit=20&keystone_height=5200225&bitcoin_height=965045&state=pending&cursor=controlled-cursor', expect.anything());
    expect(new URLSearchParams(window.location.search).get('view')).toBe('timeline');
    expect((screen.getByLabelText('Keystone height') as HTMLInputElement).value).toBe('5200225');
  });

  it('retains the publication cursor when switching table/timeline, and resets only on filter/list changes', async () => {
    const fetch = vi.fn((path: string) => respond(path === '/api/v1/index' ? summary : page([publication()], path.includes('cursor=') ? null : 'page-two')));
    vi.stubGlobal('fetch', fetch); render(<BrowseIndex />);
    await screen.findByRole('link', { name: /Inspect publication/ });
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
    await waitFor(() => expect(new URLSearchParams(window.location.search).get('cursor')).toBe('page-two'));
    fireEvent.click(screen.getByRole('button', { name: 'Timeline' }));
    await screen.findByRole('list', { name: 'Indexed publication timeline' });
    expect(new URLSearchParams(window.location.search).get('cursor')).toBe('page-two');
    expect(screen.queryByRole('table')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Publications' }));
    await screen.findByRole('table');
    expect(new URLSearchParams(window.location.search).get('cursor')).toBe('page-two');
    fireEvent.click(screen.getByRole('button', { name: 'Timeline' }));
    await screen.findByRole('list', { name: 'Indexed publication timeline' });
    fireEvent.change(screen.getByLabelText('Keystone height'), { target: { value: '5200225' } });
    fireEvent.click(screen.getByRole('button', { name: 'Apply filters' }));
    await waitFor(() => expect(new URLSearchParams(window.location.search).has('cursor')).toBe(false));
    expect(new URLSearchParams(window.location.search).get('view')).toBe('timeline');
  });

  it('returns from publication inspection with the exact timeline filters and page in the Hemi link', async () => {
    const p = publication();
    const from = '/browse?view=timeline&keystone_height=5200225&state=associated&cursor=page-two';
    window.history.replaceState(null, '', withBrowseReturn(p.record_url, from));
    vi.stubGlobal('fetch', vi.fn(() => respond({ publication: p, hemi_records: [{ height: 5200225, hash: '0x' + hash('f'), record_url: '/records/hemi/0x' + hash('f') + '?publication=' + p.id }], reported_bitcoin_heights: [965045], data: {}, retrieved_at: '2026-10-22T09:20:00Z' })));
    render(<PublicationRecordView id={p.id} />);
    const back = await screen.findByRole('link', { name: '← Back to timeline' });
    expect(back.getAttribute('href')).toBe(from);
    const next = screen.getByRole('link', { name: /Inspect Hemi keystone/ }).getAttribute('href')!;
    const url = new URL(next, window.location.origin);
    expect(url.searchParams.get('publication')).toBe(p.id);
    expect(url.searchParams.get('return')).toBe(from);
    expect(browseReturnLabel(from)).toBe('← Back to timeline');
  });

  it('does not flash keystone summaries into a publication timeline when changing list kind', async () => {
    window.history.replaceState(null, '', '/browse?view=keystones');
    vi.stubGlobal('fetch', vi.fn((path: string) => respond(path === '/api/v1/index' ? summary : path.includes('/keystones') ? { items: [{ height: 5200225, keystone_id: hash('c'), publication_count: 1, associated_count: 1, publications_url: '/browse?keystone_height=5200225' }], page: { next_cursor: null } } : page([publication()]))));
    render(<BrowseIndex />);
    await screen.findByRole('link', { name: '5,200,225' });
    fireEvent.click(screen.getByRole('button', { name: 'Timeline' }));
    const timeline = await screen.findByRole('list', { name: 'Indexed publication timeline' });
    expect(within(timeline).getByRole('link', { name: /Inspect publication/ })).toBeTruthy();
  });

  it('reports a failed timeline read without falling back to acquisition', async () => {
    window.history.replaceState(null, '', '/browse?view=timeline');
    const fetch = vi.fn(() => respond({ error: { code: 'index_unavailable' } }, 503));
    vi.stubGlobal('fetch', fetch); render(<BrowseIndex />);
    expect((await screen.findByRole('alert')).textContent).toContain('no source search was started');
    expect(fetch.mock.calls).toHaveLength(2);
    expect(screen.queryByRole('list', { name: 'Indexed publication timeline' })).toBeNull();
  });

  it('rejects nonlocal or malformed return contexts while retaining a valid timeline URL', () => {
    expect(browseLocation('https://example.com/browse?view=timeline')).toBeUndefined();
    expect(browseLocation('/browse?view=timeline&cursor=a&cursor=b')).toBeUndefined();
    expect(browseLocation('/browse?view=timeline#external')).toBeUndefined();
    expect(browseLocation('/browse?view=timeline&state=pending&cursor=abc')).toBe('/browse?view=timeline&state=pending&cursor=abc');
  });
});

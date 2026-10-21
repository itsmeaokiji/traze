import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import recordedData from '../../demo_results/main_catalogue_5243119.json';
import App from './App';
import { BrowseIndex, PublicationRecordView } from './ExplorerViews';
import { EvidenceDownload } from './EvidenceDownload';
import { EvidenceView } from './EvidenceView';
import type { IndexSummary, PublicationSummary, Receipt, ReceiptWrapper, WorkspaceState } from './types';

const state: WorkspaceState = { serving_mode: 'indexed-only', csrf_token: 'test', receipts: [], active_job: null,
  resources: { available: true, session_limit_bytes: 100000000 },
  limits: { response_mb_per_lookup: 40, bitcoin_body_attempts: 20, ancestry_links: 256, session_response_mb: 100 } };
const summary: IndexSummary = { index_kind: 'catalogue', counts: { processed_bodies: 17, supported_publications: 22, associated_publications: 10, unassociated_publications: 12, unsupported_publications: 0, failed_or_pending_bodies: 0 },
  bitcoin_height_bounds: [941741, 965931], source_observations_current: false, history_complete: false, last_import_stored_at: null };
function evidence(): ReceiptWrapper {
  const receipt = structuredClone(recordedData) as unknown as Receipt;
  return { id: 'indexed-current', label: 'Recorded evidence', filename: 'evidence.json', receipt,
    record_url: '/records/hemi/' + receipt.query.block_hash,
    verification: { proofs_checked: 4, current_chain_membership_checked: false } };
}
function item(): PublicationSummary {
  return { id: 'a'.repeat(64), record_url: '/records/publications/' + 'a'.repeat(64), bitcoin_height: 965931,
    bitcoin_block_hash: 'b'.repeat(64), height_conflict: false, txid: 'c'.repeat(64), vout_index: 1,
    keystone_height: 5243125, keystone_id: 'd'.repeat(64), state: 'associated', association_available: true };
}
const respond = (value: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(value), { status }));
function blobs() {
  const saved: Blob[] = [];
  vi.stubGlobal('URL', class extends URL {
    static createObjectURL(value: Blob) { saved.push(value); return 'blob:retained-' + saved.length; }
    static revokeObjectURL = vi.fn();
  });
  return saved;
}

describe('public explorer workflows', () => {
  it('keeps browse and API pages focused without the search or collector workspace', async () => {
    window.history.replaceState(null, '', '/browse');
    vi.stubGlobal('fetch', vi.fn((path: string) => respond(path === '/api/state' ? state : path === '/api/v1/index' ? summary : { items: [], page: { next_cursor: null } })));
    const { unmount } = render(<App />);
    await screen.findByRole('heading', { name: 'Browse publications' });
    expect(screen.queryByLabelText('Hemi block number or hash')).toBeNull();
    expect(screen.queryByText('Collection stopped')).toBeNull();
    unmount();
    window.history.replaceState(null, '', '/api-docs');
    render(<App />);
    expect(screen.getByRole('heading', { name: 'Read API · v1' })).toBeTruthy();
    expect(screen.queryByLabelText('Hemi block number or hash')).toBeNull();
  });

  it('uses an independent GET search and replaces input with an exact shareable record URL', async () => {
    blobs();
    const packet = evidence();
    const requests: string[] = [];
    vi.stubGlobal('fetch', vi.fn((path: string, options?: RequestInit) => {
      expect(options?.method).not.toBe('POST'); requests.push(path);
      return respond(path === '/api/state' ? state : packet);
    }));
    render(<App />);
    await waitFor(() => expect((screen.getByRole('button', { name: 'Find publication' }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.change(screen.getByLabelText('Hemi block number or hash'), { target: { value: '5243119' } });
    fireEvent.submit(document.querySelector('#lookup-form')!);
    await screen.findByRole('heading', { name: 'Recorded publication evidence' });
    expect(requests).toContain('/api/v1/blocks/5243119');
    expect(window.location.pathname).toBe(packet.record_url);
    expect(screen.getByRole('button', { name: 'Copy record link' })).toBeTruthy();
    expect((await screen.findByRole('link', { name: /Download JSON/ })).getAttribute('href')).toMatch(/^blob:/);
    expect(screen.queryByRole('button', { name: /Recheck sources/ })).toBeNull();
  });

  it('loads a direct exact-hash record with the linked publication preserved', async () => {
    blobs();
    const packet = evidence(); const publication = 'a'.repeat(64);
    packet.selected_publication = publication; packet.selected_publication_available = false;
    packet.record_url += '?publication=' + publication;
    window.history.replaceState(null, '', packet.record_url);
    const fetch = vi.fn((path: string) => respond(path === '/api/state' ? state : packet));
    vi.stubGlobal('fetch', fetch);
    render(<App />);
    await screen.findByRole('heading', { name: 'Recorded publication evidence' });
    expect(fetch).toHaveBeenCalledWith(`/api/v1/blocks/${packet.receipt.query.block_hash}?publication=${publication}`, expect.anything());
    expect(screen.getByRole('alert').textContent).toContain('linked publication is unavailable');
    expect(document.body.textContent).toContain('Indexed evidence may change');
  });

  it('browses summaries, follows a cursor and changes to keystone browsing', async () => {
    window.history.replaceState(null, '', '/browse');
    const first = item();
    const fetch = vi.fn((path: string) => respond(path === '/api/v1/index' ? summary : path.includes('/keystones') ? { items: [{ height: 5243125, keystone_id: 'd'.repeat(64), publication_count: 2, associated_count: 1, publications_url: '/browse?keystone_height=5243125' }], page: { next_cursor: null } } : { items: path.includes('cursor=') ? [] : [first], page: { next_cursor: path.includes('cursor=') ? null : 'next-controlled-cursor' } }));
    vi.stubGlobal('fetch', fetch);
    render(<BrowseIndex />);
    expect(await screen.findByRole('link', { name: 'Inspect publication ' + first.txid })).toBeTruthy();
    expect(document.body.textContent).toContain('Gaps may exist between these bounds');
    expect(document.body.textContent).toContain('12 publications await Hemi metadata');
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
    await screen.findByText(/No indexed records match/);
    expect(fetch).toHaveBeenCalledWith('/api/v1/publications?limit=20&cursor=next-controlled-cursor', expect.anything());
    fireEvent.click(screen.getByRole('button', { name: 'Published keystones' }));
    await screen.findByRole('link', { name: '5,243,125' });
    expect(fetch).toHaveBeenCalledWith('/api/v1/keystones?limit=20', expect.anything());
  });

  it('shows an unassociated publication honestly without inventing a Hemi route', async () => {
    const p = item(); p.state = 'pending'; p.association_available = false;
    vi.stubGlobal('fetch', vi.fn(() => respond({ publication: p, hemi_records: [], reported_bitcoin_heights: [965931], data: {}, retrieved_at: '2026-10-17T11:00:00Z' })));
    render(<PublicationRecordView id={p.id} />);
    await screen.findByRole('heading', { name: 'Bitcoin publication' });
    expect(screen.getByText('Hemi metadata pending')).toBeTruthy();
    expect(screen.getByText(/have not been associated/)).toBeTruthy();
    expect(screen.queryByRole('link', { name: /Inspect Hemi/ })).toBeNull();
    expect(screen.getByRole('button', { name: 'Copy record link' })).toBeTruthy();
  });

  it('labels a focused publication separately from the receipt default', () => {
    blobs();
    const packet = evidence(); packet.receipt.proofs[1].publication_id = 'e'.repeat(64);
    packet.selected_publication = 'e'.repeat(64); packet.selected_publication_available = true;
    render(<EvidenceView wrapper={packet} fresh busy={false} sourceOperationsEnabled={false} onRecheck={vi.fn()} onError={vi.fn()} />);
    expect(document.querySelector('#publication-view-label')?.textContent).toContain('Linked publication');
    expect(document.querySelector('#transaction-link')?.textContent).toBe(packet.receipt.proofs[1].txid);
    expect(document.body.textContent).not.toContain('Default selection follows the earliest');
  });

  it('exports the displayed snapshot and revokes old blob URLs when the view changes', async () => {
    const saved = blobs(); const packet = evidence();
    const { rerender } = render(<EvidenceDownload wrapper={packet} />);
    const first = await new Promise<string>(resolve => { const file = new FileReader(); file.onload = () => resolve(String(file.result)); file.readAsText(saved[0]); });
    expect(JSON.parse(first)).toEqual(packet.receipt);
    const later = structuredClone(packet); later.receipt.completed_at = '2026-10-18T00:00:00Z';
    rerender(<EvidenceDownload wrapper={later} />);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:retained-1');
    expect(JSON.parse(first).completed_at).not.toBe(later.receipt.completed_at);
  });

  it('reports index failure without offering a hidden source search', async () => {
    vi.stubGlobal('fetch', vi.fn(() => respond({ error: { code: 'index_unavailable' } }, 503)));
    render(<BrowseIndex />);
    expect((await screen.findByRole('alert')).textContent).toContain('no source search was started');
    expect(screen.queryByRole('button', { name: /discover|recheck|collect/i })).toBeNull();
  });
});

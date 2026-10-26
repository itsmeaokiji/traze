import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { BrowseIndex, PublicationRecordView } from './ExplorerViews';
import { PublicationTimeline } from './PublicationTimeline';
import type { PublicationSummary } from './types';

const publication: PublicationSummary = { id: 'a'.repeat(64), record_url: '/records/publications/' + 'a'.repeat(64),
  bitcoin_block_hash: 'b'.repeat(64), bitcoin_height: 965931, height_conflict: false,
  txid: 'c'.repeat(64), vout_index: 0, keystone_height: 5243125, keystone_id: 'd'.repeat(64),
  state: 'associated', association_available: true, evidence_scope: 'publication_proof' };
function response(value: unknown) { return Promise.resolve(new Response(JSON.stringify(value))); }
function detail(p = publication) { return { publication: p, retrieved_at: '2026-10-25T00:00:00Z',
  reported_bitcoin_heights: p.height_conflict ? [965930, 965931] : [965931], hemi_records: [],
  data: { raw_transaction_hex: '', bitcoin_header_hex: '', tx_index: 1, vout_index: 0, merkle_siblings_display_hex: [] } }; }

describe('Indexed publication proof scope', () => {
  it('Browse distinguishes proof-only records from fully processed block counts', async () => {
    const fetch = vi.fn((path: string) => response(path === '/api/v1/index'
      ? { index_kind: 'catalogue', counts: { processed_bodies: 0, proof_only_blocks: 1, supported_publications: 1,
          associated_publications: 1, unassociated_publications: 0, unsupported_publications: 0, failed_or_pending_bodies: 0 }, bitcoin_height_bounds: null }
      : { items: [publication], page: { limit: 20, next_cursor: null } }));
    vi.stubGlobal('fetch', fetch);
    render(<BrowseIndex />);
    await screen.findByText('Publication proof only');
    expect(screen.getByText(/without complete block scans/)).toBeTruthy();
    expect(screen.getByText(/fully scanned Bitcoin blocks/)).toBeTruthy();
    expect(screen.getByRole('link', { name: /Inspect publication/ }).getAttribute('href')).toContain(publication.record_url);
    expect(fetch.mock.calls.every(call => call[0].startsWith('/api/v1/'))).toBe(true);
  });

  it('Timeline keeps the proof-only qualification alongside the publication', () => {
    render(<PublicationTimeline records={[publication]} returnTo="/browse?view=timeline" />);
    expect(screen.getByText('Publication proof only')).toBeTruthy();
    expect(screen.getByRole('link', { name: /Inspect publication/ }).getAttribute('href')).toContain(publication.record_url);
  });

  it('publication detail does not turn inclusion evidence into a full-block scan', async () => {
    vi.stubGlobal('fetch', vi.fn(() => response(detail())));
    render(<PublicationRecordView id={publication.id} />);
    await screen.findByText(/Complete scanning of the containing Bitcoin block is not established/);
    expect(screen.getByText(/Current source views have not been rechecked/)).toBeTruthy();
  });

  it('conflicting heights retain metadata without offering an unusable Hemi link', async () => {
    vi.stubGlobal('fetch', vi.fn(() => response(detail({ ...publication, bitcoin_height: null, height_conflict: true }))));
    render(<PublicationRecordView id={publication.id} />);
    await screen.findByText(/until the reported Bitcoin height is unambiguous/);
    expect(screen.queryByRole('link', { name: /Inspect Hemi keystone/ })).toBeNull();
  });
});

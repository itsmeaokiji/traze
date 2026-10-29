import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ordinary from '../../demo_results/mainnet_5243117.json';
import noMatch from '../../demo_results/scoped_no_match.json';
import { EvidenceView } from './EvidenceView';
import { BrowseIndex, PublicationRecordView } from './ExplorerViews';
import { browseLocation, browseReturn, withBrowseReturn } from './browseNavigation';
import App from './App';
import type { Receipt, ReceiptWrapper, WorkspaceState } from './types';

function packet(): ReceiptWrapper {
  const receipt = structuredClone(ordinary) as unknown as Receipt;
  return { id: 'workflow', filename: 'evidence.json', label: 'Saved evidence', receipt,
    record_url: '/records/hemi/' + receipt.query.block_hash,
    verification: { proofs_checked: receipt.proofs.length, current_chain_membership_checked: false } };
}
const props = { fresh: false, busy: false, onRecheck: vi.fn(), onError: vi.fn() };
const respond = (value: unknown) => Promise.resolve(new Response(JSON.stringify(value)));

describe('publication investigation refinements', () => {
  it('keeps the summary and related-height link aligned with the displayed publication without new requests', () => {
    const wrapper = packet(); const before = structuredClone(wrapper);
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    render(<EvidenceView wrapper={wrapper} {...props} />);
    expect(document.getElementById('result-route-summary')!.textContent).toContain('5,243,117');
    expect(document.getElementById('result-assurance')!.textContent).toContain('Earliest observed in this result');
    expect(document.getElementById('related-publications-link')!.getAttribute('href')).toBe(`/browse?keystone_height=${wrapper.receipt.proofs[0].checkpoint.height}`);
    fireEvent.click(document.getElementById('other-proofs-summary')!);
    fireEvent.click(screen.getByRole('button', { name: 'View proof record 2' }));
    expect(document.getElementById('result-assurance')!.textContent).toContain('Not established for this selection');
    expect(document.getElementById('related-publications-link')!.getAttribute('href')).toBe(`/browse?keystone_height=${wrapper.receipt.proofs[1].checkpoint.height}`);
    expect(wrapper).toEqual(before); expect(fetch).not.toHaveBeenCalled();
  });

  it('does not invent a publication height or first-height claim for scoped no-match', () => {
    const wrapper = packet(); wrapper.receipt = structuredClone(noMatch) as unknown as Receipt;
    wrapper.verification.proofs_checked = 0;
    render(<EvidenceView wrapper={wrapper} {...props} />);
    expect(screen.getByRole('heading', { name: 'No match in checked range' })).toBeTruthy();
    expect(document.getElementById('result-route-summary')!.textContent).not.toContain('Bitcoin block');
    expect(document.getElementById('result-assurance')).toBeNull();
    expect(document.getElementById('result-summary')!.textContent).toContain('Publications may exist outside this range');
  });

  it('distinguishes retained-data coverage and original-search coverage from source observations', () => {
    const wrapper = packet(); wrapper.receipt.mode = 'offline_catalogue';
    wrapper.receipt.coverage!.fully_scanned_intervals = [[965925, 965931], [965925, 965931], [965934, 965934]];
    const before = structuredClone(wrapper.receipt.coverage);
    const view = render(<EvidenceView wrapper={wrapper} {...props} />);
    expect(document.getElementById('evidence-assessment')!.textContent).toContain('Bitcoin blocks from retained data');
    expect(screen.getByRole('heading', { name: 'Coverage for this result' })).toBeTruthy();
    expect(document.getElementById('evidence-assessment')!.textContent).toContain('Not rechecked for this result');
    expect(document.getElementById('coverage-summary')!.textContent).toContain('Retained Bitcoin coverage: 965,925–965,931, 965,934.');
    expect(document.getElementById('coverage-summary')!.textContent).not.toContain('965,925–965,931, 965,925–965,931');
    expect(wrapper.receipt.coverage).toEqual(before);
    wrapper.receipt.mode = 'live_metadata_refresh';
    view.rerender(<EvidenceView wrapper={wrapper} {...props} />);
    expect(document.getElementById('evidence-assessment')!.textContent).toContain('Bitcoin blocks from the original search');
    expect(document.getElementById('observation-line')!.textContent).toContain('Ordinary source check:');
    expect(document.getElementById('coverage-summary')!.textContent).toContain('Original search coverage:');
  });

  it('returns from publication and Hemi evidence to the same filters and cursor without sending navigation data to the API', async () => {
    const wrapper = packet(); wrapper.receipt.mode = 'offline_catalogue';
    const p = wrapper.receipt.proofs[0]; const id = 'a'.repeat(64);
    p.publication_id = id; wrapper.selected_publication = id; wrapper.selected_publication_available = true;
    const hemiUrl = wrapper.record_url! + '?publication=' + id;
    wrapper.record_url = hemiUrl;
    const location = '/browse?keystone_height=5243125&bitcoin_height=965931&state=associated&cursor=controlled-cursor';
    window.history.replaceState(null, '', location);
    const publication = { id, record_url: '/records/publications/' + id, bitcoin_height: p.bitcoin_height,
      bitcoin_block_hash: p.bitcoin_block_hash, txid: p.txid, vout_index: p.vout_index,
      keystone_height: p.checkpoint.height, keystone_id: p.checkpoint.keystone_id, state: 'associated', association_available: true };
    const state: WorkspaceState = { serving_mode: 'indexed-only', csrf_token: '', receipts: [], active_job: null,
      resources: { available: true, session_limit_bytes: 100000000 },
      limits: { response_mb_per_lookup: 40, bitcoin_body_attempts: 20, ancestry_links: 256, session_response_mb: 100 } };
    const requests: string[] = [];
    vi.stubGlobal('fetch', vi.fn((path: string) => {
      requests.push(path);
      if (path === '/api/state') return respond(state);
      if (path.startsWith('/api/v1/blocks/')) return respond(wrapper);
      if (path === '/api/v1/index') return respond({ counts: { processed_bodies: 17, supported_publications: 22, associated_publications: 10 }, bitcoin_height_bounds: [941741, 965931] });
      if (path.startsWith('/api/v1/publications?')) return respond({ items: [publication], page: { next_cursor: null } });
      return respond({ publication, hemi_records: [{ height: p.checkpoint.height, hash: wrapper.receipt.query.block_hash, record_url: hemiUrl }], reported_bitcoin_heights: [p.bitcoin_height], data: {}, retrieved_at: '2026-10-21T00:00:00Z' });
    }));
    let view = render(<BrowseIndex />);
    const inspect = await screen.findByRole('link', { name: 'Inspect publication ' + p.txid });
    const publicationUrl = inspect.getAttribute('href')!;
    expect(new URL(publicationUrl, window.location.origin).searchParams.get('return')).toBe(location);
    view.unmount(); window.history.replaceState(null, '', publicationUrl);
    view = render(<PublicationRecordView id={id} />);
    await screen.findByRole('heading', { name: 'Bitcoin publication' });
    expect(screen.getByRole('link', { name: '← Back to filtered publications' }).getAttribute('href')).toBe(location);
    const evidenceUrl = screen.getByRole('link', { name: /Inspect Hemi keystone/ }).getAttribute('href')!;
    view.unmount(); window.history.replaceState(null, '', evidenceUrl);
    view = render(<App />);
    await screen.findByRole('heading', { name: 'Publication found' });
    await waitFor(() => expect(browseReturn()).toBe(location));
    expect(screen.getByRole('link', { name: '← Back to filtered publications' }).getAttribute('href')).toBe(location);
    expect(new URL(window.location.href).searchParams.get('publication')).toBe(id);
    expect(requests.some(path => path.includes('return='))).toBe(false);
    expect(requests).toContain(`/api/v1/blocks/${wrapper.receipt.query.block_hash}?publication=${id}`);
    view.unmount(); window.history.replaceState(null, '', location);
    render(<BrowseIndex />);
    await screen.findByRole('link', { name: 'Inspect publication ' + p.txid });
    expect((screen.getByLabelText('Keystone height') as HTMLInputElement).value).toBe('5243125');
    expect((screen.getByLabelText('Bitcoin height') as HTMLInputElement).value).toBe('965931');
    expect((screen.getByLabelText('Evidence availability') as HTMLSelectElement).value).toBe('associated');
    expect(requests.filter(path => path.includes('/publications?')).every(path => path.includes('cursor=controlled-cursor'))).toBe(true);
  });

  it('ignores external, nested, duplicate and oversized return contexts', () => {
    for (const value of ['https://external.invalid/browse', '//external.invalid/browse', '/browse/other',
      '/browse?return=/browse', '/browse?state=pending&state=associated', '/browse#fragment']) {
      expect(browseLocation(value)).toBeUndefined();
    }
    const record = '/records/publications/' + 'a'.repeat(64);
    expect(withBrowseReturn(record, '/browse?cursor=' + 'x'.repeat(3000))).toBe(record);
    window.history.replaceState(null, '', record + '?return=https%3A%2F%2Fexternal.invalid%2Fbrowse');
    expect(browseReturn()).toBeUndefined();
  });
});

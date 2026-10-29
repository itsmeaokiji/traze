import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ordinaryData from '../../demo_results/mainnet_5243117.json';
import negativeData from '../../demo_results/scoped_no_match.json';
import { EvidenceView } from './EvidenceView';
import type { Receipt, ReceiptWrapper } from './types';


function wrap(data: unknown = ordinaryData): ReceiptWrapper {
  const receipt = structuredClone(data) as Receipt;
  return { id: 'motion-control', label: 'Saved evidence', filename: 'evidence.json', receipt,
    verification: { proofs_checked: receipt.proofs.length, current_chain_membership_checked: false } };
}
const props = { fresh: false, busy: false, onRecheck: vi.fn(), onError: vi.fn() };
const select = (label: string) => fireEvent.click(screen.getByRole('button', { name: `Inspect ${label}` }));
const panel = () => document.getElementById('record-inspector')!;

describe('Motion preserves evidence and immediate interaction', () => {
  it('updates the inspector immediately without remounting the route, its links, or the focus target', () => {
    const wrapper = wrap(); const before = structuredClone(wrapper);
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    render(<EvidenceView wrapper={wrapper} {...props} />);
    const route = document.getElementById('association');
    const edge = document.querySelector('.route-edge[data-evidence="observed"]');
    const inspector = panel(); const content = inspector.querySelector('.inspector-content');
    const liveHeading = document.getElementById('record-inspector-title');
    select('Requested Hemi block');
    expect(panel()).toBe(inspector);
    expect(document.getElementById('record-inspector-title')).toBe(liveHeading);
    expect(panel().querySelector('.inspector-content')).not.toBe(content);
    expect(within(panel()).getByText(wrapper.receipt.query.block_hash!)).toBeTruthy();
    expect(document.getElementById('association')).toBe(route);
    expect(document.querySelector('.route-edge[data-evidence="observed"]')).toBe(edge);
    expect(screen.getByRole('button', { name: 'Inspect Requested Hemi block' }).getAttribute('aria-pressed')).toBe('true');
    expect(wrapper).toEqual(before); expect(fetch).not.toHaveBeenCalled();
  });

  it('retains one current record and exact identifiers during rapid repeated selection', () => {
    const wrapper = wrap(); render(<EvidenceView wrapper={wrapper} {...props} />);
    for (let i = 0; i < 3; i += 1) { select('Requested Hemi block'); select('Covering keystone'); select('Bitcoin publication'); }
    expect(document.querySelectorAll('#record-inspector .inspector-content')).toHaveLength(1);
    expect(document.querySelectorAll('#association button[aria-pressed="true"]')).toHaveLength(1);
    expect(within(panel()).getByText(wrapper.receipt.proofs[0].txid)).toBeTruthy();
    expect(within(panel()).queryByText(wrapper.receipt.query.block_hash!)).toBeNull();
    expect(document.getElementById('record-inspector-title')!.getAttribute('aria-live')).toBe('polite');
  });

  it('changes publications atomically while preserving original receipt references and coverage', () => {
    const wrapper = wrap(); render(<EvidenceView wrapper={wrapper} {...props} />);
    const download = document.getElementById('download-link')!.getAttribute('href');
    const coverage = document.getElementById('coverage-summary')!.textContent;
    fireEvent.click(document.getElementById('other-proofs-summary')!);
    fireEvent.click(screen.getByRole('button', { name: 'View proof record 2' }));
    expect(document.querySelectorAll('#association [data-route-role]')).toHaveLength(4);
    expect(document.getElementById('transaction-link')!.getAttribute('href')).toBe(`https://blockstream.info/tx/${wrapper.receipt.proofs[1].txid}`);
    expect(within(panel()).getByText(wrapper.receipt.proofs[1].txid)).toBeTruthy();
    expect(within(panel()).queryByText(wrapper.receipt.proofs[0].txid)).toBeNull();
    expect(document.getElementById('download-link')!.getAttribute('href')).toBe(download);
    expect(document.getElementById('coverage-summary')!.textContent).toBe(coverage);
  });

  it('retains unresolved semantics and avoids any observed association for no-match evidence', () => {
    render(<EvidenceView wrapper={wrap(negativeData)} {...props} />);
    expect(document.querySelectorAll('.route-edge[data-evidence="observed"]')).toHaveLength(0);
    expect(document.querySelectorAll('.route-edge.is-unresolved')).toHaveLength(4);
    expect(document.getElementById('transaction-link')).toBeNull();
    expect(screen.getByRole('heading', { name: 'No match in checked range' })).toBeTruthy();
  });

  it('keeps source conflicts outside the animated inspector and preserves retained proofs', () => {
    const wrapper = wrap(); wrapper.receipt.status = 'incomplete';
    wrapper.receipt.context!.current_view_confirmed = false;
    wrapper.receipt.selection = { current_proof_index: null, best_observed_proof_index: 0 };
    wrapper.receipt.issues = [{ code: 'source_conflict', message: 'Controlled source mismatch' }];
    render(<EvidenceView wrapper={wrapper} {...props} />);
    select('Covering keystone');
    expect(document.getElementById('issues-section')!.closest('.inspector-content')).toBeNull();
    expect(document.getElementById('result-heading')!.closest('.inspector-content')).toBeNull();
    expect(document.querySelectorAll('.route-edge[data-evidence="observed"]')).toHaveLength(4);
    expect(within(document.getElementById('verification-list')!).getAllByText('Passed')).toHaveLength(4);
    expect(document.getElementById('issues-list')!.textContent).toContain('conflicting data');
  });

});

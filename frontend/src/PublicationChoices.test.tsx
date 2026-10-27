import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ordinaryData from '../../demo_results/mainnet_5243117.json';
import marchData from '../../demo_results/mainnet_4028425_hash.json';
import negativeData from '../../demo_results/scoped_no_match.json';
import { EvidenceView } from './EvidenceView';
import type { Receipt, ReceiptWrapper } from './types';

function wrap(data: unknown = ordinaryData, id = 'ordinary'): ReceiptWrapper {
  const receipt = structuredClone(data) as Receipt;
  return { id, label: 'Saved evidence', filename: 'evidence.json', receipt, verification: { proofs_checked: receipt.proofs.length, current_chain_membership_checked: false } };
}
const props = { fresh: false, busy: false, onRecheck: vi.fn(), onError: vi.fn() };
function choices() { fireEvent.click(document.getElementById('other-proofs-summary')!); }
function choose(record: number) { fireEvent.click(screen.getByRole('button', { name: `View proof record ${record}` })); }
const panel = () => within(document.getElementById('record-inspector')!);

describe('Retained publication view selection', () => {
  it('updates a real covering-to-later route, inspector and copy reference without fetches or receipt changes', () => {
    const wrapper = wrap();
    const original = structuredClone(wrapper.receipt);
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    render(<EvidenceView wrapper={wrapper} {...props} />);
    const download = document.getElementById('download-link')!.getAttribute('href');
    expect(document.querySelectorAll('#association [data-route-role]')).toHaveLength(3);
    choices();
    choose(2);
    expect(document.querySelectorAll('#association [data-route-role]')).toHaveLength(4);
    expect(panel().getByText(wrapper.receipt.proofs[1].txid)).toBeTruthy();
    expect(document.getElementById('transaction-link')!.textContent).toBe(wrapper.receipt.proofs[1].txid);
    expect(document.getElementById('publication-view-label')!.textContent).toContain('Viewing another publication');
    expect(screen.getByRole('button', { name: 'Inspect Bitcoin publication' }).getAttribute('aria-pressed')).toBe('false');
    fireEvent.click(screen.getByRole('button', { name: 'Inspect Bitcoin publication' }));
    expect(screen.getByRole('button', { name: 'Inspect Bitcoin publication' }).getAttribute('aria-pressed')).toBe('true');
    expect(document.getElementById('download-link')!.getAttribute('href')).toBe(download);
    expect(wrapper.receipt).toEqual(original);
    expect(fetch).not.toHaveBeenCalled();
  });

  it('returns to the original default, including its route, and keeps keyboard focus usable', () => {
    const wrapper = wrap();
    render(<EvidenceView wrapper={wrapper} {...props} />);
    choices(); choose(2);
    fireEvent.click(screen.getByRole('button', { name: 'Back to default selection' }));
    expect(document.querySelectorAll('#association [data-route-role]')).toHaveLength(3);
    expect(panel().getByText(wrapper.receipt.proofs[0].txid)).toBeTruthy();
    expect(document.getElementById('publication-view-label')!.textContent).toContain('Default selection');
    expect(screen.queryByRole('button', { name: 'Back to default selection' })).toBeNull();
    expect(document.activeElement).toBe(document.getElementById('other-proofs-summary'));
  });

  it('honors an earlier Bitcoin-height default rather than switching to a direct or closer Hemi publication', () => {
    // Controlled display values only, not newly validated chain evidence.
    const wrapper = wrap(marchData);
    wrapper.receipt.proofs = wrapper.receipt.proofs.slice(0, 2);
    wrapper.receipt.proofs[1].bitcoin_height += 1;
    wrapper.receipt.proofs[1].checkpoint = { ...wrapper.receipt.query.covering_checkpoint! };
    wrapper.receipt.selection = { current_proof_index: 0, best_observed_proof_index: 0 };
    render(<EvidenceView wrapper={wrapper} {...props} />);
    expect(panel().getByText(wrapper.receipt.proofs[0].txid)).toBeTruthy();
    expect(document.querySelector('[data-route-role="later"]')).toBeTruthy();
    choices(); choose(2);
    expect(document.querySelector('[data-route-role="later"]')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Back to default selection' }));
    expect(document.querySelector('[data-route-role="later"]')).toBeTruthy();
    expect(panel().getByText(wrapper.receipt.proofs[0].txid)).toBeTruthy();
  });

  it('counts proof records and avoids claiming another transaction when two records use the same transaction', () => {
    const wrapper = wrap();
    wrapper.receipt.proofs[1].txid = wrapper.receipt.proofs[0].txid;
    render(<EvidenceView wrapper={wrapper} {...props} />);
    expect(document.getElementById('other-proofs-summary')!.textContent).toContain('4 proof records');
    choices(); choose(2);
    expect(document.getElementById('publication-view-label')!.textContent).toContain('Viewing another proof record');
    expect(document.getElementById('other-proofs')!.textContent).toContain('do not imply distinct transactions');
  });

  it('retains warnings, source time and original coverage while browsing incomplete evidence', () => {
    const wrapper = wrap();
    wrapper.receipt.status = 'incomplete';
    wrapper.receipt.context = { ...wrapper.receipt.context, current_view_confirmed: false };
    wrapper.receipt.selection = { current_proof_index: null, best_observed_proof_index: 0 };
    wrapper.receipt.issues = [{ code: 'source_conflict', message: 'Controlled conflict' }];
    render(<EvidenceView wrapper={wrapper} {...props} />);
    const time = document.getElementById('observation-line')!.textContent;
    const coverage = document.getElementById('coverage-summary')!.textContent;
    choices(); choose(3);
    expect(panel().getByText(wrapper.receipt.proofs[2].txid)).toBeTruthy();
    expect(panel().getByText('Source views conflicted at the recorded check')).toBeTruthy();
    expect(document.getElementById('issues-list')!.textContent).toContain('conflicting data');
    expect(document.getElementById('observation-line')!.textContent).toBe(time);
    expect(document.getElementById('coverage-summary')!.textContent).toBe(coverage);
  });

  it('resets a manual publication view when a different receipt is opened', () => {
    const view = render(<EvidenceView wrapper={wrap()} {...props} />);
    choices(); choose(4);
    view.rerender(<EvidenceView wrapper={wrap(marchData, 'march')} {...props} />);
    expect(panel().getByText(marchData.proofs[0].txid)).toBeTruthy();
    expect(document.getElementById('publication-view-label')!.textContent).toContain('Default selection');
  });

  it('does not invent a chooser or publication for no-match evidence', () => {
    render(<EvidenceView wrapper={wrap(negativeData)} {...props} />);
    expect(document.getElementById('other-proofs')).toBeNull();
    expect(document.getElementById('publication-view')).toBeNull();
    expect(document.getElementById('transaction-link')).toBeNull();
  });

  it('does not carry delayed copy feedback over to a different transaction', async () => {
    let finishCopy!: () => void;
    const writeText = vi.fn(() => new Promise<void>(resolve => { finishCopy = resolve; }));
    vi.stubGlobal('navigator', Object.create(navigator, { clipboard: { value: { writeText }, configurable: true } }));
    const wrapper = wrap();
    render(<EvidenceView wrapper={wrapper} {...props} />);
    fireEvent.click(screen.getByRole('button', { name: 'Copy transaction ID' }));
    choices(); choose(2);
    await act(async () => finishCopy());
    expect(writeText).toHaveBeenCalledWith(wrapper.receipt.proofs[0].txid);
    expect(screen.queryByRole('button', { name: 'Copied' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Copy transaction ID' })).toBeTruthy();
  });
});

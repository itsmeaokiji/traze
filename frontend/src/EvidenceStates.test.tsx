import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ordinaryData from '../../demo_results/mainnet_5243117.json';
import negativeData from '../../demo_results/scoped_no_match.json';
import { EvidenceView } from './EvidenceView';
import { OperationProgress } from './OperationProgress';
import type { Job, Receipt, ReceiptWrapper } from './types';

function wrap(data: unknown = ordinaryData): ReceiptWrapper {
  const receipt = structuredClone(data) as Receipt;
  receipt.validation = { scope: 'controlled_provider_responses', live_public_reads: false };
  return { id: 'state-control', filename: 'evidence.json', label: 'Controlled state', receipt,
    verification: { proofs_checked: receipt.proofs.length, current_chain_membership_checked: false } };
}
const props = { fresh: false, busy: false, onRecheck: vi.fn(), onError: vi.fn() };
function incomplete(wrapper: ReceiptWrapper) {
  wrapper.receipt.status = 'incomplete';
  wrapper.receipt.context = { ...wrapper.receipt.context, current_view_confirmed: false };
  wrapper.receipt.selection = { current_proof_index: null, best_observed_proof_index: wrapper.receipt.proofs.length ? 0 : null };
}
const proofChecks = () => within(document.getElementById('verification-list')!);
const sourceChecks = () => within(document.getElementById('source-check-list')!);
function job(operation: 'lookup' | 'refresh', message: string): Job {
  return { id: 'controlled', operation, block_number: 5243117, started_at: '2026-10-07T06:00:00Z',
    state: 'running', receipt_id: null, events: [{ at: '2026-10-07T06:00:01Z', message }] };
}

describe('Evidence state qualifications', () => {
  it('separates proof success, recorded source confirmation, and actual block coverage without source reads', () => {
    const wrapper = wrap();
    const before = structuredClone(wrapper);
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    render(<EvidenceView wrapper={wrapper} {...props} />);
    expect(document.getElementById('result')!.classList.contains('result-pass')).toBe(true);
    expect(proofChecks().getAllByText('Passed')).toHaveLength(4);
    expect(sourceChecks().getByText('Source views matched at the recorded check')).toBeTruthy();
    expect(document.getElementById('evidence-assessment')!.textContent).toContain('4 proof records checked');
    expect(document.getElementById('evidence-assessment')!.textContent).toContain('7 Bitcoin blocks scanned');
    expect(document.getElementById('result-origin')!.textContent).toBe('SAVED RESULT · CONTROLLED TEST DATA');
    expect(wrapper).toEqual(before); expect(fetch).not.toHaveBeenCalled();
    expect(document.querySelector('.result-title svg')!.getAttribute('aria-hidden')).toBe('true');
  });

  it('treats a scoped no-match as neutral and does not invent a publication', () => {
    render(<EvidenceView wrapper={wrap(negativeData)} {...props} />);
    expect(document.getElementById('result')!.classList.contains('result-neutral')).toBe(true);
    expect(screen.getByRole('heading', { name: 'No match in checked range' })).toBeTruthy();
    expect(proofChecks().getByText('None to check')).toBeTruthy();
    expect(document.getElementById('transaction-link')).toBeNull();
    expect(document.getElementById('other-proofs')).toBeNull();
    expect(within(document.getElementById('result-summary')!).getByText(/Publications may exist outside this range/)).toBeTruthy();
  });

  it('keeps incomplete evidence without a proof distinct from publication absence', () => {
    const wrapper = wrap(negativeData); incomplete(wrapper);
    render(<EvidenceView wrapper={wrapper} {...props} />);
    expect(screen.getByRole('heading', { name: 'Search incomplete' })).toBeTruthy();
    expect(document.getElementById('result-summary')!.textContent).toContain('does not establish that a publication is absent');
    expect(sourceChecks().getByText('Incomplete at the recorded check')).toBeTruthy();
    expect(document.querySelector('[data-route-role="publication"]')!.classList.contains('is-unresolved')).toBe(true);
    expect(document.getElementById('transaction-link')).toBeNull();
  });

  it('retains passing proof checks after a search timeout and keeps raw deadline diagnostics collapsed', () => {
    const wrapper = wrap(); incomplete(wrapper);
    wrapper.receipt.issues = [{ code: 'time_budget_exhausted', message: 'Internal timeout detail' }];
    render(<EvidenceView wrapper={wrapper} {...props} />);
    expect(screen.getByRole('heading', { name: 'Evidence retained · search timed out' })).toBeTruthy();
    expect(proofChecks().getAllByText('Passed')).toHaveLength(4);
    expect(document.getElementById('issues-list')!.textContent).toContain('Search timed out. Partial results are available.');
    expect(document.getElementById('issues-list')!.textContent).not.toContain('Internal timeout detail');
    expect(document.getElementById('transaction-link')!.textContent).toBe(wrapper.receipt.proofs[0].txid);
  });

  it('describes a timed-out source recheck as a recheck while preserving original coverage and manual-view qualifications', () => {
    const wrapper = wrap(); incomplete(wrapper);
    wrapper.receipt.mode = 'live_metadata_refresh';
    wrapper.receipt.termination = { reason: 'time_budget_exhausted' };
    const before = structuredClone(wrapper);
    render(<EvidenceView wrapper={wrapper} {...props} fresh />);
    expect(screen.getByRole('heading', { name: 'Evidence retained · source recheck timed out' })).toBeTruthy();
    expect(document.getElementById('result-origin')!.textContent).toBe('SOURCE RECHECK · CONTROLLED TEST DATA');
    expect(document.getElementById('issues-list')!.textContent).toContain('Source recheck timed out');
    expect(document.getElementById('coverage-summary')!.textContent).toContain('Coverage from the original search');
    fireEvent.click(document.getElementById('other-proofs-summary')!);
    fireEvent.click(screen.getByRole('button', { name: 'View proof record 2' }));
    expect(document.getElementById('publication-view-label')!.textContent).toContain('Viewing another publication');
    expect(document.getElementById('result-summary')!.textContent).toContain('source recheck did not finish');
    expect(proofChecks().getAllByText('Passed')).toHaveLength(4);
    expect(wrapper).toEqual(before);
  });

  it('does not turn the original search timeout into a timeout for a completed source recheck', () => {
    const wrapper = wrap();
    wrapper.receipt.mode = 'live_metadata_refresh';
    wrapper.receipt.coverage!.stop_reason = 'time_budget_exhausted';
    render(<EvidenceView wrapper={wrapper} {...props} fresh />);
    expect(screen.getByRole('heading', { name: 'Publication found' })).toBeTruthy();
    expect(document.getElementById('stop-reason')!.textContent).toBe('Original search: Search timed out.');
    expect(document.getElementById('issues-list')).toBeNull();
    expect(document.getElementById('result-summary')!.textContent).toContain('Original search coverage is preserved');
  });

  it('describes an incomplete recheck without calling the original search unfinished', () => {
    const wrapper = wrap(); incomplete(wrapper);
    wrapper.receipt.mode = 'live_metadata_refresh';
    render(<EvidenceView wrapper={wrapper} {...props} fresh />);
    expect(screen.getByRole('heading', { name: 'Evidence retained · source recheck incomplete' })).toBeTruthy();
    expect(document.getElementById('result-summary')!.textContent).toContain('original search coverage are preserved');
    expect(document.getElementById('result-heading')!.textContent).not.toContain('search incomplete');
  });

  it('distinguishes a source disagreement from invalid proof mathematics or an established reorg', () => {
    const wrapper = wrap(); incomplete(wrapper);
    wrapper.receipt.context!.bitcoin_secondary = [{ height: 965931, same: false }];
    render(<EvidenceView wrapper={wrapper} {...props} />);
    expect(screen.getByRole('heading', { name: 'Evidence retained · source conflict' })).toBeTruthy();
    expect(proofChecks().getAllByText('Passed')).toHaveLength(4);
    expect(sourceChecks().getByText('Source views conflicted at the recorded check')).toBeTruthy();
    expect(document.getElementById('result-summary')!.textContent).toContain('does not establish a chain reorganization');
    expect(document.getElementById('issues-section')!.closest('details')).toBeNull();
  });

  it('keeps missing source time explicit and labels a fresh search separately from saved evidence', () => {
    const wrapper = wrap(); incomplete(wrapper);
    wrapper.receipt.context!.checked_at = undefined;
    render(<EvidenceView wrapper={wrapper} {...props} fresh />);
    expect(document.getElementById('observation-line')!.textContent).toContain('Ordinary source check: not recorded');
    expect(document.getElementById('result-origin')!.textContent).toBe('SEARCH RESULT · CONTROLLED TEST DATA');
    expect(document.getElementById('result')!.classList.contains('result-warning')).toBe(true);
  });
});

describe('Operation progress meaning', () => {
  it('shows observed scanned height and retained proof count without inventing completion percentages', () => {
    render(<OperationProgress job={job('lookup', 'Bitcoin 965931: 2111 txs, 7 HEMI candidates, 4 matches (cache)')} />);
    expect(screen.getByRole('status').textContent).toBe('Bitcoin block 965,931 scanned · 4 matching proof records retained.');
    expect(document.getElementById('job-progress')!.textContent).not.toContain('%');
    expect(document.getElementById('progress-time')!.textContent).toContain('elapsed');
  });

  it('does not imply additional block scanning during a source recheck', () => {
    render(<OperationProgress job={job('refresh', 'Bitcoin 965931: 2111 txs, 7 HEMI candidates, 4 matches')} />);
    expect(screen.getByRole('heading', { name: 'Rechecking sources…' })).toBeTruthy();
    expect(screen.getByRole('status').textContent).toContain('Checking recorded Hemi and Bitcoin blocks');
    expect(document.getElementById('job-progress')!.textContent).toContain('does not scan additional Bitcoin blocks');
    expect(screen.getByRole('status').textContent).not.toContain('scanned');
  });

  it('reports scan gaps without showing raw exception text in progress', () => {
    render(<OperationProgress job={job('lookup', 'Gap at Bitcoin 965925: private internal diagnostic')} />);
    expect(screen.getByRole('status').textContent).toContain('Bitcoin block 965,925 could not be checked');
    expect(document.getElementById('job-progress')!.textContent).not.toContain('private internal diagnostic');
  });
});

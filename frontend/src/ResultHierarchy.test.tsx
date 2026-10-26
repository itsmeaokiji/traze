import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import positive from '../../demo_results/mainnet_5243117.json';
import negative from '../../demo_results/scoped_no_match.json';
import finalityReceipt from '../../tests/fixtures/finality/recorded_5200117.json';
import finalityAssessment from '../../tests/fixtures/finality/website_assessment_5200117.json';
import { EvidenceView } from './EvidenceView';
import { receiptIssues } from './receiptIssues';
import { evidenceSummary } from './evidenceSummary';
import type { Receipt, ReceiptWrapper, WorkspaceState } from './types';

function wrapper(data: unknown = positive): ReceiptWrapper {
  const receipt = structuredClone(data) as Receipt;
  receipt.validation = { scope: 'controlled_provider_responses', live_public_reads: false };
  return { id: 'hierarchy', filename: 'evidence.json', label: 'Controlled hierarchy case', receipt,
    verification: { proofs_checked: receipt.proofs.length, current_chain_membership_checked: false } };
}
const workspace: WorkspaceState = { serving_mode: 'discovery', csrf_token: 'controlled-token', active_job: null,
  receipts: [], resources: { available: true, source_operations_available: true, session_limit_bytes: 100000000,
    usable_response_bytes: 33399983 }, limits: { response_mb_per_lookup: 40, bitcoin_body_attempts: 20, ancestry_links: 256, session_response_mb: 100 } };
const props = { fresh: false, busy: false, onRecheck: vi.fn(), onError: vi.fn(), workspace };
const before = (a: string, b: string) => Boolean(document.getElementById(a)!.compareDocumentPosition(document.getElementById(b)!) & Node.DOCUMENT_POSITION_FOLLOWING);

describe('Result hierarchy and material warning preservation', () => {
  it('puts the route, ordinary checks and coverage before source actions and advanced details', () => {
    const value = wrapper(); const original = structuredClone(value); const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    render(<EvidenceView wrapper={value} {...props} />);
    expect(before('transaction-line', 'evidence-assessment')).toBe(true);
    expect(before('evidence-assessment', 'result-search-actions')).toBe(true);
    expect(before('coverage-summary', 'first-publication')).toBe(true);
    expect(before('result-search-actions', 'first-publication')).toBe(true);
    expect((document.getElementById('first-publication-details') as HTMLDetailsElement).open).toBe(false);
    expect(document.getElementById('result-summary')!.textContent).toContain('Earlier publications may exist');
    expect(value).toEqual(original); expect(fetch).not.toHaveBeenCalled();
  });

  it('provides one new-lookup action for a retained result that also needs recovery', () => {
    const value = wrapper(); value.receipt.mode = 'offline_catalogue';
    value.continuation = { available: false, reason: 'continuation_unavailable' };
    const lookup = vi.fn(); const recheck = vi.fn();
    render(<EvidenceView wrapper={value} {...props} onNewLookup={lookup} onRecheck={recheck} />);
    expect(screen.getAllByRole('button', { name: 'Run new lookup' })).toHaveLength(1);
    expect(document.getElementById('search-recovery')).toBeNull();
    const region = within(document.getElementById('result-search-actions')!);
    fireEvent.click(region.getByRole('button', { name: 'Run new lookup' }));
    expect(lookup).toHaveBeenCalledTimes(1); expect(recheck).not.toHaveBeenCalled();
    fireEvent.click(region.getByRole('button', { name: /Recheck sources/ }));
    expect(lookup).toHaveBeenCalledTimes(1); expect(recheck).toHaveBeenCalledTimes(1);
  });

  it('keeps continuation separate and explicit for a no-match result', () => {
    const value = wrapper(negative); const start = vi.fn();
    value.continuation = { available: true, reason: 'next_segment', bitcoin_range: [965547, 965566], receipt_sha256: 'a'.repeat(64) };
    render(<EvidenceView wrapper={value} {...props} onContinue={start} onNewLookup={vi.fn()} />);
    expect(screen.getByRole('heading', { name: 'No match in checked range' })).toBeTruthy();
    expect(before('coverage-summary', 'association')).toBe(true);
    expect(before('result-search-actions', 'association')).toBe(true);
    expect(screen.queryByRole('button', { name: 'Run new lookup' })).toBeNull();
    expect(start).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Continue search' }));
    expect(start).toHaveBeenCalledTimes(1);
    expect(document.getElementById('first-publication')).toBeNull();
  });

  it('leaves an exhausted installation blocked without hiding retained evidence', () => {
    const value = wrapper(); value.receipt.mode = 'offline_catalogue';
    const lookup = vi.fn();
    render(<EvidenceView wrapper={value} {...props} workspace={{ ...workspace,
      resources: { ...workspace.resources, source_operations_available: false, blocked_reason: 'cumulative_response_bytes' } }} onNewLookup={lookup} />);
    const button = screen.getByRole('button', { name: 'Run new lookup' }) as HTMLButtonElement;
    expect(button.disabled).toBe(true); fireEvent.click(button); expect(lookup).not.toHaveBeenCalled();
    expect(document.getElementById('transaction-link')).toBeTruthy();
  });

  it('consolidates source-unavailable warnings while preserving the distinct details and receipt', () => {
    const value = wrapper(); value.receipt.context!.current_view_confirmed = false;
    value.receipt.issues = [{ code: 'source_unavailable' }, { code: 'network_recheck_unavailable' }, { code: 'tip_recheck_unavailable' }];
    const original = structuredClone(value); const summary = evidenceSummary(value, 0, false);
    render(<EvidenceView wrapper={value} {...props} />);
    expect(document.getElementById('issues-list')!.querySelectorAll('li')).toHaveLength(1);
    expect(document.getElementById('issues-list')!.textContent).toContain('Current chain membership is not confirmed');
    expect((document.getElementById('warning-details') as HTMLDetailsElement).open).toBe(false);
    expect(document.getElementById('warning-details')!.textContent).toContain('Bitcoin chain tip');
    expect(receiptIssues(value.receipt).raw).toEqual(original.receipt.issues);
    expect(evidenceSummary(value, 0, false)).toBe(summary); expect(value).toEqual(original);
  });

  it('keeps source conflicts, invalid proofs and wrong-network warnings outside disclosures', () => {
    const value = wrapper(); value.receipt.context!.current_view_confirmed = true;
    value.receipt.context!.bitcoin_secondary = [{ height: 965931, same: false }];
    value.receipt.issues = [{ code: 'source_conflict' }, { code: 'invalid_proof' }, { code: 'wrong_network' }];
    render(<EvidenceView wrapper={value} {...props} />);
    const warnings = document.getElementById('issues-list')!;
    expect(warnings.closest('details')).toBeNull();
    expect(warnings.textContent).toContain('Sources returned conflicting data');
    expect(warnings.textContent).toContain('did not pass its proof checks');
    expect(warnings.textContent).toContain('valid Hemi Mainnet');
    expect(warnings.querySelectorAll('li')).toHaveLength(3);
    expect(before('issues-section', 'transaction-line')).toBe(true);
    expect(document.getElementById('evidence-assessment')!.textContent).toContain('Source views conflicted');
  });

  it('keeps coverage problems in the visible core summary and detailed receipt', () => {
    const value = wrapper(); value.receipt.coverage!.gaps = [{ code: 'body_missing', message: 'Controlled missing body' }];
    value.receipt.issues = [{ code: 'no_processed_scope' }];
    render(<EvidenceView wrapper={value} {...props} />);
    expect(document.getElementById('coverage-issues')!.closest('details')).toBeNull();
    expect(document.getElementById('coverage-issues')!.textContent).toContain('1 scan gap');
    expect(document.getElementById('issues-section')).toBeNull();
    expect(receiptIssues(value.receipt).warnings.some(message => message.includes('1 scan gap'))).toBe(true);
  });

  it('does not collapse an invalid stronger-assessment warning', () => {
    const value = wrapper(); const p = value.receipt.proofs[0];
    value.first_publication = { status: 'invalid', query: { number: value.receipt.query.block_number!, hash: value.receipt.query.block_hash! },
      publication: { bitcoin_height: p.bitcoin_height, bitcoin_hash: p.bitcoin_block_hash, txid: p.txid,
        keystone_number: p.checkpoint.height, tx_index: p.tx_index!, vout: p.vout_index! },
      packet_available: false, assumptions: [], issues: [{ code: 'invalid_history', message: 'Controlled invalid history witness' }],
      globally_first_proven: false, policy_id: 'controlled-policy', accepted_by_operator: false };
    render(<EvidenceView wrapper={value} {...props} />);
    expect(screen.getByRole('heading', { name: 'First-publication evidence invalid' })).toBeTruthy();
    expect(screen.getByText('Controlled invalid history witness').closest('details')).toBeNull();
    expect(screen.getByRole('link', { name: 'Stronger evidence invalid' })).toBeTruthy();
  });

  it('keeps finality limitations visible while calculation values and export are expandable', () => {
    const value = wrapper(finalityReceipt); value.finality = structuredClone(finalityAssessment) as ReceiptWrapper['finality'];
    value.receipt.selection = { current_proof_index: 0, best_observed_proof_index: 0 };
    value.finality!.source_conflict = true;
    render(<EvidenceView wrapper={value} {...props} />);
    const disclosure = document.getElementById('reference-finality-disclosure') as HTMLDetailsElement;
    expect(disclosure.open).toBe(false);
    expect(document.getElementById('finality-qualification')!.closest('details')).toBeNull();
    expect(document.getElementById('finality-source-warning')!.closest('details')).toBeNull();
    expect(document.getElementById('finality-source-warning')!.textContent).toContain('conflicting');
    expect(document.getElementById('download-calculation')!.closest('details')).toBe(disclosure);
  });
});

import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import stored from '../../demo_results/main_catalogue_5243119.json';
import ordinary from '../../demo_results/mainnet_5243117.json';
import negative from '../../demo_results/scoped_no_match.json';
import modelReceipt from '../../tests/fixtures/finality/recorded_5200117.json';
import modelAssessment from '../../tests/fixtures/finality/website_assessment_5200117.json';
import { EvidenceView } from './EvidenceView';
import { evidenceSummary } from './evidenceSummary';
import { resultState, sourceDepthText } from './evidenceState';
import { firstPublicationIssueText } from './firstPublicationState';
import { formatDate, shortHash } from './format';
import { PublicationRoute } from './PublicationRoute';
import type { FinalityAssessment, Receipt, ReceiptWrapper } from './types';

function wrapper(data: unknown = stored): ReceiptWrapper {
  const receipt = structuredClone(data) as Receipt;
  receipt.validation = { scope: 'controlled_provider_responses', live_public_reads: false };
  return { id: 'clarity', filename: 'evidence.json', label: 'Controlled presentation case', receipt,
    verification: { proofs_checked: receipt.proofs.length, current_chain_membership_checked: false } };
}
function unavailable(value: ReceiptWrapper) {
  const p = value.receipt.proofs[0];
  value.first_publication = { status: 'not_available', query: { number: value.receipt.query.block_number!, hash: value.receipt.query.block_hash! },
    publication: { bitcoin_height: p.bitcoin_height, bitcoin_hash: p.bitcoin_block_hash, txid: p.txid,
      keystone_number: p.checkpoint.height, tx_index: p.tx_index!, vout: p.vout_index! },
    packet_available: false, assumptions: [], issues: [{ code: 'evidence_not_prepared', message: 'The stronger evidence package has not been prepared for this publication.' }],
    globally_first_proven: false, policy_id: 'hemi-full-state-first-height-assumptions-v1', accepted_by_operator: false };
}
const props = { fresh: false, busy: false, onRecheck: vi.fn(), onError: vi.fn(), sourceOperationsEnabled: false };

describe('Publication outcome, observation context and additional assessments', () => {
  it('separates a stored positive from an unavailable additional assessment without promoting current membership', () => {
    const value = wrapper(); unavailable(value);
    const before = JSON.stringify(value); const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    render(<EvidenceView wrapper={value} {...props} />);
    expect(screen.getByRole('heading', { name: 'Publication found' })).toBeTruthy();
    expect(document.getElementById('result-origin')!.textContent).toContain('Stored evidence');
    expect(document.getElementById('result')!.className).toContain('result-neutral');
    expect(document.getElementById('result-assurance')!.textContent).toBe('Publication order: Earliest observed in this result');
    expect(screen.getByRole('heading', { name: 'First-publication assessment not available' })).toBeTruthy();
    const explanation = screen.getByText('Publication evidence is available. The additional evidence required for this first-publication assessment is not available for this result.');
    expect(explanation.closest('details')).toBeNull();
    expect(document.getElementById('evidence-assessment')!.textContent).toContain('Not rechecked for this result');
    expect(screen.getByRole('link', { name: /Download evidence JSON/ }).getAttribute('href')).toBe('/api/receipts/clarity/download');
    expect(JSON.stringify(value)).toBe(before); expect(fetch).not.toHaveBeenCalled();
  });

  it('normalizes known legacy diagnostics only in the display and retains an unknown diagnostic', () => {
    const value = wrapper(); unavailable(value);
    const before = JSON.stringify(value); value.first_publication!.issues.push({ code: 'unknown_case', message: 'Retain this precise diagnostic' });
    const withIssue = JSON.stringify(value);
    render(<EvidenceView wrapper={value} {...props} />);
    fireEvent.click(screen.getByText('Assessment details'));
    expect(screen.getByText('The first-publication verification package has not been prepared for this publication.')).toBeTruthy();
    expect(screen.getByText('Retain this precise diagnostic')).toBeTruthy();
    expect(document.getElementById('first-publication')!.textContent).not.toContain('stronger');
    expect(JSON.stringify(value)).toBe(withIssue);
    expect(before).toContain('The stronger evidence package');
    expect(firstPublicationIssueText({ code: 'evidence_not_prepared' })).toContain('first-publication verification package');
    expect(firstPublicationIssueText({ code: 'unknown_code' })).toBe('unknown_code');
  });

  it.each(['gap', 'unresolved', 'unsupported'])('keeps %s coverage visibly incomplete for stored positive evidence', kind => {
    const value = wrapper();
    if (kind === 'gap') value.receipt.coverage!.gaps = [{ code: 'missing_body' }];
    if (kind === 'unresolved') value.receipt.coverage!.unresolved_candidates = [{ code: 'missing_association' }];
    if (kind === 'unsupported') value.receipt.coverage!.unsupported_payloads = 1;
    render(<EvidenceView wrapper={value} {...props} />);
    expect(screen.getByRole('heading', { name: 'Publication found · coverage incomplete' })).toBeTruthy();
    expect(document.getElementById('result')!.className).toContain('result-warning');
    expect(document.getElementById('coverage-issues')!.closest('details')).toBeNull();
  });

  it('preserves timeout, conflicting-source, incomplete and scoped no-match outcomes', () => {
    const conflict = wrapper(); conflict.receipt.issues = [{ code: 'source_conflict' }];
    expect(resultState(conflict.receipt, true).title).toBe('Evidence retained · source conflict');
    const timeout = wrapper(); timeout.receipt.resources = { deadline_expired: true };
    expect(resultState(timeout.receipt, true).title).toBe('Evidence retained · search timed out');
    const unfinished = wrapper(ordinary); unfinished.receipt.status = 'incomplete';
    expect(resultState(unfinished.receipt, true).title).toBe('Evidence found · search incomplete');
    const noMatch = wrapper(negative);
    expect(resultState(noMatch.receipt, false).title).toBe('No match in checked range');
    expect(evidenceSummary(noMatch, undefined, false)).not.toContain('Publication evidence is available.');
    expect(evidenceSummary(noMatch, undefined, false)).toContain('Publications may exist outside this range.');
  });

  it.each(['historical', 'conflict', 'missing', 'null', 'confirmed'])('keeps %s source depth separate from recorded-tip arithmetic', kind => {
    const value = wrapper(ordinary); const p = value.receipt.proofs[0];
    value.receipt.context!.current_view_confirmed = kind !== 'historical';
    p.current_bitcoin_depth_in_provider_view = kind === 'null' ? null : kind === 'missing' ? undefined : 17;
    if (kind === 'conflict') value.receipt.context!.bitcoin_secondary = [{ same: false }];
    expect(sourceDepthText(value.receipt, p)).toBe(kind === 'confirmed' ? '17 blocks' : 'Not established for this result');
  });

  it('shows historical model depth with its tip/time while the ordinary source depth remains unestablished', () => {
    const value = wrapper(modelReceipt); value.receipt.mode = 'offline_catalogue';
    value.receipt.status = 'found';
    value.receipt.selection = { current_proof_index: 0, best_observed_proof_index: 0 };
    value.receipt.context!.current_view_confirmed = false;
    value.finality = structuredClone(modelAssessment) as unknown as FinalityAssessment;
    const before = JSON.stringify(value); const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    render(<EvidenceView wrapper={value} {...props} />);
    fireEvent.click(screen.getByText('Calculation details and export'));
    expect(screen.getByText('Bitcoin depth from recorded tip')).toBeTruthy();
    expect(document.getElementById('finality-depth')!.textContent).toBe('7,245');
    expect(document.getElementById('finality-depth-context')!.textContent).toContain('not a new source check or a Hemi finality value');
    expect(document.getElementById('finality-observation')!.textContent).toContain('972,289');
    const observation = value.finality.input_binding?.retained_tip_observation?.observed_at || value.finality.calculation!.source_context.inputs.observed_at;
    expect(document.getElementById('finality-observation')!.textContent).toContain(formatDate(observation));
    const row = screen.getByText('Bitcoin depth from source checks').closest('.verification-row')!;
    expect(row.textContent).toContain('Not established for this result');
    const text = evidenceSummary(value, 0, false);
    expect(text).toContain('Bitcoin depth from source checks: Not established for this result');
    expect(text).toContain('Bitcoin depth from recorded tip: 7,245');
    expect(text).toContain('Recorded source observation:');
    expect(JSON.stringify(value)).toBe(before); expect(fetch).not.toHaveBeenCalled();
  });

  it.each([0, undefined])('distinguishes transaction, output %s and containing block without inventing an output', output => {
    const value = wrapper(); const p = value.receipt.proofs[0]; p.vout_index = output;
    const before = JSON.stringify(value);
    render(<PublicationRoute receipt={value.receipt} proof={p} />);
    const route = within(screen.getByRole('list', { name: 'Hemi to Bitcoin association' }));
    expect(route.getByRole('link', { name: shortHash(p.txid) }).getAttribute('href')).toBe(`https://blockstream.info/tx/${p.txid}`);
    expect(route.getByRole('link', { name: `block ${p.bitcoin_height.toLocaleString('en-US')}` }).getAttribute('href')).toBe(`https://blockstream.info/block/${p.bitcoin_block_hash}`);
    expect(route.getByText(output === 0 ? 'Transaction output 0' : 'Transaction output not recorded')).toBeTruthy();
    if (output === 0) expect(route.getByText('(zero-based index).')).toBeTruthy();
    expect(JSON.stringify(value)).toBe(before);
  });

  it('reduces ordinary repeated freshness text while preserving historical identity, dates and a standalone summary', () => {
    const value = wrapper(); unavailable(value); const before = JSON.stringify(value);
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    render(<EvidenceView wrapper={value} {...props} />);
    expect(document.getElementById('result-summary')!.textContent).not.toMatch(/rechecked/);
    expect(document.getElementById('observation-line')!.textContent).toContain('Result generated:');
    expect(document.getElementById('observation-line')!.textContent).toContain('Historical source observations:');
    expect(document.getElementById('observation-line')!.textContent).not.toMatch(/rechecked/);
    expect(document.getElementById('input-identity-line')!.textContent).toContain('current block at this height has not been rechecked');
    fireEvent.click(screen.getByRole('button', { name: 'Inspect Bitcoin publication' }));
    expect(document.getElementById('record-inspector')!.textContent).toContain('Historical source observation time');
    expect(document.getElementById('record-inspector')!.textContent).not.toContain('Not rechecked for this result');
    const text = evidenceSummary(value, 0, false);
    expect(text).toContain('Origin: Stored evidence');
    expect(text).toContain('Source checks: Not rechecked for this result');
    expect(text).toContain('current canonical identity is unconfirmed');
    expect(text).toContain('Publications may exist outside the checked coverage');
    expect(text).toContain('additional evidence required for this first-publication assessment');
    expect(text).toContain('Transaction output:');
    expect(text).toContain('Historical source observations:');
    expect(JSON.stringify(value)).toBe(before); expect(fetch).not.toHaveBeenCalled();
  });
});

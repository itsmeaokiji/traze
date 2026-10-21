import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import ordinary from '../../demo_results/mainnet_5243117.json';
import type { FirstPublicationAssessment, Receipt, ReceiptWrapper } from './types';
import { FirstPublication } from './FirstPublication';
import { firstPublicationState, FIRST_HEIGHT_POLICY, SNAPSHOT_POLICY, SNAPSHOT_RESULT } from './firstPublicationState';
import { evidenceSummary } from './evidenceSummary';

function fixture(): ReceiptWrapper {
  const receipt = structuredClone(ordinary) as unknown as Receipt;
  const p = receipt.proofs[0];
  const publication = { bitcoin_height: p.bitcoin_height, bitcoin_hash: p.bitcoin_block_hash,
    keystone_number: p.checkpoint.height, txid: p.txid, tx_index: p.tx_index!, vout: p.vout_index! };
  const query = { number: receipt.query.block_number!, hash: receipt.query.block_hash! };
  const assessment: FirstPublicationAssessment = {
    status: 'verified_conditional_first_height', query, publication,
    packet_available: true, packet_digest: 'a'.repeat(64),
    download_url: `/api/receipts/example-ordinary/first-publication/${'a'.repeat(64)}/download`,
    globally_first_proven: false, policy_id: FIRST_HEIGHT_POLICY, accepted_by_operator: true,
    assumptions: [{ id: 'selected_chain_validity', description: 'The selected chain follows the supported execution and derivation rules.' }],
    issues: [], source_context: { recorded_at: receipt.completed_at, source_checked_at: receipt.context?.checked_at },
    result: { status: 'verified_conditional_first_height',
      first_height_assessment: { status: 'conditional', bitcoin_height: p.bitcoin_height, policy_id: FIRST_HEIGHT_POLICY },
      assurance: { first_height_under_accepted_assumptions: true, globally_first_proven: false },
      policy: { id: FIRST_HEIGHT_POLICY, accepted_by_caller: true, accepted_from_packet: false },
      interval_evidence: { status: 'verified_interval_evidence', query, publication,
        verified_interval: { first_height: p.bitcoin_height - 2, last_height: p.bitcoin_height },
        assurance: { first_in_checked_interval: true } } },
  };
  return { id: 'example-ordinary', filename: 'evidence.json', label: 'Controlled result', receipt,
    verification: { proofs_checked: 1, current_chain_membership_checked: false }, first_publication: assessment };
}

describe('First-publication website assessment', () => {
  it('shows the qualified result, conditions and optional package for the exact record', () => {
    const wrapper = fixture();
    render(<FirstPublication wrapper={wrapper} proof={wrapper.receipt.proofs[0]} />);
    expect(screen.getByRole('heading', { name: 'First applicable publication under supported rules' })).toBeTruthy();
    expect(screen.getByText(/operator-accepted assumptions/)).toBeTruthy();
    fireEvent.click(screen.getByText('Assumptions and limits', { exact: true }));
    expect(screen.getByText(/unconditional independently established global proof/)).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Download verification package' }).getAttribute('href')).toBe(wrapper.first_publication!.download_url);
  });

  it('never transfers a stronger assessment to another output or publication', () => {
    const wrapper = fixture();
    const another = { ...wrapper.receipt.proofs[0], vout_index: 99 };
    render(<FirstPublication wrapper={wrapper} proof={another} />);
    expect(screen.getByRole('heading', { name: 'Assessment unavailable for this selection' })).toBeTruthy();
    expect(screen.queryByRole('link', { name: 'Download verification package' })).toBeNull();
    expect(firstPublicationState(wrapper, another).qualified).toBe(false);
  });

  it('keeps interval facts when history evidence is incomplete', () => {
    const wrapper = fixture();
    wrapper.first_publication!.status = 'incomplete';
    wrapper.first_publication!.issues = [{ code: 'history_witness_missing', message: 'A required history-state proof is missing.' }];
    render(<FirstPublication wrapper={wrapper} proof={wrapper.receipt.proofs[0]} />);
    expect(screen.getByRole('heading', { name: 'First in checked interval' })).toBeTruthy();
    expect(screen.getByText('A required history-state proof is missing.')).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'First applicable publication under supported rules' })).toBeNull();
  });

  it('does not treat evidence-only success as acceptance of assumptions', () => {
    const wrapper = fixture();
    wrapper.first_publication!.status = 'verified_history_evidence';
    wrapper.first_publication!.accepted_by_operator = false;
    expect(firstPublicationState(wrapper, wrapper.receipt.proofs[0]).qualified).toBe(false);
  });

  it('rejects mismatched query, self-acceptance and legacy success flags', () => {
    for (const field of ['query', 'self_acceptance', 'legacy']) {
      const wrapper = fixture();
      if (field === 'query') wrapper.first_publication!.query = { number: 1, hash: '0x' + '11'.repeat(32) };
      if (field === 'self_acceptance') wrapper.first_publication!.result!.policy!.accepted_from_packet = true;
      if (field === 'legacy') wrapper.first_publication!.result!.status = 'verified_against_provided_headers';
      expect(firstPublicationState(wrapper, wrapper.receipt.proofs[0]).qualified).toBe(false);
    }
  });

  it('suspends the qualified label and download after a source conflict', () => {
    const wrapper = fixture();
    wrapper.receipt.context!.bitcoin_blocks = [{ height: wrapper.receipt.proofs[0].bitcoin_height, same: false }];
    const state = firstPublicationState(wrapper, wrapper.receipt.proofs[0]);
    expect(state.qualified).toBe(false);
    expect(state.title).toBe('First-publication assessment suspended');
    expect(state.download).toBeUndefined();
  });

  it('does not render an external or malformed package URL', () => {
    const wrapper = fixture();
    wrapper.first_publication!.download_url = 'https://external.invalid/archive.zip';
    expect(firstPublicationState(wrapper, wrapper.receipt.proofs[0]).download).toBeUndefined();
  });

  it('distinguishes invalid stronger evidence from missing ordinary publications', () => {
    const wrapper = fixture();
    wrapper.first_publication!.status = 'invalid';
    wrapper.first_publication!.packet_available = false;
    render(<FirstPublication wrapper={wrapper} proof={wrapper.receipt.proofs[0]} />);
    expect(screen.getByRole('heading', { name: 'First-publication evidence invalid' })).toBeTruthy();
    expect(wrapper.receipt.proofs).toHaveLength(ordinary.proofs.length);
  });

  it('copies the same qualified conclusion and retains legacy summaries without promotion', () => {
    const wrapper = fixture();
    expect(evidenceSummary(wrapper, 0, false)).toContain('conditional on operator-accepted');
    expect(evidenceSummary(wrapper, 0, false)).not.toContain('Globally earliest publication: Not established. Publications may exist');
    delete wrapper.first_publication;
    expect(evidenceSummary(wrapper, 0, false)).toContain('Globally earliest publication: Not established. Publications may exist');
  });

  it('does not show a stronger-assessment panel without a positive publication', () => {
    const wrapper = fixture();
    wrapper.receipt.proofs = [];
    const { container } = render(<FirstPublication wrapper={wrapper} />);
    expect(container.textContent).toBe('');
  });

  it('distinguishes new preparation times from legacy receipt times', () => {
    const wrapper = fixture();
    const view = render(<FirstPublication wrapper={wrapper} proof={wrapper.receipt.proofs[0]} />);
    fireEvent.click(screen.getByText('Assumptions and limits', { exact: true }));
    expect(screen.getByText(/Packet prepared: Not recorded \(legacy snapshot\)/)).toBeTruthy();
    wrapper.first_publication!.source_context!.packet_prepared_at = '2026-10-20T01:02:03Z';
    view.rerender(<FirstPublication wrapper={wrapper} proof={wrapper.receipt.proofs[0]} />);
    expect(screen.queryByText(/legacy snapshot/)).toBeNull();
    expect(screen.getByText(/Packet prepared:.*Receipt completed:.*Ordinary source check:/)).toBeTruthy();
  });

  function snapshotFixture() {
    const wrapper = fixture();
    const assessment = wrapper.first_publication!;
    assessment.policy_id = SNAPSHOT_POLICY;
    const result = assessment.result!;
    result.schema = SNAPSHOT_RESULT;
    result.policy = { id: SNAPSHOT_POLICY, accepted_by_caller: true, accepted_from_packet: false,
      context_accepted_by_caller: true, context_sha256_selected_by_caller: 'c'.repeat(64) };
    result.first_height_assessment!.policy_id = SNAPSHOT_POLICY;
    result.first_height_assessment!.context_sha256 = 'c'.repeat(64);
    result.context_evidence = { status: 'verified_context_binding', sha256: 'c'.repeat(64),
      snapshot: { number: 6000000, hash: '0x' + 'b'.repeat(64), timestamp: 1 },
      domain: { first_hemi_keystone: wrapper.receipt.query.covering_checkpoint!.height,
        last_hemi_block: 6000000, tip_hash: '0x' + 'b'.repeat(64) },
      observed_from: '2026-10-21T00:00:00Z', observed_through: '2026-10-21T00:00:03Z',
      historical_execution_certified: false, current_bitcoin_membership_verified: false };
    return wrapper;
  }

  it('shows the conditional snapshot domain and separately external historical basis', () => {
    const wrapper = snapshotFixture();
    render(<FirstPublication wrapper={wrapper} proof={wrapper.receipt.proofs[0]} />);
    expect(screen.getByRole('heading', { name: 'First applicable publication under snapshot assumptions' })).toBeTruthy();
    expect(screen.getByText(/Hemi snapshot: 6,000,000/)).toBeTruthy();
    expect(screen.getByText(/Historical profile applicability and source branch validity are externally accepted/)).toBeTruthy();
    expect(screen.getByText(/this Hemi snapshot does not refresh Bitcoin chain membership/)).toBeTruthy();
    expect(evidenceSummary(wrapper, 0, false)).toContain('future snapshots excluded');
    expect(evidenceSummary(wrapper, 0, false)).toContain('Accepted context checksum:');
  });

  it('blocks the snapshot label when context, independent acceptance or domain bindings fail', () => {
    for (const field of ['context', 'acceptance', 'digest', 'domain', 'hash', 'policy', 'schema', 'certification']) {
      const wrapper = snapshotFixture(); const result = wrapper.first_publication!.result!;
      if (field === 'context') delete result.context_evidence;
      if (field === 'acceptance') result.policy!.context_accepted_by_caller = false;
      if (field === 'digest') result.policy!.context_sha256_selected_by_caller = 'd'.repeat(64);
      if (field === 'domain') result.context_evidence!.domain!.last_hemi_block = 1;
      if (field === 'hash') result.context_evidence!.domain!.tip_hash = '0x' + 'd'.repeat(64);
      if (field === 'policy') result.first_height_assessment!.policy_id = FIRST_HEIGHT_POLICY;
      if (field === 'schema') delete result.schema;
      if (field === 'certification') result.context_evidence!.historical_execution_certified = true;
      expect(firstPublicationState(wrapper, wrapper.receipt.proofs[0]).qualified, field).toBe(false);
    }
  });
});

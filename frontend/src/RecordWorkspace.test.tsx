import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ordinaryData from '../../demo_results/mainnet_5243117.json';
import keystoneData from '../../demo_results/mainnet_5243125.json';
import marchData from '../../demo_results/mainnet_4028425_hash.json';
import negativeData from '../../demo_results/scoped_no_match.json';
import { EvidenceView } from './EvidenceView';
import { formatDate, selectedProof } from './format';
import { RecordWorkspace } from './RecordWorkspace';
import type { Receipt, ReceiptWrapper } from './types';

const copy = (data: unknown): Receipt => structuredClone(data) as Receipt;
function show(data: unknown) {
  const receipt = copy(data);
  render(<RecordWorkspace receipt={receipt} proof={selectedProof(receipt)} />);
  return receipt;
}
const select = (label: string) => fireEvent.click(screen.getByRole('button', { name: `Inspect ${label}` }));
const inspector = (name: string) => within(screen.getByRole('complementary', { name }));

describe('Record inspector workflow', () => {
  it('opens the publication on demand with exact identifiers and recorded reference links', () => {
    const receipt = show(ordinaryData);
    expect(screen.queryByRole('complementary')).toBeNull();
    expect(screen.getByRole('button', { name: 'Inspect Bitcoin publication' }).getAttribute('aria-pressed')).toBe('false');
    select('Bitcoin publication');
    const panel = inspector('Bitcoin publication');
    expect(document.activeElement).toBe(document.getElementById('record-inspector'));
    expect(screen.getByRole('button', { name: 'Inspect Bitcoin publication' }).getAttribute('aria-pressed')).toBe('true');
    expect(panel.getByText(receipt.proofs[0].txid)).toBeTruthy();
    expect(panel.getByText(receipt.proofs[0].bitcoin_block_hash)).toBeTruthy();
    expect(panel.getByRole('link', { name: /View transaction/ }).getAttribute('href')).toBe(`https://blockstream.info/tx/${receipt.proofs[0].txid}`);
    expect(panel.getByRole('link', { name: /View Bitcoin block/ }).getAttribute('href')).toBe(`https://blockstream.info/block/${receipt.proofs[0].bitcoin_block_hash}`);
    expect(panel.getByText(formatDate(receipt.context?.checked_at))).toBeTruthy();
  });

  it('keeps coinciding-height block and keystone identities distinct when switching records', () => {
    const receipt = show(keystoneData);
    select('Requested Hemi block');
    expect(inspector('Requested Hemi block').getByText(receipt.query.block_hash!)).toBeTruthy();
    select('Covering keystone');
    const panel = inspector('Covering keystone');
    expect(panel.getByText(receipt.query.covering_checkpoint!.keystone_id)).toBeTruthy();
    expect(panel.queryByText(receipt.query.block_hash!)).toBeNull();
    expect(panel.getByText('Encoded in the displayed publication transaction.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Inspect Requested Hemi block' }).getAttribute('aria-pressed')).toBe('false');
    expect(screen.getByRole('button', { name: 'Inspect Covering keystone' }).getAttribute('aria-pressed')).toBe('true');
  });

  it('distinguishes the covering record from the later record actually encoded in the transaction', () => {
    const receipt = show(marchData);
    select('Covering keystone');
    expect(inspector('Covering keystone').getByText('Supported through the displayed later-keystone publication.')).toBeTruthy();
    select('Later keystone');
    const panel = inspector('Later keystone');
    expect(panel.getByText(receipt.proofs[0].checkpoint.keystone_id)).toBeTruthy();
    expect(panel.getByText('25')).toBeTruthy();
    expect(panel.queryByText(receipt.query.covering_checkpoint!.keystone_id)).toBeNull();
  });

  it('defaults an unresolved search to the requested block and lets the unresolved publication explain its scope', () => {
    show(negativeData);
    select('Requested Hemi block');
    expect(screen.getByRole('button', { name: 'Inspect Requested Hemi block' }).getAttribute('aria-pressed')).toBe('true');
    select('Bitcoin publication');
    const panel = inspector('Bitcoin publication');
    expect(panel.getByText('Unresolved')).toBeTruthy();
    expect(panel.getByText(/Publications may exist outside this range/)).toBeTruthy();
    expect(panel.queryByRole('link', { name: /View transaction/ })).toBeNull();
    expect(panel.queryByRole('link', { name: /View Bitcoin block/ })).toBeNull();
    expect(document.getElementById('association')!.querySelectorAll('[data-evidence="observed"]')).toHaveLength(0);
  });

  it('changes only the selected view while retaining timestamps, proof selection and incomplete source confidence', () => {
    const data = copy(ordinaryData);
    data.status = 'incomplete';
    data.context = { ...data.context, current_view_confirmed: false };
    data.selection = { current_proof_index: null, best_observed_proof_index: 0 };
    const snapshot = structuredClone(data);
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    render(<RecordWorkspace receipt={data} proof={selectedProof(data)} />);
    select('Covering keystone');
    select('Bitcoin publication');
    const panel = inspector('Bitcoin publication');
    expect(panel.getByText(data.proofs[0].txid)).toBeTruthy();
    expect(panel.getByText('Incomplete at the recorded check')).toBeTruthy();
    expect(panel.getByText(formatDate(data.context?.checked_at))).toBeTruthy();
    expect(data).toEqual(snapshot);
    expect(fetch).not.toHaveBeenCalled();
  });

  it('resets selection for a different receipt rather than showing stale record details', () => {
    const wrap = (data: unknown, id: string): ReceiptWrapper => {
      const receipt = copy(data);
      return { id, label: 'Saved evidence', filename: 'evidence.json', receipt, verification: { proofs_checked: receipt.proofs.length, current_chain_membership_checked: false } };
    };
    const props = { fresh: false, busy: false, onRecheck: vi.fn(), onError: vi.fn() };
    const view = render(<EvidenceView wrapper={wrap(ordinaryData, 'ordinary')} {...props} />);
    select('Covering keystone');
    view.rerender(<EvidenceView wrapper={wrap(marchData, 'march')} {...props} />);
    expect(screen.queryByRole('complementary')).toBeNull();
    select('Bitcoin publication');
    const panel = inspector('Bitcoin publication');
    expect(panel.getByText(marchData.proofs[0].txid)).toBeTruthy();
    expect(panel.queryByText(ordinaryData.proofs[0].txid)).toBeNull();
    expect(screen.getByRole('button', { name: 'Inspect Bitcoin publication' }).getAttribute('aria-pressed')).toBe('true');
  });
});

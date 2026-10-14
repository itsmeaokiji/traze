import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import ordinaryData from '../../demo_results/mainnet_5243117.json';
import keystoneData from '../../demo_results/mainnet_5243125.json';
import marchData from '../../demo_results/milestone3_march_later_hash_20261004T174823173306Z.json';
import negativeData from '../../demo_results/scoped_no_match.json';
import { selectedProof } from './format';
import { PublicationRoute } from './PublicationRoute';
import type { Receipt } from './types';

function copy(data: unknown): Receipt { return structuredClone(data) as Receipt; }
function show(data: unknown) {
  const receipt = copy(data);
  render(<PublicationRoute receipt={receipt} proof={selectedProof(receipt)} />);
  return screen.getByRole('list', { name: 'Hemi to Bitcoin association' });
}

describe('Publication route evidence meaning', () => {
  it('keeps separate requested and covering roles when their heights coincide', () => {
    const route = show(keystoneData);
    const view = within(route);
    expect(view.getAllByRole('listitem')).toHaveLength(3);
    expect(view.getByText('Requested Hemi block')).toBeTruthy();
    expect(view.getByText('Covering keystone')).toBeTruthy();
    expect(view.getAllByText('5,243,125')).toHaveLength(2);
    expect(view.queryByText('Later keystone')).toBeNull();
    expect(view.getAllByRole('img', { name: 'Hemi' })).toHaveLength(2);
    expect(view.getByRole('img', { name: 'Bitcoin' })).toBeTruthy();
  });

  it('shows the selected March publication through a later keystone and its containing block', () => {
    const route = show(marchData);
    const view = within(route);
    expect(view.getAllByRole('listitem')).toHaveLength(4);
    expect(view.getAllByText('4,028,425')).toHaveLength(2);
    expect(view.getByText('Later keystone')).toBeTruthy();
    expect(view.getByText('4,028,450')).toBeTruthy();
    expect(view.getByRole('link', { name: 'Block 941,750' }).getAttribute('href')).toBe(`https://blockstream.info/block/${marchData.proofs[0].bitcoin_block_hash}`);
    expect(view.getByText('Containing Bitcoin block. Transaction ID below.')).toBeTruthy();
  });

  it('does not add a later stage for an unselected candidate', () => {
    // Controlled display input only; this altered candidate is not a new chain observation.
    const receipt = copy(ordinaryData);
    receipt.proofs[1].checkpoint.height += 25;
    receipt.selection = { current_proof_index: 0, best_observed_proof_index: 0 };
    const route = show(receipt);
    expect(within(route).getAllByRole('listitem')).toHaveLength(3);
    expect(within(route).queryByText('Later keystone')).toBeNull();
  });

  it('retains the saved proof relationship when a fresh source view is unconfirmed', () => {
    const receipt = copy(ordinaryData);
    receipt.status = 'incomplete';
    receipt.context = { ...receipt.context, current_view_confirmed: false };
    receipt.selection = { current_proof_index: null, best_observed_proof_index: 0 };
    const route = show(receipt);
    expect(within(route).getByRole('link', { name: 'Block 965,931' })).toBeTruthy();
    expect(route.querySelectorAll('[data-evidence="unresolved"]')).toHaveLength(0);
  });

  it('shows no invented publication link or solid route for a scoped no-match', () => {
    const route = show(negativeData);
    const view = within(route);
    expect(view.getByText('Unresolved')).toBeTruthy();
    expect(view.getByText('No applicable match in the scanned range.')).toBeTruthy();
    expect(view.queryByRole('link')).toBeNull();
    expect(route.querySelectorAll('[data-evidence="observed"]')).toHaveLength(0);
  });

  it('keeps missing Hemi records unresolved without inventing heights or connections', () => {
    const receipt = copy(negativeData);
    receipt.status = 'incomplete';
    receipt.query = { block_number: null, input_type: 'hash', requested_block_hash: '0x' + 'a'.repeat(64) };
    const route = show(receipt);
    const view = within(route);
    expect(view.getByText('Unresolved height')).toBeTruthy();
    expect(view.getAllByText('Unresolved')).toHaveLength(2);
    expect(view.queryByText('5,243,125')).toBeNull();
    expect(view.queryByRole('link')).toBeNull();
    expect(route.querySelectorAll('[data-evidence="observed"]')).toHaveLength(0);
  });
});

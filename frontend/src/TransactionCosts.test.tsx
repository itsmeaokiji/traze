import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ordinary from '../../demo_results/mainnet_5243117.json';
import { TransactionCosts } from './TransactionCosts';
import { EvidenceView } from './EvidenceView';
import type { Receipt, ReceiptWrapper, TransactionCost } from './types';

const cost: TransactionCost = { proof_index: 0, txid: ordinary.proofs[0].txid, status: 'calculated',
  reason: null, fee_sats: 125, vsize_vbytes: 283, size_bytes: 283, weight_wu: 1132, segwit: false };

describe('Publication transaction costs', () => {
  it('shows whole-transaction costs without a source request', () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    render(<TransactionCosts value={cost} headingLevel={2} />);
    expect(screen.getByRole('heading', { name: 'Transaction costs', level: 2 })).toBeTruthy();
    expect(screen.getByText('125 sats')).toBeTruthy();
    expect(screen.getByText('283 vB')).toBeTruthy();
    expect(screen.getByText('0.4417 sat/vB')).toBeTruthy();
    expect(screen.getByText(/whole Bitcoin transaction, including any other publication outputs/)).toBeTruthy();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('keeps missing and invalid fees unavailable rather than zero', () => {
    const view = render(<TransactionCosts value={{ ...cost, status: 'unavailable', fee_sats: null,
      reason: 'previous_outputs_unavailable' }} />);
    expect(screen.getAllByText('Unavailable')).toHaveLength(2);
    expect(screen.getByText('283 vB')).toBeTruthy();
    expect(screen.getByText(/Required previous-output data is unavailable/)).toBeTruthy();
    view.rerender(<TransactionCosts value={{ ...cost, status: 'invalid', fee_sats: null,
      reason: 'transaction_data_invalid' }} />);
    expect(screen.getByText(/failed cost checks/)).toBeTruthy();
    expect(screen.queryByText('0 sats')).toBeNull();
  });

  it('distinguishes zero fees from a rounded positive rate', () => {
    const view = render(<TransactionCosts value={{ ...cost, fee_sats: 0 }} />);
    expect(screen.getByText('0 sats')).toBeTruthy();
    expect(screen.getByText('0 sat/vB')).toBeTruthy();
    view.rerender(<TransactionCosts value={{ ...cost, fee_sats: 1, vsize_vbytes: 20000 }} />);
    expect(screen.getByText('<0.0001 sat/vB')).toBeTruthy();
  });

  it('labels the scope of supplied SegWit bytes', () => {
    render(<TransactionCosts value={{ ...cost, segwit: true }} />);
    expect(screen.getByText(/witness-commitment verification is outside these checks/)).toBeTruthy();
  });

  it('changes costs with the exact selected proof without mutating receipt data', () => {
    const receipt = structuredClone(ordinary) as unknown as Receipt;
    const before = structuredClone(receipt);
    const wrapper: ReceiptWrapper = { id: 'cost-example', label: 'Recorded evidence', filename: 'evidence.json',
      receipt, verification: { proofs_checked: receipt.proofs.length, current_chain_membership_checked: false },
      transaction_costs: { schema: 'traze-transaction-costs-v1', scope: 'whole_transaction', source_requests: 0,
        source_views_rechecked: false, transactions: [cost, { ...cost, proof_index: 1, txid: receipt.proofs[1].txid,
          status: 'unavailable', reason: 'previous_outputs_unavailable', fee_sats: null }] } };
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    render(<EvidenceView wrapper={wrapper} fresh={false} busy={false} onRecheck={vi.fn()} onError={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Inspect Bitcoin publication' }));
    expect(within(screen.getByRole('region', { name: 'Transaction costs' })).getByText('125 sats')).toBeTruthy();
    fireEvent.click(screen.getByText(/Matching publication evidence ·/));
    fireEvent.click(screen.getByRole('button', { name: 'View proof record 2' }));
    fireEvent.click(screen.getByRole('button', { name: 'Inspect Bitcoin publication' }));
    expect(within(screen.getByRole('region', { name: 'Transaction costs' })).queryByText('125 sats')).toBeNull();
    expect(screen.getByText(/Required previous-output data is unavailable/)).toBeTruthy();
    expect(receipt).toEqual(before); expect(fetch).not.toHaveBeenCalled();
  });

  it('does not invent costs for a legacy wrapper', () => {
    const { container } = render(<TransactionCosts />);
    expect(container.textContent).toBe('');
  });
});

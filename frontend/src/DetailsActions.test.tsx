import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ordinary from '../../demo_results/mainnet_5243117.json';
import march from '../../demo_results/mainnet_4028425_hash.json';
import negative from '../../demo_results/scoped_no_match.json';
import { EvidenceView } from './EvidenceView';
import type { Receipt, ReceiptWrapper } from './types';

function wrap(data: unknown = ordinary): ReceiptWrapper {
  const receipt = structuredClone(data) as Receipt;
  return { id: 'details-actions', filename: 'evidence.json', label: 'Saved evidence', receipt,
    verification: { proofs_checked: receipt.proofs.length, current_chain_membership_checked: false } };
}
const props = { fresh: false, busy: false, onRecheck: vi.fn(), onError: vi.fn() };
function clipboard(writeText = vi.fn().mockResolvedValue(undefined)) {
  vi.stubGlobal('navigator', Object.create(navigator, { clipboard: { value: { writeText }, configurable: true } }));
  return writeText;
}
function choose(index: number) {
  fireEvent.click(document.getElementById('other-proofs-summary')!);
  fireEvent.click(screen.getByRole('button', { name: `View proof record ${index}` }));
}

describe('Consolidated record details and evidence actions', () => {
  it('keeps one compact transaction reference and copies its full value without starting source work', async () => {
    const wrapper = wrap(); const original = JSON.stringify(wrapper); const write = clipboard();
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    const recheck = vi.fn(); const lookup = vi.fn();
    render(<EvidenceView wrapper={wrapper} {...props} onRecheck={recheck} onNewLookup={lookup} />);
    const node = document.querySelector('[data-route-role="publication"]')!;
    expect(document.getElementById('transaction-line')).toBeNull();
    expect(document.querySelectorAll('#transaction-link')).toHaveLength(1);
    const reference = node.querySelector('#transaction-link')!;
    expect(reference.textContent).not.toBe(wrapper.receipt.proofs[0].txid);
    expect(reference.getAttribute('href')).toBe(`https://blockstream.info/tx/${wrapper.receipt.proofs[0].txid}`);
    expect(node.querySelector('.step-value')!.textContent).toContain('965,931');
    expect(node.querySelector('.route-output')!.textContent).toBe('Transaction output 1 (zero-based index).');
    fireEvent.click(within(node as HTMLElement).getByRole('button', { name: 'Copy transaction ID' }));
    await screen.findByRole('button', { name: 'transaction ID copied' });
    expect(write).toHaveBeenCalledWith(wrapper.receipt.proofs[0].txid);
    expect(fetch).not.toHaveBeenCalled(); expect(recheck).not.toHaveBeenCalled(); expect(lookup).not.toHaveBeenCalled();
    expect(JSON.stringify(wrapper)).toBe(original);
  });

  it.each([['direct', ordinary], ['later', march]])('retains every exact %s identifier in its inspector and keeps result checks separate', (_, data) => {
    const wrapper = wrap(data); const proof = wrapper.receipt.proofs[0];
    render(<EvidenceView wrapper={wrapper} {...props} />);
    const details = document.getElementById('record-details')!;
    expect(details.querySelector('summary')!.textContent).toBe('Result details');
    expect(details.textContent).toContain('Checked Hemi ancestry links');
    expect(details.textContent).toContain((proof.ancestry.length - 1).toLocaleString('en-US'));
    for (const identifier of [wrapper.receipt.query.block_hash!, wrapper.receipt.query.covering_checkpoint!.keystone_id, proof.checkpoint.keystone_id, proof.bitcoin_block_hash, proof.txid]) {
      expect(details.textContent).not.toContain(identifier);
    }
    fireEvent.click(screen.getByRole('button', { name: 'Inspect Requested Hemi block' }));
    expect(within(document.getElementById('record-inspector')!).getByText(wrapper.receipt.query.block_hash!)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Inspect Covering keystone' }));
    expect(within(document.getElementById('record-inspector')!).getByText(wrapper.receipt.query.covering_checkpoint!.keystone_id)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Inspect Bitcoin publication' }));
    const panel = within(document.getElementById('record-inspector')!);
    expect(panel.getByText(proof.txid)).toBeTruthy(); expect(panel.getByText(proof.bitcoin_block_hash)).toBeTruthy();
    expect(panel.getByText(proof.checkpoint.keystone_id)).toBeTruthy();
    expect(panel.getByText('Output indices start at 0. This output contains the Hemi publication data.')).toBeTruthy();
    expect(panel.getByRole('button', { name: 'Copy Published keystone ID' })).toBeTruthy();
    expect(panel.getByRole('link', { name: 'Open transaction' }).getAttribute('href')).toBe(`https://blockstream.info/tx/${proof.txid}`);
    expect(document.activeElement).toBe(document.getElementById('record-inspector'));
    expect(screen.getByText('Selected record details')).toBeTruthy();
  });

  it('binds transaction copy to a changed view while retaining the original JSON export', async () => {
    let finish!: () => void;
    const write = clipboard(vi.fn().mockImplementationOnce(() => new Promise<void>(resolve => { finish = resolve; })).mockResolvedValue(undefined));
    const wrapper = wrap(); render(<EvidenceView wrapper={wrapper} {...props} />);
    const download = document.getElementById('download-link')!.getAttribute('href');
    fireEvent.click(screen.getByRole('button', { name: 'Copy transaction ID' }));
    choose(2); await act(async () => finish());
    expect(screen.queryByRole('button', { name: 'transaction ID copied' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Copy transaction ID' }));
    await screen.findByRole('button', { name: 'transaction ID copied' });
    expect(write).toHaveBeenNthCalledWith(1, wrapper.receipt.proofs[0].txid);
    expect(write).toHaveBeenNthCalledWith(2, wrapper.receipt.proofs[1].txid);
    expect(screen.getByRole('link', { name: /Download evidence JSON/ }).getAttribute('href')).toBe(download);
    expect(document.querySelector('.publication-selection-help')!.textContent).toContain('Copy summary follows the displayed proof record');
    expect(document.querySelector('.publication-selection-help')!.textContent).toContain('the original JSON evidence export');
  });

  it('offers full-value manual copying in the route and clears it when the selected publication changes', async () => {
    clipboard(vi.fn().mockRejectedValue(new Error('Clipboard denied')));
    const wrapper = wrap(); const error = vi.fn(); render(<EvidenceView wrapper={wrapper} {...props} onError={error} />);
    fireEvent.click(screen.getByRole('button', { name: 'Copy transaction ID' }));
    const field = await screen.findByLabelText('Manual copy: transaction ID') as HTMLTextAreaElement;
    expect(field.value).toBe(wrapper.receipt.proofs[0].txid);
    expect(field.closest('[data-route-role]')!.getAttribute('data-route-role')).toBe('publication');
    fireEvent.focus(field); expect(field.selectionEnd).toBe(field.value.length);
    expect(error).toHaveBeenCalledTimes(1);
    choose(2); expect(screen.queryByLabelText('Manual copy: transaction ID')).toBeNull();
  });

  it('keeps route copying disabled during work and does not invent publication actions for a no-match', () => {
    clipboard(); const view = render(<EvidenceView wrapper={wrap()} {...props} busy />);
    expect((screen.getByRole('button', { name: 'Copy transaction ID' }) as HTMLButtonElement).disabled).toBe(true);
    view.unmount(); render(<EvidenceView wrapper={wrap(negative)} {...props} />);
    expect(screen.queryByRole('button', { name: 'Copy transaction ID' })).toBeNull();
    expect(document.getElementById('transaction-link')).toBeNull();
    expect(screen.queryByText('Result details', { exact: true })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Inspect Requested Hemi block' }));
    expect(within(document.getElementById('record-inspector')!).getByText(negative.query.block_hash)).toBeTruthy();
  });
});

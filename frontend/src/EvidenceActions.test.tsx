import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ordinaryData from '../../demo_results/mainnet_5243117.json';
import marchData from '../../demo_results/milestone3_march_later_hash_20261004T174823173306Z.json';
import negativeData from '../../demo_results/scoped_no_match.json';
import { CopyAction } from './CopyAction';
import { EvidenceView } from './EvidenceView';
import { evidenceSummary } from './evidenceSummary';
import { formatDate } from './format';
import type { Receipt, ReceiptWrapper } from './types';

function wrap(data: unknown = ordinaryData, id = 'ordinary'): ReceiptWrapper {
  const receipt = structuredClone(data) as Receipt;
  return { id, filename: `${id}.json`, label: 'Saved evidence', receipt,
    verification: { proofs_checked: receipt.proofs.length, current_chain_membership_checked: false } };
}
const props = { fresh: false, busy: false, onRecheck: vi.fn(), onError: vi.fn() };
function clipboard(writeText = vi.fn().mockResolvedValue(undefined)) {
  vi.stubGlobal('navigator', Object.create(navigator, { clipboard: { value: { writeText }, configurable: true } }));
  return writeText;
}
const text = () => (screen.getByLabelText('Evidence summary', { exact: true }) as HTMLTextAreaElement).value;
function choose(index: number) {
  fireEvent.click(document.getElementById('other-proofs-summary')!);
  fireEvent.click(screen.getByRole('button', { name: `View proof record ${index}` }));
}

describe('Portable evidence actions', () => {
  it('copies the actual full English summary without public requests or receipt mutation', async () => {
    const wrapper = wrap(); const before = structuredClone(wrapper);
    const write = clipboard(); const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    render(<EvidenceView wrapper={wrapper} {...props} />);
    const summary = text();
    expect(summary).toContain('Result: Publication found');
    expect(summary).toContain('View: Receipt default proof record 1');
    expect(summary).toContain(wrapper.receipt.query.block_hash!);
    expect(summary).toContain(wrapper.receipt.query.covering_checkpoint!.keystone_id);
    expect(summary).toContain(wrapper.receipt.proofs[0].txid);
    expect(summary).toContain(`Sources last checked: ${formatDate(wrapper.receipt.context!.checked_at)}`);
    expect(summary).toContain('Bitcoin blocks scanned: 965,925–965,931.');
    expect(summary).toContain('Fully scanned Bitcoin blocks: 7');
    expect(summary).toContain('Globally earliest publication: Not established');
    expect(summary).not.toContain('127.0.0.1');
    fireEvent.click(screen.getByRole('button', { name: 'Copy summary' }));
    await screen.findByRole('button', { name: 'Copied' });
    expect(write).toHaveBeenCalledWith(summary); expect(fetch).not.toHaveBeenCalled();
    expect(wrapper).toEqual(before);
  });

  it('labels the manually displayed publication and keeps original JSON/recheck references', async () => {
    const wrapper = wrap(); const write = clipboard(); const onRecheck = vi.fn();
    render(<EvidenceView wrapper={wrapper} {...props} onRecheck={onRecheck} />);
    const originalCoverage = document.getElementById('coverage-summary')!.textContent;
    const originalTime = document.getElementById('observation-line')!.textContent;
    choose(2);
    expect(text()).toContain('View: Manually selected proof record 2');
    expect(text()).toContain('Original receipt default: proof record 1');
    expect(text()).toContain('Published keystone: 5,243,175');
    expect(text()).toContain(wrapper.receipt.proofs[1].txid);
    expect(text()).not.toContain(`Publication transaction ID: ${wrapper.receipt.proofs[0].txid}`);
    expect(text()).toContain('The JSON export and source recheck use the original receipt');
    fireEvent.click(screen.getByRole('button', { name: 'Copy summary' }));
    await screen.findByRole('button', { name: 'Copied' });
    expect(write).toHaveBeenCalledWith(text());
    expect(document.getElementById('download-link')!.getAttribute('href')).toBe('/api/receipts/ordinary/download');
    expect(document.getElementById('coverage-summary')!.textContent).toBe(originalCoverage);
    expect(document.getElementById('observation-line')!.textContent).toBe(originalTime);
    fireEvent.click(screen.getByRole('button', { name: /Recheck sources/ })); expect(onRecheck).toHaveBeenCalledOnce();
  });

  it('keeps scoped no-match and no-proof uncertainty in the shared summary', () => {
    render(<EvidenceView wrapper={wrap(negativeData, 'no-match')} {...props} />);
    expect(text()).toContain('Result: No match in checked range');
    expect(text()).toContain('Publications may exist outside this range');
    expect(text()).toContain('No publication proof to check');
    expect(text()).toContain('Fully scanned Bitcoin blocks: 6');
    expect(text()).not.toContain('Publication transaction ID:');
    expect(text()).not.toContain('Bitcoin transaction: https://');
    expect(screen.getByRole('button', { name: 'Copy summary' })).toBeTruthy();
  });

  it('shares timed-out source rechecks with retained mathematics, original coverage and safe warnings', () => {
    const wrapper = wrap();
    wrapper.receipt.status = 'incomplete'; wrapper.receipt.mode = 'live_metadata_refresh';
    wrapper.receipt.validation = { scope: 'controlled_provider_responses', live_public_reads: false };
    wrapper.receipt.context!.current_view_confirmed = false;
    wrapper.receipt.selection = { current_proof_index: null, best_observed_proof_index: 0 };
    wrapper.receipt.issues = [{ code: 'time_budget_exhausted', message: 'Internal reservation/socket diagnostic' }];
    render(<EvidenceView wrapper={wrapper} {...props} fresh />);
    expect(text()).toContain('Result: Evidence retained · source recheck timed out');
    expect(text()).toContain('Origin: Source recheck · Controlled test data; not a live chain observation');
    expect(text()).toContain('4 proof records passed supplied-data checks');
    expect(text()).toContain('Source agreement: Incomplete at the recorded check');
    expect(text()).toContain('Coverage from the original search');
    expect(text()).toContain('Source recheck timed out. Retained evidence');
    expect(text()).not.toContain('Internal reservation/socket diagnostic');
    expect(text()).not.toContain('response_bytes');
  });

  it('preserves unresolved exact-hash identity and avoids invented source references', () => {
    const wrapper = wrap(negativeData);
    wrapper.receipt.status = 'incomplete'; wrapper.receipt.proofs = [];
    wrapper.receipt.query = { block_number: null, requested_block_hash: `0x${'ab'.repeat(32)}`, input_type: 'hash' };
    wrapper.receipt.context = { current_view_confirmed: false };
    wrapper.receipt.sources = { hemi_rpc: 'https://unsupported.invalid', bitcoin_apis: ['javascript:alert(1)', 'https://unsupported.invalid'] };
    const summary = evidenceSummary(wrapper, undefined, false);
    expect(summary).toContain(`Hemi input: 0x${'ab'.repeat(32)}`);
    expect(summary).toContain('Requested Hemi block: Unresolved height');
    expect(summary).toContain('Block identity: Not separately recorded');
    expect(summary).toContain('Sources last checked: not recorded');
    expect(summary).toContain('No supported source URL was recorded');
    expect(summary).not.toContain('unsupported.invalid');
    expect(summary).not.toContain('javascript:');
  });

  it('counts March proof records separately from its one checked Bitcoin block', () => {
    render(<EvidenceView wrapper={wrap(marchData)} {...props} />);
    expect(text()).toContain('6 proof records passed supplied-data checks');
    expect(text()).toContain('Fully scanned Bitcoin blocks: 1');
    expect(text()).toContain('Published keystone: 4,028,450');
    expect(text()).toContain('Requested block supported through the displayed later-keystone publication');
  });

  it('copies full identifiers from each selected record and preserves safe external references', async () => {
    const wrapper = wrap(); const write = clipboard();
    render(<EvidenceView wrapper={wrapper} {...props} />);
    const panel = () => within(document.getElementById('record-inspector')!);
    fireEvent.click(screen.getByRole('button', { name: 'Inspect Bitcoin publication' }));
    fireEvent.click(panel().getByRole('button', { name: 'Copy Bitcoin block hash' }));
    await panel().findByRole('button', { name: 'Bitcoin block hash copied' });
    expect(write).toHaveBeenLastCalledWith(wrapper.receipt.proofs[0].bitcoin_block_hash);
    fireEvent.click(screen.getByRole('button', { name: 'Inspect Requested Hemi block' }));
    fireEvent.click(panel().getByRole('button', { name: 'Copy Recorded Hemi block hash' }));
    await panel().findByRole('button', { name: 'Recorded Hemi block hash copied' });
    expect(write).toHaveBeenLastCalledWith(wrapper.receipt.query.block_hash);
    fireEvent.click(screen.getByRole('button', { name: 'Inspect Covering keystone' }));
    fireEvent.click(panel().getByRole('button', { name: 'Copy Keystone ID' }));
    await panel().findByRole('button', { name: 'Keystone ID copied' });
    expect(write).toHaveBeenLastCalledWith(wrapper.receipt.query.covering_checkpoint!.keystone_id);
    expect(panel().getByRole('link', { name: 'Hemi RPC source' }).getAttribute('rel')).toBe('noopener noreferrer');
    expect(document.querySelector('.external-link svg')!.getAttribute('aria-hidden')).toBe('true');
  });

  it('opens and selects the same summary for manual copying if automatic copy fails', async () => {
    clipboard(vi.fn().mockRejectedValue(new Error('Raw clipboard detail')));
    render(<EvidenceView wrapper={wrap()} {...props} />);
    fireEvent.click(screen.getByRole('button', { name: 'Copy summary' }));
    await screen.findByRole('button', { name: 'Open summary for manual copy' });
    fireEvent.click(screen.getByRole('button', { name: 'Open summary for manual copy' }));
    const field = screen.getByLabelText('Evidence summary', { exact: true }) as HTMLTextAreaElement;
    expect((document.getElementById('evidence-summary') as HTMLDetailsElement).open).toBe(true);
    expect(document.activeElement).toBe(field);
    expect(field.selectionStart).toBe(0); expect(field.selectionEnd).toBe(field.value.length);
    expect(document.body.textContent).not.toContain('Raw clipboard detail');
  });

  it('does not carry a delayed summary copy into another publication view', async () => {
    let finish!: () => void;
    const write = clipboard(vi.fn(() => new Promise<void>(resolve => { finish = resolve; })));
    render(<EvidenceView wrapper={wrap()} {...props} />);
    const copied = text();
    fireEvent.click(screen.getByRole('button', { name: 'Copy summary' }));
    choose(2); await act(async () => finish());
    expect(write).toHaveBeenCalledWith(copied);
    expect(screen.queryByRole('button', { name: 'Copied' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Copy summary' })).toBeTruthy();
    expect(text()).toContain('Manually selected proof record 2');
  });

  it('clears copy feedback after a short interval and supports an identifier manual fallback', async () => {
    vi.useFakeTimers(); clipboard();
    const view = render(<CopyAction value={'ab'.repeat(32)} label="Copy block hash" compact />);
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Copy block hash' })));
    expect(screen.getByRole('button', { name: 'block hash copied' })).toBeTruthy();
    await act(async () => vi.advanceTimersByTime(3000));
    expect(screen.getByRole('button', { name: 'Copy block hash' })).toBeTruthy();
    clipboard(vi.fn().mockRejectedValue(new Error('Blocked')));
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Copy block hash' })));
    const field = screen.getByLabelText('Manual copy: block hash') as HTMLTextAreaElement;
    expect(field.value).toBe('ab'.repeat(32));
    fireEvent.focus(field); expect(field.selectionEnd).toBe(field.value.length);
    view.unmount();
  });
});

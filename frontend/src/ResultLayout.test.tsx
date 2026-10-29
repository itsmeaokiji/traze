import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import recorded from '../../demo_results/main_catalogue_5243119.json';
import noMatch from '../../demo_results/scoped_no_match.json';
import { DiscoveryAllowance } from './SearchRecovery';
import { EvidenceView } from './EvidenceView';
import { evidenceSummary } from './evidenceSummary';
import type { Receipt, ReceiptWrapper, WorkspaceState } from './types';

function wrapper(data: unknown = recorded): ReceiptWrapper {
  const receipt = structuredClone(data) as Receipt;
  receipt.validation = { scope: 'controlled_provider_responses', live_public_reads: false };
  return { id: 'layout', label: 'Controlled layout case', filename: 'evidence.json', receipt,
    verification: { proofs_checked: receipt.proofs.length, current_chain_membership_checked: false } };
}
function workspace(): WorkspaceState {
  return { serving_mode: 'discovery', csrf_token: 'controlled-token', receipts: [], active_job: null,
    resources: { available: true, source_operations_available: true, session_limit_bytes: 200000000,
      usable_response_bytes: 37680000, total_response_bytes: 161320000 },
    limits: { response_mb_per_lookup: 40, bitcoin_body_attempts: 20, ancestry_links: 256,
      session_response_mb: 200, http_requests: 600, overall_seconds: 600 } };
}
const props = { fresh: false, busy: false, onRecheck: vi.fn(), onError: vi.fn() };
const before = (a: string, b: string) => Boolean(document.getElementById(a)!.compareDocumentPosition(document.getElementById(b)!) & Node.DOCUMENT_POSITION_FOLLOWING);

describe('Result layout and progressive operating detail', () => {
  it('keeps capacity visible while placing full accounting and operation limits in an explicit disclosure', () => {
    const value = workspace(); const original = structuredClone(value); const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    render(<DiscoveryAllowance workspace={value} />);
    expect(document.getElementById('discovery-capacity')!.textContent).toBe('37.68 MB available for discovery');
    const limits = document.getElementById('discovery-limits') as HTMLDetailsElement;
    expect(limits.open).toBe(false);
    expect(document.getElementById('discovery-capacity')!.closest('details')).toBeNull();
    fireEvent.click(screen.getByText('Search limits', { exact: true }));
    expect(limits.open).toBe(true);
    expect(limits.textContent).toContain('Ceiling: 200.00 MB · 161.32 MB counted');
    expect(limits.textContent).toContain('20 Bitcoin block download attempts, 40 MB of response data, 600 requests and 10 minutes');
    expect(limits.textContent).toContain('a new operation does not renew the cumulative allowance');
    expect(value).toEqual(original); expect(fetch).not.toHaveBeenCalled();
  });

  it.each(['cumulative_response_bytes', 'restored_state_requires_review', 'source_operation_active', 'unknown_reason'])('keeps %s visible before expanding limits', reason => {
    const value = workspace(); value.resources.source_operations_available = false; value.resources.blocked_reason = reason;
    render(<DiscoveryAllowance workspace={value} />);
    const status = document.getElementById('discovery-capacity')!;
    expect(status.closest('details')).toBeNull(); expect(status.className).toContain('allowance-warning');
    expect(status.textContent).not.toContain('available for discovery');
    expect(status.textContent).toContain(reason === 'cumulative_response_bytes' ? 'cannot reserve another full Bitcoin block'
      : reason === 'restored_state_requires_review' ? 'operator review' : reason === 'source_operation_active' ? 'operation is active' : 'Live discovery is unavailable');
  });

  it('does not fabricate a capacity number when accounting is unverified', () => {
    const value = workspace(); value.resources.available = false;
    render(<DiscoveryAllowance workspace={value} />);
    expect(document.getElementById('discovery-capacity')!.textContent).toContain('could not be verified');
    expect(document.getElementById('discovery-capacity')!.textContent).not.toMatch(/\d+\.\d+ MB/);
  });

  it('places a compact selection and connected records before index bookkeeping, preserving the original evidence', () => {
    const value = wrapper();
    value.index_update = { state: 'already_indexed', publication_count: 4, record_url: '/records/publications/' + 'a'.repeat(64), can_retry: false, source_requests: 0 };
    const original = JSON.stringify(value); const summary = evidenceSummary(value, 0, false); const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    render(<EvidenceView wrapper={value} {...props} workspace={workspace()} />);
    expect(document.getElementById('publication-view-label')!.textContent).toContain('Proof record 1 of 4');
    expect(before('association', 'evidence-assessment')).toBe(true);
    expect(before('evidence-assessment', 'index-update')).toBe(true);
    expect(screen.getByText('Default selection follows the earliest observed applicable Bitcoin height.').closest('details')).toBe(document.getElementById('other-proofs'));
    expect(document.getElementById('result-assurance')!.textContent).toContain('Earliest observed in this result');
    expect(evidenceSummary(value, 0, false)).toBe(summary);
    expect(JSON.stringify(value)).toBe(original); expect(fetch).not.toHaveBeenCalled();
  });

  it('keeps a manual-selection export qualification visible and returns focus to the chooser', () => {
    const value = wrapper(); const original = JSON.stringify(value);
    render(<EvidenceView wrapper={value} {...props} />);
    const download = document.getElementById('download-link')!.getAttribute('href');
    fireEvent.click(document.getElementById('other-proofs-summary')!);
    fireEvent.click(screen.getByRole('button', { name: 'View proof record 2' }));
    const help = document.querySelector('.publication-selection-help')!;
    expect(help.closest('details')).toBeNull();
    expect(help.textContent).toContain('the original JSON evidence export');
    expect(document.querySelectorAll('#association [data-route-role]')).toHaveLength(4);
    expect(document.getElementById('transaction-link')!.getAttribute('href')).toBe(`https://blockstream.info/tx/${value.receipt.proofs[1].txid}`);
    const publicationValue = document.querySelector('[data-route-role="publication"] .route-publication-value')!;
    expect(document.querySelector('[data-route-role="publication"] .route-label')!.textContent).toBe('Containing Bitcoin block');
    expect(publicationValue.querySelector('small')).toBeNull();
    expect([...publicationValue.querySelector('a')!.childNodes].filter(node => node.nodeType === Node.TEXT_NODE).map(node => node.textContent).join('').trim()).toBe('965,931');
    expect(document.querySelector('[data-route-role="publication"] .step-note')!.textContent).toContain('Transaction output 1');
    expect(document.getElementById('download-link')!.getAttribute('href')).toBe(download);
    fireEvent.click(screen.getByRole('button', { name: 'Back to default selection' }));
    expect(document.activeElement).toBe(document.getElementById('other-proofs-summary'));
    expect(JSON.stringify(value)).toBe(original);
  });

  it('keeps material warnings before the route and incomplete coverage outside disclosures', () => {
    const value = wrapper(); value.receipt.issues = [{ code: 'source_conflict' }, { code: 'invalid_proof' }];
    value.receipt.coverage!.gaps = [{ code: 'body_missing' }];
    render(<EvidenceView wrapper={value} {...props} />);
    expect(before('issues-section', 'association')).toBe(true);
    expect(document.getElementById('issues-list')!.closest('details')).toBeNull();
    expect(document.getElementById('issues-list')!.textContent).toContain('did not pass its proof checks');
    expect(document.getElementById('coverage-issues')!.closest('details')).toBeNull();
    expect(document.getElementById('coverage-issues')!.textContent).toContain('1 scan gap');
  });

  it('keeps no-match coverage and recovery ahead of its unresolved route without inventing a chooser', () => {
    const value = wrapper(noMatch);
    value.continuation = { available: true, reason: 'next_segment', bitcoin_range: [965547, 965566], receipt_sha256: 'a'.repeat(64) };
    render(<EvidenceView wrapper={value} {...props} workspace={workspace()} onContinue={vi.fn()} />);
    expect(before('coverage-summary', 'association')).toBe(true);
    expect(before('result-search-actions', 'association')).toBe(true);
    expect(document.querySelector('.publication-context')).toBeNull();
    expect(document.querySelectorAll('#association [data-evidence="observed"]')).toHaveLength(0);
  });
});

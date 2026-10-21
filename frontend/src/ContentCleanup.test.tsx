import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ordinary from '../../demo_results/mainnet_5243117.json';
import { EvidenceView } from './EvidenceView';
import { ApiGuide } from './ExplorerViews';
import type { Receipt, ReceiptWrapper } from './types';

function fixture(): ReceiptWrapper {
  const receipt = structuredClone(ordinary) as unknown as Receipt;
  receipt.proofs = [receipt.proofs[0]];
  receipt.selection = { current_proof_index: 0, best_observed_proof_index: 0 };
  receipt.coverage!.gaps = [];
  receipt.coverage!.unresolved_candidates = [{ code: 'ancestry_limit', message: 'Controlled unresolved association' }];
  receipt.coverage!.unsupported_payloads = 2;
  return { id: 'cleanup', filename: 'evidence.json', label: 'Controlled retained data', receipt,
    verification: { proofs_checked: 1, current_chain_membership_checked: false } };
}

describe('Evidence content hierarchy', () => {
  it('retains qualifications and coverage while making detailed inspection optional', () => {
    const wrapper = fixture(); const before = structuredClone(wrapper);
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    render(<EvidenceView wrapper={wrapper} fresh={false} busy={false} onRecheck={vi.fn()} onError={vi.fn()} />);
    expect(document.getElementById('publication-view')).toBeNull();
    expect(document.getElementById('association')).toBeTruthy();
    expect(screen.queryByRole('complementary')).toBeNull();
    expect((document.getElementById('verification-details') as HTMLDetailsElement).open).toBe(false);
    expect((document.getElementById('coverage-details') as HTMLDetailsElement).open).toBe(false);
    expect(document.getElementById('coverage-summary')!.closest('details')).toBeNull();
    expect(document.getElementById('stop-reason')!.closest('details')).toBeNull();
    expect(document.getElementById('coverage-issues')!.textContent).toContain('1 unresolved publication association · 2 unsupported payloads');
    expect(document.getElementById('evidence-assessment')!.textContent).not.toContain('gaps remain');
    expect(document.getElementById('evidence-assessment')!.textContent).not.toContain('scan gap');
    expect(document.getElementById('issues-list')!.textContent).toContain('Coverage issues: 1 unresolved publication association · 2 unsupported payloads.');
    expect(document.getElementById('issues-list')!.textContent).not.toContain('Some blocks');
    fireEvent.click(screen.getByText('Verification details', { exact: true }));
    expect(screen.getByRole('heading', { name: 'Proof checks' })).toBeTruthy();
    fireEvent.click(screen.getByText('Scanned Bitcoin blocks', { exact: true }));
    expect(screen.getByRole('table')).toBeTruthy();
    expect(wrapper).toEqual(before); expect(fetch).not.toHaveBeenCalled();
  });

  it('counts actual scan gaps separately from associations', () => {
    const wrapper = fixture(); wrapper.receipt.coverage!.gaps = [{ code: 'body_unavailable' }];
    render(<EvidenceView wrapper={wrapper} fresh={false} busy={false} onRecheck={vi.fn()} onError={vi.fn()} />);
    expect(document.getElementById('coverage-issues')!.textContent).toContain('1 scan gap · 1 unresolved publication association');
  });

  it('keeps client-facing API limits visible and implementation budgets expandable', () => {
    render(<ApiGuide />);
    const advanced = screen.getByText('Advanced service limits').closest('details')!;
    expect(advanced.open).toBe(false);
    expect(screen.getByText(/accounted SQL work/).closest('details')).toBe(advanced);
    expect(screen.getByText(/Responses are limited to 8 MB/).closest('details')).toBeNull();
    expect(screen.getByText(/Maximum page size: 50/)).toBeTruthy();
  });
});

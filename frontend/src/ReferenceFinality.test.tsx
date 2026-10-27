import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import recorded from '../../tests/fixtures/finality/recorded_5200117.json';
import assessed from '../../tests/fixtures/finality/website_assessment_5200117.json';
import { EvidenceView } from './EvidenceView';
import { evidenceSummary } from './evidenceSummary';
import { formatDate } from './format';
import type { FinalityAssessment, Receipt, ReceiptWrapper } from './types';

// Only presentation metadata is controlled. Proofs and the assessment are the
// retained historical fixture and an isolated reference-calculation worker result.
function fixture(): ReceiptWrapper {
  const receipt = structuredClone(recorded) as unknown as Receipt;
  receipt.status = 'found'; receipt.mode = 'public_read_only';
  receipt.selection = { current_proof_index: 0, best_observed_proof_index: 0 };
  return { id: 'finality', label: 'Retained finality evidence', filename: 'receipt.json', receipt,
    verification: { proofs_checked: 6, current_chain_membership_checked: false },
    finality: structuredClone(assessed) as unknown as FinalityAssessment };
}
const props = { fresh: false, busy: false, onRecheck: vi.fn(), onError: vi.fn(), sourceOperationsEnabled: false };

describe('Reference calculation integration', () => {
  it('shows the actual named BFG model and recorded times without claiming current or deployed finality', () => {
    const wrapper = fixture(); const before = structuredClone(wrapper);
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    render(<EvidenceView wrapper={wrapper} {...props} />);
    expect(document.getElementById('finality-effective')!.textContent).toBe('7,244');
    expect(document.getElementById('finality-depth')!.textContent).toBe('7,245');
    expect(document.getElementById('finality-threshold')!.textContent).toBe('Reached');
    expect(document.getElementById('finality-reference')!.textContent).toContain('Keystone 5,200,225');
    expect(document.getElementById('finality-observation')!.textContent).toContain('972,289');
    expect(document.getElementById('finality-observation')!.textContent).toContain(formatDate(wrapper.finality!.calculation!.source_context.inputs.observed_at));
    expect(document.getElementById('finality-qualification')!.textContent).toContain('current-chain finality are not established');
    expect((document.getElementById('finality-ama') as HTMLDetailsElement).open).toBe(false);
    expect(document.getElementById('finality-ama')!.textContent).toContain('7,235');
    expect((document.getElementById('finality-details') as HTMLDetailsElement).open).toBe(false);
    expect(document.getElementById('finality-details')!.textContent).toContain('not a certified simultaneous cross-chain snapshot');
    expect(wrapper).toEqual(before); expect(fetch).not.toHaveBeenCalled();
  });

  it('keeps the conditional block reference stable when another proof is inspected or linked', () => {
    const wrapper = fixture();
    wrapper.receipt.proofs.forEach((proof, i) => { proof.publication_id = String(i).padStart(64, 'a'); });
    wrapper.selected_publication = wrapper.receipt.proofs[0].publication_id;
    wrapper.selected_publication_available = true;
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    render(<EvidenceView wrapper={wrapper} {...props} />);
    const reference = document.getElementById('finality-reference')!.textContent;
    fireEvent.click(document.getElementById('other-proofs-summary')!);
    fireEvent.click(screen.getByRole('button', { name: 'View proof record 2' }));
    expect(document.getElementById('finality-reference')!.textContent).toBe(reference);
    expect(document.getElementById('finality-effective')!.textContent).toBe('7,244');
    expect(document.getElementById('finality-selection-note')!.textContent).toContain('does not change');
    const summary = (screen.getByLabelText('Evidence summary', { exact: true }) as HTMLTextAreaElement).value;
    expect(summary).toContain('Published keystone: 5,200,325');
    expect(summary).toContain('keystone 5,200,225');
    expect(fetch).not.toHaveBeenCalled();
  });

  it('shows publication-only arithmetic and an explicit incomplete source warning when block premises are missing', () => {
    const wrapper = fixture(); const value = wrapper.finality!;
    value.status = 'publication_calculations_only'; value.operator_source_acceptance = 'not_configured';
    value.calculation!.block_calculation = { status: 'not_established', values: null, reference_publication: null, issues: [] };
    value.calculation!.source_context.accepted_by_caller = false;
    value.calculation!.source_context.status = 'incomplete';
    render(<EvidenceView wrapper={wrapper} {...props} />);
    expect(document.getElementById('reference-finality')!.textContent).toContain('Publication only');
    expect(document.getElementById('finality-reference')!.textContent).toContain('Inspected publication');
    expect(document.getElementById('finality-source-warning')!.textContent).toContain('publication-only arithmetic');
    expect(document.getElementById('finality-selection-note')).toBeNull();
    expect(evidenceSummary(wrapper, 0, false)).toContain('Scope: Inspected publication only');
  });

  it.each(['finality_tip_missing', 'finality_tip_conflict', 'finality_busy', 'time_budget_exhausted'])(
    'does not display zero or stale numbers for %s', code => {
      const wrapper = fixture();
      wrapper.finality = { schema: 'traze-web-finality-assessment-v1', status: code === 'finality_tip_conflict' ? 'conflicting' : 'unavailable',
        calculation: null, comparison: null, issues: [{ code }], recorded_context_only: true, fresh_source_check_performed: false, network_requests: 0 };
      render(<EvidenceView wrapper={wrapper} {...props} />);
      expect(document.getElementById('finality-effective')).toBeNull();
      expect(document.getElementById('reference-finality')!.textContent).toContain('Unavailable');
      expect(evidenceSummary(wrapper, 0, false)).not.toContain('BFG reference effective confirmations:');
      expect(document.getElementById('association')).toBeTruthy();
    });

  it('rejects mismatched query and unsupported assessment schema in the display', () => {
    const wrapper = fixture(); wrapper.finality!.calculation!.query.hash = '0x' + '11'.repeat(32);
    const view = render(<EvidenceView wrapper={wrapper} {...props} />);
    expect(document.getElementById('finality-effective')).toBeNull();
    wrapper.finality!.schema = 'unknown';
    view.rerender(<EvidenceView wrapper={wrapper} {...props} />);
    expect(document.getElementById('finality-effective')).toBeNull();
  });

  it('retains a source-conflict warning even when historical calculator observations are internally consistent', () => {
    const wrapper = fixture(); wrapper.finality!.source_conflict = true;
    render(<EvidenceView wrapper={wrapper} {...props} />);
    expect(document.getElementById('finality-description')!.textContent).toContain('Publication-only');
    expect(document.getElementById('finality-source-warning')!.textContent).toContain('conflicting');
    expect(evidenceSummary(wrapper, 0, false)).toContain('conflicting retained source views');
  });

  it('exports exactly the returned assessment while leaving the original receipt download separate', async () => {
    const wrapper = fixture(); const before = structuredClone(wrapper);
    const blobs = new Map<string, Blob>(); let next = 0;
    vi.spyOn(URL, 'createObjectURL').mockImplementation(blob => { const url = `blob:finality-${++next}`; blobs.set(url, blob as Blob); return url; });
    const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    const view = render(<EvidenceView wrapper={wrapper} {...props} />);
    const link = document.getElementById('download-calculation') as HTMLAnchorElement;
    const blob = blobs.get(link.getAttribute('href')!)!;
    const contents = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsText(blob);
    });
    expect(JSON.parse(contents)).toEqual(wrapper.finality);
    expect(link.download).toBe('traze-finality-5200117.json');
    expect(document.getElementById('download-link')!.getAttribute('href')).toBe('/api/receipts/finality/download');
    expect(wrapper).toEqual(before);
    view.unmount(); expect(revoke).toHaveBeenCalledWith(link.getAttribute('href'));
  });
});

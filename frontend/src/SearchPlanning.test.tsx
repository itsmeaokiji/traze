import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import negative from '../../demo_results/scoped_no_match.json';
import { OperationProgress } from './OperationProgress';
import { SearchRecovery } from './SearchRecovery';
import { searchCoverageText, searchStopText } from './coveragePresentation';
import type { Job, Receipt, ReceiptWrapper } from './types';

function running(message: string): Job {
  return {id:'a'.repeat(32),operation:'lookup',phase:'public_discovery',state:'running',
    block_number:5100117,started_at:new Date().toISOString(),receipt_id:null,
    events:[{at:new Date().toISOString(),message}]};
}
function wrapper(phase: 'estimated_window' | 'forward_extension' = 'estimated_window'): ReceiptWrapper {
  const receipt = structuredClone(negative) as unknown as Receipt;
  receipt.status = 'incomplete';
  receipt.coverage!.plan = {mode:'timestamp_guided',start:963060,initial_end:963081,
    execution_segment:[963060,963079],segment_policy:'estimated_window_first_v1'};
  receipt.coverage!.stop_reason = 'search_segment_end';
  return {id:'b'.repeat(32),filename:'controlled.json',label:'Controlled planning state',receipt,
    verification:{proofs_checked:0,current_chain_membership_checked:false},
    continuation:{available:true,reason:'next_segment',search_phase:phase,estimated_window:[963060,963081],
      bitcoin_range:phase === 'estimated_window' ? [963080,963081] : [963082,963101]}};
}

describe('actual search stages and bounded window presentation', () => {
  it.each([
    ['Reading Hemi block and keystone data', 'Reading the Hemi block and its covering keystone…'],
    ['Estimating the Bitcoin search range', 'Estimating the Bitcoin search range…'],
    ['Using the fallback timestamp source', 'Using the fallback timestamp source…'],
    ['Estimating the Bitcoin search range using fallback metadata', 'Estimating the Bitcoin search range using fallback metadata…'],
    ['Checking source views', 'Checking source views…'],
  ])('shows the genuine %s stage without pretending a scan has begun', (event, expected) => {
    render(<OperationProgress job={running(event)} />);
    expect(screen.getByRole('status').textContent).toBe(expected);
    expect(screen.queryByText(/\d+%/)).toBeNull();
  });

  it('renders new and retained scan events with the same readable range', () => {
    const view = render(<OperationProgress job={running('Scanning Bitcoin blocks 963060-963079; requested Hemi 5100117, covering keystone 5100125')} />);
    expect(screen.getByRole('status').textContent).toBe('Scanning Bitcoin blocks 963,060–963,079…');
    view.rerender(<OperationProgress job={running('Search Bitcoin 963060-963079; requested Hemi 5100117, covering checkpoint 5100125')} />);
    expect(screen.getByRole('status').textContent).toBe('Scanning Bitcoin blocks 963,060–963,079…');
  });

  it('keeps the estimated window separate from the planned first segment during source checks', () => {
    const job = running('Estimated Bitcoin search window: 963060-963081; bounded first segment: 963060-963079');
    job.events.push({at:new Date().toISOString(),message:'Checking source views'});
    render(<OperationProgress job={job} />);
    expect(screen.getByRole('status').textContent).toBe('Checking source views…');
    expect(document.getElementById('progress-search-plan')!.textContent).toContain('Estimated Bitcoin window: 963,060–963,081. Planned first segment: 963,060–963,079.');
    expect(document.getElementById('progress-search-plan')!.textContent).toContain('can stop this segment earlier');
  });

  it('offers only the window remainder as an explicit action and makes no render-time request', () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    const value = wrapper(); const before = structuredClone(value);
    render(<SearchRecovery wrapper={value} busy={false} onContinue={vi.fn()} />);
    expect(screen.getByText('963,080–963,081')).toBeTruthy();
    expect(document.getElementById('continuation-window')!.textContent).toContain('before any forward extension');
    expect(screen.getByRole('button',{name:'Continue search'})).toBeTruthy();
    expect(fetch).not.toHaveBeenCalled(); expect(value).toEqual(before);
  });

  it('names forward extension separately and keeps publication absence qualified', () => {
    render(<SearchRecovery wrapper={wrapper('forward_extension')} busy={false} onContinue={vi.fn()} />);
    expect(screen.getByText('963,082–963,101')).toBeTruthy();
    expect(document.getElementById('continuation-window')!.textContent).toContain('does not establish global publication absence');
  });

  it('retains the full estimate when displaying an incomplete segment', () => {
    const receipt = wrapper().receipt;
    expect(searchCoverageText(receipt)).toContain('Initial estimated range: 963,060–963,081. Planned scan segment: 963,060–963,079.');
    expect(searchStopText(receipt)).toContain('estimated window has unfinished heights');
    receipt.coverage!.stop_reason = 'estimated_window_end';
    expect(searchStopText(receipt)).toContain('publication may exist outside the checked blocks');
  });
});

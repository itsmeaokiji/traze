import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import negative from '../../demo_results/scoped_no_match.json';
import positive from '../../demo_results/mainnet_5243117.json';
import App from './App';
import { EvidenceView } from './EvidenceView';
import { RecordInspector } from './RecordInspector';
import { evidenceSummary } from './evidenceSummary';
import { receiptIssues } from './receiptIssues';
import type { Receipt, ReceiptWrapper, WorkspaceState } from './types';

const props = {fresh:false,busy:false,onRecheck:vi.fn(),onError:vi.fn()};
function wrap(data: unknown = negative): ReceiptWrapper {
  const receipt = structuredClone(data) as Receipt;
  receipt.validation = {scope:'controlled_provider_responses',live_public_reads:false};
  return {id:'content-case',filename:'evidence.json',label:'Controlled content state',receipt,
    verification:{proofs_checked:receipt.proofs.length,current_chain_membership_checked:false}};
}
function partial(): ReceiptWrapper {
  const wrapper = wrap();
  wrapper.receipt.status = 'incomplete';
  wrapper.receipt.coverage!.stop_reason = 'budget_limit';
  wrapper.receipt.coverage!.stop_detail = {limit:'bitcoin_body_attempts',bitcoin_height:965931};
  wrapper.receipt.issues = [{code:'budget_limit',message:'Bitcoin body-attempt limit reached'}];
  wrapper.continuation = {available:true,reason:'next_segment',bitcoin_range:[965931,965950],receipt_sha256:'a'.repeat(64)};
  return wrapper;
}
const state: WorkspaceState = {serving_mode:'discovery',csrf_token:'controlled-token',active_job:null,receipts:[],
  resources:{available:true,session_limit_bytes:100000000,usable_response_bytes:33399983},
  limits:{response_mb_per_lookup:40,bitcoin_body_attempts:20,ancestry_links:256,session_response_mb:100}};
function mockForm() {
  const posts: unknown[] = [];
  vi.stubGlobal('fetch', vi.fn((path: string, options?: RequestInit) => {
    if (options?.method === 'POST') {
      posts.push(JSON.parse(options.body as string));
      return Promise.resolve(new Response(JSON.stringify({id:'a'.repeat(32),operation:'lookup',state:'complete',
        receipt_id:'content-case',started_at:'2026-10-25T00:00:00Z',events:[]})));
    }
    return Promise.resolve(new Response(JSON.stringify(path === '/api/state' ? state : wrap())));
  }));
  return posts;
}

describe('content clarity without changing evidence or acquisition rules', () => {
  it('keeps untouched range fields empty and submits automatic discovery with no manual range', async () => {
    const posts = mockForm(); render(<App />);
    const button = screen.getByRole('button',{name:'Find publication'});
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
    expect((screen.getByLabelText('From Bitcoin block') as HTMLInputElement).value).toBe('');
    expect((screen.getByLabelText('Through Bitcoin block') as HTMLInputElement).value).toBe('');
    expect(screen.getByPlaceholderText('Start height')).toBeTruthy();
    expect(screen.getByPlaceholderText('End height')).toBeTruthy();
    fireEvent.click(button);
    await waitFor(() => expect(posts).toEqual([{block:5243117,bitcoin_range:null}]));
  });

  it.each([['965925',''],['965930','965925'],['invalid','965930']])(
    'rejects invalid explicit range %s / %s before a POST', async (start,end) => {
      const posts = mockForm(); render(<App />);
      const button = screen.getByRole('button',{name:'Find publication'});
      await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
      fireEvent.change(screen.getByLabelText('From Bitcoin block'),{target:{value:start}});
      fireEvent.change(screen.getByLabelText('Through Bitcoin block'),{target:{value:end}});
      fireEvent.click(button);
      expect(await screen.findByRole('alert')).toBeTruthy(); expect(posts).toHaveLength(0);
    });

  it('keeps matched sources separate from missing publication evidence and hides only the empty calculation', () => {
    const wrapper = wrap();
    wrapper.finality = {schema:'traze-web-finality-assessment-v1',status:'unavailable',calculation:null,comparison:null,
      issues:[{code:'finality_publication_missing'}],recorded_context_only:true,fresh_source_check_performed:false,network_requests:0};
    const before = structuredClone(wrapper);
    const fetch = vi.fn(); vi.stubGlobal('fetch',fetch);
    const create = vi.spyOn(URL,'createObjectURL');
    render(<EvidenceView wrapper={wrapper} {...props} />);
    expect(screen.getByRole('heading',{name:'No match in checked range'})).toBeTruthy();
    expect(document.getElementById('evidence-assessment')!.textContent).toContain('Source checks');
    expect(document.getElementById('evidence-assessment')!.textContent).toContain('Source views matched at the recorded check');
    expect(document.getElementById('evidence-assessment')!.textContent).toContain('No publication proof to check');
    expect(document.getElementById('reference-finality')).toBeNull();
    expect(document.getElementById('download-calculation')).toBeNull();
    expect(document.getElementById('download-link')).toBeTruthy();
    expect(evidenceSummary(wrapper,undefined,false)).toContain('Source checks: Source views matched at the recorded check');
    expect(wrapper).toEqual(before); expect(fetch).not.toHaveBeenCalled(); expect(create).not.toHaveBeenCalled();
  });

  it('shows one primary limit message while retaining raw issues, coverage and meaningful conflict warnings', () => {
    const wrapper = partial(); const before = structuredClone(wrapper);
    const view = render(<EvidenceView wrapper={wrapper} {...props} />);
    expect(document.getElementById('recovery-stop')!.textContent).toContain('Bitcoin block download limit');
    expect(document.getElementById('issues-section')).toBeNull();
    expect(document.getElementById('stop-reason')!.closest('details')).toBe(document.getElementById('coverage-details'));
    expect((document.getElementById('coverage-details') as HTMLDetailsElement).open).toBe(false);
    expect(receiptIssues(wrapper.receipt).raw).toContainEqual(wrapper.receipt.issues![0]);
    expect(evidenceSummary(wrapper,undefined,false)).toContain('Search limit reached');
    expect(wrapper).toEqual(before);
    wrapper.receipt.issues!.push({code:'source_conflict',message:'Controlled conflict'});
    wrapper.receipt.context!.bitcoin_secondary = [{height:965930,same:false}];
    view.rerender(<EvidenceView wrapper={wrapper} {...props} />);
    expect(document.getElementById('issues-list')!.textContent).toContain('Sources returned conflicting data');
    expect(document.getElementById('issues-list')!.textContent).not.toContain('Search limit reached');
    expect(document.getElementById('evidence-assessment')!.textContent).toContain('Source views conflicted');
  });

  it('does not hide a current source-recheck failure behind the original search stop', () => {
    const wrapper = partial(); wrapper.receipt.mode = 'live_metadata_refresh';
    wrapper.receipt.context!.current_view_confirmed = false;
    render(<EvidenceView wrapper={wrapper} {...props} />);
    expect(document.getElementById('issues-list')!.textContent).toContain('Source recheck limit reached');
    expect(document.getElementById('recovery-stop')!.textContent).toContain('Original search:');
  });

  it('keeps inspector and copied summary consistent when a conflicting observation overrides a matched flag', () => {
    const wrapper = wrap(positive); wrapper.receipt.context!.current_view_confirmed = true;
    wrapper.receipt.context!.bitcoin_secondary = [{height:965931,same:false}];
    const before = structuredClone(wrapper);
    render(<RecordInspector receipt={wrapper.receipt} proof={wrapper.receipt.proofs[0]} role="requested" />);
    const panel = within(screen.getByRole('complementary'));
    expect(panel.getByText('Source views conflicted at the recorded check')).toBeTruthy();
    expect(panel.queryByText('Source views matched at the recorded check')).toBeNull();
    expect(evidenceSummary(wrapper,0,false)).toContain('Source checks: Source views conflicted at the recorded check');
    expect(wrapper).toEqual(before);
  });
});

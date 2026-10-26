import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import negative from '../../demo_results/scoped_no_match.json';
import { SearchRecovery, DiscoveryAllowance } from './SearchRecovery';
import { searchStopText } from './coveragePresentation';
import App from './App';
import type { Receipt, ReceiptWrapper, WorkspaceState } from './types';

const parent = 'a'.repeat(32);
function wrapper(): ReceiptWrapper {
  const receipt = structuredClone(negative) as unknown as Receipt;
  receipt.status = 'incomplete';
  receipt.coverage!.stop_reason = 'budget_limit';
  receipt.issues = [{code:'budget_limit', message:'Bitcoin body-attempt limit reached', bitcoin_height:965931}];
  return {id:parent, filename:'controlled.json', label:'Controlled partial search', receipt,
    verification:{proofs_checked:0,current_chain_membership_checked:false},
    continuation:{available:true,reason:'next_segment',receipt_sha256:'b'.repeat(64),bitcoin_range:[965931,965950]}};
}
const state: WorkspaceState = {serving_mode:'discovery',csrf_token:'test',receipts:[],active_job:null,
  resources:{available:true,session_limit_bytes:100000000,usable_response_bytes:66000000,
    source_operations_available:true,minimum_body_reservation_bytes:4065536},
  limits:{response_mb_per_lookup:40,bitcoin_body_attempts:20,ancestry_links:256,session_response_mb:100}};

describe('bounded search continuation', () => {
  it('names the actual limit in legacy and structured receipts', () => {
    const data = wrapper().receipt;
    expect(searchStopText(data)).toContain('Bitcoin block download limit');
    expect(searchStopText(data)).toContain('965,931');
    data.coverage!.stop_detail = {limit:'response_bytes'};
    expect(searchStopText(data)).toContain('response-data limit');
    data.coverage!.stop_detail = {limit:'cumulative_response_bytes'};
    expect(searchStopText(data)).toContain('cumulative response-data allowance');
    data.coverage!.stop_detail = {limit:'requests'};
    expect(searchStopText(data)).toContain('request limit');
  });

  it('offers one explicit action without making any requests during render', () => {
    const start = vi.fn(); const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    render(<SearchRecovery wrapper={wrapper()} workspace={state} busy={false} onContinue={start} />);
    expect(screen.getByText('965,931–965,950')).toBeTruthy();
    expect(screen.getByText(/does not run automatically/)).toBeTruthy();
    expect(fetch).not.toHaveBeenCalled(); expect(start).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', {name:'Continue search'}));
    expect(start).toHaveBeenCalledTimes(1);
  });

  it('explains exhausted allowance and does not offer a reset or acquisition', () => {
    const data = wrapper(); data.continuation = {available:false,reason:'cumulative_response_bytes'};
    render(<SearchRecovery wrapper={data} workspace={state} busy={false} onContinue={vi.fn()} />);
    expect(screen.queryByRole('button', {name:'Continue search'})).toBeNull();
    expect(screen.getByText(/too small to reserve another full Bitcoin block/)).toBeTruthy();
    expect(screen.getByText(/Repeating the search does not restore allowance/)).toBeTruthy();
  });

  it('shows the newer segment as a read action and preserves historical scope', () => {
    const data = wrapper(); const open = vi.fn();
    data.continuation = {available:false,reason:'newer_result_available',latest_receipt_id:'c'.repeat(32)};
    data.receipt.discovery = {parent_receipt_id:parent,parent_receipt_sha256:'b'.repeat(64),
      root_receipt_id:parent, original_extent:[965925,965950],segment:2,earlier_coverage_is_historical:true};
    render(<SearchRecovery wrapper={data} busy={false} onOpenSaved={open} />);
    expect(screen.getByText(/Earlier scans remain historical/)).toBeTruthy();
    expect(screen.getByRole('link', {name:'Open previous segment'}).getAttribute('href')).toBe(`/?receipt=${parent}`);
    fireEvent.click(screen.getByRole('button', {name:'Open latest segment'}));
    expect(open).toHaveBeenCalledWith('c'.repeat(32));
  });

  it('keeps allowance policy out of indexed-only mode', () => {
    const view = render(<DiscoveryAllowance workspace={{...state,serving_mode:'indexed-only'}} />);
    expect(view.container.textContent).toBe('');
    view.rerender(<DiscoveryAllowance workspace={state} />);
    expect(screen.getByText(/66.00 MB available/)).toBeTruthy();
    expect(screen.getByText(/does not renew the cumulative allowance/)).toBeTruthy();
  });

  it('restores a saved search URL and sends exactly the offered receipt-bound operation', async () => {
    window.history.replaceState(null, '', `/?receipt=${parent}`);
    const data = wrapper(); const posts: object[] = [];
    vi.stubGlobal('fetch', vi.fn((path: string, options?: RequestInit) => {
      if (options?.method === 'POST') {
        posts.push({path,body:JSON.parse(options.body as string)});
        return Promise.resolve(new Response(JSON.stringify({id:'c'.repeat(32),operation:'continue',block_number:5243117,
          started_at:'2026-10-24T00:00:00Z',state:'complete',receipt_id:parent,events:[]})));
      }
      return Promise.resolve(new Response(JSON.stringify(path === '/api/state' ? state : data)));
    }));
    render(<App />);
    const button = await screen.findByRole('button', {name:'Continue search'});
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(button);
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts).toEqual([{path:'/api/continue',body:{receipt_id:parent,receipt_sha256:'b'.repeat(64),bitcoin_range:[965931,965950]}}]);
  });
});

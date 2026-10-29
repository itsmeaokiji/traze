import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import recordedData from '../../demo_results/main_catalogue_5243119.json';
import App from './App';
import { EvidenceView } from './EvidenceView';
import { evidenceSummary } from './evidenceSummary';
import type { Receipt, ReceiptWrapper, WorkspaceState } from './types';

function wrapper(): ReceiptWrapper {
  const receipt = structuredClone(recordedData) as unknown as Receipt;
  return {id:'recorded-target',label:'Recorded evidence',filename:'recorded.json',receipt,
    verification:{proofs_checked:4,current_chain_membership_checked:false}};
}
const state: WorkspaceState = {csrf_token:'local-test-token',receipts:[],active_job:null,
  resources:{available:true,total_response_bytes:73948008,session_limit_bytes:100000000},
  limits:{response_mb_per_lookup:40,bitcoin_body_attempts:20,ancestry_links:256,session_response_mb:100}};

describe('main recorded-evidence integration', () => {
  it('labels retrieval as recorded, preserving old observation times and unconfirmed identity', () => {
    const evidence = wrapper();
    render(<EvidenceView wrapper={evidence} fresh busy={false} onRecheck={vi.fn()} onNewLookup={vi.fn()} onError={vi.fn()} />);
    expect(screen.getByRole('heading',{name:'Publication found'})).toBeTruthy();
    expect(document.querySelector('#result-origin')?.textContent).toBe('Stored evidence');
    expect(document.querySelector('#observation-line')?.textContent).toContain('Historical source observations:');
    expect(document.querySelector('#observation-line')?.textContent).toContain('Oct 3, 2026');
    expect(document.querySelector('#input-identity-line')?.textContent).toContain('recorded Hemi view');
    expect(document.querySelector('#evidence-assessment')?.textContent).toContain('Not rechecked for this result');
    expect(screen.queryByText('Source checks remain incomplete.')).toBeNull();
    fireEvent.click(screen.getByText('Verification details', { exact: true }));
    expect(screen.getByRole('heading',{name:'Current source checks'})).toBeTruthy();
    expect(document.querySelector('#source-check-list')?.textContent).toContain('Not rechecked for this result');
    expect(document.querySelector('#source-check-list')?.textContent).not.toContain('Incomplete at the recorded check');
    expect((screen.getByRole('button',{name:/Recheck sources/}) as HTMLButtonElement).disabled).toBe(false);
  });

  it('saved catalogue receipts keep the recorded source qualification', () => {
    render(<EvidenceView wrapper={wrapper()} fresh={false} busy={false} onRecheck={vi.fn()} onError={vi.fn()} />);
    expect(document.querySelector('#result-origin')?.textContent).toBe('Stored evidence');
    expect(screen.getByRole('heading',{name:'Publication found'})).toBeTruthy();
  });

  it('alternate inspection preserves the original target, summary and export', () => {
    const evidence = wrapper();
    const before = JSON.stringify(evidence.receipt);
    render(<EvidenceView wrapper={evidence} fresh busy={false} onRecheck={vi.fn()} onNewLookup={vi.fn()} onError={vi.fn()} />);
    const summary = evidenceSummary(evidence,1,true);
    expect(summary).toContain('Origin: Stored evidence');
    expect(summary).toContain('Not rechecked for this result');
    expect(summary).toContain('Historical source observations:');
    expect(summary).toContain('5,243,119');
    expect(summary).toContain('Block number resolved from retained metadata');
    expect(screen.getByRole('link',{name:/Download evidence JSON/}).getAttribute('href')).toBe('/api/receipts/recorded-target/download');
    expect(JSON.stringify(evidence.receipt)).toBe(before);
  });

  it('reports incomplete compiled coverage without claiming fresh confirmation', () => {
    const evidence=wrapper();
    evidence.receipt.coverage!.gaps=[{message:'Controlled processing gap'}];
    render(<EvidenceView wrapper={evidence} fresh busy={false} onRecheck={vi.fn()} onError={vi.fn()} />);
    expect(screen.getByRole('heading',{name:'Publication found · coverage incomplete'})).toBeTruthy();
    expect(document.querySelector('#evidence-assessment')?.textContent).toContain('Not rechecked for this result');
  });

  it('explains unavailable recorded-metadata rechecks and leaves new lookup available', () => {
    const evidence=wrapper();
    delete evidence.receipt.query.covering_checkpoint;
    evidence.receipt.issues=[{code:'catalogue_recheck_unavailable'}];
    render(<EvidenceView wrapper={evidence} fresh busy={false} onRecheck={vi.fn()} onNewLookup={vi.fn()} onError={vi.fn()} />);
    expect(screen.getByText('Recorded keystone metadata is not sufficient for a source recheck. Use Run new lookup for a new search.')).toBeTruthy();
    expect((screen.getByRole('button',{name:/Recheck sources/}) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button',{name:'Run new lookup'}) as HTMLButtonElement).disabled).toBe(false);
  });

  it('main search accepts a nonpreset number and new lookup uses the receipt input', async () => {
    const evidence=wrapper();
    const posts: {path:string;body:Record<string,unknown>}[]=[];
    const fetch=vi.fn((path:string,options?:RequestInit) => {
      if(options?.method==='POST') {
        posts.push({path,body:JSON.parse(options.body as string)});
        return Promise.resolve(new Response(JSON.stringify({id:'job',operation:'lookup',block_input:5243119,block_number:5243119,
          state:'complete',started_at:'2026-10-09T00:00:00Z',events:[],receipt_id:'recorded-target'})));
      }
      return Promise.resolve(new Response(JSON.stringify(path==='/api/state'?state:evidence)));
    });
    vi.stubGlobal('fetch',fetch);
    render(<App />);
    await waitFor(()=>expect((screen.getByRole('button',{name:'Find publication'}) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.change(screen.getByLabelText('Hemi block number or hash'),{target:{value:'5243119'}});
    fireEvent.submit(document.querySelector('#lookup-form')!);
    await screen.findByRole('heading',{name:'Publication found'});
    expect(posts[0].body).toEqual({block:5243119,bitcoin_range:null});
    fireEvent.change(screen.getByLabelText('Hemi block number or hash'),{target:{value:'999'}});
    fireEvent.click(screen.getByRole('button',{name:'Run new lookup'}));
    await waitFor(()=>expect(posts).toHaveLength(2));
    expect(posts[1]).toEqual({path:'/api/lookup',body:{block:5243119,bitcoin_range:null,new_lookup:true}});
    await screen.findByRole('heading',{name:'Publication found'});
  });
});

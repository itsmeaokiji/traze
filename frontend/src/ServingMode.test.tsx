import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import recordedData from '../../demo_results/main_catalogue_5243119.json';
import App from './App';
import { ApiError } from './api';
import { CollectionStatus } from './CollectionStatus';
import { EvidenceView } from './EvidenceView';
import { OperationProgress } from './OperationProgress';
import type { Job, Receipt, ReceiptWrapper, WorkspaceState } from './types';

const state: WorkspaceState = { serving_mode: 'indexed-only', csrf_token: 'test', receipts: [], active_job: null,
  resources: { available: true, session_limit_bytes: 100000000 },
  limits: { response_mb_per_lookup: 40, bitcoin_body_attempts: 20, ancestry_links: 256, session_response_mb: 100 } };
function evidence(): ReceiptWrapper {
  return { id: 'indexed', label: 'Recorded evidence', filename: 'indexed.json',
    receipt: structuredClone(recordedData) as unknown as Receipt,
    verification: { proofs_checked: 4, current_chain_membership_checked: false } };
}

describe('indexed-only serving', () => {
  it('keeps search, route and export while removing visitor source controls', async () => {
    const posts: object[] = [];
    vi.stubGlobal('fetch', vi.fn((path: string, options?: RequestInit) => {
      if (options?.method === 'POST') {
        posts.push(JSON.parse(options.body as string));
        return Promise.resolve(new Response(JSON.stringify({ id: 'job', operation: 'lookup', block_input: 5243119,
          block_number: 5243119, phase: 'recorded_lookup', state: 'complete', started_at: '2026-10-15T00:00:00Z',
          events: [], receipt_id: 'indexed' })));
      }
      return Promise.resolve(new Response(JSON.stringify(path === '/api/state' ? state : evidence())));
    }));
    render(<App />);
    await waitFor(() => expect((screen.getByRole('button', { name: 'Find publication' }) as HTMLButtonElement).disabled).toBe(false));
    expect(screen.queryByText('Bitcoin search range')).toBeNull();
    expect(screen.getByText(/availability depends on collected coverage/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Hemi block number or hash'), { target: { value: '5243119' } });
    fireEvent.submit(document.querySelector('#lookup-form')!);
    await screen.findByRole('heading', { name: 'Recorded publication evidence' });
    expect(posts).toEqual([]);
    expect(globalThis.fetch).toHaveBeenCalledWith('/api/v1/blocks/5243119', expect.objectContaining({ cache: 'no-store' }));
    expect(screen.getByRole('list', { name: 'Hemi to Bitcoin association' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Recheck sources/ })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Run new lookup' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Copy summary' })).toBeTruthy();
    expect(screen.getByRole('link', { name: /Download JSON/ }).getAttribute('href')).toBe('/api/receipts/indexed/download');
  });

  it('does not tell indexed-only visitors to use a hidden source action', () => {
    const packet = evidence();
    delete packet.receipt.query.covering_checkpoint;
    packet.receipt.issues = [{ code: 'catalogue_recheck_unavailable' }];
    render(<EvidenceView wrapper={packet} fresh={false} busy={false} sourceOperationsEnabled={false}
      onRecheck={vi.fn()} onNewLookup={vi.fn()} onError={vi.fn()} />);
    expect(screen.getByText('Recorded keystone metadata is not sufficient for a source recheck.')).toBeTruthy();
    expect(document.body.textContent).not.toContain('Use Run new lookup');
  });

  it('reports missing indexed evidence without asserting publication absence', () => {
    const error = new ApiError({ code: 'indexed_evidence_unavailable' });
    expect(error.message).toContain('coverage may be incomplete');
    expect(error.message).toContain('does not mean no publication exists');
  });

  it('clears the previous record URL before a new unindexed query and does not reload it', async () => {
    const packet = evidence();
    packet.record_url = `/records/hemi/${packet.receipt.query.block_hash}`;
    const fetch = vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(
      path === '/api/state' ? state : path === '/api/v1/blocks/1'
        ? { error: { code: 'indexed_evidence_unavailable' } } : packet
    ), { status: path === '/api/v1/blocks/1' ? 404 : 200 })));
    vi.stubGlobal('fetch', fetch);
    const view = render(<App />);
    await waitFor(() => expect((screen.getByRole('button', { name: 'Find publication' }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.submit(document.querySelector('#lookup-form')!);
    await screen.findByRole('heading', { name: 'Recorded publication evidence' });
    expect(window.location.pathname).toBe(packet.record_url);
    await waitFor(() => expect((screen.getByRole('button', { name: 'Find publication' }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.change(screen.getByLabelText('Hemi block number or hash'), { target: { value: '1' } });
    fireEvent.submit(document.querySelector('#lookup-form')!);
    await screen.findByText(/No usable indexed evidence/);
    expect(window.location.pathname).toBe('/');
    expect(screen.queryByRole('heading', { name: 'Recorded publication evidence' })).toBeNull();
    view.unmount();
    fetch.mockClear();
    render(<App />);
    await waitFor(() => expect((screen.getByRole('button', { name: 'Find publication' }) as HTMLButtonElement).disabled).toBe(false));
    expect(fetch.mock.calls.every(([path]) => path === '/api/state')).toBe(true);
    expect(screen.queryByRole('heading', { name: 'Recorded publication evidence' })).toBeNull();
  });

  it('also clears an indexed record URL when a new discovery job fails', async () => {
    const packet = evidence();
    const record = `/records/hemi/${packet.receipt.query.block_hash}`;
    window.history.replaceState(null, '', record);
    vi.stubGlobal('fetch', vi.fn((path: string, options?: RequestInit) => Promise.resolve(new Response(JSON.stringify(
      path === '/api/state' ? { ...state, serving_mode: 'discovery' }
        : options?.method === 'POST' ? { id: 'controlled-job', operation: 'lookup', block_input: 1,
          started_at: '2026-10-20T00:00:00Z', events: [], receipt_id: null,
          state: 'error', error: { code: 'source_unavailable' } } : packet
    )))));
    render(<App />);
    await screen.findByRole('heading', { name: 'Recorded publication evidence' });
    await waitFor(() => expect((screen.getByRole('button', { name: 'Find publication' }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.change(screen.getByLabelText('Hemi block number or hash'), { target: { value: '1' } });
    fireEvent.submit(document.querySelector('#lookup-form')!);
    await screen.findByRole('alert');
    expect(window.location.pathname).toBe('/');
    expect(screen.queryByRole('heading', { name: 'Recorded publication evidence' })).toBeNull();
  });
});

describe('runner and search observations', () => {
  it('describes an indexed search without claiming public source reads', () => {
    const job: Job = { id: 'job', operation: 'lookup', phase: 'recorded_lookup', block_number: 5243119,
      started_at: '2026-10-15T00:00:00Z', state: 'running', events: [], receipt_id: null };
    render(<OperationProgress job={job} />);
    expect(screen.getByText('Checking recorded publication evidence…')).toBeTruthy();
    expect(document.body.textContent).not.toContain('Reading public sources');
    expect(screen.getByText(/Historical source observations and collected coverage remain unchanged/)).toBeTruthy();
  });

  it('does not present an unconfirmed old waiting record as an active collector', () => {
    render(<CollectionStatus value={{ state: 'unavailable', recorded_state: 'waiting',
      runner: { state: 'unavailable', last_seen_at: '2026-10-15T00:00:00Z' }, updated_at: '2026-10-14T23:00:00Z' }} />);
    expect(screen.getByText('Collection status unavailable')).toBeTruthy();
    expect(screen.queryByText('Waiting for Bitcoin blocks')).toBeNull();
    expect(screen.getByText(/An active collector could not be confirmed/)).toBeTruthy();
    expect(screen.getByText(/Runner activity does not confirm current chain views/)).toBeTruthy();
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('shows an acknowledged stop and retained progress without operator buttons', () => {
    render(<CollectionStatus value={{ state: 'stopped', processed_blocks: 21,
      issue: { message: 'Operator ended this scope' }, runner: { state: 'inactive' } }} />);
    expect(screen.getByText('Collection stopped')).toBeTruthy();
    expect(screen.getByText('Operator ended this scope')).toBeTruthy();
    expect(screen.getByText(/21 Bitcoin blocks processed/)).toBeTruthy();
    expect(screen.queryByRole('button')).toBeNull();
  });
});

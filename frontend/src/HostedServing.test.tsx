import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import recordedData from '../../demo_results/main_catalogue_5243119.json';
import App from './App';
import type { ReceiptWrapper, WorkspaceState } from './types';

const state: WorkspaceState = { deployment: 'hosted-candidate', serving_mode: 'indexed-only', csrf_token: '',
  receipts: [], active_job: null, resources: { available: false, session_limit_bytes: 0 },
  limits: { response_mb_per_lookup: 0, bitcoin_body_attempts: 0, ancestry_links: 256, session_response_mb: 0 },
  service_status: { monitoring: 'available', generated_at: '2026-10-18T00:00:00Z',
    observations: { bitcoin: { recency: 'stale' }, hemi: { recency: 'unavailable' } } } };

describe('hosted read surface', () => {
  it('uses indexed GETs with an empty token and retains the connector/export', async () => {
    vi.stubGlobal('URL', class extends URL {
      static createObjectURL() { return 'blob:hosted-evidence'; }
      static revokeObjectURL = vi.fn();
    });
    const packet = structuredClone(recordedData) as unknown as ReceiptWrapper['receipt'];
    const result: ReceiptWrapper = { id: 'indexed-test', label: 'Recorded evidence', filename: 'evidence.json',
      record_url: `/records/hemi/${packet.query.block_hash}`, receipt: packet,
      verification: { proofs_checked: 4, current_chain_membership_checked: false } };
    const fetch = vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(path === '/api/state' ? state : result))));
    vi.stubGlobal('fetch', fetch);
    render(<App />);
    await waitFor(() => expect((screen.getByRole('button', { name: 'Find publication' }) as HTMLButtonElement).disabled).toBe(false));
    expect(screen.getByText('Indexed explorer')).toBeTruthy();
    expect(screen.queryByText('Local demo')).toBeNull();
    expect(screen.queryByText('Saved results')).toBeNull();
    expect(screen.queryByText('Developer diagnostics')).toBeNull();
    expect(screen.getByText(/Bitcoin stale · Hemi unavailable/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Hemi block number or hash'), { target: { value: '5243119' } });
    fireEvent.submit(document.getElementById('lookup-form')!);
    await screen.findByRole('heading', { name: 'Recorded publication evidence' });
    expect(screen.getByRole('list', { name: 'Hemi to Bitcoin association' })).toBeTruthy();
    expect((await screen.findByRole('link', { name: /Download JSON/ })).getAttribute('href')).toMatch(/^blob:/);
    expect(fetch.mock.calls.some(([path]) => path === '/api/v1/blocks/5243119')).toBe(true);
  });

  it('qualifies an outdated monitoring snapshot independently of readable evidence', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(JSON.stringify({ ...state,
      service_status: { monitoring: 'unavailable', observations: {} } })))));
    render(<App />);
    await screen.findByText(/monitoring snapshot is unavailable or outdated/);
    expect(screen.getByText(/does not establish chain synchronization/)).toBeTruthy();
  });

  it('continues status polling without a source-operation token', async () => {
    vi.useFakeTimers();
    const fetch = vi.fn(() => Promise.resolve(new Response(JSON.stringify(state))));
    vi.stubGlobal('fetch', fetch);
    await act(async () => { render(<App />); });
    expect(fetch).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});

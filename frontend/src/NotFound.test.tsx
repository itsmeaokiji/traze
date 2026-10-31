import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import App from './App';
import type { WorkspaceState } from './types';

const state: WorkspaceState = { serving_mode: 'indexed-only', csrf_token: '', receipts: [], active_job: null,
  resources: { available: false, session_limit_bytes: 0 },
  limits: { response_mb_per_lookup: 0, bitcoin_body_attempts: 0, ancestry_links: 256, session_response_mb: 0 } };

function mock() {
  const fetch = vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(path === '/api/state' ? state
    : { error: { code: 'indexed_evidence_unavailable', message: 'No indexed evidence for this block.' } }),
  { status: path === '/api/state' ? 200 : 404 })));
  vi.stubGlobal('fetch', fetch); return fetch;
}

describe('missing browser pages', () => {
  it.each(['/missing-page', '/records/hemi/invalid', '/records/publications/invalid'])(
    'offers recovery for %s without a search or publication claim', async path => {
      window.history.replaceState(null, '', path);
      const fetch = mock(); render(<App />);
      expect(await screen.findByRole('heading', { name: 'Page not found' })).toBeTruthy();
      expect(document.title).toBe('Page not found · Traze');
      expect(screen.getByRole('link', { name: 'Go to search' }).getAttribute('href')).toBe('/');
      expect(screen.getByRole('link', { name: 'Browse publications' }).getAttribute('href')).toBe('/browse');
      expect(screen.queryByLabelText('Hemi block number or hash')).toBeNull();
      expect(fetch.mock.calls.every(([url]) => url === '/api/state')).toBe(true);
    });

  it('keeps missing indexed evidence on a valid Hemi record distinct from a missing page', async () => {
    window.history.replaceState(null, '', '/records/hemi/0x' + 'a'.repeat(64));
    mock(); render(<App />);
    expect((await screen.findByRole('alert')).textContent).toContain('No usable indexed evidence');
    expect(screen.queryByRole('heading', { name: 'Page not found' })).toBeNull();
    expect(screen.getByRole('heading', { name: 'Publication evidence' })).toBeTruthy();
    expect(document.title).toBe('Publication evidence · Traze');
  });
});

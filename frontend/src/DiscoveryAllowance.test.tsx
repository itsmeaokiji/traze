import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ApiError, issueMessage } from './api';
import { DiscoveryAllowance } from './SearchRecovery';
import type { WorkspaceState } from './types';

function state(ceiling = 100_000_000, usable = 33_399_983): WorkspaceState {
  return { serving_mode: 'discovery', csrf_token: 'controlled-token', receipts: [], active_job: null,
    resources: { available: true, session_limit_bytes: ceiling, total_response_bytes: 65_600_017,
      usable_response_bytes: usable, source_operations_available: usable > 4_065_536,
      blocked_reason: usable > 4_065_536 ? undefined : 'cumulative_response_bytes' },
    limits: { response_mb_per_lookup: 40, bitcoin_body_attempts: 20, ancestry_links: 256,
      session_response_mb: ceiling / 1_000_000, http_requests: 600, overall_seconds: 600 } };
}

describe('Operator-configured discovery allowance presentation', () => {
  it('shows counted usage, default ceiling, usable capacity and separate operation limits', () => {
    render(<DiscoveryAllowance workspace={state()} />);
    expect(document.getElementById('discovery-capacity')!.textContent).toBe('33.40 MB available for discovery');
    const limits = document.getElementById('discovery-limits') as HTMLDetailsElement;
    expect(limits.open).toBe(false);
    expect(screen.getByText(/Ceiling: 100.00 MB · 65.60 MB counted/)).toBeTruthy();
    expect(screen.getByText(/20 Bitcoin block download attempts, 40 MB of response data, 600 requests and 10 minutes/)).toBeTruthy();
    expect(screen.getByText(/does not renew the cumulative allowance/)).toBeTruthy();
  });

  it('renders a custom total ceiling without treating it as newly granted capacity', () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    const view = render(<DiscoveryAllowance workspace={state(200_000_000, 133_399_983)} />);
    expect(document.getElementById('discovery-capacity')!.textContent).toBe('133.40 MB available for discovery');
    expect(screen.getByText(/Ceiling: 200.00 MB · 65.60 MB counted/)).toBeTruthy();
    expect((document.getElementById('discovery-limits') as HTMLDetailsElement).open).toBe(false);
    expect(view.container.querySelector('input,select,form')).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('shows a lower exhausted ceiling without discarding the counted usage', () => {
    render(<DiscoveryAllowance workspace={state(66_000_000, 0)} />);
    expect(document.getElementById('discovery-capacity')!.textContent).toContain('cannot reserve another full Bitcoin block');
    expect(screen.getByText(/0.00 MB available/)).toBeTruthy();
    expect(screen.getByText(/Ceiling: 66.00 MB · 65.60 MB counted/)).toBeTruthy();
  });

  it('reports unverified accounting without fabricating zero remaining capacity', () => {
    const workspace = state(); workspace.resources.available = false;
    render(<DiscoveryAllowance workspace={workspace} />);
    expect(screen.getByText(/allowance could not be verified/)).toBeTruthy();
    expect(screen.queryByText(/0.00 MB available/)).toBeNull();
  });

  it('keeps local discovery accounting out of indexed-only pages', () => {
    const workspace = state(); workspace.serving_mode = 'indexed-only';
    const view = render(<DiscoveryAllowance workspace={workspace} />);
    expect(view.container.textContent).toBe('');
  });

  it('classifies a structured cumulative stop independently of the configured number', () => {
    expect(issueMessage({ code: 'budget_limit', limit: 'cumulative_response_bytes', message: 'Controlled variable ceiling' }))
      .toBe('The local demo has reached its search allowance. Saved results remain available.');
    expect(new ApiError({ code: 'budget_limit', limit: 'cumulative_response_bytes', message: 'Combined 200 MB session budget reached' }).message)
      .toContain('reached its search allowance');
  });

  it('handles legacy variable-ceiling errors while respecting explicit operation limits', () => {
    expect(issueMessage({ code: 'budget_limit', message: 'Combined 200 MB session budget reached' })).toContain('search allowance');
    expect(issueMessage({ code: 'budget_limit', limit: 'requests', message: 'Combined 100 MB session budget reached' }, { partial: true }))
      .toBe('Search limit reached. Results cover only the Bitcoin blocks shown.');
    expect(issueMessage({ code: 'budget_limit', limit: 'response_bytes' })).toBe('Search limit reached. The operation could not finish.');
  });

  it('makes an incomplete retained read actionable without suggesting acquisition occurred', () => {
    expect(issueMessage({ code: 'retained_read_capacity' })).toContain('Open a saved result');
    expect(issueMessage({ code: 'retained_read_capacity' })).toContain('No source search was started.');
    expect(issueMessage({ code: 'restored_state_requires_review' })).toContain('reconciliation');
    expect(issueMessage({ code: 'allowance_unavailable' })).toContain('review accounting');
  });
});

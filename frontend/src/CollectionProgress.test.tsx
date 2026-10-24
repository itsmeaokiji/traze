import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { CollectionProgress } from './CollectionProgress';
import type { CollectionStatus } from './types';

export function ongoing(): CollectionStatus {
  return { state: 'waiting', kind: 'service', progress: {
    scope: 'ongoing_service', service_start_height: 965928, checked_through_height: 965930,
    next_height: 965931, remaining_to_observed_tip: 2, last_ready_at: null,
    observed_bitcoin_tip: { height: 965932, hash: '1'.repeat(64), observed_at: '2026-10-23T00:00:00Z', age_seconds: 120, clock_valid: true },
    controlled_replay: false, qualification: 'Recorded provider view',
    current_job: { range: [965931, 965932], required_heights: 2, processed_bodies: 1,
      unprocessed_body_heights: [965932], supported_publications: 7, unsupported_publications: 0,
      pending_metadata: 3, pending_ancestry: 4, checked_update_heights: 0 },
  } };
}

describe('Recorded collection freshness', () => {
  it('keeps checked coverage, observed tip age and pending work separate', () => {
    render(<CollectionProgress value={ongoing()} showState />);
    expect(screen.getByText('Waiting for Bitcoin blocks')).toBeTruthy();
    expect(screen.getByText(/Bitcoin 965,928–965,930/)).toBeTruthy();
    expect(screen.getByText(/2 heights remain from the service cursor/)).toBeTruthy();
    expect(screen.getByText(/2m ago/)).toBeTruthy();
    expect((document.querySelector('.collection-progress-details') as HTMLDetailsElement).open).toBe(false);
    fireEvent.click(screen.getByText('Collection coverage details'));
    expect(screen.getByText(/3 publications need Hemi metadata.*4 publication paths need ancestry/)).toBeTruthy();
    expect(screen.getByText(/Unprocessed body heights: 965,932/)).toBeTruthy();
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('does not call a zero recorded gap current chain synchronization', () => {
    const value = ongoing(); value.progress!.remaining_to_observed_tip = 0;
    const { container } = render(<CollectionProgress value={value} />);
    expect(screen.getByText(/0 heights remain.*last observed tip/)).toBeTruthy();
    expect(container.textContent).not.toMatch(/caught up|fully synced|current finality/i);
    expect(screen.getByText(/do not confirm current chain membership/)).toBeTruthy();
  });

  it('shows unknown progress for missing or inconsistent observations', () => {
    const value = ongoing(); value.progress!.remaining_to_observed_tip = null;
    value.progress!.observed_bitcoin_tip!.clock_valid = false;
    value.progress!.observed_bitcoin_tip!.age_seconds = null;
    value.progress!.checked_through_height = null;
    render(<CollectionProgress value={value} />);
    expect(screen.getByText(/not yet established.*Gap to the observed tip is unavailable/)).toBeTruthy();
    expect(screen.getByText(/Observation time unavailable or inconsistent/)).toBeTruthy();
    expect(screen.queryByText(/ago/)).toBeNull();
  });

  it('labels controlled observations and avoids applying service lag to a finite job', () => {
    const value = ongoing(); value.progress!.scope = 'finite_job'; value.progress!.controlled_replay = true;
    render(<CollectionProgress value={value} showState />);
    expect(screen.getByText(/Controlled replay observations/)).toBeTruthy();
    expect(screen.queryByText(/Checked service coverage/)).toBeNull();
    expect(screen.queryByText(/heights remain/)).toBeNull();
  });
});

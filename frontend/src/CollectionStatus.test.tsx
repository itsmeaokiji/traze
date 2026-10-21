import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { CollectionStatus } from './CollectionStatus';

describe('Indexed evidence status', () => {
  it('keeps block processing separate from unresolved associations and ancestry', () => {
    render(<CollectionStatus value={{ state: 'running', range: [965934, 965941], processed_blocks: 4, processed_job_blocks: 4, requested_job_blocks: 8, supported_publications: 7, associated_publications: 3, unresolved_publications: 4, pending_ancestry: 2, source_conflicts: 0 }} />);
    expect(screen.getByText('Collection in progress')).toBeTruthy();
    expect(screen.getByText(/4 Bitcoin blocks processed/)).toBeTruthy();
    expect(screen.getByText(/4 unresolved publications.*2 pending ancestry/)).toBeTruthy();
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('describes source conflict without claiming finality or fresh verification', () => {
    const { container } = render(<CollectionStatus value={{ state: 'source_conflict', updated_at: '2026-10-14T01:00:00Z', issue: { message: 'Retained evidence remains historical.' } }} />);
    expect(screen.getByText('Source views changed')).toBeTruthy();
    expect(screen.getByText(/Conflicting source views require review/)).toBeTruthy();
    expect(container.textContent).toContain('recorded observations');
    expect(container.textContent).not.toContain('Superfinality');
  });

  it('shows a stopped cursor and operator review without suggesting complete indexing', () => {
    render(<CollectionStatus value={{ state: 'blocked', kind: 'service', next_height: 965931,
      unresolved_publications: 2, issue: { message: 'Retained evidence remains historical.' } }} />);
    expect(screen.getByText('Collection needs operator review')).toBeTruthy();
    expect((document.querySelector('.collection-diagnostics') as HTMLDetailsElement).open).toBe(false);
    fireEvent.click(screen.getByText('Collection diagnostics', { exact: true }));
    expect(screen.getByText(/Next collection height: Bitcoin 965,931/)).toBeTruthy();
    expect(screen.getByText(/contiguous, checked updates/)).toBeTruthy();
    expect(screen.getByText(/2 unresolved publications/)).toBeTruthy();
    expect(screen.queryByRole('button')).toBeNull();
  });
});

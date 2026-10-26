import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { BrowseTable } from './BrowseTable';

const hint = 'Scroll horizontally to view all columns.';
function records(label = 'Indexed Bitcoin publications') {
  return <table><caption>{label}</caption><thead><tr><th>Bitcoin block</th><th>Record</th></tr></thead>
    <tbody><tr><td>965,931</td><td><a href="/records/publications/retained">Inspect publication</a></td></tr></tbody></table>;
}

describe('Browse table scroll access', () => {
  it('offers a named keyboard scroll region only while columns overflow and updates when records change', () => {
    let notify = () => {};
    vi.stubGlobal('ResizeObserver', class {
      constructor(callback: () => void) { notify = callback; }
      observe() {}
      disconnect() {}
    });
    const { rerender } = render(<BrowseTable label="Indexed Bitcoin publications">{records()}</BrowseTable>);
    const container = document.querySelector('.browse-records')!;
    let available = 300;
    Object.defineProperties(container, { clientWidth: { get: () => available }, scrollWidth: { get: () => 600 } });
    act(() => notify());
    const region = screen.getByRole('region', { name: 'Indexed Bitcoin publications scroll area' });
    expect(region.tabIndex).toBe(0);
    expect(region.getAttribute('aria-describedby')).toBe(screen.getByText(hint).id);
    expect(screen.getByRole('table', { name: 'Indexed Bitcoin publications' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Inspect publication' }).getAttribute('href')).toBe('/records/publications/retained');
    available = 800;
    act(() => notify());
    expect(screen.queryByText(hint)).toBeNull();
    expect(screen.queryByRole('region')).toBeNull();
    expect(container.getAttribute('tabindex')).toBeNull();
    available = 300;
    rerender(<BrowseTable label="Published Hemi keystones">{records('Published Hemi keystones')}</BrowseTable>);
    expect(screen.getByRole('region', { name: 'Published Hemi keystones scroll area' })).toBeTruthy();
    expect(screen.getByRole('table', { name: 'Published Hemi keystones' })).toBeTruthy();
  });

  it('responds to window resizing without ResizeObserver and removes the fallback listener on unmount', () => {
    vi.stubGlobal('ResizeObserver', undefined);
    const { unmount } = render(<BrowseTable label="Indexed Bitcoin publications">{records()}</BrowseTable>);
    const container = document.querySelector('.browse-records')!;
    let available = 300;
    Object.defineProperties(container, { clientWidth: { get: () => available }, scrollWidth: { get: () => 600 } });
    fireEvent.resize(window);
    expect(screen.getByText(hint)).toBeTruthy();
    available = 800;
    fireEvent.resize(window);
    expect(screen.queryByText(hint)).toBeNull();
    const removed = vi.spyOn(window, 'removeEventListener');
    unmount();
    expect(removed).toHaveBeenCalledWith('resize', expect.any(Function));
    removed.mockRestore();
  });
});

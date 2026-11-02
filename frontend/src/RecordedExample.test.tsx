import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import recorded from '../../demo_results/mainnet_5243117.json';
import { RecordedExample } from './RecordedExample';
import { formatDate } from './format';

describe('recorded homepage walkthrough', () => {
  it('keeps the initial example compact without a source request', () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    render(<RecordedExample />);
    expect(screen.queryByRole('complementary')).toBeNull();
    expect(screen.getAllByRole('button', { name: /^Inspect recorded/ })).toHaveLength(3);
    expect(screen.getByText(/Current source views have not been rechecked/)).toBeTruthy();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('opens authentic historical Hemi details and returns focus on Escape', async () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    render(<RecordedExample />);
    const trigger = screen.getByRole('button', { name: 'Inspect recorded Hemi block' });
    fireEvent.click(trigger);
    const panel = await screen.findByRole('complementary', { name: 'Requested Hemi block' });
    expect(within(panel).getByText(recorded.query.block_hash)).toBeTruthy();
    expect(within(panel).getByText('Historical source observation time')).toBeTruthy();
    expect(within(panel).getByText(formatDate(recorded.context.checked_at))).toBeTruthy();
    expect(within(panel).queryByText('Recorded source-check time')).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(panel));
    fireEvent.keyDown(panel, { key: 'Escape' });
    expect(screen.queryByRole('complementary')).toBeNull();
    expect(document.activeElement).toBe(trigger);
    expect(fetch).not.toHaveBeenCalled();
  });

  it('updates the actual publication when switching to later-keystone support', async () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    render(<RecordedExample />);
    fireEvent.click(screen.getByRole('button', { name: 'Show later-keystone support' }));
    await screen.findByRole('complementary', { name: 'Later keystone' });
    expect(screen.getAllByRole('button', { name: /^Inspect recorded/ })).toHaveLength(4);
    const later = recorded.proofs.filter(proof => proof.checkpoint.height > recorded.query.covering_keystone)
      .sort((a, b) => a.checkpoint.height - b.checkpoint.height)[0];
    fireEvent.click(screen.getByRole('button', { name: 'Inspect recorded Bitcoin publication' }));
    let panel = screen.getByRole('complementary', { name: 'Bitcoin publication' });
    expect(within(panel).getByText(later.txid)).toBeTruthy();
    expect(within(panel).queryByText(recorded.proofs[0].txid)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Show covering-keystone publication' }));
    await screen.findByRole('complementary', { name: 'Covering keystone' });
    expect(screen.getAllByRole('button', { name: /^Inspect recorded/ })).toHaveLength(3);
    fireEvent.click(screen.getByRole('button', { name: 'Inspect recorded Bitcoin publication' }));
    panel = screen.getByRole('complementary', { name: 'Bitcoin publication' });
    expect(within(panel).getByText(recorded.proofs[0].txid)).toBeTruthy();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('closes the inspector with its button and retains the selected node as the keyboard return target', async () => {
    render(<RecordedExample />);
    const trigger = screen.getByRole('button', { name: 'Inspect recorded Bitcoin publication' });
    fireEvent.click(trigger);
    await screen.findByRole('complementary', { name: 'Bitcoin publication' });
    fireEvent.click(screen.getByRole('button', { name: /Close details/ }));
    await waitFor(() => expect(document.activeElement).toBe(trigger));
    expect(trigger.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByRole('complementary')).toBeNull();
  });
});

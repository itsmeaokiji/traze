import { afterEach, describe, expect, it, vi } from 'vitest';

const NativeDateTimeFormat = Intl.DateTimeFormat;

async function viewerFormat(timeZone: string) {
  vi.spyOn(Intl, 'DateTimeFormat').mockImplementation(function (locales, options) {
    expect(options?.timeZone).toBeUndefined();
    return new NativeDateTimeFormat(locales, { ...options, timeZone });
  });
  vi.resetModules();
  return (await import('./format')).formatDate;
}

afterEach(() => { vi.restoreAllMocks(); vi.resetModules(); });

describe('Viewer-local timestamps', () => {
  it.each([
    ['Asia/Manila', '2026-10-31T23:30:00Z', 'Nov 1, 2026, 7:30:00 AM GMT+8'],
    ['UTC', '2026-10-31T23:30:00Z', /^Oct 31, 2026, 11:30:00 PM GMT(?:\+0)?$/],
    ['America/New_York', '2026-10-31T23:30:00Z', 'Oct 31, 2026, 7:30:00 PM GMT-4'],
    ['America/New_York', '2026-11-01T07:30:00Z', 'Nov 1, 2026, 2:30:00 AM GMT-5'],
    ['Asia/Kathmandu', '2026-10-31T23:30:00Z', 'Nov 1, 2026, 5:15:00 AM GMT+5:45'],
  ])('shows %s at %s with its date-specific offset', async (zone, instant, expected) => {
    const formatDate = await viewerFormat(zone);
    const rendered = formatDate(instant).replace(/\s/g, ' ');
    if (expected instanceof RegExp) expect(rendered).toMatch(expected);
    else expect(rendered).toBe(expected);
  });

  it('renders equivalent UTC and offset timestamps as the same recorded moment', async () => {
    const formatDate = await viewerFormat('UTC');
    expect(formatDate('2026-11-01T07:30:00+08:00')).toBe(formatDate('2026-10-31T23:30:00Z'));
  });

  it('retains the missing/invalid-time fallback', async () => {
    const formatDate = await viewerFormat('UTC');
    expect(formatDate()).toBe('not recorded');
    expect(formatDate('')).toBe('not recorded');
    expect(formatDate('not-a-timestamp')).toBe('not recorded');
  });
});

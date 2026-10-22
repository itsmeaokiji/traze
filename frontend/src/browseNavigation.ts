const browseParameters = new Set(['view', 'keystone_height', 'bitcoin_height', 'state', 'cursor']);

// Navigation context is local UI state, never an API filter or record identity.
export function browseLocation(value?: string | null): string | undefined {
  if (!value || value.length > 4096 || !value.startsWith('/browse')) return;
  const url = new URL(value, window.location.origin);
  if (url.origin !== window.location.origin || url.pathname !== '/browse' || url.hash) return;
  const seen = new Set<string>();
  for (const [key] of url.searchParams) {
    if (!browseParameters.has(key) || seen.has(key)) return;
    seen.add(key);
  }
  return url.pathname + url.search;
}

export function browseReturn(): string | undefined {
  return browseLocation(new URLSearchParams(window.location.search).get('return'));
}

export function withBrowseReturn(record: string, from?: string): string {
  const location = browseLocation(from);
  if (!location) return record;
  const url = new URL(record, window.location.origin);
  if (url.origin !== window.location.origin || !/^\/records\/(?:publications\/[0-9a-f]{64}|hemi\/0x[0-9a-f]{64})$/.test(url.pathname)) return record;
  url.searchParams.set('return', location);
  const path = url.pathname + url.search;
  return path.length <= 2048 ? path : record;
}

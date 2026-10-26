import { useEffect, useRef, useState } from 'react';
import { api, ApiError } from './api';
import { formatDate, formatNumber, shortHash } from './format';
import type { SavedHistoryPage, SavedResult } from './types';

function savedLabel(receipt: SavedResult): string {
  const examples: Record<string, string> = { 'example-ordinary': 'Ordinary block', 'example-checkpoint': 'Keystone block', 'example-no-match': 'No match in range' };
  if (receipt.origin === 'saved_example') return examples[receipt.id] || receipt.label;
  if (receipt.available === false) return 'Saved file unavailable';
  const status = receipt.status === 'found' ? 'found' : receipt.status === 'not_found_within_coverage' ? 'no match in checked range' : 'incomplete';
  const operation = receipt.mode === 'offline_catalogue' ? 'Recorded evidence' : receipt.mode === 'live_metadata_refresh' ? 'Source recheck'
    : receipt.segment ? `Search segment ${receipt.segment}` : 'Search';
  return `${operation} · ${status}`;
}

export function SavedResults({ receipts, ready, busy, historyAvailable = true, onSelect }: {
  receipts: SavedResult[]; ready: boolean; busy: boolean; historyAvailable?: boolean; onSelect: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [history, setHistory] = useState(false);
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const [pageIndex, setPageIndex] = useState(0);
  const [page, setPage] = useState<SavedHistoryPage>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const summary = useRef<HTMLElement>(null);
  const cursor = cursors[pageIndex];
  const examples = receipts.filter(r => r.origin === 'saved_example');
  const recent = receipts.filter(r => r.origin !== 'saved_example').slice(0, 2);

  useEffect(() => {
    if (!open || !history) return;
    const controller = new AbortController();
    setLoading(true); setError(''); setPage(undefined);
    void api<SavedHistoryPage>(`/api/saved-history${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`, controller.signal)
      .then(value => { if (!controller.signal.aborted) setPage(value); })
      .catch(cause => { if (!controller.signal.aborted) setError(cause instanceof ApiError ? cause.message : 'Saved history could not be loaded. Try again.'); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [open, history, cursor, retry]);

  function choose(id: string) {
    setOpen(false); setHistory(false);
    summary.current?.focus();
    onSelect(id);
  }
  function firstPage() { setCursors([null]); setPageIndex(0); setRetry(value => value + 1); }
  function row(receipt: SavedResult) {
    const example = receipt.origin === 'saved_example';
    const target = receipt.block_number !== null ? formatNumber(receipt.block_number) : receipt.requested_block_hash ? shortHash(receipt.requested_block_hash) : 'Unresolved block';
    const unavailable = receipt.available === false;
    const detail = example ? '' : ` · ${formatDate(receipt.completed_at)} · result ${receipt.id.slice(0, 8)}`;
    return <li key={receipt.id}><button type="button" data-receipt-id={receipt.id} className="saved-item" disabled={busy || unavailable}
      aria-label={`Open saved ${savedLabel(receipt)} for Hemi ${target}${detail}`} onClick={() => choose(receipt.id)}>
      <strong>{unavailable ? 'Unavailable result' : `Hemi ${target}`}</strong><span>{savedLabel(receipt)}</span>
      {!example && <span className="saved-date">{unavailable ? `Result ${receipt.id.slice(0, 8)} · file cannot be read` : <>Completed: {formatDate(receipt.completed_at)} <span className="mono">· {receipt.id.slice(0, 8)}</span></>}</span>}
    </button></li>;
  }

  return <details id="saved-results" className="saved-evidence" open={open}>
    <summary ref={summary} aria-expanded={open} onClick={event => { event.preventDefault(); setOpen(value => !value); }}><span>Saved results</span></summary>
    {open && <div className="saved-content">
      <p>Opening a saved result does not contact public sources or refresh its observation time.</p>
      {!ready ? <p role="status">Loading saved results…</p> : <>
        <section aria-labelledby="saved-examples-heading"><h2 id="saved-examples-heading">Examples</h2>
          {examples.length ? <ul className="saved-list">{examples.map(row)}</ul> : <p>No examples are available.</p>}
        </section>
        <section aria-labelledby="saved-recent-heading"><h2 id="saved-recent-heading">Recent results</h2>
          {recent.length ? <ul className="saved-list">{recent.map(row)}</ul> : <p>{historyAvailable ? 'No recent results are available.' : 'Recent results could not be loaded. Try saved history.'}</p>}
        </section>
        <button type="button" className="secondary" aria-expanded={history} aria-controls="saved-history" disabled={busy}
          onClick={() => { if (!history) firstPage(); setHistory(value => !value); }}>{history ? 'Hide saved history' : 'View saved history'}</button>
        {history && <section id="saved-history" className="saved-history" aria-labelledby="saved-history-heading">
          <h2 id="saved-history-heading">Saved history</h2>
          <p>Latest saved files first. Dates and outcomes belong to the original result; evidence is checked when opened. New or changed files appear when you refresh.</p>
          <div aria-busy={loading}>
            {page && <ul className="saved-list">{page.items.map(row)}</ul>}
            {page && !page.items.length && <p>No saved results on this page.</p>}
            {page?.items.some(item => item.available === false) && <p>Some files are unreadable or unavailable. They have not been opened or removed.</p>}
          </div>
          <p role="status" aria-live="polite" aria-atomic="true">{loading ? 'Loading saved history…' : page ? `Page ${pageIndex + 1} loaded · ${page.items.length} saved ${page.items.length === 1 ? 'result' : 'results'}` : ''}</p>
          {error && <p role="alert">{error}</p>}
          <nav className="saved-pagination" aria-label="Saved history pages">
            <button type="button" className="secondary" disabled={busy} aria-disabled={loading || pageIndex === 0} onClick={() => { if (!loading && pageIndex > 0) setPageIndex(value => value - 1); }}>Newer results</button>
            <button type="button" className="secondary" disabled={busy} aria-disabled={loading || !page?.page.next_cursor} onClick={() => {
              if (loading || !page?.page.next_cursor) return;
              setCursors(values => [...values.slice(0, pageIndex + 1), page!.page.next_cursor]); setPageIndex(value => value + 1);
            }}>Older results</button>
            <button type="button" className="secondary" disabled={busy} aria-disabled={loading} onClick={() => { if (!loading) { if (error) setRetry(value => value + 1); else firstPage(); } }}>{error ? 'Retry history' : 'Refresh history'}</button>
            {error && <button type="button" className="secondary" disabled={busy || loading} onClick={firstPage}>Start history again</button>}
          </nav>
        </section>}
      </>}
    </div>}
  </details>;
}

import { useEffect, useRef, useState } from 'react';
import { api, delay } from './api';
import type { IndexUpdate } from './types';

export function IndexUpdateStatus({ initial, receiptId, token, enabled, busy }: {
  initial?: IndexUpdate; receiptId: string; token: string; enabled: boolean; busy: boolean;
}) {
  const [value, setValue] = useState(initial);
  const [error, setError] = useState('');
  const [retrying, setRetrying] = useState(false);
  const life = useRef<AbortController | null>(null);
  useEffect(() => { const controller = new AbortController(); life.current = controller; return () => controller.abort(); }, []);
  useEffect(() => { setValue(initial); setError(''); }, [initial, receiptId]);
  useEffect(() => {
    if (value?.state !== 'pending' || value.automatic_retry_pending === false) return;
    const controller = new AbortController();
    void (async () => {
      while (!controller.signal.aborted) {
        try {
          await delay(2000, controller.signal);
          const next = await api<IndexUpdate>(`/api/index-updates/${receiptId}`, controller.signal);
          if (controller.signal.aborted) return;
          setValue(next); setError('');
          if (next.state !== 'pending' || next.automatic_retry_pending === false) return;
        } catch (cause) {
          if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Index status could not be loaded.');
          return;
        }
      }
    })();
    return () => controller.abort();
  }, [receiptId, value?.state, value?.automatic_retry_pending]);

  async function retry() {
    if (!enabled || busy || retrying || !life.current || life.current.signal.aborted) return;
    setRetrying(true); setError('');
    try {
      const next = await api<IndexUpdate>('/api/index-retry', life.current.signal, { receipt_id: receiptId }, token);
      if (!life.current.signal.aborted) setValue(next);
    } catch (cause) {
      if (!life.current.signal.aborted) setError(cause instanceof Error ? cause.message : 'The index update could not finish.');
    } finally { if (!life.current?.signal.aborted) setRetrying(false); }
  }
  if (!value) return null;
  const complete = value.state === 'indexed' || value.state === 'already_indexed';
  const reasons: Record<string, string> = {
    index_busy: 'The index writer is busy.', ingest_capacity: 'More local processing is needed.',
    index_unavailable: 'The active index needs operator review.', receipt_changed: 'The saved receipt changed; this handoff cannot be used.',
    active_index_changed: 'The active index changed; this receipt needs an update in that index.',
  };
  const label = value.state === 'indexed' ? (value.publication_count ? 'Index updated.' : 'Search data indexed.')
    : value.state === 'already_indexed' ? 'Already indexed.' : value.state === 'not_queued' ? 'Saved evidence is not queued for indexing.'
      : value.state === 'rejected' ? 'Index update unavailable.' : 'Index update pending.';
  return <div id="index-update" className="index-update">
    <p className="field-help" role="status" aria-live="polite" aria-atomic="true"><strong>{label}</strong>
      {complete && value.record_url && <> <a href={value.record_url}>View indexed publication →</a></>}
      {complete && !value.publication_count && <> No applicable publication record was added.</>}
      {!complete && value.reason && reasons[value.reason] && <> {reasons[value.reason]}</>}
      {!complete && value.attempts !== undefined && value.attempts >= 8 && <> Automatic retries have stopped.</>}
    </p>
    {complete && (value.body_scope?.proof_only || value.body_scope?.unavailable) ? <p className="field-help">At this index update, some Bitcoin blocks had proof-only evidence or unavailable bodies. Full-scan coverage depends on the bodies actually processed in the active index; original saved search coverage is unchanged.</p> : null}
    {!complete && enabled && value.can_retry && <><button type="button" className="text-button" disabled={busy || retrying} onClick={() => void retry()}>{retrying ? 'Updating index…' : value.state === 'not_queued' ? 'Add saved evidence to index' : 'Retry index update'}</button><p className="field-help">Uses retained data only. Saved evidence remains readable; no source search is started.</p></>}
    {error && <p className="field-help" role="alert">{error}</p>}
  </div>;
}
